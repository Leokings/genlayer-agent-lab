import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts/verify-custom-clients.py"
spec = importlib.util.spec_from_file_location("verify_custom_clients", SCRIPT)
verification = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verification)


def successful_report():
    binding = {"id": "delivery-assessment", "binding_sha256": "b" * 64, "source_sha256": "c" * 64}
    report = {"run_id": "custom-run", "scenario": "escrow-normal", "status": "completed",
              "verdict": "pass", "agent": "external", "grades": {
                  name: {"status": "pass"} for name in verification.GRADE_NAMES
              }, "manifest": {"backend": "container-glsim", "binding": binding,
                              "runtime": {"backend": "container-glsim", **binding,
                                          "image_id": "sha256:" + "a" * 64,
                                          "contract_executed": True, "execution_success": True,
                                          "mocked_io": True, "isolated": True}}}
    return binding, report


def test_child_environment_never_inherits_administrator_or_provider_access(monkeypatch):
    for key in ("LAB_TOKEN", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "HTTP_PROXY", "HTTPS_PROXY",
                "NODE_OPTIONS", "PYTHONPATH", "AWS_SECRET_ACCESS_KEY", "LAB_DATA_DIR"):
        monkeypatch.setenv(key, "administrator-or-provider-secret")
    env = verification.child_environment("http://127.0.0.1:8765", "run-only-secret", "one-run")
    assert env["LAB_TOKEN"] == "run-only-secret"
    assert env["LAB_ROLE"] == "agent" and env["LAB_RUN_ID"] == "one-run"
    assert "administrator-or-provider-secret" not in json.dumps(env)
    assert "LAB_DATA_DIR" not in env and "HTTP_PROXY" not in env and "NODE_OPTIONS" not in env


def test_verification_requires_server_evidence_and_exports_only_summary():
    binding, report = successful_report()
    report["findings"] = ["private-fixture"]
    report["manifest"]["binding"]["definition"] = {"llm_response": "private-fixture"}
    report["manifest"]["runtime"]["raw_result"] = "private-fixture"
    verification.check_report(report, binding)
    compact = verification.summary("python", report)
    assert "private-fixture" not in json.dumps(compact)
    assert compact["binding_sha256"] == binding["binding_sha256"]


@pytest.mark.parametrize("mutation", ["fixture", "no_execution", "wrong_hash", "failed_grade", "wrong_image"])
def test_fake_or_mismatched_contract_evidence_cannot_pass(mutation):
    binding, report = successful_report()
    if mutation == "fixture":
        report["manifest"]["runtime"]["backend"] = "fixture"
    elif mutation == "no_execution":
        report["manifest"]["runtime"]["contract_executed"] = False
    elif mutation == "wrong_hash":
        report["manifest"]["runtime"]["source_sha256"] = "d" * 64
    elif mutation == "failed_grade":
        report["grades"]["behavior"]["status"] = "fail"
    else:
        report["manifest"]["runtime"]["image_id"] = "mutable-tag:latest"
    with pytest.raises(RuntimeError):
        verification.check_report(report, binding)
