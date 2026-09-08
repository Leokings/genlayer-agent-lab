"""Guided setup with explicit Docker/service doubles; no host reconfiguration."""

import copy
import json
import types

import httpx
import pytest

from genlayer_agent_lab import __version__
from genlayer_agent_lab import onboarding_setup as setup
from genlayer_agent_lab.api import initialize_data_dir, read_admin_token
from genlayer_agent_lab.cli import main

READY = {"installed": True, "image_id": "sha256:test", "ready": True,
         "runtime_verified": True, "network_internal": True, "fixture_ready": True,
         "validator_count": 12, "port": 8796}


@pytest.fixture
def services(monkeypatch):
    calls = []
    monkeypatch.delenv("SSH_CONNECTION", raising=False)
    monkeypatch.delenv("SSH_TTY", raising=False)
    monkeypatch.delenv("LAB_URL", raising=False)
    monkeypatch.setenv("DISPLAY", ":test")
    monkeypatch.setattr(setup, "prerequisites", lambda: {
        "ready": True, "checks": [{"ready": True, "message": "Docker and Compose available."}],
        "git_available": True})
    monkeypatch.setattr(setup.studio_profiles, "modern_profile_status", lambda _: copy.deepcopy(READY))
    monkeypatch.setattr(setup.studio_profiles, "setup_modern_profile", lambda *a, **kw: (
        calls.append(("build", a, kw)), copy.deepcopy(READY))[1])
    monkeypatch.setattr(setup.studio_profiles, "start_profile", lambda *a: (
        calls.append(("start", a)), copy.deepcopy(READY))[1])
    monkeypatch.setattr(setup, "_port_state", lambda *a: "free")
    monkeypatch.setattr(setup.webbrowser, "open", lambda *a, **kw: (calls.append(("browser", a, kw)), True)[1])

    def serve(data_dir, port, ready):
        calls.append(("serve", data_dir, port))
        ready()
        return True

    monkeypatch.setattr(setup, "_serve", serve)
    return calls


def test_check_is_read_only_and_never_initializes_or_launches(tmp_path, services, capsys):
    target = tmp_path / "new-installation"
    assert main(["setup", "--data-dir", str(target), "--check"]) == 0
    assert not target.exists() and not services
    assert "Prerequisites passed" in capsys.readouterr().out


def test_ready_studio_is_reused_and_token_never_printed(tmp_path, services, capsys):
    initialize_data_dir(tmp_path)
    token = read_admin_token(tmp_path)
    assert main(["setup", "--data-dir", str(tmp_path)]) == 0
    assert [item[0] for item in services] == ["serve", "browser"]
    assert read_admin_token(tmp_path) == token
    url = services[1][1][0]
    assert url.startswith("http://127.0.0.1:8765/assets/workflows.html#token=")
    assert token in url and "?" not in url
    output = capsys.readouterr()
    assert token not in output.out + output.err and "#token=" not in output.out + output.err


def test_existing_same_installation_server_is_opened_without_duplicate_serve(tmp_path, services, monkeypatch):
    initialize_data_dir(tmp_path)
    monkeypatch.setattr(setup, "_port_state", lambda *a: "same_installation")
    assert setup.run_setup(tmp_path) == 0
    assert [item[0] for item in services] == ["browser"]


def test_unrecognized_port_blocks_before_initializing_or_starting_studio(tmp_path, services, monkeypatch, capsys):
    monkeypatch.setattr(setup, "_port_state", lambda *a: "occupied")
    target = tmp_path / "new"
    assert setup.run_setup(target) == 2
    assert not target.exists() and not services
    assert "another service" in capsys.readouterr().out


@pytest.mark.parametrize("built,expected", [(False, "build"), (True, "start")])
def test_only_missing_builds_are_built_and_stopped_profiles_are_started(tmp_path, services, monkeypatch, built, expected):
    state = {**READY, "ready": False, "runtime_verified": False} if built else {"installed": False}
    monkeypatch.setattr(setup.studio_profiles, "modern_profile_status", lambda _: state)
    assert setup.run_setup(tmp_path, no_open=True) == 0
    assert [item[0] for item in services] == [expected, "serve"]


def test_failed_studio_readiness_prevents_lab_and_browser_launch(tmp_path, services, monkeypatch, capsys):
    monkeypatch.setattr(setup.studio_profiles, "modern_profile_status", lambda _: {"installed": False})
    monkeypatch.setattr(setup.studio_profiles, "setup_modern_profile", lambda *a, **kw: {"ready": False})
    assert setup.run_setup(tmp_path) == 2
    assert not services
    assert "not passed its readiness checks" in capsys.readouterr().out


