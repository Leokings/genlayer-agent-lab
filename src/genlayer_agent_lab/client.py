"""Small synchronous client for the public Lab HTTP interface.

No engine imports: the same client works from a separately installed agent.
"""

from __future__ import annotations

import ipaddress
from typing import Any
from urllib.parse import quote, urlsplit

import httpx


class LabError(RuntimeError):
    """An HTTP failure, carrying its status for bounded caller retry policies."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def validate_base_url(base_url: str) -> str:
    """Keep credentials local; remote installations are reached through a tunnel."""
    parsed = urlsplit(base_url)
    host = parsed.hostname or ""
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = host == "localhost"
    if (
        parsed.scheme not in {"http", "https"}
        or not loopback
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("LAB_URL must be a loopback HTTP(S) origin, e.g. http://127.0.0.1:8765")
    _ = parsed.port  # Reject malformed ports before any request.
    return base_url.rstrip("/")


class LabClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout: float = 30,
        transport: httpx.BaseTransport | None = None,
    ):
        if not token or not token.isascii() or len(token) > 256 or any(char.isspace() for char in token):
            raise ValueError("An ASCII bearer token of 1 to 256 characters without whitespace is required")
        self._token = token
        self._http = httpx.Client(
            base_url=validate_base_url(base_url),
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> LabClient:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = self._http.request(method, path, **kwargs)
        except httpx.RequestError as exc:
            raise LabError(f"Lab connection failed ({type(exc).__name__})") from None
        if not response.is_success:
            try:
                detail = response.json().get("detail", "Request rejected")
            except (ValueError, AttributeError):
                detail = "Request rejected"
            message = str(detail).replace(self._token, "[redacted]")[:1000]
            raise LabError(f"Lab HTTP {response.status_code}: {message}", response.status_code)
        try:
            return response.json()
        except ValueError:
            raise LabError("Lab returned an invalid JSON response", response.status_code) from None

    @staticmethod
    def _run_path(run_id: str) -> str:
        if not run_id or "/" in run_id or run_id in {".", ".."}:
            raise ValueError("Invalid run identifier")
        return f"/v1/runs/{quote(run_id, safe='')}"

    def list_scenarios(self) -> list[dict]:
        return self._request("GET", "/v1/scenarios")

    def list_bindings(self) -> list[dict]:
        return self._request("GET", "/v1/bindings")

    def list_runs(self) -> list[dict]:
        return self._request("GET", "/v1/runs")

    def create_run(
        self, scenario_id: str, agent: str = "external", backend: str = "glsim",
        binding_id: str | None = None,
    ) -> dict:
        if backend not in {"glsim", "fixture", "container-glsim", "studio"}:
            raise ValueError("Unsupported runtime backend")
        if ((binding_id is not None and backend not in {"container-glsim", "studio"})
                or (backend == "container-glsim" and not binding_id)):
            raise ValueError("Bindings require container-glsim or studio; container-glsim requires a binding")
        payload = {"scenario_id": scenario_id, "agent": agent, "backend": backend}
        if binding_id is not None:
            payload["binding_id"] = binding_id
        return self._request("POST", "/v1/runs", json=payload)

    def get_run(self, run_id: str) -> dict:
        return self._request("GET", self._run_path(run_id))

    def cancel_run(self, run_id: str) -> dict:
        return self._request("POST", self._run_path(run_id) + "/cancel")

    def report(self, run_id: str) -> dict:
        return self._request("GET", self._run_path(run_id) + "/report")

    def observe(self, run_id: str) -> dict:
        return self._request("POST", self._run_path(run_id) + "/observe")

    def request_decision(self, run_id: str, idempotency_key: str) -> dict:
        return self._request(
            "POST", self._run_path(run_id) + "/decision", json={"idempotency_key": idempotency_key}
        )

    def read_decision(self, run_id: str) -> dict:
        return self._request("GET", self._run_path(run_id) + "/decision")

    def act(self, run_id: str, action: dict) -> dict:
        return self._request("POST", self._run_path(run_id) + "/actions", json=action)

    def finish(self, run_id: str) -> dict:
        return self._request("POST", self._run_path(run_id) + "/finish")

    @staticmethod
    def _workflow_path(run_id: str) -> str:
        if not run_id or "/" in run_id or run_id in {".", ".."}:
            raise ValueError("Invalid workflow identifier")
        return f"/v1/workflows/{quote(run_id, safe='')}"

    def workflow_create(self, spec: dict[str, Any]) -> dict:
        return self._request("POST", "/v1/workflows", json={"spec": spec})

    def workflow_list(self) -> list[dict]:
        return self._request("GET", "/v1/workflows")

    def workflow_get(self, run_id: str) -> dict:
        return self._request("GET", self._workflow_path(run_id))

    def workflow_observe(self, run_id: str) -> dict:
        return self._request("POST", self._workflow_path(run_id) + "/observe")

    def workflow_invoke(
        self, run_id: str, operation: str, arguments: dict[str, Any], idempotency_key: str,
        expected_decision_id: str | None = None,
    ) -> dict:
        payload = {"operation": operation, "arguments": arguments,
                   "idempotency_key": idempotency_key}
        if expected_decision_id is not None:
            payload["expected_decision_id"] = expected_decision_id
        return self._request("POST", self._workflow_path(run_id) + "/operations", json=payload)

    def workflow_appeal(
        self, run_id: str, idempotency_key: str, expected_decision_id: str,
    ) -> dict:
        return self._request("POST", self._workflow_path(run_id) + "/appeals", json={
            "idempotency_key": idempotency_key, "expected_decision_id": expected_decision_id,
        })

    def workflow_finish(self, run_id: str) -> dict:
        return self._request("POST", self._workflow_path(run_id) + "/finish")

    def workflow_cancel(self, run_id: str) -> dict:
        return self._request("POST", self._workflow_path(run_id) + "/cancel")

    def workflow_report(self, run_id: str) -> dict:
        return self._request("GET", self._workflow_path(run_id) + "/report")
