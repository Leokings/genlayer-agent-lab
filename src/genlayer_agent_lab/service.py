"""User-session startup integration; never installs an administrator/system daemon.

Windows Task Scheduler, systemd --user, and LaunchAgents execute a fixed Python
module with explicit arguments. No shell command or token is stored in a service
definition. All manager operations check the actual owned definition first.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import logging
import os
import platform
import plistlib
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path

import httpx

from . import __version__
from .runtime.container import _bounded_process

NS = "http://schemas.microsoft.com/windows/2004/02/mit/task"
ET.register_namespace("", NS)
MANIFEST_NAME = "installation.json"
MAX_DEFINITION = 131072


class ServiceError(RuntimeError):
    """Safe, actionable service-manager failure with no raw command output."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def _fail(code, message):
    raise ServiceError(code, message)


def _absolute(path) -> Path:
    # Do not resolve interpreter symlinks: a venv's Python symlink must retain its
    # lexical venv path, otherwise startup may use the base interpreter instead.
    value = os.path.abspath(os.path.expanduser(os.fspath(path)))
    if any(ord(char) < 32 for char in value):
        _fail("invalid_path", "Service paths cannot contain control characters")
    return Path(value)


def _platform() -> str:
    value = platform.system().lower()
    if value not in {"windows", "linux", "darwin"}:
        _fail("unsupported_platform", "Startup services support Windows, Linux and macOS")
    return value


def _run_command(args, *, timeout=15):
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("SYSTEMD_")}
    try:
        return _bounded_process(args, timeout=timeout, output_limit=MAX_DEFINITION * 2, env=env)
    except RuntimeError as exc:
        _fail("manager_command_failed", "Service manager command failed or exceeded its time/output limit")
        raise exc  # pragma: no cover


