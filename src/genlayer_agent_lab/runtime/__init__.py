"""Fresh-process, fixture-only execution of our bundled GenLayer contract.

GLSim executes the real Python contract and its validator functions. It is a
simplified local simulator, not a public network or a hostile-code sandbox.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from .pins import BUNDLE_SHA256, GENVM_VERSION, RUNNER_HASH, TEST_SUITE_VERSION


def _worker_environment(work_dir: str) -> dict[str, str]:
    # Deliberate allowlist: never inherit wallet/model keys, proxies, PYTHONPATH,
    # cloud credentials or a developer's provider configuration into the worker.
    allowed = ("SystemRoot", "SYSTEMROOT", "WINDIR", "USERPROFILE", "HOME", "LOCALAPPDATA")
    env = {key: os.environ[key] for key in allowed if key in os.environ}
    env.update(
        TEMP=work_dir,
        TMP=work_dir,
        TMPDIR=work_dir,
        PYTHONUTF8="1",
        PYTHONDONTWRITEBYTECODE="1",
        PYTHONNOUSERSITE="1",
    )
    return env


def _execute_request(request: dict, *, timeout: float) -> dict:
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    with tempfile.TemporaryDirectory(prefix="gl-agent-lab-") as work_dir:
        try:
            completed = subprocess.run(
                [sys.executable, "-I", "-B", "-m", "genlayer_agent_lab.runtime.worker"],
                input=json.dumps(request),
                text=True,
                encoding="utf-8",
                capture_output=True,
                cwd=work_dir,
                env=_worker_environment(work_dir),
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                "GenLayer worker timed out; no decision was accepted. "
                "First-time runner preparation may require running doctor first."
            ) from exc
        except OSError as exc:
            raise RuntimeError(f"Cannot launch GenLayer worker: {type(exc).__name__}") from exc
    try:
        result = json.loads(completed.stdout)
    except (ValueError, TypeError) as exc:
        raise RuntimeError(
            f"GenLayer worker returned invalid output (exit {completed.returncode})"
        ) from exc
    if completed.returncode != 0 or not isinstance(result, dict) or result.get("error"):
        detail = result.get("error", "worker failed") if isinstance(result, dict) else "worker failed"
        raise RuntimeError(f"GenLayer runtime: {str(detail)[:800]}")
    provenance = result.get("provenance", {})
    if not isinstance(provenance, dict) or not isinstance(provenance.get("consensus"), dict):
        raise RuntimeError("GenLayer worker returned an incomplete execution receipt")
    if (
        result.get("verdict") not in ("approve", "deny")
        or provenance.get("backend") != "glsim"
        or provenance.get("execution_success") is not True
        or provenance.get("mocked_io") is not True
        or provenance.get("runner_hash") != RUNNER_HASH
        or provenance.get("bundle_sha256") != BUNDLE_SHA256
        or provenance.get("consensus", {}).get("status") != "FINALIZED"
    ):
        raise RuntimeError("GenLayer worker returned an incomplete or unsuccessful execution receipt")
    return result


def evaluate(evidence: str, fixture_verdict: str, *, timeout: float = 60) -> dict:
    """Evaluate through the bundled contract, using an explicit external IO fixture.

    The fixture is not an expected grade. A wrong fixture can produce a contract
    verdict that fails the independently defined scenario expectation.
    """
    if not isinstance(evidence, str) or not 1 <= len(evidence) <= 16000:
        raise ValueError("evidence must contain 1 to 16000 characters")
    if fixture_verdict not in ("approve", "deny"):
        raise ValueError("fixture_verdict must be approve or deny")
    return _execute_request(
        {"evidence": evidence, "fixture_response": {"verdict": fixture_verdict}},
        timeout=timeout,
    )


def doctor() -> dict:
    """Prepare pinned artifacts and execute a real contract readiness probe."""
    info = {
        "backend": "glsim",
        "python": sys.version.split()[0],
        "python_executable": str(Path(sys.executable)),
        "genlayer_test_version": TEST_SUITE_VERSION,
        "genvm_version": GENVM_VERSION,
        "runner_hash": RUNNER_HASH,
        "bundle_sha256": BUNDLE_SHA256,
        "mode": "fixture-only; bundled trusted contract; simplified consensus",
    }
    try:
        result = evaluate("Runtime readiness probe: valid evidence.", "approve", timeout=300)
        if result["verdict"] != "approve":
            raise RuntimeError("Runtime readiness probe returned the wrong verdict")
        return {**info, "ready": True, "status": "ready", "provenance": result["provenance"]}
    except (RuntimeError, ValueError) as exc:
        return {**info, "ready": False, "status": "error", "error": str(exc)}
