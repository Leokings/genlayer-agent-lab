"""Recovery workflow faults: ownership, quiescence, preserved state, and cleanup."""

import copy
import json
from types import SimpleNamespace

import pytest

from genlayer_agent_lab import studio_recovery as recovery
from genlayer_agent_lab.recovery import _exclusive_lock
from genlayer_agent_lab.runtime import studio_stack as stack
from genlayer_agent_lab.runtime.container import CommandResult

ADDRESS = "0x" + "a" * 40
TX_DEPLOY = "0x" + "a" * 64
TX_APPROVE = "0x" + "b" * 64
TX_DENY = "0x" + "c" * 64


def receipt(tx_id, result):
    return {"tx_id": tx_id, "contract_address": ADDRESS, "status": "FINALIZED",
            "execution_success": True, "raw_result": result, "result_code": "return",
            "votes": ["agree"] * 5, "rounds": [{"kind": "Accepted",
                "validator_votes": ["agree"] * 5}], "appealed": False,
            "private_key": "PRIVATE_RECEIPT_KEY", "plugin_config": {"key": "PROVIDER_SECRET"}}


@pytest.fixture
def rig(tmp_path, monkeypatch):
    state = stack.initialize(tmp_path)
    state["image_id"] = "sha256:" + "d" * 64
    stack._save(tmp_path / "studio", state)
    project = "gl-agent-lab-" + state["owner"]
    labels = {stack.OWNER_LABEL: state["owner"], stack.PROJECT_LABEL: project}
    ctx = SimpleNamespace(state=state, mode="running", generation=0, pending=False,
        valid=True, calls=[], fault=None, state_hash="a" * 64, receipts={
            TX_DEPLOY: receipt(TX_DEPLOY, None), TX_APPROVE: receipt(TX_APPROVE, "approve")})

    def inventory(*args, deadline=None):
        assert deadline is not None
        containers = [{"Id": f"{index + 1 + ctx.generation * 16:064x}",
            "Config": {"Labels": {**labels, stack.SERVICE_LABEL: name}}}
            for index, name in enumerate(stack.compose_config(state)["services"])]
        volumes = {name: {"Name": project + "_" + name, "CreatedAt": "2026-09-06T10:00:00Z",
                          "Labels": labels} for name in ("database", "vm-cache")}
        if ctx.fault == "volume" and ctx.mode == "stopped":
            volumes["database"]["CreatedAt"] = "2026-09-06T10:01:00Z"
        if ctx.fault == "missing_volume" and ctx.mode == "stopped":
            volumes["database"] = None
        if ctx.fault == "foreign":
            containers[0]["Config"]["Labels"][stack.OWNER_LABEL] = "foreign"
        return {"containers": containers if ctx.mode == "running" else [],
            "network": {"Labels": labels} if ctx.mode == "running" else None,
            "access_network": {"Labels": labels} if ctx.mode == "running" else None,
            "volumes": volumes}

    def compose(directory, args, *, timeout, endpoint):
        assert directory == tmp_path and 0 < timeout <= 600 and endpoint == "unix:///test.sock"
        # Both locks must remain held even for failure recovery.
        with pytest.raises(RuntimeError):
            with _exclusive_lock(tmp_path):
                pass
        with pytest.raises(RuntimeError):
            with stack._operation_lock(tmp_path):
                pass
        ctx.calls.append(args[0])
        if args[0] == "down":
            assert args == ["down", "--timeout", "15"] and "--volumes" not in args
            ctx.mode = "stopped"
            if ctx.fault == "down":
                raise RuntimeError("PRIVATE_DOCKER_LOG")
        else:
            ctx.mode = "running"
            ctx.generation += 1
            if ctx.fault == "up":
                raise RuntimeError("PRIVATE_DOCKER_LOG")
            if ctx.fault == "receipt":
                ctx.receipts[TX_APPROVE]["raw_result"] = "SENSITIVE_DIFFERENT_RESULT"
            if ctx.fault == "state":
                ctx.state_hash = "e" * 64
            if ctx.fault == "image":
                state["image_id"] = "sha256:" + "e" * 64
                stack._save(tmp_path / "studio", state)

    def command(endpoint, args, *, timeout, output_limit):
        assert 0 < timeout <= 10 and output_limit == 4096
        assert args[:2] == ["exec", "--env"]
        assert "default_transaction_read_only=on" in args[2]
        assert "statement_timeout=5000" in args[2]
        assert "--no-psqlrc" in args and "--set=ON_ERROR_STOP=1" in args
        query = args[-1]
        if "FROM transactions" in query:
            value = "1" if ctx.pending else "0"
        else:
            assert "FROM current_state" in query and ADDRESS in query
            value = ctx.state_hash
        return CommandResult(0, value.encode() + b"\n", b"")

    class Client:
        def __init__(self, endpoint, timeout):
            assert endpoint == "http://127.0.0.1:8766" and 0 < timeout <= 600
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def doctor(self):
            return {"ready": True}

        def transaction(self, identifier):
            return copy.deepcopy(ctx.receipts[identifier])

        def write(self, address, snapshot, context, sim_config):
            assert address == ADDRESS and context["fixture_verdict"] == "deny"
            assert len(sim_config["validators"]) == 5
            ctx.receipts[TX_DENY] = receipt(TX_DENY, "deny")
            return TX_DENY

        def wait(self, identifier, until):
            assert identifier == TX_DENY and until == "finalized"
            ctx.state_hash = "b" * 64
            if ctx.fault == "malformed_verdict":
                ctx.receipts[identifier]["raw_result"] = {"unexpected": "PRIVATE_BAD_RESULT"}
            elif ctx.fault == "wrong_verdict":
                ctx.receipts[identifier]["raw_result"] = "approve"
            return self.transaction(identifier)

    def conformance(*args, **kwargs):
        assert kwargs["expected_verdict"] == "approve" and 0 < kwargs["timeout"] <= 510
        if ctx.fault == "late_pending":
            ctx.pending = True
        return {"verification": "pass", "contract_address": ADDRESS,
                "transactions": {"deployment": TX_DEPLOY, "execution": TX_APPROVE},
                "private_key": "PRIVATE_CONFORMANCE_KEY"}

    monkeypatch.setattr(stack, "_endpoint", lambda **kwargs: "unix:///test.sock")
    monkeypatch.setattr(stack, "_inventory", inventory)
    monkeypatch.setattr(stack, "_verify_runtime", lambda *args, **kwargs: ctx.valid)
    monkeypatch.setattr(stack, "_command", command)
    monkeypatch.setattr(stack, "_compose", compose)
    monkeypatch.setattr(recovery, "StudioClient", Client)
    monkeypatch.setattr(recovery, "run_studio_conformance", conformance)
    return tmp_path, ctx


