import copy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts/verify-custom-clients.py"
spec = importlib.util.spec_from_file_location("verify_studio_clients", SCRIPT)
verification = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verification)


def studio_report():
    binding = {"id": "delivery-assessment", "binding_sha256": "b" * 64, "source_sha256": "c" * 64}
    transactions = {"deployment": "0x" + "d" * 64, "execution": "0x" + "e" * 64}
    evidence = {
        "backend": "studio", "verification": "pass", "verdict": "approve",
        "binding": copy.deepcopy(binding), "transactions": transactions,
        "expected_pins": {"studio_commit": "a" * 40},
        "stack_pins": {"provenance": "owner_reported", "rpc_release_verified": False,
                       "source_commit": "a" * 40, "image_id": "sha256:" + "f" * 64,
                       "network_internal": True},
        "observations": [
            {"checkpoint": name + "_finalization_result", "tx_id": tx_id,
             "status": "FINALIZED", "execution_success": True}
            for name, tx_id in transactions.items()
        ],
    }
    report = {"run_id": "studio-run", "scenario": "escrow-normal", "status": "completed",
              "verdict": "pass", "agent": "external", "grades": {
                  name: {"status": "pass"} for name in verification.GRADE_NAMES
              }, "manifest": {"backend": "studio", "binding": copy.deepcopy(binding),
                              "runtime": {"backend": "studio", "studio_evidence": evidence,
                                          "contract_executed": True, "execution_success": True,
                                          "mocked_io": True}}}
    return binding, report


def test_studio_summary_uses_owner_image_and_never_exports_raw_evidence():
    binding, report = studio_report()
    report["manifest"]["runtime"]["raw_result"] = "private-contract-return"
    report["manifest"]["binding"]["definition"] = {"llm_response": "private-fixture"}
    verification.check_report(report, binding, "studio")
    compact = verification.summary("typescript", report)
    assert compact["image_id"] == "sha256:" + "f" * 64
    assert compact["binding_sha256"] == binding["binding_sha256"]
    assert "private-" not in json.dumps(compact)
    assert "studio_evidence" not in compact
    # The default remains the older container worker, so Studio cannot silently
    # satisfy a check that requested that other backend.
    with pytest.raises(RuntimeError):
        verification.check_report(report, binding)


@pytest.mark.parametrize("mutation", [
    "failed_conformance", "accepted_only", "failed_execution", "wrong_transaction",
    "missing_deployment", "mutable_image", "mismatched_commit", "rpc_provenance_claim",
    "external_network", "wrong_binding", "wrong_source", "scripted_only", "invalid_verdict",
])
def test_studio_checks_require_matching_actual_finalized_evidence(mutation):
    binding, report = studio_report()
    runtime = report["manifest"]["runtime"]
    evidence = runtime["studio_evidence"]
    if mutation == "failed_conformance":
        evidence["verification"] = "inconclusive"
    elif mutation == "accepted_only":
        evidence["observations"][-1]["status"] = "ACCEPTED"
    elif mutation == "failed_execution":
        evidence["observations"][-1]["execution_success"] = False
    elif mutation == "wrong_transaction":
        evidence["observations"][-1]["tx_id"] = "0x" + "0" * 64
    elif mutation == "missing_deployment":
        evidence["observations"].pop(0)
    elif mutation == "mutable_image":
        evidence["stack_pins"]["image_id"] = "studio:latest"
    elif mutation == "mismatched_commit":
        evidence["stack_pins"]["source_commit"] = "0" * 40
    elif mutation == "rpc_provenance_claim":
        evidence["stack_pins"]["rpc_release_verified"] = True
    elif mutation == "external_network":
        evidence["stack_pins"]["network_internal"] = False
    elif mutation == "wrong_binding":
        evidence["binding"]["id"] = "unrelated"
    elif mutation == "wrong_source":
        evidence["binding"]["source_sha256"] = "0" * 64
    elif mutation == "scripted_only":
        evidence["observations"] = []
        report["events"] = [{"type": "decision_read", "data": {"status": "final"}}]
    else:
        evidence["verdict"] = "unmapped"
    with pytest.raises(RuntimeError):
        verification.check_report(report, binding, "studio")


def test_all_three_studio_examples_receive_only_scoped_tokens(monkeypatch):
    binding, report = studio_report()
    requests, children = [], []

    class FakeAdmin:
        def __init__(self, url, token):
            assert url == "http://127.0.0.1:8765" and token == "admin-secret"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def list_bindings(self):
            return [binding]

        def list_scenarios(self):
            return [{"id": "escrow-normal"}]

        def create_run(self, scenario, **kwargs):
            requests.append((scenario, kwargs))
            return {"run_id": f"run-{len(requests)}", "agent_token": f"scope-{len(requests)}"}

        def get_run(self, run_id):
            return {"status": "running"}

        def report(self, run_id):
            return {**copy.deepcopy(report), "run_id": run_id}

    def child(command, **kwargs):
        children.append((command, kwargs))
        assert kwargs["env"]["LAB_TOKEN"] == f"scope-{len(children)}"
        assert kwargs["env"]["LAB_RUN_ID"] == f"run-{len(children)}"
        assert kwargs["env"]["LAB_ROLE"] == "agent"
        assert "admin-secret" not in json.dumps(kwargs["env"])
        return SimpleNamespace(returncode=0, stdout="untrusted-child-output", stderr="secret-stderr")

    monkeypatch.setenv("LAB_TOKEN", "admin-secret")
    monkeypatch.setattr(verification, "LabClient", FakeAdmin)
    monkeypatch.setattr(verification.subprocess, "run", child)
    results, code = verification.verify("http://127.0.0.1:8765", "admin-secret",
                                        "delivery-assessment", "escrow-normal", 180, "node",
                                        backend="studio")
    assert code == 0
    assert [item["client"] for item in results] == ["python", "typescript", "mcp"]
    assert all(item[1] == {"agent": "external", "backend": "studio",
                           "binding_id": "delivery-assessment"} for item in requests)
    assert all(item["image_id"] == "sha256:" + "f" * 64 for item in results)
    assert "--_mcp-agent" in children[-1][0]
    assert "untrusted-child-output" not in json.dumps(results)
    assert "secret-stderr" not in json.dumps(results)


def test_unknown_backend_rejected_before_connecting(monkeypatch):
    monkeypatch.setattr(verification, "LabClient", lambda *a: pytest.fail("must not connect"))
    with pytest.raises(RuntimeError, match="Unsupported"):
        verification.verify("http://127.0.0.1:8765", "admin", "delivery-assessment",
                            "escrow-normal", 180, "node", backend="public-chain")
