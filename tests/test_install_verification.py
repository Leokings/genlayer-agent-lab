"""Guards for the clean artifact verification boundary, not a second app suite."""

import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

SPEC = importlib.util.spec_from_file_location(
    "verify_install", Path(__file__).parents[1] / "scripts/verify-install.py")
verification = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verification)


def test_clean_install_environment_cannot_inherit_source_credentials_or_proxies(tmp_path, monkeypatch):
    for name in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "LAB_TOKEN", "LAB_URL", "OPENAI_API_KEY",
                 "AWS_SECRET_ACCESS_KEY", "HTTP_PROXY", "HTTPS_PROXY", "PIP_INDEX_URL", "UV_INDEX_URL",
                 "GITHUB_TOKEN", "DOCKER_HOST", "NODE_OPTIONS", "PYTEST_ADDOPTS"):
        monkeypatch.setenv(name, "credential-or-source-sentinel")
    python = tmp_path / "environment" / "bin" / "python"
    env = verification.clean_environment(tmp_path, python)
    assert "credential-or-source-sentinel" not in str(env)
    assert Path(env["HOME"]).is_relative_to(tmp_path)
    assert Path(env["LOCALAPPDATA"]).is_relative_to(tmp_path)
    assert env["PATH"].startswith(str(python.parent))
    assert env["PIP_CONFIG_FILE"] == verification.os.devnull


def test_wheel_selection_refuses_ambiguous_old_artifacts(tmp_path):
    for tag in ("a3", "a4"):
        (tmp_path / f"genlayer_agent_lab-0.1.0{tag}-py3-none-any.whl").write_bytes(b"artifact")
    with pytest.raises(verification.VerificationError, match="exactly one"):
        verification.choose_wheel(str(tmp_path / "*.whl"))
    wheel = tmp_path / "genlayer_agent_lab-0.1.0a4-py3-none-any.whl"
    assert verification.choose_wheel(str(wheel)) == wheel.resolve()


def test_verification_installs_private_hashed_wheel_when_original_is_rebuilt(tmp_path, monkeypatch):
    wheel = tmp_path / "genlayer_agent_lab-0.1.0a7-py3-none-any.whl"
    original = b"original selected artifact"
    wheel.write_bytes(original)
    installed = []

    def run(command, *, cwd, env, stage, **kwargs):
        if stage == "create fresh virtual environment":
            wheel.write_bytes(b"newly rebuilt artifact at the same source path")
        elif stage == "install built wheel and its declared dependencies":
            artifact = Path(command[-1])
            assert artifact != wheel and artifact.name == wheel.name
            assert artifact.is_relative_to(cwd.parent)
            installed.append(artifact.read_bytes())
        elif stage == "exercise installed package":
            return json.dumps({"verification": "pass"})
        return ""

    monkeypatch.setattr(verification, "run", run)
    result = verification.verify(wheel)
    assert result["verification"] == "pass"
    assert installed == [original]
    assert result["wheel_sha256"] == hashlib.sha256(original).hexdigest()
    assert result["wheel_sha256"] != hashlib.sha256(wheel.read_bytes()).hexdigest()


def test_smoke_grade_does_not_trust_only_a_claimed_pass():
    with pytest.raises(verification.VerificationError, match="every grade"):
        verification._checked_report({"status": "completed", "verdict": "pass"})


def test_smoke_action_checks_decision_scope_and_finality():
    task = {"operation": "release_payment", "resource_id": "escrow-1", "policy_version": "v1", "amount": 100}
    decision = {"status": "final", "execution_result": "success", "verdict": "approve",
                "resource_id": "another-resource", "policy_version": "v1"}
    with pytest.raises(verification.VerificationError, match="scoped approval"):
        verification._action(task, decision, "run-1")


def test_doctor_failure_diagnostic_never_exports_raw_payload_or_paths():
    payload = json.dumps({"runtime": {"error": "PermissionError: [Errno 13] /secret/path TOKEN_SENTINEL"},
                          "admin_token": "CREDENTIAL_SENTINEL"}).encode()
    detail = verification._cli_failure("CLI doctor", payload)
    assert detail == ": PermissionError, OS-error-13"
    assert verification._cli_failure("CLI init", payload) == ""


@pytest.mark.parametrize("damage", [None, "unauthenticated", "catalog", "stale_review", "changed_review"])
def test_installed_onboarding_probe_checks_review_integrity_without_starting_runs(tmp_path, damage):
    from genlayer_agent_lab.api import create_app, read_admin_token
    from genlayer_agent_lab.project_verification import examples_root

    def forbidden(*args, **kwargs):
        pytest.fail("Authoring verification must not start a project or call Studio")

    manager = SimpleNamespace(create=forbidden)
    app = create_app(tmp_path, engine=SimpleNamespace(workflows=manager))
    token = read_admin_token(tmp_path)
    url = "http://127.0.0.1:8765"
    seen = []
    with TestClient(app, base_url=url) as client:
        class Boundary:
            def get(self, path, **kwargs):
                seen.append(path)
                response = client.get(path, **kwargs)
                if damage == "unauthenticated" and not kwargs.get("headers"):
                    return httpx.Response(200, json={"templates": []})
                if damage == "catalog" and response.status_code == 200:
                    body = response.json()
                    body["templates"].pop()
                    return httpx.Response(200, json=body)
                return response

            def post(self, path, **kwargs):
                seen.append(path)
                response = client.post(path, **kwargs)
                if damage == "stale_review" and response.status_code == 409:
                    return httpx.Response(200, json={})
                if damage == "changed_review" and path.endswith("/review") and response.status_code == 200:
                    body = response.json()
                    body["spec"]["fixtures"]["initial"][0]["response"]["confidence_bps"] += 1
                    return httpx.Response(200, json=body)
                return response

        if damage:
            with pytest.raises(verification.VerificationError):
                verification._onboarding_probe(Boundary(), url, token, examples_root())
        else:
            result = verification._onboarding_probe(Boundary(), url, token, examples_root())
            assert result["template_count"] == 11 and result["controlled_responses_preserved"] is True
            assert result["stale_review_rejected"] is True and result["project_runs_created"] == 0
            assert token not in json.dumps(result)
    assert all(path.startswith(url + "/v1/onboarding/") for path in seen)
    assert not any(path.endswith("/status") for path in seen)


def test_installed_onboarding_probe_refuses_source_checkout_fallback(tmp_path):
    with pytest.raises(verification.VerificationError, match="installed kit"):
        verification._onboarding_probe(None, "http://127.0.0.1:8765", "private-token", tmp_path)