def test_preserved_volumes_receipts_state_and_new_write_on_same_contract(rig):
    directory, ctx = rig
    stages = []
    result = recovery.run_studio_recovery(directory, progress=stages.append)
    assert result["verification"] == "pass" and result["stage"] == "complete"
    assert ctx.calls == ["down", "up"] and all(result["checks"].values())
    assert result["contract_address"] == result["post_restart_receipt"]["contract_address"] == ADDRESS
    assert result["before"]["volumes"] == result["after"]["volumes"]
    assert result["before"]["containers"] != result["after"]["containers"]
    assert result["post_restart_receipt"]["verdict"] == "deny"
    assert result["cleanup"] == {"attempted": False} and result["cleanup_reserve_seconds"] == 90
    assert result["host_reboot"] is result["disaster_recovery"] is False
    assert stages
    serialized = json.dumps(result)
    for sensitive in ("PRIVATE_", "PROVIDER_SECRET", "raw_result", "plugin_config", "private_key"):
        assert sensitive not in serialized


@pytest.mark.parametrize("fault,code", [("foreign", "studio_recovery_operation_failed"),
    ("tampered", "owned_runtime_not_verified"), ("pending", "pending_studio_transactions"),
    ("late_pending", "pending_studio_transactions")])
def test_unsafe_or_busy_stack_is_never_stopped(rig, fault, code):
    directory, ctx = rig
    ctx.fault = fault
    ctx.valid = fault != "tampered"
    ctx.pending = fault == "pending"
    result = recovery.run_studio_recovery(directory)
    assert result["verification"] == "inconclusive" and result["error_code"] == code
    assert not ctx.calls and result["cleanup"]["attempted"] is False