@pytest.mark.parametrize("ssh,explicit", [(True, False), (False, True)])
def test_headless_setup_prints_tunnel_and_private_auth_instructions_without_opening(tmp_path, services, monkeypatch, capsys, ssh, explicit):
    if ssh:
        monkeypatch.setenv("SSH_CONNECTION", "203.0.113.2 10000 203.0.113.5 22")
    assert setup.run_setup(tmp_path, port=8888, no_open=explicit) == 0
    assert [item[0] for item in services] == ["serve"]
    output = capsys.readouterr().out
    assert "ssh -N -L 8875:127.0.0.1:8888 your-user@your-server" in output
    assert "http://127.0.0.1:8875/assets/workflows.html" in output
    assert "--show-token" in output and "private terminal" in output
    assert read_admin_token(tmp_path) not in output


def test_browser_exception_never_echoes_secret_url(tmp_path, services, monkeypatch, capsys):
    def failed(url, **kwargs):
        raise RuntimeError("Could not open " + url)

    monkeypatch.setattr(setup.webbrowser, "open", failed)
    assert setup.run_setup(tmp_path) == 0
    output = capsys.readouterr().out
    assert read_admin_token(tmp_path) not in output and "#token=" not in output
    assert "could not open automatically" in output


def test_build_failure_shows_bounded_redacted_log_without_launch(tmp_path, services, monkeypatch, capsys):
    initialize_data_dir(tmp_path)
    token = read_admin_token(tmp_path)
    root = tmp_path / "studio-modern"
    root.mkdir()
    (root / "build.log").write_text("original log " + token + "\nBearer secret-from-log\nlast diagnostic")
    monkeypatch.setattr(setup.studio_profiles, "modern_profile_status", lambda _: {"installed": False})

    def fail(*a, **kw):
        raise RuntimeError("Build problem " + token)

    monkeypatch.setattr(setup.studio_profiles, "setup_modern_profile", fail)
    assert setup.run_setup(tmp_path) == 2
    assert not services
    output = capsys.readouterr().out
    assert token not in output and "secret-from-log" not in output
    assert "last diagnostic" in output and "build.log" in output


@pytest.mark.parametrize("port", [0, 80, 65536, 8796])
def test_invalid_or_studio_conflicting_port_has_no_side_effects(tmp_path, services, port):
    assert main(["setup", "--data-dir", str(tmp_path / "new"), "--port", str(port)]) == 2
    assert not (tmp_path / "new").exists() and not services


def test_remote_url_is_rejected_without_any_setup_calls(tmp_path, services):
    assert main(["setup", "--url", "http://127.0.0.1:9999", "--data-dir", str(tmp_path / "new")]) == 2
    assert not services and not (tmp_path / "new").exists()


@pytest.mark.parametrize("missing", ["docker", "compose", "daemon", "architecture", "context"])
def test_prerequisite_failures_have_actionable_messages_without_mutations(monkeypatch, missing):
    monkeypatch.setattr(setup.shutil, "which", lambda name: None if name == missing else "/bin/" + name)
    monkeypatch.setattr(setup.platform, "system", lambda: "Linux")

    def endpoint(**kwargs):
        if missing == "context":
            raise RuntimeError("remote context")
        return "unix:///var/run/docker.sock"

    def info(endpoint):
        if missing == "daemon":
            raise RuntimeError("daemon unavailable")
        return {"OSType": "linux", "Architecture": "arm64" if missing == "architecture" else "x86_64"}

    monkeypatch.setattr(setup.container, "_endpoint", endpoint)
    monkeypatch.setattr(setup.container, "_linux_info", info)
    monkeypatch.setattr(setup.container, "_command", lambda *a, **kw: types.SimpleNamespace(
        returncode=1 if missing == "compose" else 0, stdout=b"2.39.1"))
    result = setup.prerequisites()
    assert result["ready"] is False
    text = json.dumps(result)
    assert {"docker": "docs.docker.com/engine/install", "compose": "docker compose version",
            "daemon": "docker info", "architecture": "x86-64", "context": "docker context ls"}[missing] in text


@pytest.mark.parametrize("identity", ["correct", "foreign", "missing", "huge", "static", "replay",
                                    "wrong_secret", "malformed_proof"])
