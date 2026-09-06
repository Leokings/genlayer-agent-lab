"""Verify an installed wheel through an actual Linux systemd user service.

Run as a dedicated, disposable regular user with an available user manager.
This verifies process startup/restart, not a VM reboot, logout or Studio startup.
Only sanitized evidence is emitted; credentials and service logs stay private.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path


class VerificationError(RuntimeError):
    pass


def command(args, *, stage, cwd, timeout=60, expected=0):
    print(f"Linux service verification: {stage}", file=sys.stderr, flush=True)
    try:
        result = subprocess.run(args, cwd=cwd, capture_output=True, timeout=timeout,
                                stdin=subprocess.DEVNULL, check=False)
    except subprocess.TimeoutExpired:
        raise VerificationError(f"{stage}: timeout") from None
    except OSError as exc:
        raise VerificationError(f"{stage}: OS error {exc.errno}") from None
    if result.returncode != expected:
        # CLI errors can contain third-party payloads. Never publish those here.
        raise VerificationError(f"{stage}: exit {result.returncode}")
    return result.stdout


def checked(condition, message):
    if not condition:
        raise VerificationError(message)


def probe(root):
    import genlayer_agent_lab
    from genlayer_agent_lab.client import LabClient

    result = {"verification": "fail", "service_cleanup": False}
    data = root / "state"
    console = Path(sys.prefix) / "bin/gl-agent-lab"
    checked(Path(genlayer_agent_lab.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()),
            "Imported package is outside the fresh environment")

    def cli(*args, timeout=60, expected=0):
        return json.loads(command([str(console), "--data-dir", str(data), *args],
                                  stage="CLI " + " ".join(args[:2]), cwd=root,
                                  timeout=timeout, expected=expected))

    def run_agent(admin):
        created = admin.create_run("escrow-normal", agent="safe", backend="glsim")
        run_id = created["run_id"]
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            if admin.get_run(run_id)["status"] in {"completed", "failed", "cancelled", "interrupted"}:
                break
            time.sleep(0.25)
        report = admin.report(run_id)
        checked(report.get("status") == "completed" and report.get("verdict") == "pass",
                "Managed HTTP run did not complete successfully")
        checked(all(report.get("grades", {}).get(name, {}).get("status") == "pass"
                    for name in ("decision", "behavior", "outcome", "completion")),
                "Managed HTTP run did not pass all four grades")
        runtime = report.get("manifest", {}).get("runtime", {})
        checked(report.get("manifest", {}).get("backend") == "glsim"
                and runtime.get("execution_success") is True,
                "Managed HTTP run lacks actual GLSim execution evidence")
        return run_id, report

    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    try:
        command(["systemctl", "--user", "show-environment"], stage="user manager preflight", cwd=root)
        cli("init")
        doctor = cli("doctor", timeout=960)
        runtime = doctor.get("runtime", {})
        checked(runtime.get("ready") is True
                and runtime.get("provenance", {}).get("execution_success") is True,
                "Cold installed GLSim doctor failed")
        installed = cli("service", "install", "--port", str(port), "--start")
        checked(all(installed.get(key) is True for key in ("installed", "enabled", "running", "ready")),
                "User service was not enabled and ready")
        token = (data / "admin.token").read_text(encoding="utf-8").strip()
        token_hash = hashlib.sha256((data / "admin.token").read_bytes()).hexdigest()
        with LabClient(url, token, timeout=10) as admin:
            first_id, first_report = run_agent(admin)
        stopped = cli("service", "stop")
        checked(stopped.get("stopped") is True and stopped.get("running") is False,
                "User service did not stop")
        with socket.socket() as connection:
            connection.settimeout(2)
            checked(connection.connect_ex(("127.0.0.1", port)) != 0,
                    "Owned service port remained open after stopping")
        restarted = cli("service", "start")
        checked(restarted.get("ready") is True, "User service was not ready after restarting")
        with LabClient(url, token, timeout=10) as admin:
            checked(admin.report(first_id) == first_report,
                    "Frozen report changed across service restart")
            second_id, _ = run_agent(admin)
            checked({first_id, second_id} <= {item["run_id"] for item in admin.list_runs()},
                    "Managed service lost persisted run history")
        removed = cli("service", "uninstall")
        checked(removed.get("uninstalled") is True and removed.get("data_preserved") is True,
                "Service uninstall failed")
        checked(cli("service", "status", expected=2).get("installed") is False,
                "Service registration survived uninstall")
        checked(not Path(installed["definition"]).exists(), "User service definition survived uninstall")
        with socket.socket() as connection:
            connection.settimeout(2)
            checked(connection.connect_ex(("127.0.0.1", port)) != 0,
                    "Owned service port remained open after uninstall")
        checked((data / "lab.sqlite3").is_file()
                and hashlib.sha256((data / "admin.token").read_bytes()).hexdigest() == token_hash,
                "Uninstall changed persistent data or the admin credential")
        checked((data / "service/service.log").is_file(), "Uninstall removed the service log")
        result.update(verification="pass", package_version=importlib.metadata.version("genlayer-agent-lab"),
                      imported_from_fresh_venv=True, manager="systemd --user",
                      installed=True, enabled=True, started=True, stopped=True, restarted=True,
                      reports_preserved=True, uninstalled=True, data_preserved=True,
                      service_cleanup=True, glsim_http_runs=[first_id, second_id],
                      doctor={"execution_success": True, "runner_hash": runtime["runner_hash"],
                              "bundle_sha256": runtime["bundle_sha256"]})
    except Exception as exc:
        result["error"] = str(exc) if isinstance(exc, VerificationError) else type(exc).__name__
    finally:
        if not result["service_cleanup"]:
            try:
                removed = cli("service", "uninstall")
                result["service_cleanup"] = removed.get("uninstalled") is True
            except Exception:
                result["cleanup_error"] = "Owned service uninstall failed; disposable user cleanup required"
    return result


def verify(wheel):
    started = time.monotonic()
    result = {"schema_version": 1, "verification": "fail", "platform": platform.system(),
              "host_python": platform.python_version(), "wheel": wheel.name,
              "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
              "limits": {"host_reboot_tested": False, "logout_tested": False,
                         "studio_tested": False, "external_human_onboarding_tested": False}}
    root = Path.home() / ".local/share" / ("gl-agent-lab-service-check-" + uuid.uuid4().hex)
    root.mkdir(parents=True, mode=0o700)
    try:
        environment = root / "environment"
        command([sys.executable, "-I", "-m", "venv", str(environment)],
                stage="create persistent-path virtual environment", cwd=root)
        python = environment / "bin/python"
        command([str(python), "-I", "-m", "pip", "--isolated", "install", "--no-cache-dir",
                 "--index-url", "https://pypi.org/simple", str(wheel)],
                stage="install actual wheel", cwd=root, timeout=600)
        copied = root / "probe.py"
        shutil.copyfile(Path(__file__).resolve(), copied)
        raw = command([str(python), "-I", "-B", str(copied), "--_probe", str(root)],
                      stage="installed user-service lifecycle", cwd=root, timeout=1800)
        installed = json.loads(raw)
        result["installed"] = installed
        result["verification"] = installed["verification"]
        if installed.get("service_cleanup"):
            # An exclusive new directory owned by this verifier, underneath HOME.
            checked(root.resolve().parent == (Path.home() / ".local/share").resolve(),
                    "Verification cleanup path changed")
            shutil.rmtree(root)
    except Exception as exc:
        result["verification"] = "fail"
        result["error"] = str(exc) if isinstance(exc, VerificationError) else type(exc).__name__
    result["elapsed_seconds"] = round(time.monotonic() - started, 3)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--_probe", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if platform.system() != "Linux" or os.getuid() == 0:
        parser.error("A dedicated regular Linux user is required")
    if args._probe:
        result = probe(args._probe)
    else:
        if not args.wheel or not args.wheel.is_file() or args.wheel.suffix != ".whl":
            parser.error("--wheel must identify an existing wheel")
        result = verify(args.wheel.resolve())
    text = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")
    # The private probe returns structured failure evidence to its driver.
    return 0 if args._probe or result["verification"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