def test_running_lab_refused_before_docker_inspection(rig):
    directory, ctx = rig
    with _exclusive_lock(directory):
        result = recovery.run_studio_recovery(directory)
    assert result["error_code"] == "lab_must_be_stopped" and not ctx.calls


def test_concurrent_studio_lifecycle_refused(rig):
    directory, ctx = rig
    with stack._operation_lock(directory):
        result = recovery.run_studio_recovery(directory)
    assert result["error_code"] == "studio_lifecycle_busy" and not ctx.calls


@pytest.mark.parametrize("fault,code", [("receipt", "finalized_receipt_changed"), ("image", "runtime_identity_changed"),
    ("state", "contract_state_changed"), ("down", "studio_recovery_operation_failed")])
def test_changed_evidence_fails_and_restarts_original_stack(rig, fault, code):
    directory, ctx = rig
    ctx.fault = fault
    result = recovery.run_studio_recovery(directory)
    assert result["verification"] == ("inconclusive" if fault == "down" else "fail")
    assert result["error_code"] == code
    assert result["cleanup"] == {"attempted": True, "ready": True}
    assert ctx.calls[-1] == "up"
    assert "SENSITIVE_DIFFERENT_RESULT" not in json.dumps(result)
    assert "PRIVATE_DOCKER_LOG" not in json.dumps(result)


@pytest.mark.parametrize("fault,code", [("volume", "persistent_volume_changed"),
    ("missing_volume", "persistent_volume_unavailable")])
def test_missing_or_replaced_volume_is_never_recreated_by_cleanup(rig, fault, code):
    directory, ctx = rig
    ctx.fault = fault
    result = recovery.run_studio_recovery(directory)
    assert result["verification"] == "fail" and result["error_code"] == code
    assert result["cleanup"] == {"attempted": True, "ready": False, "error_code": code}
    assert ctx.calls == ["down"] and ctx.mode == "stopped"


@pytest.mark.parametrize("fault", ["malformed_verdict", "wrong_verdict"])
def test_unmapped_or_wrong_post_restart_result_is_an_observed_failure(rig, fault):
    directory, ctx = rig
    ctx.fault = fault
    result = recovery.run_studio_recovery(directory)
    assert result["verification"] == "fail"
    assert result["error_code"] == "post_restart_execution_failed"
    assert result["cleanup"] == {"attempted": True, "ready": True}
    assert ctx.calls == ["down", "up", "up"]
    assert "PRIVATE_BAD_RESULT" not in json.dumps(result)


def test_failed_restart_and_failed_cleanup_are_both_reported(rig):
    directory, ctx = rig
    ctx.fault = "up"
    result = recovery.run_studio_recovery(directory)
    assert ctx.calls == ["down", "up", "up"]
    assert result["error_code"] == "studio_recovery_operation_failed"
    assert result["cleanup"] == {"attempted": True, "ready": False,
                                  "error_code": "studio_restart_failed"}
    assert "PRIVATE_DOCKER_LOG" not in json.dumps(result)


@pytest.mark.parametrize("timeout", [True, False, 0, -1, float("nan"), float("inf"), 3601, "600"])
def test_invalid_timeout_never_touches_stack(rig, timeout):
    directory, ctx = rig
    assert recovery.run_studio_recovery(directory, timeout)["error_code"] == "invalid_timeout"
    assert not ctx.calls


def test_elapsed_budget_is_clamped_before_each_docker_call(rig, monkeypatch):
    directory, ctx = rig
    ticks = iter([0, 0, 0, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15])
    monkeypatch.setattr(recovery.time, "monotonic", lambda: next(ticks, 100))
    result = recovery.run_studio_recovery(directory, timeout=1)
    assert result["verification"] == "inconclusive"
    assert result["error_code"] == "deadline_exceeded" and not ctx.calls


def test_coarse_clock_cannot_round_baseline_budget_above_its_cap(rig, monkeypatch):
    directory, ctx = rig
    # Seen on a Windows runner: subtracting this start from the deadline yields
    # 510.00000000000006. The baseline helper must still receive at most 510.
    monkeypatch.setattr(recovery.time, "monotonic", lambda: 0.07)
    result = recovery.run_studio_recovery(directory)
    assert result["verification"] == "pass"
    assert ctx.calls == ["down", "up"]
