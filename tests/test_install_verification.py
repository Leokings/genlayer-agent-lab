"""Guards for the clean artifact verification boundary, not a second app suite."""

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

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
