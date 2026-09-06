"""Verify an installed wheel across a real, disposable Ubuntu guest OS reboot.

The outer Linux runner observes a KVM guest; it is never rebooted. Lab runs as a
different account from the SSH observer, with linger configured by guest admin.
Only compact evidence leaves the guest. Tokens, SSH keys and logs remain private.
No provider-host reboot, power-loss, macOS, logout or Studio claim is made.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

# Versioned Canonical release, not the changing daily/current image.
IMAGE_BASE = "https://cloud-images.ubuntu.com/releases/noble/release-20260826/"
IMAGE_NAME = "ubuntu-24.04-server-cloudimg-amd64.img"
IMAGE_SHA256 = "d0fe84bb5f80853425fa6be28e2c106f30104c3cfe8611933f2e65c9b63f0e30"
IMAGE_SIGNER = "D2EB44626FDDC30B513D5BB71A5D6C4C7DB87C81"
LAB_USER = "lab"
LAB_ROOT = Path("/home/lab/reboot-check")
DATA = LAB_ROOT / "state"
VENV = LAB_ROOT / "environment"
PROOF = Path("/var/lib/gl-agent-lab-reboot-proof")
GUEST_SCRIPT = "/home/observer/verify-linux-reboot.py"
GUEST_MARKER = Path("/etc/gl-agent-lab-reboot-guest")
URL = "http://127.0.0.1:8765"
STAGE = "initialization"


class VerificationError(RuntimeError):
    pass


def checked(condition, message):
    if not condition:
        raise VerificationError(message)


def stage(name):
    global STAGE
    STAGE = name
    print("Linux guest reboot verification: " + name, file=sys.stderr, flush=True)


def command(args, *, name, timeout=60, expected=(0,)):
    stage(name)
    try:
        result = subprocess.run([str(arg) for arg in args], capture_output=True,
                                stdin=subprocess.DEVNULL, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        raise VerificationError(name + ": timeout") from None
    except OSError as exc:
        raise VerificationError(f"{name}: OS error {exc.errno}") from None
    checked(result.returncode in expected, f"{name}: exit {result.returncode}")
    # Never expose raw command output: it can contain keys or third-party payloads.
    return result.stdout


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def boot_id():
    return str(uuid.UUID(Path("/proc/sys/kernel/random/boot_id").read_text().strip()))


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def lab_command(*args, name, timeout=60):
    import pwd

    uid = pwd.getpwnam(LAB_USER).pw_uid
    return command(["sudo", "-n", "-u", LAB_USER, "env", "-i", "HOME=/home/lab",
                    "USER=lab", "LOGNAME=lab", "PATH=/usr/bin:/bin",
                    f"XDG_RUNTIME_DIR=/run/user/{uid}",
                    f"DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{uid}/bus",
                    "PYTHONUTF8=1", "PYTHONNOUSERSITE=1", "PYTHONDONTWRITEBYTECODE=1", *args],
                   name=name, timeout=timeout)


def cli(*args, timeout=60):
    return json.loads(lab_command(VENV / "bin/gl-agent-lab", "--data-dir", DATA, *args,
                                 name="Lab CLI " + " ".join(args[:2]), timeout=timeout))


def run_agent(admin):
    stage("actual GLSim HTTP run")
    run_id = admin.create_run("escrow-normal", agent="safe", backend="glsim")["run_id"]
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        if admin.get_run(run_id)["status"] in {"completed", "failed", "cancelled", "interrupted"}:
            break
        time.sleep(0.5)
    report = admin.report(run_id)
    checked(report.get("status") == "completed" and report.get("verdict") == "pass",
            "GLSim HTTP run did not complete successfully")
    checked(all(report.get("grades", {}).get(key, {}).get("status") == "pass"
                for key in ("decision", "behavior", "outcome", "completion")),
            "GLSim HTTP run did not pass all four grades")
    manifest = report.get("manifest", {})
    checked(manifest.get("backend") == "glsim"
            and manifest.get("runtime", {}).get("execution_success") is True,
            "Run lacks actual GLSim execution evidence")
    return run_id, report


def guest_prepare(wheel):
    import pwd

    checked(not LAB_ROOT.exists() and not PROOF.exists(), "Guest test paths already exist")
    PROOF.mkdir(mode=0o700)
    command(["apt-get", "update", "-qq"], name="guest package index", timeout=180)
    command(["env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "-o",
             "DPkg::Lock::Timeout=120", "install", "-y",
             "--no-install-recommends", "python3-venv", "dbus-user-session"],
            name="guest prerequisite installation", timeout=300)
    lab_command("mkdir", "-m", "700", LAB_ROOT, name="persistent Lab directory")
    lab_command("/usr/bin/python3", "-I", "-m", "venv", VENV, name="fresh Lab venv")
    lab_command(VENV / "bin/python", "-I", "-m", "pip", "--isolated", "install",
                "--no-cache-dir", "--index-url", "https://pypi.org/simple", wheel,
                name="install actual wheel in guest", timeout=600)
    # This is explicit host-administrator setup, not a feature of Lab install.
    command(["loginctl", "enable-linger", LAB_USER], name="configure Lab linger")
    uid = pwd.getpwnam(LAB_USER).pw_uid
    command(["systemctl", "start", f"user@{uid}.service"], name="initial user manager")
    return {"prepared": True, "wheel_sha256": digest(wheel), "linger_configured_by_admin": True}


def guest_before():
    import importlib.metadata

    import genlayer_agent_lab
    from genlayer_agent_lab.client import LabClient

    checked(Path(genlayer_agent_lab.__file__).resolve().is_relative_to(VENV.resolve()),
            "Imported package is outside the installed guest venv")
    cli("init")
    doctor = cli("doctor", timeout=960)
    runtime = doctor.get("runtime", {})
    checked(runtime.get("ready") is True
            and runtime.get("provenance", {}).get("execution_success") is True,
            "Cold installed GLSim doctor failed")
    installed = cli("service", "install", "--port", "8765", "--start")
    checked(all(installed.get(key) is True for key in ("installed", "enabled", "running", "ready")),
            "Initial service did not become enabled and ready")
    token = (DATA / "admin.token").read_text().strip()
    with LabClient(URL, token, timeout=10) as admin:
        run_id, report = run_agent(admin)
    baseline = {"boot_id": boot_id(), "machine_id": Path("/etc/machine-id").read_text(),
                "token": token, "token_sha256": digest(DATA / "admin.token"),
                "run_id": run_id, "report": report, "owner": installed["owner"],
                "installation_sha256": digest(DATA / "service/installation.json")}
    baseline_path = PROOF / "baseline.json"
    baseline_path.write_bytes(canonical(baseline))
    baseline_path.chmod(0o600)
    command(["sync"], name="flush pre-reboot evidence")
    return {"boot_id": baseline["boot_id"], "glsim_http_run": run_id,
            "report_sha256": hashlib.sha256(canonical(report)).hexdigest(),
            "package_version": importlib.metadata.version("genlayer-agent-lab"),
            "installed_from_wheel": True, "enabled": True, "ready": True}


def guest_after():
    import httpx

    from genlayer_agent_lab.client import LabClient

    baseline = json.loads((PROOF / "baseline.json").read_bytes())
    current_boot = boot_id()
    checked(current_boot != baseline["boot_id"], "Guest kernel boot ID did not change")
    checked(Path("/etc/machine-id").read_text() == baseline["machine_id"],
            "Guest machine identity changed")
    # Crucial: no sudo -u lab, Lab login, start or systemctl --user command here.
    # SSH is authenticated only as observer, so it cannot trigger Lab's manager.
    stage("observe automatic service readiness before any Lab session")
    deadline = time.monotonic() + 90
    ready = False
    with httpx.Client(trust_env=False, timeout=2, follow_redirects=False) as http:
        while time.monotonic() < deadline:
            try:
                response = http.get(URL + "/service/health")
                ready = response.status_code == 200 and response.json().get("service_id") == baseline["owner"]
            except (httpx.HTTPError, ValueError, AttributeError):
                ready = False
            if ready:
                break
            time.sleep(1)
    checked(ready, "Service failed to start automatically before any Lab login")
    sessions = command(["loginctl", "show-user", LAB_USER, "--property=Sessions", "--value"],
                       name="verify no Lab login sessions").strip()
    checked(not sessions, "Lab login session could invalidate automatic startup evidence")
    linger = command(["loginctl", "show-user", LAB_USER, "--property=Linger", "--value"],
                     name="verify persistent linger").strip()
    checked(linger == b"yes", "Lab linger did not persist")
    checked(digest(DATA / "admin.token") == baseline["token_sha256"], "Admin token changed")
    checked(digest(DATA / "service/installation.json") == baseline["installation_sha256"],
            "Service installation changed")
    with LabClient(URL, baseline["token"], timeout=10) as admin:
        old_report = admin.report(baseline["run_id"])
        checked(old_report == baseline["report"], "Frozen report changed across guest reboot")
        checked(baseline["run_id"] in {item["run_id"] for item in admin.list_runs()},
                "Original run history was lost")
        second_id, _ = run_agent(admin)
        checked({baseline["run_id"], second_id} <= {item["run_id"] for item in admin.list_runs()},
                "Post-reboot run history is incomplete")
    return {"boot_id": current_boot, "guest_kernel_rebooted": True, "same_guest_disk": True,
            "automatic_ready_before_lab_login": True, "lab_login_sessions_absent": True,
            "linger_persisted": True, "admin_token_preserved_and_accepted": True,
            "installation_preserved": True, "frozen_report_preserved": True,
            "history_preserved": True, "glsim_http_run": second_id,
            "report_sha256": hashlib.sha256(canonical(old_report)).hexdigest()}


def guest(args):
    checked(os.geteuid() == 0 and GUEST_MARKER.is_file(), "Guest-only operation refused")
    checked(GUEST_MARKER.read_text().strip() == args.guest_id,
            "Disposable guest identity does not match")
    if args._guest == "prepare":
        return guest_prepare(args.wheel)
    if args._guest == "before":
        return guest_before()
    if args._guest == "after":
        return guest_after()
    # This branch is invoked only through the pinned observer SSH connection.
    # Never expose a host-side reboot command, even as a fallback.
    checked((PROOF / "baseline.json").is_file(), "No pre-reboot evidence")
    command(["systemctl", "reboot"], name="request graceful guest-only reboot")
    return {"reboot_requested": True}


def download(url, path, *, limit=1048576, timeout=60):
    command(["curl", "--fail", "--silent", "--show-error", "--location",
             "--proto", "=https", "--proto-redir", "=https", "--retry", "2",
             "--connect-timeout", "20", "--max-time", str(timeout),
             "--max-filesize", str(limit), "--output", path, url],
            name="download " + path.name, timeout=timeout * 3 + 15)


def verify_image(root):
    for name in ("SHA256SUMS", "SHA256SUMS.gpg"):
        download(IMAGE_BASE + name, root / name)
    download("https://keyserver.ubuntu.com/pks/lookup?op=get&search=0x" + IMAGE_SIGNER,
             root / "ubuntu-signing-key.asc")
    keyring = root / "gnupg"
    keyring.mkdir(mode=0o700)
    gpg = ["gpg", "--homedir", keyring, "--batch", "--no-auto-key-retrieve"]
    command([*gpg, "--import", root / "ubuntu-signing-key.asc"], name="import pinned Ubuntu key")
    signature = command([*gpg, "--status-fd", "1", "--verify", root / "SHA256SUMS.gpg",
                         root / "SHA256SUMS"], name="verify Ubuntu checksum signature")
    valid = [line.split() for line in signature.decode().splitlines()
             if line.startswith("[GNUPG:] VALIDSIG ")]
    checked(any(parts[2] == IMAGE_SIGNER or parts[-1] == IMAGE_SIGNER for parts in valid),
            "Checksum signature does not match pinned Ubuntu signer")
    expected = [line.split()[0] for line in (root / "SHA256SUMS").read_text().splitlines()
                if line.split()[-1].lstrip("*") == IMAGE_NAME]
    checked(expected == [IMAGE_SHA256], "Official checksum differs from pinned image")
    image = root / IMAGE_NAME
    download(IMAGE_BASE + IMAGE_NAME, image, limit=1024**3, timeout=300)
    checked(digest(image) == IMAGE_SHA256, "Ubuntu image checksum failed")
    return image


def verify(wheel):
    import fcntl

    started = time.monotonic()
    result = {"schema_version": 1, "verification": "fail", "wheel": wheel.name,
              "wheel_sha256": digest(wheel), "guest_cleanup": False,
              "limits": {"provider_host_reboot_tested": False, "power_loss_tested": False,
                         "macos_reboot_tested": False, "logout_tested": False,
                         "studio_tested": False, "external_human_onboarding_tested": False}}
    parent = Path(tempfile.gettempdir()).resolve()
    root = Path(tempfile.mkdtemp(prefix="gl-lab-linux-reboot-", dir=parent))
    process = None
    private_log = None
    host_before = boot_id()
    guest_id = uuid.uuid4().hex
    try:
        checked(shutil.disk_usage(root).free >= 6 * 1024**3, "Less than 6 GiB free disk space")
        stage("KVM preflight without emulation fallback")
        with open("/dev/kvm", "rb+") as kvm:
            checked(fcntl.ioctl(kvm, 0xAE00, 0) == 12, "Unsupported KVM API version")
            vm_descriptor = fcntl.ioctl(kvm, 0xAE01, 0)
            os.close(vm_descriptor)
        result["kvm_available"] = True
        image = verify_image(root)
        result["ubuntu_image"] = {"release": "24.04/20260826", "sha256": IMAGE_SHA256,
                                  "official_signature_verified": True}
        for name in ("observer-key", "guest-host-key"):
            command(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", root / name],
                    name="generate disposable " + name)
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        known_hosts = root / "known_hosts"
        known_hosts.write_text(f"[127.0.0.1]:{port} " + (root / "guest-host-key.pub").read_text())
        config = {"users": [
            {"name": "observer", "shell": "/bin/bash", "lock_passwd": True,
             "sudo": ["ALL=(ALL) NOPASSWD:ALL"],
             "ssh_authorized_keys": [(root / "observer-key.pub").read_text().strip()]},
            {"name": LAB_USER, "shell": "/bin/bash", "lock_passwd": True}],
            "disable_root": True, "ssh_pwauth": False, "ssh_quiet_keygen": True,
            "no_ssh_fingerprints": True, "ssh_publish_hostkeys": {"enabled": False},
            "ssh_keys": {"ed25519_private": (root / "guest-host-key").read_text(),
                         "ed25519_public": (root / "guest-host-key.pub").read_text()},
            "write_files": [{"path": str(GUEST_MARKER), "permissions": "0600",
                             "content": guest_id}]}
        (root / "user-data").write_text("#cloud-config\n" + json.dumps(config))
        (root / "meta-data").write_text(json.dumps({"instance-id": guest_id,
                                                     "local-hostname": "lab-reboot-guest"}))
        command(["cloud-localds", root / "seed.img", root / "user-data", root / "meta-data"],
                name="prepare private NoCloud seed")
        disk = root / "guest.qcow2"
        command(["qemu-img", "create", "-f", "qcow2", "-F", "qcow2", "-b", image, disk, "8G"],
                name="create disposable persistent guest disk")
        private_log = (root / "qemu-private.log").open("wb")
        stage("boot owned KVM guest")
        process = subprocess.Popen([
            "qemu-system-x86_64", "-name", "gl-lab-reboot-" + guest_id,
            "-accel", "kvm", "-cpu", "host", "-smp", "2", "-m", "4096",
            "-display", "none", "-monitor", "none", "-serial", "stdio",
            "-drive", f"file={disk},if=virtio,format=qcow2",
            "-drive", f"file={root / 'seed.img'},if=virtio,format=raw,readonly=on",
            "-nic", f"user,model=virtio-net-pci,hostfwd=tcp:127.0.0.1:{port}-:22"],
            stdin=subprocess.DEVNULL, stdout=private_log, stderr=subprocess.STDOUT)
        ssh_options = ["-F", "/dev/null", "-i", str(root / "observer-key"),
                       "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
                       "-o", "StrictHostKeyChecking=yes", "-o", f"UserKnownHostsFile={known_hosts}",
                       "-o", "GlobalKnownHostsFile=/dev/null", "-o", "ConnectTimeout=4",
                       "-o", "ServerAliveInterval=5", "-o", "ServerAliveCountMax=2"]

        def ssh(args, *, name, timeout=60, expected=(0,)):
            checked(process.poll() is None, "Owned QEMU process exited")
            return command(["ssh", *ssh_options, "-p", str(port), "observer@127.0.0.1",
                            shlex.join([str(arg) for arg in args])],
                           name=name, timeout=timeout, expected=expected)

        def wait_ssh(previous=None):
            deadline = time.monotonic() + 240
            while time.monotonic() < deadline:
                checked(process.poll() is None, "Owned QEMU process exited")
                try:
                    value = ssh(["cat", "/proc/sys/kernel/random/boot_id"],
                                name="wait for guest boot", timeout=12).decode().strip()
                    value = str(uuid.UUID(value))
                    if value != previous:
                        return value
                except VerificationError:
                    pass
                time.sleep(3)
            raise VerificationError("Guest SSH boot deadline exceeded")

        initial_boot = wait_ssh()
        ssh(["sudo", "-n", "cloud-init", "status", "--wait"],
            name="finish first-boot cloud-init", timeout=240)
        # Only these two files are transferred; no repository mounts or host credentials.
        command(["scp", *ssh_options, "-P", str(port), wheel, Path(__file__).resolve(),
                 "observer@127.0.0.1:/home/observer/"], name="copy wheel and guest probe")
        guest_wheel = Path("/home/observer") / wheel.name
        ssh(["chmod", "755", "/home/observer"], name="allow Lab to read transferred wheel")

        def probe(mode, python, timeout):
            raw = ssh(["sudo", "-n", python, "-I", "-B", GUEST_SCRIPT, "--_guest", mode,
                       "--guest-id", guest_id, "--wheel", guest_wheel],
                      name="guest " + mode, timeout=timeout)
            value = json.loads(raw)
            checked(value.get("verification") == "pass",
                    "Guest " + mode + " failed at " + value.get("stage", "unknown")
                    + ": " + value.get("error", "unspecified"))
            return value

        result["prepare"] = probe("prepare", "/usr/bin/python3", 1000)
        checked(result["prepare"]["wheel_sha256"] == result["wheel_sha256"],
                "Transferred wheel hash differs")
        result["before"] = probe("before", VENV / "bin/python", 1200)
        checked(result["before"]["boot_id"] == initial_boot, "Unexpected guest reboot during setup")
        # An SSH disconnect is expected while this exact guest shuts down. Success
        # is decided by a new guest kernel boot ID, never by this command's exit.
        ssh(["sudo", "-n", "/usr/bin/python3", "-I", GUEST_SCRIPT, "--_guest", "reboot",
             "--guest-id", guest_id], name="reboot only the identified guest", expected=(0, 255))
        new_boot = wait_ssh(previous=initial_boot)
        checked(boot_id() == host_before, "Outer host kernel boot ID changed")
        result["after"] = probe("after", VENV / "bin/python", 360)
        checked(result["after"]["boot_id"] == new_boot, "Guest boot identity changed again")
        result.update(verification="pass", guest_os_reboot_tested=True,
                      outer_runner_boot_unchanged=True, same_qemu_process=True,
                      no_lab_login_or_manual_start_after_reboot=True)
    except Exception as exc:
        result.update(verification="fail", stage=STAGE,
                      error=str(exc) if isinstance(exc, VerificationError) else type(exc).__name__)
    finally:
        signal.alarm(0)
        try:
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
            if private_log is not None:
                private_log.close()
            checked(root.resolve().parent == parent and root.name.startswith("gl-lab-linux-reboot-"),
                    "Owned guest cleanup path changed")
            shutil.rmtree(root)
            result["guest_cleanup"] = True
        except Exception:
            result.update(verification="fail", cleanup_error="Owned guest cleanup failed")
    result["elapsed_seconds"] = round(time.monotonic() - started, 3)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--_guest", choices=("prepare", "before", "after", "reboot"),
                        help=argparse.SUPPRESS)
    parser.add_argument("--guest-id", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if platform.system() != "Linux":
        parser.error("A Linux KVM host is required")
    os.umask(0o077)
    if args._guest:
        try:
            result = {"verification": "pass", **guest(args)}
        except Exception as exc:
            result = {"verification": "fail", "stage": STAGE,
                      "error": str(exc) if isinstance(exc, VerificationError) else type(exc).__name__}
    else:
        if not args.wheel or not args.wheel.is_file() or args.wheel.suffix != ".whl":
            parser.error("--wheel must identify an existing wheel")

        def interrupted(_signum, _frame):
            raise VerificationError("Host verifier interrupted or 30-minute deadline exceeded")

        signal.signal(signal.SIGTERM, interrupted)
        signal.signal(signal.SIGINT, interrupted)
        signal.signal(signal.SIGALRM, interrupted)
        signal.alarm(1800)
        result = verify(args.wheel.resolve())
    evidence = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.write_text(evidence, encoding="utf-8")
    print(evidence, end="", flush=True)
    return 0 if args._guest or result["verification"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
