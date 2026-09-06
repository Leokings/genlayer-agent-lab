"""Initialize a diskless macOS-type VM on an owned Intel GitHub Mac runner.

Uses the unmodified Oracle base package and its default macOS hardware settings.
No Apple installation media, guest OS, host restart or hardware-check override.
VirtualBox logs remain private because upstream SMC diagnostics can contain data.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "macos_capacity", Path(__file__).with_name("macos-reboot-preflight.py"))
capacity = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(capacity)

URL = "https://download.virtualbox.org/virtualbox/7.2.16/VirtualBox-7.2.16-174877-OSX.dmg"
SHA256 = "8237c1c8ef0c837c47394b82959d7ea42626ad3140e452f4f59561021b428eed"
VERSION = "7.2.16r174877"
VBOX = Path("/Applications/VirtualBox.app/Contents/MacOS/VBoxManage")


def run(args, *, env=None, timeout=60):
    return subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True,
                          text=True, timeout=timeout, check=False, env=env)


def checked(result, code):
    if result.returncode != 0:
        raise capacity.PreflightError(code)
    return result.stdout


def machine_state(text):
    match = re.search(r'^VMState="([a-z]+)"$', text, re.MULTILINE)
    return match.group(1) if match else "unknown"


@contextmanager
def private_workspace(runner_temp, report):
    private = Path(tempfile.mkdtemp(prefix="glab-vbox-probe-", dir=runner_temp))
    try:
        yield private
    finally:
        # A failed detach or running VM must be left for runner disposal, never
        # recursively removed through an attached filesystem or active machine.
        safe = (not os.path.ismount(private / "mount")
                and not report.get("owned_vm_registered", False))
        if safe:
            shutil.rmtree(private)
        report["private_vm_files_removed"] = not private.exists()


def probe(runner_temp):
    report = {
        "schema_version": 1, "status": "running",
        "scope": "Default VirtualBox macOS device initialization and diskless EFI only",
        "oracle_dmg_sha256": SHA256, "expected_virtualbox_version": VERSION,
        "macos_guest_installed": False, "reboot_verified": False,
        "hardware_check_overrides": False, "host_reboot_requested": False,
        "raw_logs_exported": False, "private_vm_files_removed": False,
    }
    try:
        capacity.require(not VBOX.parent.parent.exists(), "existing_virtualbox_installation")
        with private_workspace(runner_temp, report) as private:
            mount = private / "mount"
            mount.mkdir()
            mounted = registered = False
            vm_id = str(uuid.uuid4())
            name = "glab-macos-" + vm_id
            vm_dir = private / "vms" / name
            env = {key: os.environ[key] for key in ("HOME", "USER", "LOGNAME", "TMPDIR") if key in os.environ}
            env.update(PATH="/usr/bin:/bin:/usr/sbin:/sbin", VBOX_USER_HOME=str(private / "settings"))
            try:
                report["stage"] = "download_verified_oracle_base_package"
                dmg = private / "VirtualBox.dmg"
                checked(run(["/usr/bin/curl", "--fail", "--location", "--retry", "2", "--proto", "=https",
                             "--proto-redir", "=https", "--max-time", "300", "--output", str(dmg), URL],
                            timeout=330), "oracle_download_failed")
                digest = hashlib.sha256()
                with dmg.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
                capacity.require(digest.hexdigest() == SHA256, "oracle_checksum_mismatch")
                checked(run(["/usr/bin/hdiutil", "attach", "-readonly", "-nobrowse", "-mountpoint",
                             str(mount), str(dmg)]), "oracle_media_mount_failed")
                mounted = True
                package = mount / "VirtualBox.pkg"
                signature = checked(run(["/usr/sbin/pkgutil", "--check-signature", str(package)]),
                                    "oracle_package_signature_failed")
                capacity.require("Developer ID Installer: Oracle America, Inc." in signature,
                                 "unexpected_package_signer")
                checked(run(["/usr/sbin/spctl", "--assess", "--type", "install", str(package)]),
                        "oracle_package_policy_rejected")
                report["oracle_signature_and_policy_verified"] = True
                report["stage"] = "install_oracle_base_package_on_disposable_runner"
                checked(run(["/usr/bin/sudo", "-n", "/usr/sbin/installer", "-pkg", str(package),
                             "-target", "/"], timeout=300), "oracle_package_install_failed")
                version = checked(run([str(VBOX), "--version"], env=env), "virtualbox_version_failed").strip()
                capacity.require(version == VERSION, "unexpected_virtualbox_version")
                report["virtualbox_version"] = version
                ostypes = checked(run([str(VBOX), "list", "ostypes"], env=env), "virtualbox_ostypes_failed")
                capacity.require(re.search(r'^ID:\s+MacOS_64\s*$', ostypes, re.MULTILINE),
                                 "default_macos_type_unavailable")
                report["stage"] = "create_default_macos_configuration"
                checked(run([str(VBOX), "createvm", "--name", name, "--uuid", vm_id,
                             "--platform-architecture", "x86", "--ostype", "MacOS_64", "--default",
                             "--basefolder", str(private / "vms"), "--register"], env=env),
                        "default_macos_configuration_failed")
                registered = True
                report["owned_vm_registered"] = True
                # Resource and connectivity choices only; retain default Apple hardware checks.
                checked(run([str(VBOX), "modifyvm", vm_id, "--memory", "2048", "--cpus", "2",
                             "--firmware", "efi", "--nic1", "none", "--vrde", "off"], env=env),
                        "vm_resource_configuration_failed")
                report["stage"] = "initialize_macos_devices_and_efi"
                start = run([str(VBOX), "startvm", vm_id, "--type", "headless"], env=env, timeout=90)
                report["start_exit_code"] = start.returncode
                # Only fixed error identifiers and one boolean leave the private logs.
                diagnostic = start.stderr
                log = vm_dir / "Logs/VBox.log"
                if log.is_file():
                    with log.open("r", errors="replace") as stream:
                        diagnostic += stream.read(2 * 1024 * 1024)
                report["virtualbox_error_identifiers"] = sorted(set(re.findall(
                    r'\b(?:VERR|VBOX_E|NS_ERROR)_[A-Z0-9_]{1,80}\b', diagnostic)))[:20]
                report["host_smc_query_failed"] = "Failed to query SMC value from the host" in diagnostic
                checked(start, "default_macos_vm_start_failed")
                time.sleep(5)
                info = checked(run([str(VBOX), "showvminfo", vm_id, "--machinereadable"], env=env),
                               "vm_state_inspection_failed")
                report["diskless_vm_state"] = machine_state(info)
                capacity.require(report["diskless_vm_state"] == "running", "diskless_vm_not_running")
                report["default_macos_devices_initialized"] = True
                report["status"] = "pass"
            finally:
                try:
                    if registered:
                        # UUID lives only in this fresh private VirtualBox registry.
                        info = run([str(VBOX), "showvminfo", vm_id, "--machinereadable"], env=env)
                        if machine_state(info.stdout) in {"running", "paused", "stuck", "starting"}:
                            stopped = run([str(VBOX), "controlvm", vm_id, "poweroff"], env=env)
                            report["owned_vm_powered_off"] = stopped.returncode == 0
                        removed = run([str(VBOX), "unregistervm", vm_id, "--delete"], env=env)
                        report["owned_vm_unregistered"] = removed.returncode == 0
                        report["owned_vm_registered"] = removed.returncode != 0
                        capacity.require(removed.returncode == 0, "owned_vm_cleanup_failed")
                finally:
                    if mounted:
                        detached = run(["/usr/bin/hdiutil", "detach", str(mount)])
                        report["oracle_media_detached"] = detached.returncode == 0
                        capacity.require(detached.returncode == 0, "oracle_media_detach_failed")
    except (capacity.PreflightError, OSError, subprocess.TimeoutExpired) as exc:
        report["status"] = "fail"
        report["failure"] = str(exc) if isinstance(exc, capacity.PreflightError) else type(exc).__name__
    finally:
        if "private" in locals():
            report["private_vm_files_removed"] = not private.exists()
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    runner_temp = capacity.hosted_intel_temp()
    output = (args.output or runner_temp / "macos-virtualbox-preflight.json").resolve()
    capacity.require(output.is_relative_to(runner_temp) and output != runner_temp,
                     "Evidence output must stay within RUNNER_TEMP")
    capacity.require(output.parent.is_dir() and not output.exists(), "Evidence destination must be new")
    result = probe(runner_temp)
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except capacity.PreflightError as error:
        print("macOS VirtualBox probe refused: " + str(error), file=sys.stderr)
        raise SystemExit(1) from None