def _powershell(script: str, payload: dict | None = None) -> dict:
    encoded_payload = base64.b64encode(json.dumps(payload or {}).encode()).decode()
    source = ("$ErrorActionPreference='Stop'; $ProgressPreference='SilentlyContinue'; "
              "$OutputEncoding=[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); "
              "$i=ConvertFrom-Json ([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('"
              + encoded_payload + "'))); try { " + script + " } catch { "
              "$hr=$_.Exception.HResult; $code=if($hr -eq -2147024891){'access_denied'}"
              "else{'manager_error'}; @{error=$code;hresult=$hr}|ConvertTo-Json -Compress; exit 1 }")
    encoded = base64.b64encode(source.encode("utf-16-le")).decode()
    binary = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    result = _run_command([str(binary), "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded])
    try:
        value = json.loads(result.stdout.decode("utf-8-sig"))
        if type(value) is not dict:
            raise ValueError("Invalid manager response")
    except (ValueError, UnicodeError):
        _fail("manager_unavailable", "Windows Task Scheduler could not be inspected")
    if result.returncode or value.get("error"):
        if value.get("error") == "access_denied":
            _fail("access_denied", "Windows Task Scheduler denied current-user registration or control; "
                  "this installation does not elevate privileges")
        _fail("manager_error", "Windows Task Scheduler rejected the operation "
              f"(HRESULT {value.get('hresult', 'unavailable')})")
    return value


def _user(kind: str) -> str:
    if kind == "windows":
        return _powershell("@{user=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value}"
                           "|ConvertTo-Json -Compress")["user"]
    if os.getuid() == 0:
        _fail("privileged_user", "Install startup from a regular user login, not root")
    return str(os.getuid())


def _owner(data_dir: str, kind: str, user: str) -> str:
    path = os.path.normcase(data_dir) if kind == "windows" else data_dir
    return hashlib.sha256((kind + "\0" + user + "\0" + path).encode()).hexdigest()[:24]


def _root(data_dir) -> Path:
    root = _absolute(data_dir).resolve() / "service"
    if root.is_symlink():
        _fail("unowned_path", "The service metadata directory must not be a symlink")
    return root


@contextmanager
def _lock(data_dir):
    root = _root(data_dir)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = root / "operation.lock"
    if path.is_symlink():
        _fail("unowned_path", "The service lock must not be a symlink")
    handle = path.open("a+b")
    try:
        if handle.seek(0, 2) == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            _fail("operation_busy", "Another service operation is using this installation")
        yield root
    finally:
        handle.close()


def _definition_path(state: dict) -> Path:
    if state["platform"] == "linux":
        return Path.home() / ".config/systemd/user" / (state["name"] + ".service")
    if state["platform"] == "darwin":
        return Path.home() / "Library/LaunchAgents" / (state["name"] + ".plist")
    return _root(state["data_dir"]) / "task.xml"


def _new_state(data_dir, port, executable) -> dict:
    if type(port) is not int or not 1024 <= port <= 65535:
        _fail("invalid_port", "Service port must be an integer from 1024 to 65535")
    data_dir = str(_absolute(data_dir).resolve())
    executable = _absolute(executable or sys.executable)
    if not executable.is_file():
        _fail("interpreter_missing", "The service requires an existing absolute Python interpreter")
    temporary = _absolute(tempfile.gettempdir())
    if executable.is_relative_to(temporary) or any(
        part in {"archive-v0", "builds-v0", "uvx"} for part in executable.parts
    ):
        _fail("temporary_interpreter", "Install the lab in a persistent virtual environment before enabling startup")
    kind = _platform()
    user = _user(kind)
    owner = _owner(data_dir, kind, user)
    launcher = executable
    if kind == "windows" and executable.name.lower() == "python.exe":
        windowless = executable.with_name("pythonw.exe")
        if windowless.is_file():
            launcher = windowless
    return {"schema": 1, "platform": kind, "user": user, "owner": owner,
            "name": "genlayer-agent-lab-" + owner, "data_dir": data_dir,
            "port": port, "executable": str(executable), "launcher": str(launcher),
            "package_version": __version__}


def _load(data_dir) -> dict:
    root = _root(data_dir)
    path = root / MANIFEST_NAME
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 16384:
        _fail("metadata_invalid", "Service installation metadata is missing or invalid")
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        kind = _platform()
        user = _user(kind)
        canonical = str(_absolute(data_dir).resolve())
        owner = _owner(canonical, kind, user)
        executable = _absolute(state["executable"])
        allowed_launchers = {str(executable)}
        if kind == "windows" and executable.name.lower() == "python.exe":
            allowed_launchers.add(str(executable.with_name("pythonw.exe")))
        if (type(state["port"]) is not int or not 1024 <= state["port"] <= 65535
                or state["launcher"] not in allowed_launchers):
            raise ValueError("Invalid startup configuration")
        expected = {"schema": 1, "platform": kind, "user": user, "owner": owner,
                    "name": "genlayer-agent-lab-" + owner, "data_dir": canonical,
                    "executable": str(executable), "launcher": state["launcher"]}
    except (ValueError, KeyError, TypeError):
        _fail("metadata_invalid", "Service installation metadata is invalid")
    for key in ("schema", "platform", "user", "owner", "name", "data_dir", "executable", "launcher"):
        if state.get(key) != expected[key]:
            _fail("metadata_invalid", "Service installation identity does not match this user and path")
    return state


def _save(root, state):
    path = root / MANIFEST_NAME
    if path.is_symlink():
        _fail("unowned_path", "Service metadata must not be a symlink")
    pending = root / ("installation." + uuid.uuid4().hex + ".tmp")
    descriptor = os.open(pending, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as out:
        json.dump(state, out, indent=2)
    pending.replace(path)


def _arguments(state):
    return [state["launcher"], "-I", "-m", "genlayer_agent_lab.service", "run",
            "--data-dir", state["data_dir"], "--port", str(state["port"]), "--owner", state["owner"]]


def _systemd_quote(value, *, environment=True):
    # systemd unit files have their own specifier and environment expansion rules;
    # these are not shell arguments. Escape both before C-style double quoting.
    value = value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
    return '"' + (value.replace("$", "$$") if environment else value) + '"'


def _definition(state) -> bytes:
    args = _arguments(state)
    marker = "GenLayer Agent Lab owner=" + state["owner"]
    kind = state["platform"]
    if kind == "linux":
        return (f"# {marker}\n[Unit]\nDescription={marker}\n"
                "StartLimitIntervalSec=60\nStartLimitBurst=3\n\n[Service]\nType=simple\n"
                "ExecStart=" + " ".join(_systemd_quote(arg) for arg in args) + "\n"
                "WorkingDirectory=" + _systemd_quote(state["data_dir"], environment=False) + "\n"
                "Restart=on-failure\nRestartSec=5\nTimeoutStopSec=20\n"
                "UMask=0077\nNoNewPrivileges=true\n\n[Install]\nWantedBy=default.target\n").encode()
    if kind == "darwin":
        return plistlib.dumps({"Label": state["name"], "ProgramArguments": args,
            "WorkingDirectory": state["data_dir"], "RunAtLoad": True,
            "KeepAlive": {"SuccessfulExit": False}, "ThrottleInterval": 10,
            "ProcessType": "Background", "AbandonProcessGroup": False,
            "GenLayerAgentLabOwner": state["owner"]}, sort_keys=True)
    root = ET.Element(f"{{{NS}}}Task", version="1.3")

    def element(parent, name, value=None, **attrs):
        item = ET.SubElement(parent, f"{{{NS}}}" + name, attrs)
        if value is not None:
            item.text = value
        return item

    element(element(root, "RegistrationInfo"), "Description", marker)
    trigger = element(element(root, "Triggers"), "LogonTrigger")
    element(trigger, "Enabled", "true")
    element(trigger, "UserId", state["user"])
    principal = element(element(root, "Principals"), "Principal", id="Author")
    element(principal, "UserId", state["user"])
    element(principal, "LogonType", "InteractiveToken")
    element(principal, "RunLevel", "LeastPrivilege")
    settings = element(root, "Settings")
    for key, value in {"MultipleInstancesPolicy": "IgnoreNew", "DisallowStartIfOnBatteries": "false",
                       "StopIfGoingOnBatteries": "false", "AllowHardTerminate": "true",
                       "StartWhenAvailable": "true", "Enabled": "true", "Hidden": "true",
                       "ExecutionTimeLimit": "PT0S"}.items():
        element(settings, key, value)
    restart = element(settings, "RestartOnFailure")
    element(restart, "Interval", "PT1M")
    element(restart, "Count", "3")
    action = element(element(root, "Actions", Context="Author"), "Exec")
    element(action, "Command", args[0])
    element(action, "Arguments", subprocess.list2cmdline(args[1:]))
    element(action, "WorkingDirectory", state["data_dir"])
    # RegisterTask receives a UTF-16 COM string. An explicit UTF-8 XML declaration
    # inside that string makes Task Scheduler reject it with 0x8004131A.
    return ET.tostring(root, encoding="utf-8", xml_declaration=False)


def _task_matches(xml, state):
    try:
        actual = ET.fromstring(xml)
        expected = ET.fromstring(_definition(state))
    except (ValueError, ET.ParseError):
        return False
    prefix = {"t": NS}
    # Scheduler can insert harmless default metadata. Match all generated action,
    # user, trigger and settings fields, and forbid additional actions/principals.
    for group in ("Actions", "Principals", "Triggers"):
        if len(actual.findall(f"t:{group}/*", prefix)) != 1:
            return False
    if (actual.find("t:Principals/t:Principal/t:RequiredPrivileges", prefix) is not None
            or actual.find("t:Principals/t:Principal/t:GroupId", prefix) is not None):
        return False
    paths = ["RegistrationInfo/Description", "Actions/Exec/Command", "Actions/Exec/Arguments",
             "Actions/Exec/WorkingDirectory", "Principals/Principal/UserId",
             "Principals/Principal/LogonType", "Principals/Principal/RunLevel",
             "Triggers/LogonTrigger/UserId", "Triggers/LogonTrigger/Enabled"]
    paths += ["Settings/" + child.tag.split("}")[-1]
              for child in expected.find("t:Settings", prefix) if len(child) == 0]
    paths += ["Settings/RestartOnFailure/Interval", "Settings/RestartOnFailure/Count"]
    # Task Scheduler omits these documented default values when exporting XML.
    defaults = {"Principals/Principal/RunLevel": "LeastPrivilege",
                "Triggers/LogonTrigger/Enabled": "true",
                "Settings/AllowHardTerminate": "true", "Settings/Enabled": "true"}
    for path in paths:
        xpath = "/".join("t:" + item for item in path.split("/"))
        if actual.findtext(xpath, default=defaults.get(path), namespaces=prefix) != expected.findtext(xpath, namespaces=prefix):
            return False
    return True


_TASK_LOOKUP = """
$s=New-Object -ComObject Schedule.Service; $s.Connect(); $f=$s.GetFolder('\\'); $task=$null;
try { $task=$f.GetTask($i.name) } catch {
  if($_.Exception.HResult -ne -2147024894){throw}
}
"""


def _windows(state, operation="read", expected_xml=None):
    payload = {"name": state["name"], "user": state["user"], "xml": _definition(state).decode()}
    payload["expected_hash"] = hashlib.sha256(expected_xml.encode()).hexdigest() if expected_xml else ""
    if operation == "read":
        script = _TASK_LOOKUP + "$r=if($null -eq $task){@{exists=$false}}else{" \
            "$x=[xml]$task.Xml; foreach($node in $x.SelectNodes(\"//*[local-name()='UserId']\")){" \
            "if($node.InnerText -notmatch '^S-\\d(-\\d+)+$'){" \
            "$a=New-Object Security.Principal.NTAccount($node.InnerText);" \
            "$node.InnerText=$a.Translate([Security.Principal.SecurityIdentifier]).Value}};" \
            "@{exists=$true;xml=$task.Xml;normalized_xml=$x.OuterXml;" \
            "running=([int]$task.State -eq 4);enabled=$task.Enabled}}" \
            "; $r|ConvertTo-Json -Compress"
    elif operation == "install":
        script = _TASK_LOOKUP + "if($null -ne $task){throw 'Task already exists'}; " \
            "$null=$f.RegisterTask($i.name,$i.xml,2,$i.user,$null,3,$null); " \
            "@{ok=$true}|ConvertTo-Json -Compress"
    else:
        script = _TASK_LOOKUP + "if($null -eq $task){throw 'Task missing'}; " \
            "$sha=[Security.Cryptography.SHA256]::Create(); " \
            "$hash=([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($task.Xml))))" \
            ".Replace('-','').ToLowerInvariant(); if($hash -ne $i.expected_hash){throw 'Task changed'}; "
        script += {"start": "$null=$task.Run($null);", "stop": "$task.Stop(0);",
                   "uninstall": "$task.Stop(0); $f.DeleteTask($i.name,0);"}[operation]
        script += "@{ok=$true}|ConvertTo-Json -Compress"
    return _powershell(script, payload)


def _tool(name):
    result = shutil.which(name)
    if not result:
        _fail("manager_unavailable", f"The current-user {name} manager is not available")
    return result


def _unix(state, operation="read"):
    kind = state["platform"]
    if kind == "linux":
        base = [_tool("systemctl"), "--user"]
        name = state["name"] + ".service"
        if operation == "read":
            result = _run_command([*base, "show", name, "--no-pager",
                                   "--property=LoadState,ActiveState,SubState,FragmentPath,UnitFileState,NeedDaemonReload,DropInPaths"])
            if result.returncode:
                _fail("manager_unavailable", "The systemd user manager is unavailable; use a logged-in user session")
            values = dict(line.split("=", 1) for line in result.stdout.decode().splitlines() if "=" in line)
            return {"exists": values.get("LoadState") != "not-found",
                    "running": values.get("ActiveState") == "active",
                    "enabled": values.get("UnitFileState") == "enabled",
                    "fragment": values.get("FragmentPath"),
                    "needs_reload": values.get("NeedDaemonReload") == "yes",
                    "drop_ins": values.get("DropInPaths", "")}
        commands = {"install": [["daemon-reload"], ["enable", name]], "start": [["start", name]],
                    "stop": [["stop", name]], "uninstall": [["disable", "--now", name]],
                    "reload": [["daemon-reload"]]}[operation]
        for args in commands:
            if _run_command([*base, *args], timeout=25).returncode:
                _fail("manager_error", "The systemd user manager rejected the operation")
        return {"ok": True}
    domain = "gui/" + state["user"]
    target = domain + "/" + state["name"]
    base = [_tool("launchctl")]
    if operation == "read":
        result = _run_command([*base, "print", target])
        if result.returncode:
            # A missing label is normal, but a missing GUI session is not an
            # implicit invitation to install a privileged system LaunchDaemon.
            session = _run_command([*base, "print", domain])
            if session.returncode:
                _fail("manager_unavailable", "A macOS GUI login session is required for this LaunchAgent")
            return {"exists": False, "running": False, "enabled": False}
        raw = result.stdout.decode()
        return {"exists": True, "running": bool(re.search(r"^\s*pid = \d+\s*$", raw, re.M)),
                "enabled": True, "loaded_definition": raw}
    args = {"install": ["bootstrap", domain, str(_definition_path(state))],
            "start": ["kickstart", target], "stop": ["bootout", target],
            "uninstall": ["bootout", target]}[operation]
    if _run_command([*base, *args], timeout=25).returncode:
        _fail("manager_error", "The macOS user LaunchAgent manager rejected the operation")
    return {"ok": True}


def _inspect(state):
    path = _definition_path(state)
    if path.is_symlink():
        _fail("unowned_definition", "Refusing a symlinked startup definition")
    if path.exists() and (path.stat().st_size > MAX_DEFINITION or path.read_bytes() != _definition(state)):
        _fail("unowned_definition", "Refusing an existing startup definition that is not exactly owned by this installation")
    result = _windows(state) if state["platform"] == "windows" else _unix(state)
    if result.get("exists"):
        if state["platform"] == "windows":
            if not _task_matches(result.get("normalized_xml", result.get("xml", "")), state):
                _fail("unowned_definition", "Refusing an existing Scheduled Task with another owner, command or policy")
        elif not path.exists():
            _fail("unowned_definition", "A loaded service exists without this installation's owned definition")
        elif state["platform"] == "linux" and (result.get("fragment") != str(path)
                                                 or result.get("needs_reload") or result.get("drop_ins")):
            _fail("unowned_definition", "The systemd user manager loaded a different service definition")
        elif state["platform"] == "darwin" and not _launchd_matches(result.get("loaded_definition", ""), state):
            _fail("unowned_definition", "The loaded LaunchAgent command does not match this installation")
    return result


def _launchd_matches(raw, state):
    values = {}
    for key in ("path", "program"):
        match = re.search(r"^\s*" + key + r" = (.+)$", raw, re.M)
        values[key] = match[1].strip() if match else None
    arguments = re.search(r"^\s*arguments = \{\n(.*?)^\s*\}", raw, re.M | re.S)
    actual = [line.strip() for line in arguments[1].splitlines()] if arguments else []
    return (values["path"] == str(_definition_path(state))
            and values["program"] == state["launcher"] and actual == _arguments(state))


def _manager_action(state, operation, observed):
    if state["platform"] == "windows":
        return _windows(state, operation, observed.get("xml"))
    return _unix(state, operation)


def _write_definition(state):
    path = _definition_path(state)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not path.exists():
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as out:
            out.write(_definition(state))


def install(data_dir, *, port=8765, executable=None, start_now=False) -> dict:
    with _lock(data_dir) as root:
        state = _new_state(data_dir, port, executable)
        if (root / MANIFEST_NAME).exists():
            old = _load(data_dir)
            if any(old[key] != state[key] for key in ("port", "executable", "launcher")):
                _fail("configuration_conflict", "Uninstall the existing startup definition before changing its port or interpreter")
            state = old
        observed = _inspect(state)
        # Probe the chosen persistent interpreter with isolated imports before
        # saving executable paths that the OS will invoke at the next login.
        probe = _run_command([state["executable"], "-I", "-c",
            "import genlayer_agent_lab.service; import uvicorn; print('agent-lab-service-ready')"])
        if probe.returncode or probe.stdout.strip() != b"agent-lab-service-ready":
            _fail("interpreter_incompatible", "The selected interpreter cannot import the installed lab service")
        from .api import initialize_data_dir
        initialize_data_dir(Path(state["data_dir"]))
        _save(root, state)
        _write_definition(state)
        if not observed.get("exists"):
            _manager_action(state, "install", observed)
        elif state["platform"] == "linux" and not observed.get("enabled"):
            _unix(state, "install")
    return start(data_dir) if start_now else status(data_dir)


def _health(state):
    try:
        deadline = time.monotonic() + 2
        with httpx.Client(trust_env=False, follow_redirects=False, timeout=1) as client:
            with client.stream("GET", f"http://127.0.0.1:{state['port']}/service/health",
                               headers={"Accept-Encoding": "identity"}) as response:
                if response.status_code != 200:
                    return False
                raw = bytearray()
                for chunk in response.iter_raw():
                    raw.extend(chunk)
                    if len(raw) > 4096 or time.monotonic() >= deadline:
                        return False
        value = json.loads(raw)
        return type(value) is dict and value.get("service_id") == state["owner"]
    except (httpx.HTTPError, ValueError, TypeError):
        return False


def status(data_dir) -> dict:
    if not (_root(data_dir) / MANIFEST_NAME).exists():
        return {"installed": False, "running": False, "ready": False, "data_preserved": True}
    state = _load(data_dir)
    observed = _inspect(state)
    running = observed.get("running") is True
    defined_agent = state["platform"] == "darwin" and _definition_path(state).is_file()
    return {"installed": observed.get("exists") is True or defined_agent, "running": running,
            "ready": running and _health(state), "enabled": observed.get("enabled", False),
            "name": state["name"], "owner": state["owner"], "platform": state["platform"],
            "data_dir": state["data_dir"], "executable": state["executable"],
            "definition": str(_definition_path(state)), "endpoint": f"http://127.0.0.1:{state['port']}",
            "log_file": str(_root(data_dir) / "service.log"), "startup": "user_login",
            "interpreter_available": Path(state["launcher"]).is_file(),
            "data_preserved": True}


def start(data_dir) -> dict:
    with _lock(data_dir):
        state = _load(data_dir)
        observed = _inspect(state)
        if not Path(state["launcher"]).is_file():
            _fail("interpreter_missing", "The installed service interpreter moved or was removed; reinstall startup with its stable path")
        if not observed.get("running"):
            with socket.socket() as probe:
                try:
                    if os.name == "nt":
                        probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                    probe.bind(("127.0.0.1", state["port"]))
                except OSError:
                    _fail("port_in_use", "The loopback service port is in use; no existing process was stopped")
            if not observed.get("exists"):
                if state["platform"] != "darwin" or not _definition_path(state).exists():
                    _fail("not_installed", "Install this user's startup service before starting it")
                _manager_action(state, "install", observed)
            else:
                _manager_action(state, "start", observed)
    deadline = time.monotonic() + 15
    while True:
        result = status(data_dir)
        if result["ready"] or time.monotonic() >= deadline:
            return result
        time.sleep(0.5)


def stop(data_dir) -> dict:
    with _lock(data_dir):
        state = _load(data_dir)
        observed = _inspect(state)
        if observed.get("running") or (state["platform"] == "darwin" and observed.get("exists")):
            _manager_action(state, "stop", observed)
    result = status(data_dir)
    result["stopped"] = not result["running"]
    return result


def uninstall(data_dir) -> dict:
    with _lock(data_dir) as root:
        if not (root / MANIFEST_NAME).exists():
            return {"uninstalled": True, "data_preserved": True}
        state = _load(data_dir)
        observed = _inspect(state)
        if observed.get("exists"):
            _manager_action(state, "uninstall", observed)
        path = _definition_path(state)
        if path.exists():
            path.unlink()
        if state["platform"] == "linux":
            _unix(state, "reload")
        (root / MANIFEST_NAME).unlink()
    return {"uninstalled": True, "data_preserved": True, "logs_preserved": True}


def _serve(data_dir, port, owner):
    state = _load(data_dir)
    if state["owner"] != owner or state["port"] != port:
        _fail("identity_mismatch", "The startup invocation does not match its installation")
    for key in ("LAB_URL", "LAB_TOKEN", "LAB_DATA_DIR"):
        os.environ.pop(key, None)
    log_path = _root(data_dir) / "service.log"
    handler = RotatingFileHandler(log_path, maxBytes=2 * 1024 * 1024, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    for name in ("uvicorn", "uvicorn.error", "genlayer_agent_lab"):
        logger = logging.getLogger(name)
        logger.handlers = [handler]
        logger.propagate = False
        logger.setLevel(logging.INFO)
    import uvicorn

    from .api import create_app
    app = create_app(Path(state["data_dir"]))

    @app.get("/service/health", include_in_schema=False)
    def service_health():
        return {"service_id": owner, "version": __version__}

    uvicorn.run(app, host="127.0.0.1", port=port, access_log=False,
                server_header=False, log_config=None)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Internal user-service startup entry point")
    parser.add_argument("operation", choices=["run"])
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--owner", required=True)
    args = parser.parse_args(argv)
    _serve(args.data_dir, args.port, args.owner)


if __name__ == "__main__":
    main()
