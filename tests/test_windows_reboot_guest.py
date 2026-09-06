"""Guest reboot observer tests never touch Windows services or reboot a host."""

from __future__ import annotations

import copy
import importlib.util
import json
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts/windows_reboot_guest.py"
spec = importlib.util.spec_from_file_location("windows_reboot_guest", SCRIPT)
guest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guest)

NONCE = "a" * 32
SID = "S-1-5-21-100-200-300-1001"
HASH = "c" * 64


def context(root):
    marker = {"nonce": NONCE, "bios_serial": "GLAB-" + NONCE, "wheel_sha256": HASH,
              "version": "0.1.0a7", "user": "LabUser"}
    system = {"bios_serial": marker["bios_serial"], "system_serial": "irrelevant",
              "account": "LABVM\\LabUser", "sid": SID, "local_sid": SID, "administrator": False,
              "session_id": 1, "boot_utc": "2026-09-06T10:00:00+00:00",
              "observed_utc": "2026-09-06T10:02:00+00:00", "uptime_ms": 120000,
              "caption": "Microsoft Windows 11 Enterprise Evaluation", "version": "10.0.26200",
              "product_type": 1}
    package = {"version": marker["version"], "wheel_sha256": HASH,
               "executable": str(root / "venv/Scripts/python.exe"),
               "package_path": str(root / "venv/Lib/site-packages/genlayer_agent_lab")}
    return marker, system, package


@pytest.mark.parametrize(("section", "key", "value", "error"), [
    (1, "bios_serial", "PERSONAL-LAPTOP", "guest_bios_mismatch"),
    (0, "nonce", "b" * 32, "guest_marker_mismatch"),
    (1, "administrator", True, "regular_interactive_user_required"),
    (1, "session_id", 0, "regular_interactive_user_required"),
    (1, "account", "LABVM\\Administrator", "local_lab_user_required"),
    (1, "local_sid", "S-1-5-21-100-200-300-9999", "local_lab_user_required"),
    (1, "product_type", 3, "windows_11_client_required"),
    (1, "caption", "Windows Server 2025", "windows_11_client_required"),
    (2, "executable", "C:/Users/Personal/Python/python.exe", "owned_installed_interpreter_required"),
    (2, "package_path", "C:/Users/Personal/source", "owned_installed_interpreter_required"),
    (2, "wheel_sha256", "b" * 64, "installed_wheel_mismatch"),
    (2, "wheel_sha256", None, "installed_wheel_mismatch"),
    (2, "version", "0.1.0a6", "installed_version_mismatch"),
])
def test_gate_refuses_wrong_machine_user_or_wheel(tmp_path, section, key, value, error):
    values = context(tmp_path)
    values[section][key] = value
    with pytest.raises(guest.TrialError, match=error):
        guest.validate_identity(values[0], NONCE, values[1], values[2], tmp_path)


def test_exact_system_serial_fallback_and_public_identity(tmp_path):
    marker, system, package = context(tmp_path)
    system["bios_serial"] = "QEMU"
    system["system_serial"] = marker["bios_serial"]
    guest.validate_identity(marker, NONCE, system, package, tmp_path)
    public = guest.public_system(system)
    assert SID not in json.dumps(public)
    assert "account" not in public and "bios_serial" not in public
    system["system_serial"] += "-extra"
    with pytest.raises(guest.TrialError, match="guest_bios_mismatch"):
        guest.validate_identity(marker, NONCE, system, package, tmp_path)


def report():
    return {"status": "completed", "verdict": "pass",
            "grades": {key: {"status": "pass"} for key in ("decision", "behavior", "outcome", "completion")},
            "manifest": {"backend": "glsim", "runtime": {"execution_success": True}},
            "private_test_evidence": "provider-secret-must-not-be-exported"}


class FakeClient:
    def __init__(self):
        self.reports = {}
        self.creates = 0
        self.next_report = report()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def create_run(self, scenario, agent, backend):
        assert (scenario, agent, backend) == ("escrow-normal", "safe", "glsim")
        self.creates += 1
        run_id = f"{self.creates:032x}"
        self.reports[run_id] = copy.deepcopy(self.next_report)
        return {"run_id": run_id}

    def get_run(self, run_id):
        return {"status": "completed"}

    def report(self, run_id):
        return self.reports[run_id]

    def list_runs(self):
        return [{"run_id": run_id} for run_id in self.reports]


