"""Controller checks use mocks and a local status server; they never launch a VM."""

from __future__ import annotations

import base64
import copy
import hashlib
import http.client
import importlib.util
import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts/verify-windows-reboot.py"
spec = importlib.util.spec_from_file_location("windows_reboot_controller", SCRIPT)
controller = importlib.util.module_from_spec(spec)
spec.loader.exec_module(controller)
NONCE = "a" * 32
HASH = "b" * 64
NS = {"u": "urn:schemas-microsoft-com:unattend"}


def baseline():
    return {
        "schema_version": 1, "nonce": NONCE,
        "wheel_sha256": controller.WHEEL_SHA, "version": controller.VERSION,
        "run_id": "c" * 32, "report_sha256": HASH,
        "marker_sha256": HASH, "package_record_sha256": HASH, "helper_sha256": HASH,
        "token_sha256": HASH, "installation_sha256": HASH, "task_identity_sha256": HASH,
        "owner": "d" * 32,
        "system": {
            "boot_utc": "2026-09-06T10:00:00+00:00",
            "observed_utc": "2026-09-06T10:02:00+00:00", "uptime_ms": 120000,
            "caption": "Microsoft Windows 11 Enterprise Evaluation", "version": "10.0.26200",
            "product_type": 1, "session_id": 1, "user_sid_sha256": HASH,
        },
        "checks": {
            "guest_identity_verified": True, "regular_interactive_user": True,
            "actual_glsim_four_grades_pass": True, "owned_logon_service_ready": True,
        },
    }


def recovery(before):
    after = copy.deepcopy(before["system"])
    after.update(boot_utc="2026-09-06T10:04:00+00:00",
                 observed_utc="2026-09-06T10:05:00+00:00", uptime_ms=60000)
    return {
        "schema_version": 1, "verification": "pass", "nonce": NONCE,
        "wheel_sha256": controller.WHEEL_SHA, "package_version": controller.VERSION,
        "before": copy.deepcopy(before["system"]), "after": after,
        "baseline_run": before["run_id"], "baseline_report_sha256": before["report_sha256"],
        "post_reboot_run": "e" * 32, "post_reboot_report_sha256": "f" * 64,
        "manual_service_start_after_reboot": False,
        "task_last_run_utc": "2026-09-06T10:04:30+00:00", "task_last_result": 0x41301,
        "checks": dict.fromkeys((
            "new_windows_boot_same_regular_user", "automatic_ready_after_login",
            "task_started_after_boot", "installation_and_credentials_preserved",
            "frozen_report_and_history_preserved", "new_actual_glsim_four_grades_pass",
        ), True),
    }


@pytest.mark.parametrize("system", ["Windows", "Darwin"])
def test_guard_refuses_personal_os_before_inspection_or_mutation(monkeypatch, system):
    monkeypatch.setattr(controller.platform, "system", lambda: system)
    monkeypatch.setattr(controller.os, "geteuid", lambda: pytest.fail("UID queried on personal OS"), raising=False)
    with pytest.raises(controller.TrialError, match="github_linux_required"):
        controller.hosted_temp()


@pytest.mark.parametrize(("key", "value"), [
    ("GITHUB_ACTIONS", "false"), ("RUNNER_ENVIRONMENT", "self-hosted"),
    ("RUNNER_OS", "Windows"),
])
def test_guard_refuses_ordinary_or_self_hosted_linux(monkeypatch, tmp_path, key, value):
    monkeypatch.setattr(controller.platform, "system", lambda: "Linux")
    monkeypatch.setattr(controller.os, "geteuid", lambda: 1001, raising=False)
    for name, configured in {"GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "github-hosted",
                             "RUNNER_OS": "Linux", "RUNNER_TEMP": str(tmp_path)}.items():
        monkeypatch.setenv(name, configured)
    monkeypatch.setenv(key, value)
    with pytest.raises(controller.TrialError, match="github_hosted_environment_required"):
        controller.hosted_temp()


def test_guard_accepts_regular_hosted_account_and_refuses_root(monkeypatch, tmp_path):
    monkeypatch.setattr(controller.platform, "system", lambda: "Linux")
    monkeypatch.setattr(controller.os, "geteuid", lambda: 1001, raising=False)
    for name, value in {"GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "github-hosted",
                        "RUNNER_OS": "Linux", "RUNNER_TEMP": str(tmp_path)}.items():
        monkeypatch.setenv(name, value)
    assert controller.hosted_temp() == tmp_path.resolve()
    monkeypatch.setattr(controller.os, "geteuid", lambda: 0)
    with pytest.raises(controller.TrialError, match="regular_runner_user_required"):
        controller.hosted_temp()


