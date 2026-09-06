"""Observe a real Windows 11 guest reboot inside a disposable GitHub Linux runner.

This controller refuses Windows, macOS, self-hosted runners, and root. The outer
Linux runner and QEMU process stay alive. Only the marked guest accepts a reboot
command, after the observer has retained a successful baseline. Guest disks,
credentials, installation logs and media are private and are never uploaded.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import http.server
import io
import json
import os
import platform
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

ISO_URL = (
    "https://software-static.download.prss.microsoft.com/dbazure/"
    "888969d5-f34g-4e03-ac9d-1f9786c66749/"
    "26200.6584.250915-1905.25h2_ge_release_svc_refresh_"
    "CLIENTENTERPRISEEVAL_OEMRET_x64FRE_en-us.iso"
)
# Microsoft's Verify-Download-Win11-Enterprise.pdf, Enterprise Eval x64 EN-US.
ISO_SHA = "a61adeab895ef5a4db436e0a7011c92a2ff17bb0357f58b13bbc4062e535e7b9"
PYTHON_URL = "https://www.python.org/ftp/python/3.13.7/python-3.13.7-amd64.exe"
# Digest in python.org's release sigstore bundle; guest also checks Authenticode.
PYTHON_SHA = "b12e2e82461ac8e51fc43289050bc8eb937a32d84ce4d242e2c88258c37cf2bb"
WHEEL_SHA = "9a53191df7eda55c2fe8d132127c0327051ec9cdd6a581fa2f420655204a1634"
VERSION = "0.1.0a7"
GIB = 1024**3
SETUP_SNAPSHOT_INTERVAL = 600
SETUP_SNAPSHOT_LIMIT = 4
SETUP_PNG_MAX_BYTES = 96 * 1024
BOOT_PROMPT_TIMEOUT = 90
STAGE = "initialization"


class TrialError(RuntimeError):
    pass


def require(condition, code):
    if not condition:
        raise TrialError(code)


def stage(name):
    global STAGE
    STAGE = name
    print("Windows guest trial: " + name, flush=True)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def outer_boot():
    return str(uuid.UUID(Path("/proc/sys/kernel/random/boot_id").read_text().strip()))


def hosted_temp():
    require(platform.system() == "Linux", "github_linux_required")
    for key, value in (("GITHUB_ACTIONS", "true"), ("RUNNER_ENVIRONMENT", "github-hosted"),
                       ("RUNNER_OS", "Linux")):
        require(os.environ.get(key) == value, "github_hosted_environment_required")
    require(os.geteuid() != 0, "regular_runner_user_required")
    value = os.environ.get("RUNNER_TEMP", "")
    require(bool(value) and Path(value).is_absolute(), "runner_temp_required")
    root = Path(value).resolve(strict=True)
    require(root.is_dir() and root != Path("/"), "invalid_runner_temp")
    return root


def command(args, *, timeout=60):
    try:
        result = subprocess.run([str(x) for x in args], stdin=subprocess.DEVNULL,
                                capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        raise TrialError("command_timeout") from None
    require(result.returncode == 0, "command_failed_exit_" + str(result.returncode))
    # Third-party output can contain credentials. Never include it in exceptions.
    return result.stdout


def download(url, destination, expected, *, timeout=1200):
    command(["curl", "--fail", "--location", "--retry", "2", "--proto", "=https",
             "--proto-redir", "=https", "--max-time", str(timeout), "--output", destination,
             url], timeout=timeout + 30)
    require(digest(destination) == expected, "download_sha256_mismatch")


def unattend(nonce, user_password, admin_password):
    """Documented Windows Setup passes; no hardware/account bypass registry hacks."""
    require(re.fullmatch(r"[0-9a-f]{32}", nonce), "invalid_nonce")
    ns = "urn:schemas-microsoft-com:unattend"
    wcm = "http://schemas.microsoft.com/WMIConfig/2002/State"
    ET.register_namespace("", ns)
    ET.register_namespace("wcm", wcm)

    def add(parent, tag, value=None, **attrs):
        node = ET.SubElement(parent, "{" + ns + "}" + tag, attrs)
        if value is not None:
            node.text = str(value)
        return node

    root = ET.Element("{" + ns + "}unattend")

    def component(settings, name):
        return add(settings, "component", name=name, processorArchitecture="amd64",
                   publicKeyToken="31bf3856ad364e35", language="neutral", versionScope="nonSxS")

    pe = add(root, "settings", **{"pass": "windowsPE"})
    locale = component(pe, "Microsoft-Windows-International-Core-WinPE")
    add(add(locale, "SetupUILanguage"), "UILanguage", "en-US")
    for key in ("InputLocale", "SystemLocale", "UILanguage", "UserLocale"):
        add(locale, key, "en-US")
    setup = component(pe, "Microsoft-Windows-Setup")
    add(add(setup, "DynamicUpdate"), "Enable", "false")
    disk_config = add(setup, "DiskConfiguration")
    disk = add(disk_config, "Disk", **{"{" + wcm + "}action": "add"})
    add(disk, "DiskID", 0)
    add(disk, "WillWipeDisk", "true")  # Only the private, empty QEMU qcow2 disk exists.
    create = add(disk, "CreatePartitions")
    for order, kind, size in ((1, "EFI", 260), (2, "MSR", 16), (3, "Primary", None)):
        part = add(create, "CreatePartition", **{"{" + wcm + "}action": "add"})
        add(part, "Order", order)
        add(part, "Type", kind)
        add(part, "Size" if size else "Extend", size if size else "true")
    modify = add(disk, "ModifyPartitions")
    for order, (partition_id, filesystem, label) in enumerate(
        ((1, "FAT32", "System"), (3, "NTFS", "Windows")), start=1
    ):
        part = add(modify, "ModifyPartition", **{"{" + wcm + "}action": "add"})
        add(part, "Order", order)
        add(part, "PartitionID", partition_id)
        add(part, "Format", filesystem)
        add(part, "Label", label)
        if partition_id == 3:
            add(part, "Letter", "C")
    add(disk_config, "WillShowUI", "OnError")
    install = add(add(setup, "ImageInstall"), "OSImage")
    metadata = add(add(install, "InstallFrom"), "MetaData", **{"{" + wcm + "}action": "add"})
    add(metadata, "Key", "/IMAGE/INDEX")
    add(metadata, "Value", "1")
    destination = add(install, "InstallTo")
    add(destination, "DiskID", 0)
    add(destination, "PartitionID", 3)
    add(install, "WillShowUI", "OnError")
    user_data = add(setup, "UserData")
    add(user_data, "AcceptEula", "true")
    add(user_data, "FullName", "Lab evaluation")
    add(user_data, "Organization", "Compatibility testing")
    specialize = add(root, "settings", **{"pass": "specialize"})
    shell = component(specialize, "Microsoft-Windows-Shell-Setup")
    add(shell, "ComputerName", "GLAB-VM")
    add(shell, "TimeZone", "UTC")
    deployment = component(specialize, "Microsoft-Windows-Deployment")
    sync = add(add(deployment, "RunSynchronous"), "RunSynchronousCommand",
               **{"{" + wcm + "}action": "add"})
    add(sync, "Order", 1)
    add(sync, "Description", "Prepare the marked disposable test guest")
    # This setting has a documented 259-character maximum. Keep the full
    # serial-guarded launcher on the private seed, not in -EncodedCommand.
    path = (
        'powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command '
        '"& ((Get-CimInstance Win32_LogicalDisk|Where-Object VolumeName -eq GLABSEED)'
        '.DeviceID+\'\\seed-launch.ps1\')"'
    )
    require(len(path) <= 259, "windows_setup_command_too_long")
    add(sync, "Path", path)
    add(sync, "WillReboot", "Never")
    oobe = add(root, "settings", **{"pass": "oobeSystem"})
    shell = component(oobe, "Microsoft-Windows-Shell-Setup")
    locale = component(oobe, "Microsoft-Windows-International-Core")
    for key in ("InputLocale", "SystemLocale", "UILanguage", "UserLocale"):
        add(locale, key, "en-US")
    accounts = add(add(shell, "UserAccounts"), "LocalAccounts")
    for name, group, password in (("LabAdmin", "Administrators", admin_password),
                                  ("LabUser", "Users", user_password)):
        account = add(accounts, "LocalAccount", **{"{" + wcm + "}action": "add"})
        add(account, "Name", name)
        add(account, "Group", group)
        secret = add(account, "Password")
        add(secret, "Value", password)
        add(secret, "PlainText", "true")
    autologon = add(shell, "AutoLogon")
    add(autologon, "Enabled", "true")
    add(autologon, "Username", "LabUser")
    add(autologon, "Domain", "GLAB-VM")
    add(autologon, "LogonCount", 2)
    password = add(autologon, "Password")
    add(password, "Value", user_password)
    add(password, "PlainText", "true")
    options = add(shell, "OOBE")
    for key in ("HideEULAPage", "HideOnlineAccountScreens", "HideWirelessSetupInOOBE"):
        add(options, key, "true")
    add(options, "ProtectYourPC", 3)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def setup_complete_cmd(nonce):
    """Fixed, owned later-setup entry point; bootstrap returns before user logon."""
    require(re.fullmatch(r"[0-9a-f]{32}", nonce), "invalid_nonce")
    return (
        "@echo off\r\n"
        f"rem GenLayer owned guest setup {nonce}\r\n"
        '"C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" '
        '-NoProfile -NonInteractive -ExecutionPolicy Bypass '
        '-File "C:\\LabTrial\\bootstrap.ps1"\r\n'
        "exit /b %ERRORLEVEL%\r\n"
    ).encode("ascii")


def seed_launcher(nonce):
    """Specialize copies the seed and arms SetupComplete without bootstrapping."""
    require(re.fullmatch(r"[0-9a-f]{32}", nonce), "invalid_nonce")
    expected = base64.b64encode(setup_complete_cmd(nonce)).decode("ascii")
    return (
        "$ErrorActionPreference='Stop';"
        "if([Security.Principal.WindowsIdentity]::GetCurrent().User.Value -cne 'S-1-5-18')"
        "{throw 'SYSTEM_required'};"
        "$s=[string](Get-CimInstance Win32_ComputerSystemProduct).IdentifyingNumber;"
        f"if($s.Trim() -cne 'GLAB-{nonce}'){{throw 'guest_identity_required'}};"
        "$disks=@(Get-CimInstance Win32_LogicalDisk | Where-Object {$_.VolumeName -eq 'GLABSEED'});"
        "if($disks.Count -ne 1){throw 'seed_required'};"
        "$setup='C:\\Windows\\Setup\\Scripts\\SetupComplete.cmd';"
        f"$expected='{expected}';"
        "if(Test-Path -LiteralPath $setup){"
        "if([Convert]::ToBase64String([IO.File]::ReadAllBytes($setup)) -cne $expected)"
        "{throw 'unexpected_existing_setup_complete'}};"
        "New-Item -ItemType Directory -Path 'C:\\LabTrial' -ErrorAction Stop | Out-Null;"
        "Copy-Item -Path ($disks[0].DeviceID+'\\payload\\*') -Destination 'C:\\LabTrial' -Recurse;"
        "New-Item -ItemType Directory -Path 'C:\\Windows\\Setup\\Scripts' -Force | Out-Null;"
        "if(-not(Test-Path -LiteralPath $setup)){"
        "$bytes=[Convert]::FromBase64String($expected);"
        "$stream=[IO.File]::Open($setup,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None);"
        "try{$stream.Write($bytes,0,$bytes.Length);$stream.Flush($true)}finally{$stream.Dispose()}};"
        "try{$c=Get-Content -LiteralPath 'C:\\LabTrial\\guest.json' -Raw | ConvertFrom-Json;"
        "$body=@{nonce=$c.nonce;stage='guest_seed_setup_complete_armed'}|ConvertTo-Json -Compress;"
        "Invoke-RestMethod -UseBasicParsing -Uri ($c.mailbox+'/stage') -Method Post "
        "-ContentType 'application/json' -Headers @{Authorization=('Bearer '+$c.mailbox_token)} "
        "-Body $body -TimeoutSec 3 | Out-Null}catch{};"
        "exit 0"
    )


class Mailbox:
    """Loopback-only, nonce-authenticated, bounded guest status transport."""

    def __init__(self, nonce):
        self.nonce = nonce
        self.token = secrets.token_hex(32)
        self.lock = threading.Lock()
        self.latest = {}
        self.command = {"command": "wait"}
        self.last_stage = "awaiting_guest"
        self.received = 0
        self.last_error = None
        self.error_count = 0
        self.baseline_hash = None
        mailbox = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                try:
                    require(hmac.compare_digest(self.headers.get("Authorization", ""),
                                                "Bearer " + mailbox.token), "unauthorized")
                    length = int(self.headers.get("Content-Length", "0"))
                    require(0 < length <= 65536 and self.path in {"/poll", "/stage"}, "bad_request")
                    self.connection.settimeout(5)
                    payload = json.loads(self.rfile.read(length))
                    require(payload.get("nonce") == mailbox.nonce, "invalid_guest")
                    with mailbox.lock:
                        mailbox.received += 1
                        if re.fullmatch(r"[a-z_]{1,80}", str(payload.get("stage", ""))):
                            mailbox.last_stage = payload["stage"]
                        if self.path == "/poll":
                            mailbox.latest = payload
                            mailbox.last_error = None
                            mailbox.error_count = 0
                        elif re.fullmatch(r"[A-Za-z0-9_]{1,100}", str(payload.get("error_code", ""))):
                            code = payload["error_code"]
                            mailbox.error_count = mailbox.error_count + 1 if mailbox.last_error == code else 1
                            mailbox.last_error = code
                        reply = mailbox.command if self.path == "/poll" else {"ok": True}
                        if reply.get("command") == "reboot" and (
                            payload.get("boot_utc") != reply["before_boot_utc"] or payload.get("result")
                        ):
                            reply = {"command": "wait"}
                        response = canonical(reply)
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(response)))
                    self.end_headers()
                    self.wfile.write(response)
                except Exception:
                    self.send_error(400)

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self):
        return f"http://10.0.2.2:{self.server.server_port}"

    def snapshot(self):
        with self.lock:
            return json.loads(json.dumps(self.latest)), self.last_stage

    def request_reboot(self, baseline, raw_hash):
        validate_baseline(baseline, self.nonce)
        require(re.fullmatch(r"[0-9a-f]{64}", raw_hash or ""), "baseline_file_hash_missing")
        with self.lock:
            require(self.command["command"] == "wait", "duplicate_reboot_request")
            self.baseline_hash = hashlib.sha256(canonical(baseline)).hexdigest()
            self.command = {"command": "reboot", "nonce": self.nonce, "baseline_sha256": raw_hash,
                            "before_boot_utc": baseline["system"]["boot_utc"]}

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def timestamp(value):
    require(isinstance(value, str), "boot_timestamp_required")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise TrialError("invalid_boot_timestamp") from None
    require(result.tzinfo is not None, "timestamp_timezone_required")
    return result


def validate_system(value):
    require(isinstance(value, dict), "system_evidence_required")
    require(value.get("product_type") == 1 and "Windows 11" in value.get("caption", ""),
            "windows_11_client_required")
    require(timestamp(value.get("observed_utc")) >= timestamp(value.get("boot_utc")),
            "observation_predates_boot")
    require(type(value.get("session_id")) is int and value["session_id"] > 0,
            "interactive_session_required")
    require(re.fullmatch(r"[0-9a-f]{64}", value.get("user_sid_sha256", "")), "user_identity_required")
    require(isinstance(value.get("version"), str) and value["version"], "os_version_required")


def validate_baseline(value, nonce):
    require(isinstance(value, dict) and value.get("nonce") == nonce, "baseline_identity_mismatch")
    require(value.get("wheel_sha256") == WHEEL_SHA and value.get("version") == VERSION,
            "baseline_wheel_mismatch")
    checks = value.get("checks", {})
    require(all(checks.get(key) is True for key in ("guest_identity_verified", "regular_interactive_user",
                "actual_glsim_four_grades_pass", "owned_logon_service_ready")), "baseline_failed")
    validate_system(value.get("system"))
    require(re.fullmatch(r"[0-9a-f]{32}", value.get("run_id", "")), "baseline_run_required")
    require(isinstance(value.get("owner"), str) and value["owner"], "baseline_owner_required")
    for key in ("report_sha256", "token_sha256", "installation_sha256", "task_identity_sha256",
                "marker_sha256", "package_record_sha256", "helper_sha256"):
        require(re.fullmatch(r"[0-9a-f]{64}", value.get(key, "")), "baseline_digest_required_" + key)


def validate_result(value, baseline, nonce):
    validate_baseline(baseline, nonce)
    require(isinstance(value, dict) and value.get("nonce") == nonce, "result_identity_mismatch")
    require(value.get("verification") == "pass", "guest_verification_failed")
    require(value.get("wheel_sha256") == WHEEL_SHA and value.get("package_version") == VERSION,
            "result_wheel_mismatch")
    require(value.get("before") == baseline["system"], "result_baseline_changed")
    after = value.get("after")
    validate_system(after)
    require(timestamp(after["boot_utc"]) > timestamp(baseline["system"]["observed_utc"]),
            "guest_did_not_reboot_after_baseline")
    require(all(after[key] == baseline["system"][key]
                for key in ("caption", "version", "product_type", "user_sid_sha256")),
            "guest_os_or_user_changed")
    require(value.get("baseline_run") == baseline["run_id"]
            and value.get("baseline_report_sha256") == baseline["report_sha256"], "baseline_history_changed")
    require(value.get("manual_service_start_after_reboot") is False, "manual_recovery_forbidden")
    require(re.fullmatch(r"[0-9a-f]{32}", value.get("post_reboot_run", ""))
            and value["post_reboot_run"] != baseline["run_id"], "fresh_post_reboot_run_required")
    require(re.fullmatch(r"[0-9a-f]{64}", value.get("post_reboot_report_sha256", "")),
            "post_reboot_report_digest_required")
    require(all(value.get("checks", {}).get(key) is True for key in (
        "new_windows_boot_same_regular_user", "automatic_ready_after_login", "task_started_after_boot",
        "installation_and_credentials_preserved", "frozen_report_and_history_preserved",
        "new_actual_glsim_four_grades_pass")), "recovery_checks_missing")


class QMP:
    def __init__(self, path):
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.socket.settimeout(10)
        self.socket.connect(str(path))
        self.reader = self.socket.makefile("rb")
        require("QMP" in json.loads(self.reader.readline()), "qmp_greeting_missing")
        self.execute("qmp_capabilities")

    def execute(self, name, arguments=None):
        self.socket.sendall(canonical({"execute": name, "arguments": arguments or {}}) + b"\n")
        while True:
            item = json.loads(self.reader.readline())
            if "return" in item:
                return item["return"]
            require("error" not in item, "qmp_command_failed")

    def close(self):
        self.reader.close()
        self.socket.close()


def seed(directory, wheel, nonce, mailbox):
    payload = directory / "seed" / "payload"
    payload.mkdir(parents=True, mode=0o700)
    config = {"nonce": nonce, "bios_serial": "GLAB-" + nonce, "wheel_sha256": WHEEL_SHA,
              "version": VERSION, "user": "LabUser", "wheel_name": wheel.name,
              "python_sha256": PYTHON_SHA, "mailbox": mailbox.url, "mailbox_token": mailbox.token}
    (payload / "guest.json").write_bytes(canonical(config))
    (directory / "seed" / "seed-launch.ps1").write_text(seed_launcher(nonce), encoding="utf-8")
    shutil.copyfile(wheel, payload / wheel.name)
    scripts = Path(__file__).resolve().parent
    for source, target in (("windows_reboot_guest.py", "windows_reboot_guest.py"),
                           ("windows-reboot-bootstrap.ps1", "bootstrap.ps1"),
                           ("windows-reboot-mailbox.ps1", "mailbox.ps1"),
                           ("windows-reboot-probe.ps1", "probe.ps1")):
        shutil.copyfile(scripts / source, payload / target)
    stage("download_verified_python_installer")
    download(PYTHON_URL, payload / "python-installer.exe", PYTHON_SHA, timeout=180)
    (directory / "seed" / "Autounattend.xml").write_bytes(
        unattend(nonce, "Glab!9" + secrets.token_hex(16), "Admin!9" + secrets.token_hex(16)))
    stage("create_private_unattended_seed")
    command(["xorriso", "-as", "mkisofs", "-J", "-r", "-V", "GLABSEED", "-o",
             directory / "seed.iso", directory / "seed"], timeout=90)


def boot_prompt_matches(text):
    """Require the explicit optical-media prompt; never infer it from timing."""
    normalized = " ".join(re.findall(r"[a-z0-9]+", text.lower()))
    return bool(re.search(r"\bpress any key to boot from (?:cd or dvd|cd dvd|cd|dvd)\b", normalized))


def read_boot_prompt(qmp, directory, deadline):
    """Read a private guest frame with bounded OCR; no OCR text leaves the job."""
    from PIL import Image

    screen = directory / "boot-prompt.ppm"
    image = directory / "boot-prompt.png"
    ocr_output = directory / "boot-prompt-ocr.txt"
    try:
        qmp.execute("screendump", {"filename": str(screen)})
        with Image.open(screen) as frame:
            # Firmware text is small. Upscale only this private OCR input, not
            # the setup images retained as visual diagnostic evidence.
            with frame.resize((frame.width * 2, frame.height * 2)) as enlarged:
                enlarged.save(image, format="PNG")
        remaining = deadline - time.monotonic()
        require(remaining > 0, "boot_prompt_ocr_deadline")
        with ocr_output.open("wb") as output:
            result = subprocess.run(
                ["tesseract", str(image), "stdout", "-l", "eng", "--psm", "6"],
                stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.DEVNULL,
                timeout=min(5, remaining), check=False)
        require(result.returncode == 0, "boot_prompt_ocr_failed")
        # Read no more than the bound, even if the external OCR process writes
        # unexpected output. Its raw output is never printed or retained.
        with ocr_output.open("rb") as output:
            text = output.read(65537)
        require(len(text) <= 65536, "boot_prompt_ocr_oversized")
        return boot_prompt_matches(text.decode("utf-8", errors="replace"))
    finally:
        for path in (screen, image, ocr_output):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass


def confirm_optical_boot(qemu, qmp, directory):
    """Send one space only after seeing the actual CD/DVD boot prompt."""
    deadline = time.monotonic() + BOOT_PROMPT_TIMEOUT
    stage("await_visible_optical_boot_prompt")
    while time.monotonic() < deadline:
        require(qemu.poll() is None, "qemu_exited_before_boot_prompt")
        visible = False
        try:
            visible = read_boot_prompt(qmp, directory, deadline)
        except Exception:
            # Incomplete frames or OCR failures allow another observation, never
            # an inferred keypress. No third-party text is copied to the log.
            stage("guest_boot_prompt_frame_unavailable")
        require(qemu.poll() is None, "qemu_exited_before_boot_prompt")
        remaining = deadline - time.monotonic()
        if visible and remaining > 0:
            stage("guest_optical_boot_prompt_recognized")
            # Deliberately outside the observation retry block: a failed QMP
            # response could still mean the key arrived. Never retry this input.
            qmp.execute("send-key", {"keys": [{"type": "qcode", "data": "spc"}], "hold-time": 100})
            stage("guest_optical_boot_confirmed_once")
            return
        if remaining > 0:
            time.sleep(min(1, remaining))
    raise TrialError("visible_optical_boot_prompt_timeout")


def emit_setup_snapshot(qmp, directory):
    """Emit one bounded, full-resolution image from the disposable setup guest.

    This optional diagnostic must not change verification or its failure stage.
    Credentials and application data are never deliberately displayed by setup.
    """
    screen = directory / "setup-screen.ppm"
    try:
        from PIL import Image

        print("Windows guest trial: guest_setup_snapshot_started", flush=True)
        qmp.execute("screendump", {"filename": str(screen)})
        with Image.open(screen) as snapshot, io.BytesIO() as encoded:
            snapshot.save(encoded, format="PNG", optimize=True)
            png = encoded.getvalue()
        if len(png) > SETUP_PNG_MAX_BYTES:
            print("Windows guest trial: guest_setup_snapshot_skipped_oversize", flush=True)
            return
        print("GLAB_GUEST_SETUP_PNG:" + base64.b64encode(png).decode("ascii"), flush=True)
        print("Windows guest trial: guest_setup_snapshot_emitted", flush=True)
    except Exception:
        # QMP, image decoding, missing Pillow, and private-file failures are only
        # diagnostic failures. Never include raw exception details in public logs.
        print("Windows guest trial: guest_setup_snapshot_skipped_error", flush=True)
    finally:
        try:
            screen.unlink(missing_ok=True)
        except OSError:
            pass


def wait_for(qemu, mailbox, predicate, seconds, phase, output, evidence, *, setup_snapshot=None):
    started = time.monotonic()
    deadline = started + seconds
    next_snapshot = started + SETUP_SNAPSHOT_INTERVAL
    snapshot_attempts = 0
    last_note = 0
    while time.monotonic() < deadline:
        require(qemu.poll() is None, "qemu_exited_" + str(qemu.returncode))
        snapshot, guest_stage = mailbox.snapshot()
        require(mailbox.error_count < 10, mailbox.last_error or "guest_repeated_error")
        if time.monotonic() - last_note >= 30:
            stage(phase + ":" + guest_stage)
            evidence.update(stage=phase, guest_stage=guest_stage, mailbox_messages=mailbox.received,
                            guest_error_code=mailbox.last_error)
            output.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
            last_note = time.monotonic()
        result = snapshot.get("result") or snapshot.get("probe") or {}
        if result.get("verification") == "inconclusive":
            failure = {key: str(result.get(key, ""))[:100]
                       for key in ("verification", "stage", "error_code")}
            diagnostic = result.get("preparation_diagnostic")
            if isinstance(diagnostic, dict):
                detail = diagnostic.get("error", "")
                detail = detail[:2048] if isinstance(detail, str) else ""
                detail = re.sub(r"[\x00-\x1f\x7f]", " ", detail)
                detail = re.sub(r"(?i)\bauthorization\s*[:=]\s*(?:bearer\s+)?\S+",
                                "Authorization: <redacted>", detail)
                detail = re.sub(r"(?i)\bbearer\s+\S+", "Bearer <redacted>", detail)
                failure["preparation_diagnostic"] = {
                    "python": str(diagnostic.get("python", ""))[:32],
                    "status": str(diagnostic.get("status", ""))[:32],
                    "ready": diagnostic.get("ready") is True, "error": detail[:800],
                }
            evidence["guest_failure"] = failure
        require(result.get("verification") != "inconclusive", "guest_" + str(result.get("error_code", "failed")))
        require(not guest_stage.endswith(("_failed", "_mismatch")), guest_stage)
        if predicate(snapshot):
            return snapshot
        if (phase == "await_first_logon_and_baseline" and setup_snapshot is not None
                and not snapshot.get("baseline") and snapshot_attempts < SETUP_SNAPSHOT_LIMIT
                and time.monotonic() >= next_snapshot):
            snapshot_attempts += 1
            try:
                setup_snapshot()
            except Exception:
                print("Windows guest trial: guest_setup_snapshot_skipped_error", flush=True)
            next_snapshot = time.monotonic() + SETUP_SNAPSHOT_INTERVAL
        time.sleep(2)
    raise TrialError(phase + "_timeout")


def trial(wheel, output, runner_temp, first_login_timeout=2700):
    evidence = {"verification": "inconclusive", "scope": "Windows 11 guest OS reboot and user login",
                "outer_runner_rebooted": False, "personal_computer_accessed": False,
                "studio_included": False, "before_login_startup": False,
                "wheel_sha256": WHEEL_SHA, "windows_iso_sha256": ISO_SHA, "package_version": VERSION,
                "first_login_timeout_seconds": first_login_timeout}
    nonce = uuid.uuid4().hex
    directory = Path(tempfile.mkdtemp(prefix="glab-win-", dir=runner_temp)).resolve()
    qemu = swtpm = mailbox = qmp = None
    log = None
    try:
        stage("capacity_and_artifact_gates")
        require(digest(wheel) == WHEEL_SHA, "released_wheel_sha256_mismatch")
        free = shutil.disk_usage(directory).free
        evidence["initial_free_disk_gib"] = round(free / GIB, 2)
        require(free >= 35 * GIB, "insufficient_actual_disk_need_35_GiB")
        require(os.cpu_count() >= 2, "two_cpu_required")
        for program in ("qemu-system-x86_64", "qemu-img", "swtpm", "xorriso", "curl", "tesseract"):
            require(shutil.which(program) is not None, "missing_" + program)
        outer_before = outer_boot()
        # Firmware with Microsoft keys enrolled, TPM 2.0, UEFI and SMM. No Windows
        # CPU/RAM/TPM/Secure Boot checks are bypassed by the unattended answer file.
        code = Path("/usr/share/OVMF/OVMF_CODE_4M.ms.fd")
        nvram = Path("/usr/share/OVMF/OVMF_VARS_4M.ms.fd")
        require(code.is_file() and nvram.is_file(), "microsoft_secureboot_firmware_required")
        shutil.copyfile(nvram, directory / "vars.fd")
        (directory / "tpm").mkdir()
        mailbox = Mailbox(nonce)
        seed(directory, wheel, nonce, mailbox)
        stage("download_verified_windows_evaluation_iso")
        download(ISO_URL, directory / "windows.iso", ISO_SHA)
        stage("create_empty_guest_disk_and_tpm")
        command(["qemu-img", "create", "-f", "qcow2", directory / "windows.qcow2", "80G"])
        log = (directory / "private-qemu.log").open("wb")
        swtpm = subprocess.Popen(["swtpm", "socket", "--tpm2", "--tpmstate", f"dir={directory / 'tpm'}",
                                  "--ctrl", f"type=unixio,path={directory / 'tpm.sock'}",
                                  "--flags", "not-need-init"], stdin=subprocess.DEVNULL,
                                 stdout=log, stderr=log)
        for _ in range(100):
            if (directory / "tpm.sock").exists():
                break
            require(swtpm.poll() is None, "swtpm_exited")
            time.sleep(.1)
        require((directory / "tpm.sock").exists(), "tpm_socket_timeout")
        stage("install_windows_in_private_guest")
        qemu = subprocess.Popen([
            "qemu-system-x86_64", "-machine", "q35,accel=kvm,smm=on", "-cpu",
            "host,hv_relaxed,hv_vapic,hv_spinlocks=0x1fff,hv_vpindex,hv_runtime,hv_synic,hv_stimer,hv_time",
            "-smp", "2", "-m", "8192", "-global", "driver=cfi.pflash01,property=secure,value=on",
            "-drive", f"if=pflash,format=raw,unit=0,readonly=on,file={code}",
            "-drive", f"if=pflash,format=raw,unit=1,file={directory / 'vars.fd'}",
            "-drive", f"if=none,id=osdisk,format=qcow2,file={directory / 'windows.qcow2'}",
            "-device", "nvme,drive=osdisk,serial=GLABDISK,bootindex=2",
            "-drive", f"if=none,id=installcd,format=raw,media=cdrom,readonly=on,file={directory / 'windows.iso'}",
            "-device", "ide-cd,drive=installcd,bus=ide.0,bootindex=1",
            "-drive", f"if=none,id=seedcd,format=raw,media=cdrom,readonly=on,file={directory / 'seed.iso'}",
            "-device", "ide-cd,drive=seedcd,bus=ide.1",
            "-chardev", f"socket,id=chrtpm,path={directory / 'tpm.sock'}",
            "-tpmdev", "emulator,id=tpm0,chardev=chrtpm", "-device", "tpm-tis,tpmdev=tpm0",
            "-netdev", "user,id=net0", "-device", "e1000e,netdev=net0",
            "-smbios", f"type=1,serial=GLAB-{nonce}", "-uuid", str(uuid.UUID(nonce)),
            "-display", "none", "-vga", "std", "-monitor", "none", "-serial", "none",
            "-qmp", f"unix:{directory / 'qmp.sock'},server=on,wait=off",
        ], stdin=subprocess.DEVNULL, stdout=log, stderr=log)
        for _ in range(100):
            if (directory / "qmp.sock").exists():
                break
            require(qemu.poll() is None, "qemu_initial_start_failed")
            time.sleep(.1)
        require((directory / "qmp.sock").exists(), "qmp_socket_timeout")
        qmp = QMP(directory / "qmp.sock")
        # A blind input loop can continue into Setup and activate Cancel. Observe
        # the actual optical boot prompt, confirm it once, then stop all input.
        confirm_optical_boot(qemu, qmp, directory)
        before = wait_for(qemu, mailbox, lambda body: bool(body.get("baseline")), first_login_timeout,
                          "await_first_logon_and_baseline", output, evidence,
                          setup_snapshot=lambda: emit_setup_snapshot(qmp, directory))
        baseline = before["baseline"]
        validate_baseline(baseline, nonce)
        require(before.get("boot_utc") == baseline["system"]["boot_utc"], "observer_baseline_boot_mismatch")
        # Retain evidence outside Windows before sending the only reboot command.
        evidence.update(baseline=baseline, baseline_retained_before_reboot=True,
                        outer_boot_id=outer_before, qemu_pid=qemu.pid)
        output.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
        disk_identity = (directory / "windows.qcow2").stat().st_ino
        require(outer_boot() == outer_before, "outer_runner_changed_before_reboot")
        stage("request_owned_windows_guest_reboot")
        mailbox.request_reboot(baseline, before.get("baseline_sha256"))
        after = wait_for(qemu, mailbox, lambda body: bool(body.get("result")), 900,
                         "await_reboot_logon_and_automatic_recovery", output, evidence)
        result = after["result"]
        validate_result(result, baseline, nonce)
        require(after.get("boot_utc") == result["after"]["boot_utc"], "outside_observer_boot_mismatch")
        require(outer_boot() == outer_before and qemu.poll() is None, "outer_observer_changed")
        require((directory / "windows.qcow2").stat().st_ino == disk_identity, "guest_disk_replaced")
        require(hashlib.sha256(canonical(after["baseline"])).hexdigest() == mailbox.baseline_hash,
                "retained_baseline_changed")
        evidence.update(verification="pass", result=result, outer_linux_boot_unchanged=True,
                        qemu_process_survived_guest_reboot=True, same_persistent_guest_disk=True)
        stage("guest_reboot_verification_passed")
    except Exception as exc:
        evidence.update(verification="inconclusive", failure_stage=STAGE,
                        error_code=str(exc) if isinstance(exc, TrialError) else type(exc).__name__)
        # The guest desktop is never used to display tokens or enter passwords;
        # retain only its last setup/login screen to diagnose installation errors.
        # Installer logs, unattended XML, and guest disks are never published.
        if qmp and qemu and qemu.poll() is None:
            try:
                from PIL import Image

                screen = directory / "screen.ppm"
                qmp.execute("screendump", {"filename": str(screen)})
                with Image.open(screen) as snapshot:
                    snapshot.save(runner_temp / "windows-guest-screen.png")
                evidence["diagnostic_guest_screen_saved"] = True
            except Exception:
                evidence["diagnostic_guest_screen_saved"] = False
        if log:
            log.flush()
            diagnostic = (directory / "private-qemu.log").read_text(errors="replace")[:2000]
            # QEMU receives no passwords or app credentials in its arguments.
            evidence["qemu_diagnostic"] = diagnostic.replace(str(directory), "<private-guest>").replace(nonce, "<nonce>")
        stage("guest_reboot_verification_inconclusive")
    finally:
        if qmp:
            qmp.close()
        for process in (qemu, swtpm):
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
        if log:
            log.close()
        if mailbox:
            mailbox.stop()
        # This fresh private subtree is the sole recursive deletion target.
        require(directory.parent == runner_temp and directory.name.startswith("glab-win-"),
                "cleanup_path_invalid")
        shutil.rmtree(directory)
        evidence["private_guest_files_removed"] = True
        output.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(evidence, indent=2))
    return 0 if evidence["verification"] == "pass" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--first-login-timeout", type=int, default=2700,
                        help="300..2700 seconds; short diagnostics do not establish reboot coverage")
    args = parser.parse_args()
    # First action: refuse the user's Windows computer and any ordinary machine.
    root = hosted_temp()
    require(300 <= args.first_login_timeout <= 2700, "invalid_first_login_timeout")
    output = args.output.resolve()
    require(output.parent == root, "evidence_must_be_in_runner_temp")
    return trial(args.wheel.resolve(strict=True), output, root, args.first_login_timeout)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except TrialError as exc:
        print("Windows guest controller refused: " + str(exc), file=sys.stderr)
        raise SystemExit(1) from None