class FakeRuntime:
    def __init__(self, root):
        self.marker, system, self.package = context(root)
        self.system = guest.public_system(system)
        self.admin = FakeClient()
        self.installs = 0
        self.preparations = 0
        self.status_reads = 0
        self.waits = 0
        self.task_hash = "d" * 64
        self.last_result = 0x41301
        self.failure = None
        guest.save(root / "guest.json", self.marker)

    def guard(self, root, nonce):
        assert nonce == NONCE
        return self.marker, self.system.copy(), "e" * 64

    def prepare(self):
        self.preparations += 1

    def install(self, data):
        self.installs += 1
        (data / "service").mkdir(parents=True)
        (data / "admin.token").write_text("secret-admin-token")
        guest.save(data / "service/installation.json", {"owner": "owner", "name": "managed-task"})
        return self.state()

    def state(self):
        return {"owner": "owner", "installed": True, "running": True, "ready": True, "enabled": True}

    def status(self, data):
        self.status_reads += 1
        return self.state()

    def task(self, manifest):
        return {"identity_sha256": self.task_hash, "running": True, "enabled": True,
                "last_run_utc": self.system["observed_utc"], "last_result": self.last_result}

    def wait_ready(self, owner, deadline):
        now = time.monotonic()
        assert now < deadline <= now + 120
        self.waits += 1
        if self.failure:
            raise self.failure

    def client(self, data):
        return self.admin

    def reboot(self):
        self.system["boot_utc"] = "2026-09-06T10:05:00+00:00"
        self.system["observed_utc"] = "2026-09-06T10:06:00+00:00"
        self.system["uptime_ms"] = 60000


def arm(root):
    runtime = FakeRuntime(root)
    assert guest.observe(root, NONCE, runtime)["verification"] == "armed"
    return runtime


def test_real_boot_required_then_only_reads_and_new_run(tmp_path):
    runtime = arm(tmp_path)
    assert guest.observe(tmp_path, NONCE, runtime)["verification"] == "pending_reboot"
    assert runtime.installs == 1 and runtime.admin.creates == 1
    runtime.reboot()
    result = guest.observe(tmp_path, NONCE, runtime)
    assert result["verification"] == "pass", result
    assert runtime.installs == 1 and runtime.status_reads == 1 and runtime.admin.creates == 2
    assert runtime.preparations == 1
    assert result["task_last_result"] == 0x41301
    assert result["manual_service_start_after_reboot"] is False
    assert all(result["checks"].values())
    for private in ("secret-admin-token", "provider-secret", str(tmp_path), SID):
        assert private not in json.dumps(result)
        assert private not in (tmp_path / "baseline.json").read_text()
    assert guest.observe(tmp_path, NONCE, runtime) == result
    assert runtime.admin.creates == 2 and runtime.installs == 1


def test_deadline_rounding_does_not_fail_post_reboot_verification(tmp_path, monkeypatch):
    # A clock tick can repeat on Windows. Subtracting this rounded deadline
    # would yield 120.00000000000001 even though the requested budget is 120.
    monkeypatch.setattr(time, "monotonic", lambda: 100.002)
    runtime = arm(tmp_path)
    runtime.reboot()
    result = guest.observe(tmp_path, NONCE, runtime)
    assert result["verification"] == "pass", result
    assert runtime.waits == 1 and runtime.installs == 1 and runtime.admin.creates == 2