def test_unattend_escapes_passwords_and_targets_only_private_uefi_disk():
    user_password, admin_password = "Lab!<&\"'9", "Admin!<&\"'9"
    tree = ET.fromstring(controller.unattend(NONCE, user_password, admin_password))
    pe = tree.find("u:settings[@pass='windowsPE']", NS)
    disk = pe.find(".//u:DiskConfiguration/u:Disk", NS)
    assert disk.findtext("u:DiskID", namespaces=NS) == "0"
    assert disk.findtext("u:WillWipeDisk", namespaces=NS) == "true"
    partitions = disk.findall("u:CreatePartitions/u:CreatePartition", NS)
    assert [part.findtext("u:Type", namespaces=NS) for part in partitions] == ["EFI", "MSR", "Primary"]
    assert int(partitions[0].findtext("u:Size", namespaces=NS)) >= 200
    assert partitions[1].findtext("u:Size", namespaces=NS) == "16"
    assert partitions[2].findtext("u:Extend", namespaces=NS) == "true"
    modifications = disk.findall("u:ModifyPartitions/u:ModifyPartition", NS)
    # Microsoft requires contiguous modification Order values even when an MSR
    # PartitionID is skipped because that partition needs no formatting.
    assert [item.findtext("u:Order", namespaces=NS) for item in modifications] == ["1", "2"]
    assert [item.findtext("u:PartitionID", namespaces=NS) for item in modifications] == ["1", "3"]
    destination = pe.find(".//u:OSImage/u:InstallTo", NS)
    assert destination.findtext("u:DiskID", namespaces=NS) == "0"
    assert destination.findtext("u:PartitionID", namespaces=NS) == "3"
    oobe = tree.find("u:settings[@pass='oobeSystem']", NS)
    accounts = oobe.findall(".//u:LocalAccount", NS)
    account_settings = {
        item.findtext("u:Name", namespaces=NS): (
            item.findtext("u:Group", namespaces=NS), item.findtext("u:Password/u:Value", namespaces=NS))
        for item in accounts
    }
    assert account_settings == {"LabUser": ("Users", user_password),
                                "LabAdmin": ("Administrators", admin_password)}
    autologon = oobe.find(".//u:AutoLogon", NS)
    assert autologon.findtext("u:Username", namespaces=NS) == "LabUser"
    assert autologon.findtext("u:Password/u:Value", namespaces=NS) == user_password
    command = tree.findtext("u:settings[@pass='specialize']//u:RunSynchronousCommand/u:Path", namespaces=NS)
    launcher = base64.b64decode(command.rsplit(" ", 1)[-1]).decode("utf-16-le")
    assert "GLAB-" + NONCE in launcher and "guest_identity_required" in launcher
    assert "GLABSEED" in launcher
    assert "shutdown" not in launcher.lower() and "restart-computer" not in launcher.lower()


@pytest.mark.parametrize("missing", ["run_id", "report_sha256"])
def test_incomplete_baseline_cannot_authorize_reboot(missing):
    value = baseline()
    del value[missing]
    with pytest.raises(controller.TrialError):
        controller.validate_baseline(value, NONCE)


def test_baseline_requires_explicit_boot_identity():
    value = baseline()
    del value["system"]["boot_utc"]
    with pytest.raises(controller.TrialError):
        controller.validate_baseline(value, NONCE)


@pytest.mark.parametrize(("section", "key", "value"), [
    (None, "nonce", "0" * 32), (None, "wheel_sha256", "0" * 64),
    (None, "version", "0.1.0a6"), ("checks", "actual_glsim_four_grades_pass", False),
    ("checks", "regular_interactive_user", 1), ("system", "product_type", 3),
])
def test_baseline_rejects_failed_or_mismatched_proof(section, key, value):
    proof = baseline()
    (proof if section is None else proof[section])[key] = value
    with pytest.raises(controller.TrialError):
        controller.validate_baseline(proof, NONCE)


