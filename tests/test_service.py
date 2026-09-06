import base64
import copy
import json
import plistlib
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx
import pytest

from genlayer_agent_lab import service
from genlayer_agent_lab.runtime.container import CommandResult


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "_platform", lambda: "windows")
    monkeypatch.setattr(service, "_user", lambda _: "S-1-5-21-111-222-333-1001")
    return service._new_state(tmp_path / "lab with spaces & apostrophe's", 8875, sys.executable)


def test_identity_is_deterministic_and_scoped_to_user_path_and_platform(state):
    assert state["owner"] == service._owner(state["data_dir"], "windows", state["user"])
    assert state["owner"] != service._owner(state["data_dir"] + "2", "windows", state["user"])
    assert state["owner"] != service._owner(state["data_dir"], "windows", "another-user")
    assert state["owner"] != service._owner(state["data_dir"], "linux", state["user"])


def test_executable_symlink_keeps_venv_path(tmp_path, monkeypatch):
    executable = tmp_path / "base-python"
    executable.write_text("placeholder")
    link = tmp_path / "venv/bin/python"
    link.parent.mkdir(parents=True)
    try:
        link.symlink_to(executable)
    except OSError:
        pytest.skip("This host does not permit symlink creation")
    assert service._absolute(link) == link.absolute()
    assert service._absolute(link) != link.resolve()


@pytest.mark.parametrize("port", [0, 80, 65536, True, "8875", 1.5])
def test_privileged_or_invalid_ports_are_rejected(state, port):
    with pytest.raises(service.ServiceError, match="port"):
        service._new_state(state["data_dir"], port, state["executable"])


def test_windows_definition_uses_current_user_and_exact_structured_arguments(state):
    xml = service._definition(state)
    assert not xml.startswith(b"<?xml")  # COM receives Unicode, not UTF-8 XML bytes.
    root = ET.fromstring(xml)
    ns = {"t": service.NS}
    assert root.findtext("t:Principals/t:Principal/t:RunLevel", namespaces=ns) == "LeastPrivilege"
    assert root.findtext("t:Principals/t:Principal/t:LogonType", namespaces=ns) == "InteractiveToken"
    assert root.findtext("t:Triggers/t:LogonTrigger/t:UserId", namespaces=ns) == state["user"]
    assert root.findtext("t:Actions/t:Exec/t:Arguments", namespaces=ns) == subprocess.list2cmdline(service._arguments(state)[1:])
    assert service._task_matches(xml, state)
    assert b"LAB_TOKEN" not in xml and b"admin.token" not in xml


def test_windows_exported_defaults_are_normalized_without_accepting_elevation(state):
    root = ET.fromstring(service._definition(state))
    ns = {"t": service.NS}
    principal = root.find("t:Principals/t:Principal", ns)
    principal.remove(principal.find("t:RunLevel", ns))
    trigger = root.find("t:Triggers/t:LogonTrigger", ns)
    trigger.remove(trigger.find("t:Enabled", ns))
    assert service._task_matches(ET.tostring(root), state)
    ET.SubElement(principal, "{" + service.NS + "}RunLevel").text = "HighestAvailable"
    assert not service._task_matches(ET.tostring(root), state)


@pytest.mark.parametrize("field", ["Command", "Arguments", "WorkingDirectory"])
def test_windows_changed_action_is_not_owned(state, field):
    root = ET.fromstring(service._definition(state))
    root.find("t:Actions/t:Exec/t:" + field, {"t": service.NS}).text = "foreign value"
    assert not service._task_matches(ET.tostring(root), state)


def test_windows_additional_privileges_are_not_owned(state):
    root = ET.fromstring(service._definition(state))
    principal = root.find("t:Principals/t:Principal", {"t": service.NS})
    ET.SubElement(principal, "{" + service.NS + "}RequiredPrivileges")
    assert not service._task_matches(ET.tostring(root), state)


@pytest.mark.parametrize("body", [b"[]", b"x" * 5000, b'{"service_id":"another-installation"}'],
                         ids=["json_shape", "oversize", "foreign"])
def test_readiness_rejects_unrelated_or_unbounded_response(state, monkeypatch, body):
    original = httpx.Client
    transport = httpx.MockTransport(lambda request: httpx.Response(200, stream=httpx.ByteStream(body)))
    monkeypatch.setattr(service.httpx, "Client", lambda **kwargs: original(transport=transport, **kwargs))
    assert service._health(state) is False


def test_readiness_accepts_only_matching_wrapper_identity(state, monkeypatch):
    original = httpx.Client
    body = json.dumps({"service_id": state["owner"]}).encode()
    transport = httpx.MockTransport(lambda request: httpx.Response(200, stream=httpx.ByteStream(body)))
    monkeypatch.setattr(service.httpx, "Client", lambda **kwargs: original(transport=transport, **kwargs))
    assert service._health(state) is True


def test_systemd_escapes_specifiers_environment_and_shell_punctuation_separately(state):
    state = {**state, "platform": "linux", "launcher": '/venv with space/py"thon',
             "data_dir": "/home/test/a%id$b;$(touch nope)"}
    unit = service._definition(state).decode()
    assert '"/venv with space/py\\"thon"' in unit
    assert '"/home/test/a%%id$$b;$$(touch nope)"' in unit  # ExecStart arguments.
    assert 'WorkingDirectory=%h\n' in unit
    assert "WantedBy=default.target" in unit
    assert "User=root" not in unit and "/bin/sh" not in unit


def test_launchagent_uses_argument_array_and_user_login_without_shell(state):
    state = {**state, "platform": "darwin"}
    definition = plistlib.loads(service._definition(state))
    assert definition["ProgramArguments"] == service._arguments(state)
    assert definition["RunAtLoad"] is True
    assert definition["KeepAlive"] == {"SuccessfulExit": False}
    assert "UserName" not in definition and "StandardOutPath" not in definition