@pytest.mark.parametrize("kind", ["token", "metadata", "report", "task", "grade", "last_result"])
def test_changed_evidence_never_passes_or_retries(tmp_path, kind):
    runtime = arm(tmp_path)
    runtime.reboot()
    if kind == "token":
        (tmp_path / "data/admin.token").write_text("changed-secret")
    elif kind == "metadata":
        guest.save(tmp_path / "data/service/installation.json", {"owner": "other"})
    elif kind == "report":
        runtime.admin.reports["1".zfill(32)]["changed"] = True
    elif kind == "task":
        runtime.task_hash = "changed"
    elif kind == "grade":
        runtime.admin.next_report["grades"]["behavior"]["status"] = "fail"
    else:
        runtime.last_result = 0x80070005
    result = guest.observe(tmp_path, NONCE, runtime)
    assert result["verification"] != "pass" and result["error_code"]
    before = runtime.admin.creates
    runtime.task_hash = "d" * 64
    runtime.last_result = 0x41301
    runtime.admin.next_report = report()
    assert guest.observe(tmp_path, NONCE, runtime) == result
    assert runtime.admin.creates == before and runtime.installs == 1


def test_crashed_after_claim_cannot_submit_another_run(tmp_path):
    runtime = arm(tmp_path)
    runtime.reboot()
    guest.claim(tmp_path / "after-attempt.json", runtime.system)
    result = guest.observe(tmp_path, NONCE, runtime)
    assert result["error_code"] == "prior_attempt_incomplete"
    assert runtime.admin.creates == 1 and runtime.waits == 0
    assert not (tmp_path / "result.json").exists()


def test_crashed_baseline_cannot_install_again(tmp_path):
    runtime = FakeRuntime(tmp_path)
    guest.claim(tmp_path / "baseline-attempt.json", runtime.system)
    result = guest.observe(tmp_path, NONCE, runtime)
    assert result["error_code"] == "prior_attempt_incomplete"
    assert runtime.installs == 0


def test_raw_provider_failure_is_redacted_and_durable(tmp_path):
    runtime = arm(tmp_path)
    runtime.reboot()
    runtime.failure = RuntimeError("provider secret-admin-token C:/Personal/private")
    result = guest.observe(tmp_path, NONCE, runtime)
    assert result["error_code"] == "guest_verification_error"
    assert "secret-admin-token" not in json.dumps(result)
    runtime.failure = None
    assert guest.observe(tmp_path, NONCE, runtime) == result
    assert runtime.admin.creates == 1


def test_failed_guard_never_installs_or_writes_evidence(tmp_path):
    runtime = FakeRuntime(tmp_path)

    def refuse(root, nonce):
        raise guest.TrialError("guest_bios_mismatch")

    runtime.guard = refuse
    with pytest.raises(guest.TrialError, match="guest_bios_mismatch"):
        guest.observe(tmp_path, NONCE, runtime)
    assert runtime.installs == 0
    assert not (tmp_path / "result.json").exists()
    assert not (tmp_path / "baseline-attempt.json").exists()


def test_fixture_or_incomplete_contract_evidence_is_rejected():
    admin = FakeClient()
    admin.next_report["manifest"]["backend"] = "fixture"
    with pytest.raises(guest.TrialError, match="actual_contract_evidence_missing"):
        guest.run_agent(admin, time.monotonic() + 5)


def test_inconclusive_runtime_is_immediately_terminal():
    admin = FakeClient()
    admin.next_report["status"] = "inconclusive"

    def inconclusive(run_id):
        return {"status": "inconclusive"}

    admin.get_run = inconclusive
    started = time.monotonic()
    with pytest.raises(guest.TrialError, match="glsim_run_failed"):
        guest.run_agent(admin, started + 60)
    assert time.monotonic() - started < 1


def test_failed_cold_preparation_does_not_install_service(tmp_path):
    runtime = FakeRuntime(tmp_path)

    def refuse():
        raise guest.TrialError("glsim_preparation_failed")

    runtime.prepare = refuse
    result = guest.observe(tmp_path, NONCE, runtime)
    assert result["error_code"] == "glsim_preparation_failed"
    assert result["stage"] == "baseline_runtime_preparation"
    assert runtime.installs == 0 and runtime.admin.creates == 0


def test_initial_service_gets_bounded_readiness_wait(tmp_path):
    runtime = FakeRuntime(tmp_path)
    original = runtime.install

    def slow(data):
        return original(data) | {"ready": False}

    runtime.install = slow
    assert guest.observe(tmp_path, NONCE, runtime)["verification"] == "armed"
    assert runtime.waits == 1 and runtime.status_reads == 1