def test_port_recognition_uses_public_identity_and_never_sends_bearer(tmp_path, monkeypatch, identity):
    initialize_data_dir(tmp_path)
    token = read_admin_token(tmp_path)
    requests = []

    class Probe:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def settimeout(self, value):
            assert value <= 1

        def connect_ex(self, address):
            return 0

    monkeypatch.setattr(setup.socket, "socket", lambda *a: Probe())
    original = httpx.Client

    def handler(request):
        requests.append(request)
        body = {"status": "ok", "version": __version__}
        nonce = request.url.params["setup_nonce"]
        assert len(nonce) == 64
        if identity in {"correct", "static", "replay", "wrong_secret", "malformed_proof"}:
            body["installation_id"] = setup.installation_identity(tmp_path, token)
            if identity == "correct":
                body["setup_proof"] = setup.installation_proof(token, nonce)
            elif identity == "replay":
                other_nonce = ("1" if nonce[0] == "0" else "0") + nonce[1:]
                body["setup_proof"] = setup.installation_proof(token, other_nonce)
            elif identity == "wrong_secret":
                body["setup_proof"] = setup.installation_proof("another-secret-token", nonce)
            elif identity == "malformed_proof":
                body["setup_proof"] = "non-ascii-proof-\u00e9"
        elif identity == "foreign":
            body["installation_id"] = "foreign-installation"
        elif identity == "huge":
            body["padding"] = "a" * 5000
        return httpx.Response(200, json=body)

    monkeypatch.setattr(setup.httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    assert setup._port_state(tmp_path, 8765) == ("same_installation" if identity == "correct" else "occupied")
    assert len(requests) == 1 and requests[0].url.path == "/health"
    assert "authorization" not in requests[0].headers
    assert token not in str(requests[0].url) + str(requests[0].headers)
    if identity == "correct":
        assert setup._port_state(tmp_path, 8765) == "same_installation"
        assert requests[0].url.params["setup_nonce"] != requests[1].url.params["setup_nonce"]


def test_installation_fingerprint_changes_with_directory_or_token(tmp_path):
    identity = setup.installation_identity(tmp_path, "a-secret-token")
    assert identity == setup.installation_identity(tmp_path, "a-secret-token")
    assert identity != setup.installation_identity(tmp_path / "another", "a-secret-token")
    assert identity != setup.installation_identity(tmp_path, "another-token")
    assert "secret" not in identity and len(identity) == 64


@pytest.mark.parametrize("nonce", [None, "", "a" * 63, "a" * 65, "A" * 64, "z" * 64, ["a"] * 64])
def test_setup_proof_rejects_unbounded_or_noncanonical_challenges(nonce):
    with pytest.raises(ValueError, match="64 lowercase hexadecimal"):
        setup.installation_proof("installation-token", nonce)


def test_missing_git_only_blocks_when_the_first_build_is_needed(tmp_path, services, monkeypatch):
    monkeypatch.setattr(setup, "prerequisites", lambda: {"ready": True, "checks": [], "git_available": False})
    assert setup.run_setup(tmp_path, check=True) == 0
    monkeypatch.setattr(setup.studio_profiles, "modern_profile_status", lambda _: {"installed": False})
    target = tmp_path / "new"
    assert setup.run_setup(target) == 2
    assert not target.exists() and not services


def test_headless_linux_without_display_never_launches_text_browser(tmp_path, services, monkeypatch):
    monkeypatch.setattr(setup.platform, "system", lambda: "Linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    assert setup.run_setup(tmp_path) == 0
    assert [item[0] for item in services] == ["serve"]


def test_port_claimed_during_studio_start_never_opens_or_starts_another_service(tmp_path, services, monkeypatch):
    states = iter(["free", "occupied"])
    monkeypatch.setattr(setup, "_port_state", lambda *a: next(states))
    assert setup.run_setup(tmp_path) == 2
    assert not services


def test_failed_service_start_does_not_open_browser(tmp_path, services, monkeypatch):
    monkeypatch.setattr(setup, "_serve", lambda *a: False)
    assert setup.run_setup(tmp_path) == 2
    assert not services


@pytest.mark.parametrize("console_available", [True, False])
def test_windows_manual_sign_in_command_uses_powershell_call_operator(tmp_path, monkeypatch, capsys, console_available):
    directory = tmp_path / "Lab's environment"
    directory.mkdir()
    python = directory / "python.exe"
    console = directory / "gl-agent-lab.exe"
    if console_available:
        console.write_bytes(b"placeholder")
    monkeypatch.setattr(setup, "os", types.SimpleNamespace(name="nt"))
    monkeypatch.setattr(setup, "sys", types.SimpleNamespace(executable=str(python)))
    setup._connection_instructions(tmp_path, 8765, headless=False)
    line = next(line.strip() for line in capsys.readouterr().out.splitlines() if "--show-token" in line)
    executable = str(console if console_available else python).replace("'", "''")
    assert line.startswith("& '" + executable + "' ")
    assert "' init --data-dir " in line if console_available else " -m genlayer_agent_lab.cli init --data-dir " in line


def test_failed_studio_inspection_is_not_treated_as_permission_to_rebuild(tmp_path, services, monkeypatch, capsys):
    monkeypatch.setattr(setup.studio_profiles, "modern_profile_status", lambda _: {
        "installed": True, "ready": False, "error": "modern_runtime_inspection_failed"})
    target = tmp_path / "new"
    assert setup.run_setup(target) == 2
    assert not target.exists() and not services
    assert "could not be inspected safely" in capsys.readouterr().out
