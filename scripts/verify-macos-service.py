"""Verify a fresh wheel using the existing macOS user's actual GUI LaunchAgent.

This exercises explicit process start/stop/restart, not login, logout or reboot.
It never creates users, changes launchctl settings, or falls back to root. The
new persistent test directory is removed only after the owned job is unloaded,
its plist is absent, and its port is closed. Inconclusive ownership preserves
that directory for diagnosis; a disposable CI host can then be discarded.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

ROOT_PREFIX = ".gl-agent-lab-macos-service-check-"
DATA_NAME = 'state with spaces % $ " and trailing\\ '
MARKER = "verification-owner.json"
LAUNCHCTL = "/bin/launchctl"
OUTPUT_LIMIT = 2 * 1024 * 1024


class VerificationError(RuntimeError):
    """A fixed, safe diagnostic authored by this verifier."""


def checked(condition, message):
    if not condition:
        raise VerificationError(message)


def clean_environment():
    # Keep the actual user's HOME and inherited Mach bootstrap session. An
    # artificial HOME/session would not exercise that user's LaunchAgent.
    import pwd

    account = pwd.getpwuid(os.getuid())
    checked(Path.home().resolve() == Path(account.pw_dir).resolve(),
            "HOME does not identify the current user's real home directory")
    allowed = ("HOME", "USER", "LOGNAME", "PATH", "TMPDIR", "LANG", "LC_ALL",
               "__CF_USER_TEXT_ENCODING")
    env = {key: os.environ[key] for key in allowed if key in os.environ}
    env.update(HOME=str(Path(account.pw_dir)), USER=account.pw_name, LOGNAME=account.pw_name,
               PYTHONUTF8="1", PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1",
               PIP_CONFIG_FILE=os.devnull)
    env.setdefault("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")
    return env


def command(args, *, stage, cwd, env, timeout=60, expected=0, capture=True, quiet=False):
    if not quiet:
        print(f"macOS service verification: {stage}", file=sys.stderr, flush=True)
    try:
        process = subprocess.Popen(
            args, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
            stderr=subprocess.PIPE if capture else subprocess.DEVNULL,
            # Descendants of a timed-out installer/probe belong to this group.
            # A launchd-managed job is separate and requires guarded CLI cleanup.
            start_new_session=True,
        )
    except OSError as exc:
        raise VerificationError(f"{stage}: OS error {exc.errno}") from None
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except (subprocess.TimeoutExpired, KeyboardInterrupt):
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.communicate(timeout=5)
        raise VerificationError(f"{stage}: timeout or interruption") from None
    stdout, stderr = stdout or b"", stderr or b""
    checked(len(stdout) + len(stderr) <= OUTPUT_LIMIT, f"{stage}: output limit exceeded")
    if expected is not None and process.returncode != expected:
        detail = ""
        if stage.startswith("CLI service"):
            for phrase, category in (
                (b"GUI login session is required", "gui_session_unavailable"),
                (b"LaunchAgent manager rejected", "manager_operation_rejected"),
                (b"loaded LaunchAgent command does not match", "loaded_definition_mismatch"),
                (b"not exactly owned", "definition_ownership_mismatch"),
                (b"loopback service port is in use", "port_in_use"),
            ):
                if phrase in stderr:
                    detail = ": " + category
                    break
            try:
                state = json.loads(stdout)
                flags = {key: state[key] for key in ("installed", "running", "ready", "enabled")
                         if type(state.get(key)) is bool}
                if flags:
                    detail += ": " + json.dumps(flags, sort_keys=True)
            except (ValueError, AttributeError, TypeError):
                pass
        raise VerificationError(f"{stage}: exit {process.returncode}{detail}")
    return process.returncode, stdout, stderr


def gui_preflight(root, env):
    # Never capture or print the GUI domain tree: it includes unrelated jobs.
    command([LAUNCHCTL, "print", f"gui/{os.getuid()}"], stage="existing GUI session preflight",
            cwd=root, env=env, timeout=15, capture=False)


def owned_root(root):
    home = Path.home().resolve()
    checked(not root.is_symlink() and root.resolve().parent == home
            and re.fullmatch(re.escape(ROOT_PREFIX) + r"[0-9a-f]{32}", root.name),
            "Verification root is not the expected direct child of HOME")
    info = root.stat()
    checked(info.st_uid == os.getuid() and stat.S_ISDIR(info.st_mode)
            and stat.S_IMODE(info.st_mode) == 0o700,
            "Verification root ownership or permissions changed")
    marker = root / MARKER
    checked(not marker.is_symlink() and marker.is_file() and marker.stat().st_size <= 4096,
            "Verification ownership marker is missing or invalid")
    value = json.loads(marker.read_text(encoding="utf-8"))
    checked(value.get("schema") == 1 and value.get("uid") == os.getuid()
            and value.get("root") == root.name and type(value.get("port")) is int
            and 1024 <= value["port"] <= 65535, "Verification ownership marker does not match")
    return value


def identity(root):
    data = root / DATA_NAME
    checked(not data.is_symlink() and data.resolve().parent == root.resolve(),
            "Verification data path changed")
    owner = hashlib.sha256(("darwin\0" + str(os.getuid()) + "\0" + str(data.resolve()))
                           .encode()).hexdigest()[:24]
    name = "genlayer-agent-lab-" + owner
    definition = Path.home() / "Library/LaunchAgents" / (name + ".plist")
    return data, owner, name, definition


def observe_job(root, env):
    _, _, name, _ = identity(root)
    code, stdout, stderr = command(
        [LAUNCHCTL, "print", f"gui/{os.getuid()}/{name}"], stage="owned LaunchAgent inspection",
        cwd=root, env=env, timeout=15, expected=None, quiet=True,
    )
    if code == 0:
        return True, stdout.decode("utf-8", errors="replace")
    gui_preflight(root, env)
    checked(b"Could not find service" in stderr and name.encode() in stderr,
            "Owned LaunchAgent absence could not be confirmed")
    return False, ""


def port_closed(port, *, timeout=10):
    deadline = time.monotonic() + timeout
    while True:
        with socket.socket() as connection:
            connection.settimeout(1)
            if connection.connect_ex(("127.0.0.1", port)) != 0:
                return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.1)


def cli(root, env, *args, timeout=60, expected=0):
    data, _, _, _ = identity(root)
    console = root / "environment/bin/gl-agent-lab"
    _, stdout, _ = command([str(console), "--data-dir", str(data), *args],
                           stage="CLI " + " ".join(args[:2]), cwd=root, env=env,
                           timeout=timeout, expected=expected)
    return json.loads(stdout)


def cleanup(root, env):
    marker = owned_root(root)
    _, _, _, definition = identity(root)
    console = root / "environment/bin/gl-agent-lab"
    if console.is_file():
        removed = cli(root, env, "service", "uninstall")
        checked(removed.get("uninstalled") is True, "Owned LaunchAgent uninstall did not succeed")
    exists, _ = observe_job(root, env)
    checked(not exists and not definition.exists() and not definition.is_symlink(),
            "Owned LaunchAgent registration remains; verification directory preserved")
    checked(port_closed(marker["port"]),
            "Owned service port remains open; verification directory preserved")
    return True


def diagnostics(root, env):
    """Counts, lengths and equality only; no job arguments, paths, logs or tokens."""
    exists, raw = observe_job(root, env)
    result = {"loaded": exists}
    if not exists:
        return result
    data, owner, _, definition = identity(root)
    expected = [str(root / "environment/bin/python"), "-I", "-m",
                "genlayer_agent_lab.service", "run", "--data-dir", str(data.resolve()),
                "--port", str(owned_root(root)["port"]), "--owner", owner]
    for key, target in (("path", str(definition)), ("program", expected[0])):
        match = re.search(r"^\s*" + key + r" = (.+)$", raw, re.M)
        observed = match[1] if match else None
        result[key + "_matches"] = observed == target
        result[key + "_length"] = len(observed) if observed is not None else None
    arguments = re.search(r"^\s*arguments = \{\n(.*?)^\s*\}", raw, re.M | re.S)
    lines = arguments[1].splitlines() if arguments else []
    checked(len(lines) <= 64, "Owned LaunchAgent diagnostic argument limit exceeded")
    stripped = [line.strip() for line in lines]
    left_stripped = [line.lstrip() for line in lines]
    result.update(expected_argument_count=len(expected), observed_argument_count=len(lines),
                  expected_argument_lengths=[len(value) for value in expected],
                  stripped_argument_lengths=[len(value) for value in stripped],
                  left_stripped_argument_lengths=[len(value) for value in left_stripped],
                  leading_tab_counts=[len(line) - len(line.lstrip("\t")) for line in lines],
                  leading_whitespace_counts=[len(line) - len(line.lstrip()) for line in lines],
                  trailing_space_counts=[len(line) - len(line.rstrip(" ")) for line in lines],
                  stripped_arguments_match=stripped == expected,
                  left_stripped_arguments_match=left_stripped == expected)
    for key in ("pid", "runs", "last exit code"):
        match = re.search(r"^\s*" + re.escape(key) + r" = (-?\d{1,10})\s*$", raw, re.M)
        if match:
            result[key.replace(" ", "_")] = int(match[1])
    match = re.search(r"^\s*state = (.+)$", raw, re.M)
    if match and match[1] in {"running", "waiting", "exited", "not running", "spawn scheduled"}:
        result["state"] = match[1]
    return result


def probe(root):
    import genlayer_agent_lab
    from genlayer_agent_lab.client import LabClient

    env = clean_environment()
    marker = owned_root(root)
    data, _, name, definition = identity(root)
    port = marker["port"]
    url = f"http://127.0.0.1:{port}"
    result = {"verification": "fail", "service_cleanup": False}

    def run_agent(admin):
        created = admin.create_run("escrow-normal", agent="safe", backend="glsim")
        run_id = created["run_id"]
        checked(type(run_id) is str and re.fullmatch(r"[0-9a-f]{32}", run_id),
                "Managed HTTP run returned an invalid identity")
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

    try:
        checked(sys.prefix != sys.base_prefix
                and Path(sys.prefix).resolve() == (root / "environment").resolve()
                and Path(genlayer_agent_lab.__file__).resolve().is_relative_to(Path(sys.prefix)),
                "Imported package is outside the fresh environment")
        checked(not any(os.getenv(key) for key in ("PYTHONPATH", "VIRTUAL_ENV", "LAB_TOKEN")),
                "Probe inherited application or source configuration")
        gui_preflight(root, env)
        exists, _ = observe_job(root, env)
        checked(not exists and not definition.exists() and not definition.is_symlink(),
                "Verification service identity already exists")
        initialized = cli(root, env, "init")
        checked(initialized.get("initialized") is True and "admin_token" not in initialized,
                "CLI initialization failed or exposed a credential")
        doctor = cli(root, env, "doctor", timeout=960)
        runtime = doctor.get("runtime", {})
        checked(runtime.get("ready") is True
                and runtime.get("provenance", {}).get("execution_success") is True,
                "Installed GLSim doctor failed")
        installed = cli(root, env, "service", "install", "--port", str(port), "--start")
        checked(all(installed.get(key) is True for key in ("installed", "enabled", "running", "ready"))
                and installed.get("name") == name and installed.get("definition") == str(definition),
                "LaunchAgent was not owned, loaded and ready")
        checked(observe_job(root, env)[0], "Started LaunchAgent is not registered")
        token = (data / "admin.token").read_text(encoding="utf-8").strip()
        token_hash = hashlib.sha256((data / "admin.token").read_bytes()).hexdigest()
        with LabClient(url, token, timeout=10) as admin:
            first_id, first_report = run_agent(admin)
        stopped = cli(root, env, "service", "stop")
        checked(stopped.get("stopped") is True and stopped.get("running") is False,
                "LaunchAgent did not stop")
        checked(not observe_job(root, env)[0], "Stopped LaunchAgent remained loaded")
        checked(definition.is_file(), "Stopping removed the restartable LaunchAgent definition")
        checked(port_closed(port), "Owned service port remained open after stopping")
        restarted = cli(root, env, "service", "start")
        checked(restarted.get("ready") is True and observe_job(root, env)[0],
                "LaunchAgent was not loaded and ready after restarting")
        with LabClient(url, token, timeout=10) as admin:
            checked(admin.report(first_id) == first_report,
                    "Frozen report changed across LaunchAgent restart")
            second_id, _ = run_agent(admin)
            checked({first_id, second_id} <= {item["run_id"] for item in admin.list_runs()},
                    "LaunchAgent lost persisted run history")
        removed = cli(root, env, "service", "uninstall")
        checked(removed.get("uninstalled") is True and removed.get("data_preserved") is True,
                "LaunchAgent uninstall failed")
        checked(cli(root, env, "service", "status", expected=2).get("installed") is False,
                "CLI reports a registration after uninstall")
        checked(not observe_job(root, env)[0] and not definition.exists(),
                "LaunchAgent registration or definition survived uninstall")
        checked(port_closed(port), "Owned service port remained open after uninstall")
        checked((data / "lab.sqlite3").is_file()
                and hashlib.sha256((data / "admin.token").read_bytes()).hexdigest() == token_hash,
                "Uninstall changed persistent data or the admin credential")
        checked((data / "service/service.log").is_file(), "Uninstall removed the service log")
        result.update(
            verification="pass", package_version=importlib.metadata.version("genlayer-agent-lab"),
            imported_from_fresh_venv=True, manager="launchctl GUI LaunchAgent",
            existing_gui_session=True, installed=True, enabled=True, started=True, stopped=True,
            restarted=True, reports_preserved=True, history_preserved=True, token_preserved=True,
            uninstalled=True, data_preserved=True, log_preserved=True, port_closed=True,
            glsim_http_runs=[first_id, second_id], agent_kind="bundled scripted safe reference",
            doctor={"execution_success": True, "runner_hash": runtime["runner_hash"],
                    "bundle_sha256": runtime["bundle_sha256"]},
        )
    except Exception as exc:
        result["error"] = str(exc) if isinstance(exc, VerificationError) else type(exc).__name__
        try:
            result["owned_job_diagnostics"] = diagnostics(root, env)
        except Exception:
            result["service_diagnostics_unavailable"] = True
    finally:
        try:
            result["service_cleanup"] = cleanup(root, env)
        except Exception:
            result.update(verification="fail", cleanup_error=
                          "Guarded uninstall or unloaded-state check failed; owned directory preserved")
    return result


def verify(wheel):
    started = time.monotonic()
    result = {
        "schema_version": 1, "verification": "fail", "platform": platform.system(),
        "host_python": platform.python_version(), "wheel": wheel.name,
        "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
        "source_checkout_used_by_probe": False, "inherited_environment": "current-user allowlist only",
        "limits": {"host_reboot_tested": False, "login_tested": False, "logout_tested": False,
                   "automatic_failure_restart_tested": False, "studio_tested": False,
                   "external_human_onboarding_tested": False},
        "owned_directory_removed": False,
    }
    root = None
    root_created = False
    env = clean_environment()
    try:
        gui_preflight(Path.home(), env)
        root = Path.home().resolve() / (ROOT_PREFIX + uuid.uuid4().hex)
        checked(not root.is_relative_to(Path(tempfile.gettempdir()).resolve()),
                "The real user's home is a temporary location")
        root.mkdir(mode=0o700)
        root_created = True
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        marker = {"schema": 1, "uid": os.getuid(), "root": root.name, "port": port}
        with (root / MARKER).open("x", encoding="utf-8") as output:
            json.dump(marker, output)
        owned_root(root)
        environment = root / "environment"
        command([sys.executable, "-I", "-m", "venv", str(environment)],
                stage="create persistent-path virtual environment", cwd=root, env=env)
        python = environment / "bin/python"
        command([str(python), "-I", "-m", "pip", "--isolated", "install", "--no-cache-dir",
                 "--index-url", "https://pypi.org/simple", str(wheel)],
                stage="install actual wheel", cwd=root, env=env, timeout=600, capture=False)
        copied = root / "probe.py"
        shutil.copyfile(Path(__file__).resolve(), copied)
        _, raw, _ = command([str(python), "-I", "-B", str(copied), "--_probe", str(root)],
                            stage="installed LaunchAgent lifecycle", cwd=root, env=env, timeout=1800)
        installed = json.loads(raw)
        result["installed"] = installed
        result["verification"] = installed["verification"]
    except Exception as exc:
        result["error"] = str(exc) if isinstance(exc, VerificationError) else type(exc).__name__
    finally:
        if root_created and root is not None and root.is_dir():
            try:
                # Also runs if installation/probe timed out. Never stop a job
                # directly or bypass the installed CLI's ownership validation.
                cleanup(root, env)
                owned_root(root)
                checked(root.resolve().parent == Path.home().resolve() and not root.is_symlink(),
                        "Verification cleanup target changed")
                shutil.rmtree(root)
                result["owned_directory_removed"] = True
            except Exception:
                result.update(verification="fail", cleanup_error=
                              "Guarded cleanup failed; owned directory and registration preserved")
    result["elapsed_seconds"] = round(time.monotonic() - started, 3)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path, help="Actual genlayer_agent_lab wheel to install")
    parser.add_argument("--output", type=Path, help="Sanitized JSON verification evidence")
    parser.add_argument("--_probe", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if platform.system() != "Darwin" or os.getuid() == 0:
        parser.error("An existing nonroot macOS user with a GUI login session is required")
    try:
        if args._probe:
            result = probe(args._probe)
        else:
            if (not args.wheel or not args.wheel.is_file()
                    or not re.fullmatch(r"genlayer_agent_lab-[A-Za-z0-9_.+-]+\.whl", args.wheel.name)):
                parser.error("--wheel must identify an existing genlayer_agent_lab wheel")
            result = verify(args.wheel.resolve())
    except Exception as exc:
        result = {"verification": "fail", "error": str(exc) if isinstance(exc, VerificationError)
                  else type(exc).__name__}
    text = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")
    # The private probe returns structured failures so its driver can clean up.
    return 0 if args._probe or result["verification"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
