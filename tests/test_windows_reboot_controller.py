"""Controller checks use mocks and a local status server; they never launch a VM."""

from __future__ import annotations

import base64
import copy
import hashlib
import http.client
import importlib.util
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

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
    setup_command = tree.findtext(".//u:RunSynchronousCommand/u:Path", namespaces=NS)
    assert len(setup_command) <= 259
    assert "seed-launch.ps1" in setup_command
    launcher = controller.seed_launcher(NONCE)
    assert "GLAB-" + NONCE in launcher
    assert launcher.index("guest_identity_required") < launcher.index("New-Item")
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
    assert "GLAB-" + NONCE in launcher and "guest_identity_required" in launcher
    assert "GLABSEED" in launcher
    assert "shutdown" not in launcher.lower() and "restart-computer" not in launcher.lower()


def test_setup_complete_is_fixed_crlf_and_specialize_only_arms_it(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Generating guest setup must never execute commands")

    monkeypatch.setattr(controller, "command", forbidden)
    monkeypatch.setattr(controller.subprocess, "run", forbidden)
    expected = (
        "@echo off\r\n"
        f"rem GenLayer owned guest setup {NONCE}\r\n"
        '"C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" '
        '-NoProfile -NonInteractive -ExecutionPolicy Bypass '
        '-File "C:\\LabTrial\\bootstrap.ps1"\r\n'
        "exit /b %ERRORLEVEL%\r\n"
    ).encode("ascii")
    assert controller.setup_complete_cmd(NONCE) == expected
    assert b"\n" not in expected.replace(b"\r\n", b"")
    launcher = controller.seed_launcher(NONCE)
    encoded = re.search(r"\$expected='([A-Za-z0-9+/=]+)'", launcher).group(1)
    assert base64.b64decode(encoded, validate=True) == expected
    # All guards precede every directory/file mutation. Existing foreign setup
    # code is refused; exclusive creation cannot overwrite a racing new file.
    for guard in ("SYSTEM_required", "guest_identity_required", "seed_required",
                  "unexpected_existing_setup_complete"):
        assert launcher.index(guard) < launcher.index("New-Item") < launcher.index("Copy-Item")
    assert "[IO.File]::ReadAllBytes($setup)) -cne $expected" in launcher
    assert "[IO.FileMode]::CreateNew" in launcher
    assert "SetupComplete.cmd" in launcher
    assert "powershell.exe" not in launcher and "bootstrap.ps1" not in launcher
    assert "Start-Process" not in launcher and "Wait-Process" not in launcher
    assert "shutdown" not in launcher.lower() and "restart-computer" not in launcher.lower()
    assert launcher.endswith("exit 0")
    tree = ET.fromstring(controller.unattend(NONCE, "Lab!test9", "Admin!test9"))
    path = tree.findtext(".//u:RunSynchronousCommand/u:Path", namespaces=NS)
    assert len(path) <= 259 and "seed-launch.ps1" in path
    assert tree.findtext(".//u:RunSynchronousCommand/u:WillReboot", namespaces=NS) == "Never"


@pytest.mark.parametrize("nonce", ["", "a" * 31, "A" * 32, "a" * 32 + "';exit 1;"])
def test_later_setup_generation_refuses_invalid_nonce(nonce):
    for generate in (controller.setup_complete_cmd, controller.seed_launcher):
        with pytest.raises(controller.TrialError, match="invalid_nonce"):
            generate(nonce)


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


@pytest.mark.parametrize("size", [96 * 1024, 96 * 1024 + 1])
def test_setup_snapshot_emits_complete_bounded_png_or_fixed_skip(monkeypatch, tmp_path, capsys, size):
    png = b"\x89PNG\r\n\x1a\n" + b"x" * (size - 8)

    class FakeImage:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def save(self, stream, **options):
            assert options == {"format": "PNG", "optimize": True}
            stream.write(png)

    def open_image(path):
        assert path == tmp_path / "setup-screen.ppm"
        return FakeImage()

    def screendump(name, arguments):
        assert name == "screendump"
        assert Path(arguments["filename"]) == tmp_path / "setup-screen.ppm"
        Path(arguments["filename"]).write_bytes(b"private frame")

    monkeypatch.setitem(sys.modules, "PIL", SimpleNamespace(Image=SimpleNamespace(open=open_image)))
    monkeypatch.setattr(controller, "STAGE", "first_login_wait")
    controller.emit_setup_snapshot(SimpleNamespace(execute=screendump), tmp_path)
    lines = capsys.readouterr().out.splitlines()
    encoded = [line.removeprefix("GLAB_GUEST_SETUP_PNG:") for line in lines
               if line.startswith("GLAB_GUEST_SETUP_PNG:")]
    if size <= 96 * 1024:
        assert len(encoded) == 1 and base64.b64decode(encoded[0], validate=True) == png
    else:
        assert not encoded
        assert lines[-1] == "Windows guest trial: guest_setup_snapshot_skipped_oversize"
    assert controller.STAGE == "first_login_wait"
    assert not (tmp_path / "setup-screen.ppm").exists()


def test_setup_snapshot_failure_is_nonfatal_and_does_not_export_exception(monkeypatch, tmp_path, capsys):
    monkeypatch.setitem(sys.modules, "PIL", SimpleNamespace(Image=object()))

    def failed_dump(*_args):
        raise RuntimeError("private diagnostic payload")

    controller.emit_setup_snapshot(SimpleNamespace(execute=failed_dump), tmp_path)
    output = capsys.readouterr().out
    assert "guest_setup_snapshot_skipped_error" in output
    assert "private diagnostic payload" not in output and "GLAB_GUEST_SETUP_PNG:" not in output


def test_failed_preparation_retains_only_bounded_diagnostic_and_never_arms(tmp_path):
    result = {"verification": "inconclusive", "stage": "baseline_runtime_preparation",
              "error_code": "glsim_preparation_failed", "private_config": "excluded",
              "preparation_diagnostic": {"python": "3.13.7", "status": "error", "ready": False,
                                         "error": "Authorization: Bearer PRIVATE\nTLS failure " + "x" * 1000,
                                         "environment": "excluded"}}
    mailbox = SimpleNamespace(snapshot=lambda: ({"result": result}, "guest_observer_running"),
                              error_count=0, last_error=None, received=1)
    qemu = SimpleNamespace(poll=lambda: None, returncode=None)
    evidence = {}
    with pytest.raises(controller.TrialError, match="guest_glsim_preparation_failed"):
        controller.wait_for(qemu, mailbox, lambda _body: True, 10,
                            "await_first_logon_and_baseline", tmp_path / "evidence.json", evidence)
    diagnostic = evidence["guest_failure"]["preparation_diagnostic"]
    assert len(diagnostic["error"]) == 800 and "TLS failure" in diagnostic["error"]
    assert not diagnostic["ready"]
    assert set(diagnostic) == {"python", "status", "ready", "error"}
    assert "PRIVATE" not in json.dumps(evidence) and "excluded" not in json.dumps(evidence)


@pytest.mark.parametrize(("phase", "baseline_at", "raises", "expected"), [
    ("await_first_logon_and_baseline", 3600, False, [600, 1200, 1800, 2400]),
    ("await_first_logon_and_baseline", 3600, True, [600, 1200, 1800, 2400]),
    ("await_first_logon_and_baseline", 600, False, []),
    ("await_reboot_logon_and_automatic_recovery", 3600, False, []),
])
def test_setup_snapshots_are_timed_capped_nonfatal_and_only_before_baseline(
        monkeypatch, tmp_path, phase, baseline_at, raises, expected):
    clock = {"now": 0}
    attempts = []

    def sleep(_seconds):
        clock["now"] += 600

    def snapshot():
        body = {"baseline": {"retained": True}} if clock["now"] >= baseline_at else {}
        return body, "guest_observer_running"

    def diagnostic():
        attempts.append(clock["now"])
        if raises:
            raise RuntimeError("private callback failure")

    monkeypatch.setattr(controller.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(controller.time, "sleep", sleep)
    mailbox = SimpleNamespace(snapshot=snapshot, error_count=0, last_error=None, received=0)
    qemu = SimpleNamespace(poll=lambda: None, returncode=None)
    result = controller.wait_for(qemu, mailbox, lambda body: bool(body.get("baseline")),
                                 3700, phase, tmp_path / "evidence.json", {}, setup_snapshot=diagnostic)
    assert result == {"baseline": {"retained": True}}
    assert attempts == expected


@pytest.mark.parametrize(("text", "matches"), [
    ("Press any key to boot from CD or DVD.....", True),
    ("PRESS  ANY KEY\nTO BOOT FROM CD/DVD . . .", True),
    ("Press any key to boot from DVD", True),
    ("Are you sure you want to quit?", False),
    ("Windows Setup 10% Cancel", False),
    ("Press any key to continue", False),
    ("Press any key to boot from hard disk", False),
    ("Press any key to boot from", False),
])
def test_optical_boot_prompt_requires_explicit_recognized_words(text, matches):
    assert controller.boot_prompt_matches(text) is matches


def boot_clock(monkeypatch):
    value = {"now": 0}
    monkeypatch.setattr(controller.time, "monotonic", lambda: value["now"])

    def sleep(seconds):
        value["now"] += seconds

    monkeypatch.setattr(controller.time, "sleep", sleep)
    return value


def test_boot_confirmation_waits_for_positive_ocr_and_sends_only_one_key(monkeypatch, tmp_path, capsys):
    clock = boot_clock(monkeypatch)
    commands, observations = [], []

    def observe(_qmp, _directory, deadline):
        assert deadline == 90 and commands == []
        observations.append(clock["now"])
        if len(observations) == 1:
            raise RuntimeError("private OCR frame failure")
        return len(observations) == 3

    monkeypatch.setattr(controller, "read_boot_prompt", observe)
    qmp = SimpleNamespace(execute=lambda command, arguments: commands.append((command, arguments)))
    controller.confirm_optical_boot(SimpleNamespace(poll=lambda: None), qmp, tmp_path)
    assert observations == [0, 1, 2]
    assert commands == [("send-key", {"keys": [{"type": "qcode", "data": "spc"}], "hold-time": 100})]
    assert "private OCR frame failure" not in capsys.readouterr().out


@pytest.mark.parametrize("late_positive", [False, True])
def test_boot_confirmation_timeout_never_sends_input(monkeypatch, tmp_path, late_positive):
    clock = boot_clock(monkeypatch)

    def observe(*_args):
        if late_positive:
            clock["now"] = 91
        return late_positive

    monkeypatch.setattr(controller, "read_boot_prompt", observe)
    qmp = SimpleNamespace(execute=lambda *_args: pytest.fail("No input without timely visible prompt"))
    with pytest.raises(controller.TrialError, match="visible_optical_boot_prompt_timeout"):
        controller.confirm_optical_boot(SimpleNamespace(poll=lambda: None), qmp, tmp_path)
    assert clock["now"] >= 90


def test_boot_confirmation_refuses_exited_guest_and_never_retries_ambiguous_input(monkeypatch, tmp_path):
    boot_clock(monkeypatch)
    monkeypatch.setattr(controller, "read_boot_prompt", lambda *_args: pytest.fail("Exited guest inspected"))
    with pytest.raises(controller.TrialError, match="qemu_exited_before_boot_prompt"):
        controller.confirm_optical_boot(SimpleNamespace(poll=lambda: 1), None, tmp_path)
    calls = []
    monkeypatch.setattr(controller, "read_boot_prompt", lambda *_args: True)

    def ambiguous_send(*args):
        calls.append(args)
        raise controller.TrialError("qmp_command_failed")

    with pytest.raises(controller.TrialError, match="qmp_command_failed"):
        controller.confirm_optical_boot(SimpleNamespace(poll=lambda: None),
                                        SimpleNamespace(execute=ambiguous_send), tmp_path)
    assert len(calls) == 1 and calls[0][0] == "send-key"


@pytest.mark.parametrize("oversize", [False, True])
def test_boot_ocr_subprocess_and_private_output_are_bounded(monkeypatch, tmp_path, oversize):
    boot_clock(monkeypatch)

    class Frame:
        width, height = 1024, 768

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def resize(self, dimensions):
            assert dimensions == (2048, 1536)
            return self

        def save(self, path, **options):
            assert options == {"format": "PNG"}
            path.write_bytes(b"private OCR image")

    monkeypatch.setitem(sys.modules, "PIL", SimpleNamespace(Image=SimpleNamespace(open=lambda _path: Frame())))

    def fake_run(arguments, **options):
        assert arguments == ["tesseract", str(tmp_path / "boot-prompt.png"), "stdout", "-l", "eng", "--psm", "6"]
        assert 0 < options["timeout"] <= 5 and not options["check"]
        assert options["stdin"] == options["stderr"] == controller.subprocess.DEVNULL
        options["stdout"].write(b"x" * 65537 if oversize else b"Press any key to boot from CD or DVD")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(controller.subprocess, "run", fake_run)
    qmp = SimpleNamespace(execute=lambda _command, args: Path(args["filename"]).write_bytes(b"private PPM"))
    if oversize:
        with pytest.raises(controller.TrialError, match="boot_prompt_ocr_oversized"):
            controller.read_boot_prompt(qmp, tmp_path, 90)
    else:
        assert controller.read_boot_prompt(qmp, tmp_path, 90)
    assert list(tmp_path.iterdir()) == []