def test_result_binds_the_retained_baseline_and_successful_recovery():
    before = baseline()
    result = recovery(before)
    controller.validate_result(result, before, NONCE)
    result["before"]["boot_utc"] = "2026-09-06T09:00:00+00:00"
    with pytest.raises(controller.TrialError):
        controller.validate_result(result, before, NONCE)


@pytest.mark.parametrize(("key", "value"), [
    ("verification", "inconclusive"), ("baseline_run", "0" * 32),
    ("baseline_report_sha256", "0" * 64), ("manual_service_start_after_reboot", True),
    ("post_reboot_run", "c" * 32),
])
def test_result_rejects_failed_recovery_or_reused_run(key, value):
    before = baseline()
    result = recovery(before)
    result[key] = value
    with pytest.raises(controller.TrialError):
        controller.validate_result(result, before, NONCE)


@pytest.mark.parametrize("change", ["missing_after", "same_boot", "earlier_boot", "different_user"])
def test_result_requires_observed_later_boot_and_same_user(change):
    before = baseline()
    result = recovery(before)
    if change == "missing_after":
        result["after"] = {}
    elif change == "same_boot":
        result["after"]["boot_utc"] = before["system"]["boot_utc"]
    elif change == "earlier_boot":
        result["after"]["boot_utc"] = "2026-09-06T09:00:00+00:00"
    else:
        result["after"]["user_sid_sha256"] = "0" * 64
    with pytest.raises(controller.TrialError):
        controller.validate_result(result, before, NONCE)


@pytest.fixture
def mailbox():
    value = controller.Mailbox(NONCE)
    try:
        yield value
    finally:
        value.stop()


def post(mailbox, payload, *, token=None, length=None, path="/poll"):
    connection = http.client.HTTPConnection("127.0.0.1", mailbox.server.server_port, timeout=3)
    raw = json.dumps(payload).encode()
    headers = {"Authorization": "Bearer " + (mailbox.token if token is None else token),
               "Content-Length": str(len(raw) if length is None else length),
               "Content-Type": "application/json"}
    try:
        connection.request("POST", path, body=raw, headers=headers)
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def test_mailbox_authenticates_nonce_and_bounds_payload_before_acceptance(mailbox):
    assert mailbox.server.server_address[0] == "127.0.0.1"
    valid = {"nonce": NONCE, "stage": "guest_observer_running"}
    for payload, options in ((valid, {"token": "wrong"}),
                             ({**valid, "nonce": "0" * 32}, {}),
                             (valid, {"length": 65537}), (valid, {"path": "/shell"})):
        try:
            status, raw = post(mailbox, payload, **options)
        except (ConnectionAbortedError, ConnectionResetError):
            # Windows can reset a socket closed with rejected/unread body bytes.
            # A closed connection is also rejection; acceptance is checked below.
            pass
        else:
            assert status >= 400 and mailbox.token.encode() not in raw
    assert mailbox.received == 0 and mailbox.snapshot()[0] == {}
    status, raw = post(mailbox, valid)
    assert status == 200 and json.loads(raw) == {"command": "wait"}
    assert mailbox.snapshot() == (valid, "guest_observer_running")


def test_reboot_command_is_single_use_and_binds_retained_baseline(mailbox):
    proof = baseline()
    with pytest.raises(controller.TrialError):
        mailbox.request_reboot(proof, "missing hash")
    assert mailbox.command == {"command": "wait"}
    mailbox.request_reboot(proof, HASH)
    assert mailbox.baseline_hash == hashlib.sha256(controller.canonical(proof)).hexdigest()
    initial_poll = {"nonce": NONCE, "stage": "guest_observer_running",
                    "boot_utc": proof["system"]["boot_utc"]}
    status, raw = post(mailbox, initial_poll)
    assert status == 200
    assert json.loads(raw) == {"command": "reboot", "nonce": NONCE, "baseline_sha256": HASH,
                               "before_boot_utc": proof["system"]["boot_utc"]}
    for changed_poll in ({**initial_poll, "boot_utc": "2026-09-06T10:04:00+00:00"},
                         {**initial_poll, "result": {"verification": "inconclusive"}}):
        status, raw = post(mailbox, changed_poll)
        assert status == 200 and json.loads(raw) == {"command": "wait"}
    with pytest.raises(controller.TrialError, match="duplicate_reboot_request"):
        mailbox.request_reboot(proof, HASH)
