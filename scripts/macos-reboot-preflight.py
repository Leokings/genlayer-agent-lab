"""Measure an Intel GitHub Mac runner and create/destroy an empty VM.

No operating system is installed or booted, and no machine is rebooted.
The only created files are a small compiled probe and bounded JSON evidence
under the disposable hosted runner's own temporary directory.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

GIB = 1024 ** 3
PROBE = r'''
#include <Hypervisor/hv.h>
#include <stdio.h>
#if !defined(__x86_64__)
#error This probe is only for Intel macOS.
#endif
int main(void) {
    hv_return_t created = hv_vm_create(HV_VM_DEFAULT);
    if (created != HV_SUCCESS) {
        printf("{\"empty_vm_created\":false,\"create_result\":\"0x%08x\"}\n",
               (unsigned)created);
        return 1;
    }
    hv_return_t destroyed = hv_vm_destroy();
    printf("{\"empty_vm_created\":true,\"create_result\":\"0x%08x\","
           "\"empty_vm_destroyed\":%s,\"destroy_result\":\"0x%08x\"}\n",
           (unsigned)created, destroyed == HV_SUCCESS ? "true" : "false", (unsigned)destroyed);
    return destroyed == HV_SUCCESS ? 0 : 1;
}
'''


class PreflightError(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise PreflightError(message)


def hosted_intel_temp():
    require(platform.system() == "Darwin", "macOS is required")
    require(platform.machine() == "x86_64", "Intel macOS is required")
    for key, value in (("GITHUB_ACTIONS", "true"),
                       ("RUNNER_ENVIRONMENT", "github-hosted"),
                       ("RUNNER_OS", "macOS")):
        require(os.environ.get(key) == value, "Required hosted-runner guard: " + key)
    require(os.geteuid() != 0, "Run as the regular runner user, not root")
    raw = os.environ.get("RUNNER_TEMP", "")
    require(bool(raw), "RUNNER_TEMP is required")
    supplied = Path(raw)
    require(supplied.is_absolute() and supplied.is_dir(), "RUNNER_TEMP must be an existing absolute directory")
    resolved = supplied.resolve(strict=True)
    require(resolved != Path("/"), "RUNNER_TEMP cannot be the filesystem root")
    require(os.access(resolved, os.W_OK | os.X_OK), "RUNNER_TEMP must be writable")
    return resolved


def run(args, *, timeout=30):
    return subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True,
                          text=True, timeout=timeout, check=False)


def inspect_value(args):
    result = run(args)
    return result.stdout.strip()[:200] if result.returncode == 0 else None


def inspect(runner_temp):
    usage = shutil.disk_usage(runner_temp)
    report = {
        "schema_version": 1,
        "scope": "Intel GitHub macOS capacity and empty Hypervisor VM only",
        "status": "running", "architecture": platform.machine(),
        "cpu_count": os.cpu_count(),
        "disk_bytes": {"total": usage.total, "used": usage.used, "free": usage.free},
        "free_disk_gib": round(usage.free / GIB, 2),
        "macos_guest_installed": False, "guest_os_booted": False,
        "reboot_verified": False, "host_reboot_requested": False,
        "private_probe_files_removed": False,
    }
    try:
        report["os_version"] = inspect_value(["/usr/bin/sw_vers", "-productVersion"])
        report["os_build"] = inspect_value(["/usr/bin/sw_vers", "-buildVersion"])
        report["hardware_model"] = inspect_value(["/usr/sbin/sysctl", "-n", "hw.model"])
        report["cpu_model"] = inspect_value(["/usr/sbin/sysctl", "-n", "machdep.cpu.brand_string"])
        memory = inspect_value(["/usr/sbin/sysctl", "-n", "hw.memsize"])
        report["physical_memory_bytes"] = int(memory) if memory and memory.isdigit() else None
        report["hypervisor_support_sysctl"] = inspect_value(["/usr/sbin/sysctl", "-n", "kern.hv_support"])
        report["outer_virtual_machine_sysctl"] = inspect_value(["/usr/sbin/sysctl", "-n", "kern.hv_vmm_present"])
        # Presence only; never output SMC data or machine serial numbers.
        smc = run(["/usr/sbin/ioreg", "-r", "-c", "AppleSMC", "-d", "0"])
        report["apple_smc_class_present"] = "<class AppleSMC," in smc.stdout if smc.returncode == 0 else None
        report["planning_headroom"] = {
            "free_disk_at_least_60_gib": usage.free >= 60 * GIB,
            "memory_at_least_10_gib": (report["physical_memory_bytes"] or 0) >= 10 * GIB,
            "note": "Planning estimates only; guest installation requirements depend on the selected hypervisor.",
        }
        for tool in ("/usr/bin/clang", "/usr/bin/codesign"):
            require(Path(tool).is_file(), "Required build tool missing")
        with tempfile.TemporaryDirectory(prefix="glab-macos-probe-", dir=runner_temp) as temporary:
            private = Path(temporary)
            source, binary = private / "probe.c", private / "probe"
            entitlement = private / "entitlements.plist"
            source.write_text(PROBE, encoding="utf-8")
            entitlement.write_bytes(plistlib.dumps({"com.apple.security.hypervisor": True}))
            built = run(["/usr/bin/clang", "-arch", "x86_64", "-mmacosx-version-min=11.0",
                         "-framework", "Hypervisor", str(source), "-o", str(binary)], timeout=120)
            require(built.returncode == 0, "Hypervisor probe compilation failed")
            signed = run(["/usr/bin/codesign", "--force", "--sign", "-", "--entitlements",
                          str(entitlement), str(binary)])
            require(signed.returncode == 0, "Hypervisor probe local signing failed")
            result = run([str(binary)], timeout=15)
            require(len(result.stdout) <= 4096, "Probe output exceeded limit")
            try:
                report["hypervisor"] = json.loads(result.stdout)
            except ValueError:
                raise PreflightError("Hypervisor probe did not return JSON") from None
            require(result.returncode == 0
                    and report["hypervisor"].get("empty_vm_created") is True
                    and report["hypervisor"].get("empty_vm_destroyed") is True,
                    "Empty Hypervisor VM creation or destruction failed")
        report["status"] = "pass"
    except (PreflightError, OSError, subprocess.TimeoutExpired) as exc:
        report["status"] = "fail"
        report["failure"] = str(exc) if isinstance(exc, PreflightError) else type(exc).__name__
    finally:
        if "private" in locals():
            report["private_probe_files_removed"] = not private.exists()
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--list-installers", action="store_true",
                        help="Also query Apple's available full-installer versions; download nothing")
    args = parser.parse_args(argv)
    runner_temp = hosted_intel_temp()  # Before files, compilation or Hypervisor operations.
    output = (args.output or runner_temp / "macos-reboot-preflight.json").resolve()
    require(output.is_relative_to(runner_temp) and output != runner_temp,
            "Evidence output must stay within RUNNER_TEMP")
    require(output.parent.is_dir() and not output.exists(), "Evidence destination must be new")
    report = inspect(runner_temp)
    if args.list_installers and report["status"] == "pass":
        try:
            catalog = run(["/usr/sbin/softwareupdate", "--list-full-installers"], timeout=180)
            require(catalog.returncode == 0, "Apple installer catalog query failed")
            require(len(catalog.stdout) <= 65536, "Apple installer catalog exceeded output limit")
            versions = sorted(set(re.findall(r"\bVersion:\s*(\d+(?:\.\d+){1,3})\s*,", catalog.stdout)))
            report["apple_installer_catalog"] = {
                "status": "pass", "versions": versions,
                "catalina_available": any(version.startswith("10.15") for version in versions),
                "download_requested": False,
            }
        except (PreflightError, OSError, subprocess.TimeoutExpired) as exc:
            report["apple_installer_catalog"] = {
                "status": "fail", "failure": str(exc) if isinstance(exc, PreflightError) else type(exc).__name__,
                "download_requested": False,
            }
            report["status"] = "fail"
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PreflightError as error:
        print("macOS guest preflight refused: " + str(error), file=sys.stderr)
        raise SystemExit(1) from None
