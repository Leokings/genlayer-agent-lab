"""Runtime boundary tests plus actual fresh-process GLSim conformance tests."""

import json
import subprocess

import pytest

from genlayer_agent_lab import runtime
from genlayer_agent_lab.runtime.pins import BUNDLE_SHA256, RUNNER_HASH


def test_worker_environment_does_not_inherit_secrets(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-leak")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "must-not-leak")
    monkeypatch.setenv("PRIVATE_KEY", "must-not-leak")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "must-not-leak")
    monkeypatch.setenv("PYTHONPATH", "untrusted-import-path")
    env = runtime._worker_environment(str(tmp_path))
    assert "must-not-leak" not in env.values()
    assert "PYTHONPATH" not in env
    assert env["TEMP"] == str(tmp_path)


@pytest.mark.parametrize("bad", ["", "approve-ish", None, {}])
def test_reject_invalid_fixture_before_launch(bad):
    with pytest.raises(ValueError, match="fixture_verdict"):
        runtime.evaluate("some evidence", bad)


def test_timeout_is_infrastructure_failure(monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 1)

    monkeypatch.setattr(runtime.subprocess, "run", timeout)
    with pytest.raises(RuntimeError, match="timed out"):
        runtime.evaluate("valid", "approve", timeout=1)


@pytest.mark.parametrize(
    ("stdout", "exit_code"),
    [
        ("not-json", 0),
        (json.dumps({"error": "contract crashed"}), 1),
        (json.dumps({"verdict": "approve"}), 0),
        (json.dumps({"verdict": "approve", "provenance": []}), 0),
        (json.dumps({"verdict": "approve", "provenance": {}}), 1),
    ],
)
def test_worker_failure_or_partial_receipt_never_becomes_success(monkeypatch, stdout, exit_code):
    monkeypatch.setattr(
        runtime.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], exit_code, stdout, ""),
    )
    with pytest.raises(RuntimeError):
        runtime.evaluate("valid", "approve")


@pytest.mark.runtime
def test_actual_contract_runs_validators_and_starts_clean_each_time():
    approved = runtime.evaluate("Delivery verified by recipient.", "approve")
    denied = runtime.evaluate("Delivery evidence is missing.", "deny")
    repeated = runtime.evaluate("Delivery verified by recipient.", "approve")
    assert [approved["verdict"], denied["verdict"], repeated["verdict"]] == [
        "approve", "deny", "approve"
    ]
    assert len({item["provenance"]["worker_pid"] for item in (approved, denied, repeated)}) == 3
    for item in (approved, denied, repeated):
        receipt = item["provenance"]
        assert receipt["runner_hash"] == RUNNER_HASH
        assert receipt["bundle_sha256"] == BUNDLE_SHA256
        assert receipt["initial_state"] == {"verdict": "pending", "evidence": ""}
        assert receipt["final_state"]["verdict"] == item["verdict"]
        assert receipt["consensus"]["votes"] == ["agree"] * 3
        assert receipt["consensus"]["captured_validators"] == 1
        assert receipt["consensus"]["llm_fixture_calls"] == 4
        assert receipt["strict_mocks"] is True
        assert receipt["consensus"]["public_network"] is False
    assert approved["provenance"]["contract_address"] == repeated["provenance"]["contract_address"]
    assert approved["provenance"]["final_state"] == repeated["provenance"]["final_state"]


@pytest.mark.runtime
@pytest.mark.parametrize("response", ["approve", {}, {"verdict": "maybe"}])
def test_malformed_model_response_fails_actual_contract(response):
    with pytest.raises(RuntimeError, match="Contract execution failed"):
        runtime._execute_request(
            {"evidence": "Delivery evidence.", "fixture_response": response}, timeout=60
        )


@pytest.mark.runtime
def test_unicode_work_directory_is_cleaned(monkeypatch, tmp_path):
    work = tmp_path / "agent lab café 日本語"
    work.mkdir()
    monkeypatch.setattr(runtime.tempfile, "tempdir", str(work))
    assert runtime.evaluate("Unicode evidence: café 日本語", "approve")["verdict"] == "approve"
    assert list(work.iterdir()) == []


def test_doctor_reports_runtime_failure(monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("missing runtime")

    monkeypatch.setattr(runtime, "evaluate", fail)
    result = runtime.doctor()
    assert result["ready"] is False
    assert "missing runtime" in result["error"]


def test_preparation_budget_does_not_increase_normal_evaluation_deadline(monkeypatch):
    calls = []

    def executed(request, *, timeout):
        calls.append(timeout)
        return {"verdict": "approve", "provenance": {"execution_success": True}}

    monkeypatch.setattr(runtime, "_execute_request", executed)
    assert runtime.doctor()["ready"]
    assert runtime.doctor(timeout=1200)["preparation_timeout_seconds"] == 1200
    runtime.evaluate("valid", "approve")
    assert calls == [900, 1200, 60]


@pytest.mark.parametrize("timeout", [float("nan"), float("inf"), 0, -1, 1801])
def test_doctor_rejects_invalid_deadline_before_preparing(monkeypatch, timeout):
    def unexpected(*args, **kwargs):
        pytest.fail("An invalid deadline must not start the worker")

    monkeypatch.setattr(runtime, "evaluate", unexpected)
    with pytest.raises(ValueError, match="Doctor timeout"):
        runtime.doctor(timeout=timeout)
