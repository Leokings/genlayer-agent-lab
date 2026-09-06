"""Inspect a disposable GitHub-hosted Linux runner for a Windows guest trial.

This does not download Windows, boot a guest, reboot a machine, or delete SDKs.
The KVM check creates and immediately closes an empty VM as the regular user.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
from pathlib import Path


class PreflightError(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise PreflightError(message)


def hosted_linux_temp():
    require(platform.system() == "Linux", "Linux is required")
    for key, value in (("GITHUB_ACTIONS", "true"),
                       ("RUNNER_ENVIRONMENT", "github-hosted"),
                       ("RUNNER_OS", "Linux")):
        require(os.environ.get(key) == value, "Required hosted-runner guard: " + key)
    require(os.geteuid() != 0, "Run as the regular runner user, not root")
    raw_temp = os.environ.get("RUNNER_TEMP", "")
    require(bool(raw_temp), "RUNNER_TEMP is required")
    supplied = Path(raw_temp)
    require(supplied.is_absolute() and supplied.is_dir(), "RUNNER_TEMP must be an existing absolute directory")
    runner_temp = supplied.resolve(strict=True)
    require(runner_temp != Path("/"), "RUNNER_TEMP cannot be the filesystem root")
    require(os.access(runner_temp, os.W_OK | os.X_OK), "RUNNER_TEMP must be writable")
    return runner_temp


def run(args, timeout=30):
    result = subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True,
                            text=True, timeout=timeout, check=False)
    require(result.returncode == 0, "Inspection command failed: " + args[0])
    return result.stdout


def kvm_probe():
    import fcntl

    require(stat.S_ISCHR(Path("/dev/kvm").stat().st_mode), "/dev/kvm must be a character device")
    fd = os.open("/dev/kvm", os.O_RDWR | os.O_CLOEXEC)
    try:
        api = fcntl.ioctl(fd, 0xAE00, 0)  # KVM_GET_API_VERSION
        require(api == 12, "Unexpected KVM API version")
        vm_fd = fcntl.ioctl(fd, 0xAE01, 0)  # KVM_CREATE_VM; no vCPU or guest RAM
        os.close(vm_fd)
        return {"api_version": api, "empty_vm_created": True,
                "effective_uid": os.geteuid(), "supplementary_group_ids": os.getgroups()}
    finally:
        os.close(fd)


def inspect(runner_temp):
    usage = shutil.disk_usage(runner_temp)
    report = {
        "scope": "GitHub-hosted Linux capacity and KVM preflight; no Windows boot or reboot coverage",
        "status": "running",
        "architecture": platform.machine(),
        "cpu_count": os.cpu_count(),
        "runner_temp_disk_bytes": {"total": usage.total, "used": usage.used, "free": usage.free},
        "memory": [line for line in Path("/proc/meminfo").read_text().splitlines()
                   if line.startswith(("MemTotal:", "MemAvailable:"))],
    }
    try:
        report["tools"] = {}
        for tool in ("qemu-system-x86_64", "qemu-img", "swtpm", "xorriso"):
            resolved = shutil.which(tool)
            require(resolved is not None, "Required tool missing: " + tool)
            report["tools"][tool] = resolved
        report["qemu_version"] = run(["qemu-system-x86_64", "--version"]).splitlines()[0]
        report["firmware_package_paths"] = [
            line for line in run(["dpkg-query", "-L", "ovmf"]).splitlines()
            if line.startswith("/") and Path(line).is_file()
            and (line.endswith(".fd") or line.endswith(".json"))
        ]
        require(bool(report["firmware_package_paths"]), "No OVMF firmware paths found")
        report["preinstalled_sdk_disk_bytes"] = {}
        for candidate in ("/usr/share/dotnet", "/usr/local/lib/android", "/opt/ghc", "/opt/hostedtoolcache"):
            if Path(candidate).is_dir():
                try:
                    size = int(run(["du", "--summarize", "--block-size=1", candidate], timeout=90).split()[0])
                    report["preinstalled_sdk_disk_bytes"][candidate] = size
                except (PreflightError, OSError, ValueError, subprocess.TimeoutExpired):
                    report["preinstalled_sdk_disk_bytes"][candidate] = "unavailable"
        report["kvm"] = kvm_probe()
        report["status"] = "pass"
    except (PreflightError, OSError, subprocess.TimeoutExpired) as exc:
        report["status"] = "fail"
        report["failure"] = str(exc) if isinstance(exc, PreflightError) else type(exc).__name__
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-environment", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    runner_temp = hosted_linux_temp()
    if args.check_environment:
        print("Disposable hosted Linux environment guard passed")
        return 0
    output = (args.output or runner_temp / "windows-reboot-preflight.json").resolve()
    require(output.is_relative_to(runner_temp) and output != runner_temp,
            "Evidence output must stay within RUNNER_TEMP")
    require(output.parent.is_dir(), "Evidence parent directory must exist")
    report = inspect(runner_temp)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PreflightError as error:
        print("Windows guest preflight refused: " + str(error), file=sys.stderr)
        raise SystemExit(1) from None