def test_powershell_payload_cannot_become_script_source(monkeypatch):
    captured = []
    monkeypatch.setattr(service, "_run_command", lambda args, **kw: (
        captured.append(args) or CommandResult(0, b'{"ok":true}', b"")))
    hostile = "'; Start-Process not-a-command; #' $(anything) & stuff"
    assert service._powershell("@{ok=$true}|ConvertTo-Json -Compress", {"path": hostile}) == {"ok": True}
    args = captured[0]
    assert "-NoProfile" in args and "-NonInteractive" in args
    script = base64.b64decode(args[-1]).decode("utf-16-le")
    assert hostile not in script
    assert base64.b64encode(json.dumps({"path": hostile}).encode()).decode() in script


def test_actual_definition_must_match_before_mutation(state, monkeypatch):
    root = service._root(state["data_dir"])
    root.mkdir(parents=True)
    service._save(root, state)
    service._write_definition(state)
    foreign = copy.deepcopy(state)
    foreign["launcher"] = "foreign.exe"
    monkeypatch.setattr(service, "_windows", lambda *args, **kw: {
        "exists": True, "xml": service._definition(foreign).decode(), "running": True})
    monkeypatch.setattr(service, "_manager_action", lambda *a, **k: pytest.fail("Foreign task mutated"))
    with pytest.raises(service.ServiceError, match="another owner, command or policy"):
        service.uninstall(state["data_dir"])
    assert (root / service.MANIFEST_NAME).exists()


def test_linux_dropins_are_rejected_even_when_fragment_is_owned(state, tmp_path, monkeypatch):
    state = {**state, "platform": "linux"}
    path = tmp_path / "user.service"
    path.write_bytes(service._definition(state))
    monkeypatch.setattr(service, "_definition_path", lambda _: path)
    monkeypatch.setattr(service, "_unix", lambda _: {
        "exists": True, "fragment": str(path), "drop_ins": "/other/override.conf"})
    with pytest.raises(service.ServiceError, match="different service definition"):
        service._inspect(state)


def test_loaded_launchagent_arguments_are_verified(state, tmp_path, monkeypatch):
    state = {**state, "platform": "darwin"}
    path = tmp_path / "agent.plist"
    path.write_bytes(service._definition(state))
    monkeypatch.setattr(service, "_definition_path", lambda _: path)
    raw = (f"gui/501/test = {{\n\tpath = {path}\n\tprogram = {state['launcher']}\n\targuments = {{\n"
           + "".join("\t\t" + arg + "\n" for arg in service._arguments(state)) + "\t}\n}\n")
    assert service._launchd_matches(raw, state)
    assert not service._launchd_matches(raw.replace("--port", "--other"), state)


def test_launchagent_preserves_path_whitespace_and_rejects_changed_arguments(state, tmp_path, monkeypatch):
    state = {**state, "platform": "darwin", "launcher": '/Users/test/python with trailing ',
             "data_dir": '/Users/test/state % $ " and trailing\\ '}
    path = tmp_path / "agent.plist"
    monkeypatch.setattr(service, "_definition_path", lambda _: path)
    raw = (f"gui/501/test = {{\n\tpath = {path}\n\tprogram = {state['launcher']}\n\targuments = {{\n"
           + "".join("\t\t" + arg + "\n" for arg in service._arguments(state)) + "\t}\n}\n")
    assert service._launchd_matches(raw, state)
    assert not service._launchd_matches(raw.replace("trailing\\ \n", "trailing\\\n"), state)
    assert not service._launchd_matches(raw.replace("\t\t--port\n", "\t\t --port\n"), state)
    assert not service._launchd_matches(raw.replace("\t\t--port\n", "\t--port\n"), state)
    assert not service._launchd_matches(raw + f"\tprogram = {state['launcher']}\n", state)


def test_uninstall_preserves_database_token_and_logs_even_if_interpreter_was_removed(state, monkeypatch):
    root = service._root(state["data_dir"])
    root.mkdir(parents=True)
    service._save(root, state)
    service._write_definition(state)
    data = Path(state["data_dir"])
    (data / "admin.token").write_text("private-token-must-not-change")
    (data / "lab.sqlite3").write_bytes(b"existing database")
    (root / "service.log").write_text("existing logs")
    monkeypatch.setattr(service, "_windows", lambda *args, **kw: {"exists": False})
    result = service.uninstall(data)
    assert result["data_preserved"] and result["logs_preserved"]
    assert (data / "admin.token").read_text() == "private-token-must-not-change"
    assert (data / "lab.sqlite3").read_bytes() == b"existing database"
    assert (root / "service.log").exists()
    assert not (root / service.MANIFEST_NAME).exists()
    assert not service._definition_path(state).exists()


def test_lifecycle_lock_and_poisoned_local_definition(state):
    with service._lock(state["data_dir"]):
        with pytest.raises(service.ServiceError, match="Another service operation"):
            with service._lock(state["data_dir"]):
                pytest.fail("Duplicate ownership lock acquired")
    service._write_definition(state)
    service._definition_path(state).write_text("foreign startup instruction")
    with pytest.raises(service.ServiceError, match="not exactly owned"):
        service._inspect(state)


def test_metadata_identity_is_checked_without_requiring_interpreter_to_exist(state):
    root = service._root(state["data_dir"])
    root.mkdir(parents=True)
    state["executable"] = str(root / "removed-python.exe")
    state["launcher"] = state["executable"]
    service._save(root, state)
    assert service._load(state["data_dir"])["executable"] == state["executable"]
    state["owner"] = "f" * 24
    service._save(root, state)
    with pytest.raises(service.ServiceError, match="identity"):
        service._load(state["data_dir"])
