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
MANAGED = {"installed": True, "running": True, "ready": True, "enabled": True,
           "endpoint": "http://127.0.0.1:8765"}
ENVIRONMENT_READY = {"ready": True, "checks": [
    {"id": "service", "status": "pass"}, {"id": "studio", "status": "pass"}]}
VERIFY_MANAGED_ENVIRONMENT = setup._managed_environment


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
    monkeypatch.setattr(setup.studio_profiles, "start_profile", lambda *a, **kw: (
        calls.append(("start", a)), copy.deepcopy(READY))[1])
    managed = {"installed": False, "running": False, "ready": False}
    monkeypatch.setattr(setup, "_port_state", lambda *a:
                        "same_installation" if managed["ready"] else "free")
    monkeypatch.setattr(setup.webbrowser, "open", lambda *a, **kw: (calls.append(("browser", a, kw)), True)[1])

    def install(data_dir, *, port, start_now):
        calls.append(("managed", data_dir, port, start_now))
        managed.update(MANAGED, endpoint=f"http://127.0.0.1:{port}")
        return copy.deepcopy(managed)

    monkeypatch.setattr(setup.service, "install", install)
    monkeypatch.setattr(setup.service, "status", lambda data_dir: copy.deepcopy(managed))
    monkeypatch.setattr(setup, "_managed_environment", lambda data_dir, port: None)

    def serve(data_dir, port, ready):
        calls.append(("serve", data_dir, port))
        ready()
        return True

    monkeypatch.setattr(setup, "_serve", serve)
    return calls


def test_check_is_read_only_and_never_initializes_or_launches(tmp_path, services, monkeypatch, capsys):
    def forbidden_status(*args, **kwargs):
        raise AssertionError("Read-only setup must not create service metadata")

    monkeypatch.setattr(setup.service, "status", forbidden_status)
    target = tmp_path / "new-installation"
    assert main(["setup", "--data-dir", str(target), "--check"]) == 0
    assert not target.exists() and not services
    assert "Prerequisites passed" in capsys.readouterr().out


def test_ready_studio_is_reused_and_token_never_printed(tmp_path, services, capsys):
    initialize_data_dir(tmp_path)
    token = read_admin_token(tmp_path)
    assert main(["setup", "--data-dir", str(tmp_path)]) == 0
    assert [item[0] for item in services] == ["managed", "browser"]
    assert services[0] == ("managed", tmp_path.resolve(), 8765, True)
    assert read_admin_token(tmp_path) == token
    url = services[1][1][0]
    assert url.startswith("http://127.0.0.1:8765/assets/workflows.html#token=")
    assert token in url and "?" not in url
    output = capsys.readouterr()
    assert token not in output.out + output.err and "#token=" not in output.out + output.err
    assert "independently of this terminal" in output.out
    assert "Keep this terminal open" not in output.out


def test_existing_managed_installation_is_opened_without_duplicate_launch(tmp_path, services, monkeypatch):
    initialize_data_dir(tmp_path)
    monkeypatch.setattr(setup, "_port_state", lambda *a: "same_installation")
    monkeypatch.setattr(setup.service, "status", lambda *a: copy.deepcopy(MANAGED))
    assert setup.run_setup(tmp_path) == 0
    assert [item[0] for item in services] == ["browser"]


def test_existing_foreground_server_is_not_mistaken_for_managed_setup(
        tmp_path, services, monkeypatch, capsys):
    initialize_data_dir(tmp_path)
    token = read_admin_token(tmp_path)
    monkeypatch.setattr(setup, "_port_state", lambda *a: "same_installation")
    assert setup.run_setup(tmp_path) == 2
    assert not services
    assert read_admin_token(tmp_path) == token
    output = capsys.readouterr().out
    assert "Finish any active tests" in output and "No existing process was stopped" in output
    assert "--foreground" in output and "independently of this terminal" not in output


def test_foreground_mode_is_explicit_and_keeps_manual_terminal_behavior(tmp_path, services, capsys):
    assert main(["setup", "--data-dir", str(tmp_path), "--foreground"]) == 0
    assert [item[0] for item in services] == ["serve", "browser"]
    output = capsys.readouterr().out
    assert "Keep this terminal open" in output
    assert "independently of this terminal" not in output


def test_linux_managed_setup_explains_user_session_lifetime(tmp_path, services, monkeypatch, capsys):
    monkeypatch.setattr(setup.platform, "system", lambda: "Linux")
    assert setup.run_setup(tmp_path, no_open=True) == 0
    output = capsys.readouterr().out
    assert "restart your agent without stopping the Lab" in output
    assert "service depends on your user session" in output
    assert "last VPS login" in output and "user services running after sign-out" in output
    assert "close this setup terminal" not in output


def test_foreground_mode_can_reuse_existing_server_without_registering_startup(
        tmp_path, services, monkeypatch):
    initialize_data_dir(tmp_path)
    monkeypatch.setattr(setup, "_port_state", lambda *a: "same_installation")

    def forbidden_status(*args, **kwargs):
        raise AssertionError("Explicit foreground reuse needs no service-manager operation")

    monkeypatch.setattr(setup.service, "status", forbidden_status)
    assert setup.run_setup(tmp_path, foreground=True) == 0
    assert [item[0] for item in services] == ["browser"]


@pytest.mark.parametrize("failure", [
    {"ready": False}, {"installed": False}, {"running": False}, {"enabled": False},
    {"endpoint": "http://127.0.0.1:9999"},
])
def test_unverified_managed_service_never_falls_back_to_agent_terminal(
        tmp_path, services, monkeypatch, capsys, failure):
    monkeypatch.setattr(setup.service, "install", lambda *a, **kw: {**MANAGED, **failure})
    assert setup.run_setup(tmp_path) == 2
    assert not services
    output = capsys.readouterr().out
    assert "managed service is not ready" in output
    assert "service status" in output and "service/service.log" in output
    assert "independently of this terminal" not in output


@pytest.mark.parametrize("code,message", [
    ("access_denied", "Windows Task Scheduler denied current-user registration or control"),
    ("manager_unavailable", "The systemd user manager is unavailable; use a logged-in user session"),
])
def test_service_manager_restriction_preserves_actionable_failure_without_fallback(
        tmp_path, services, monkeypatch, capsys, code, message):
    def blocked(*args, **kwargs):
        raise setup.service.ServiceError(code, message)

    monkeypatch.setattr(setup.service, "install", blocked)
    assert setup.run_setup(tmp_path, no_open=True) == 2
    assert not services
    output = capsys.readouterr().out
    assert message in output
    assert "setup --data-dir" in output and "--no-open" in output
    assert "independently of this terminal" not in output


def test_service_readiness_does_not_replace_actual_installation_health_proof(
        tmp_path, services, monkeypatch):
    states = iter(["free", "free", "occupied"])
    monkeypatch.setattr(setup, "_port_state", lambda *a: next(states))
    assert setup.run_setup(tmp_path) == 2
    assert [item[0] for item in services] == ["managed"]


@pytest.fixture
def managed_environment_http(monkeypatch, services):
    original_client = httpx.Client
    monkeypatch.setattr(setup, "_managed_environment", VERIFY_MANAGED_ENVIRONMENT)

    def install(handler):
        def streaming_handler(request):
            response = handler(request)
            return httpx.Response(response.status_code, headers=response.headers,
                                  stream=httpx.ByteStream(response.content))

        def client(**kwargs):
            assert kwargs["follow_redirects"] is False and kwargs["trust_env"] is False
            return original_client(transport=httpx.MockTransport(streaming_handler), **kwargs)

        monkeypatch.setattr(setup.httpx, "Client", client)

    return install


def test_managed_setup_checks_studio_inside_service_and_waits_for_pending_probe(
        tmp_path, services, managed_environment_http, monkeypatch):
    requests = []

    def handler(request):
        requests.append(request)
        assert request.url == "http://127.0.0.1:8765/v1/onboarding/status"
        assert request.headers["Authorization"] == "Bearer " + read_admin_token(tmp_path)
        assert request.headers["Accept-Encoding"] == "identity"
        result = (ENVIRONMENT_READY if len(requests) > 1 else {
            "ready": False, "checks": [
                {"id": "service", "status": "pass"}, {"id": "studio", "status": "pending"}]})
        return httpx.Response(200, json=result)

    managed_environment_http(handler)
    monkeypatch.setattr(setup.time, "sleep", lambda _: None)
    assert setup.run_setup(tmp_path, no_open=True) == 0
    assert len(requests) == 2
    assert [item[0] for item in services] == ["managed"]


@pytest.mark.parametrize("response", [
    {"ready": False, "checks": [
        {"id": "service", "status": "pass"}, {"id": "studio", "status": "fail"}]},
    {"ready": True, "checks": [{"id": "service", "status": "pass"}]},
    {"ready": True, "checks": "invalid"},
])
def test_terminal_readiness_cannot_hide_service_environment_failure(
        tmp_path, services, managed_environment_http, capsys, response):
    managed_environment_http(lambda request: httpx.Response(200, json=response))
    assert setup.run_setup(tmp_path) == 2
    assert [item[0] for item in services] == ["managed"]
    output = capsys.readouterr().out
    assert "Docker context in the service may differ from this setup terminal" in output
    assert "independently of this terminal" not in output
    assert read_admin_token(tmp_path) not in output


def test_environment_credentials_are_not_sent_when_installation_proof_fails(
        tmp_path, services, managed_environment_http, monkeypatch):
    requests = []
    managed_environment_http(lambda request: requests.append(request) or httpx.Response(200, json=ENVIRONMENT_READY))
    states = iter(["free", "free", "occupied"])
    monkeypatch.setattr(setup, "_port_state", lambda *a: next(states))
    assert setup.run_setup(tmp_path) == 2
    assert not requests


@pytest.mark.parametrize("failure", ["redirect", "large", "malformed", "timeout", "unauthorized"])
def test_managed_environment_http_errors_are_bounded_private_and_not_redirected(
        tmp_path, services, managed_environment_http, capsys, failure):
    requests = []

    def handler(request):
        requests.append(request)
        assert len(requests) == 1, "A failed environment request must not follow a redirect or retry"
        if failure == "redirect":
            return httpx.Response(302, headers={"Location": "http://unrelated.invalid/collect"})
        if failure == "large":
            return httpx.Response(200, content=b" " * 16_385)
        if failure == "malformed":
            return httpx.Response(200, content=b"private-service-detail")
        if failure == "unauthorized":
            return httpx.Response(401, content=b"private-service-detail")
        raise httpx.ReadTimeout("private-service-detail " + request.headers["Authorization"], request=request)

    managed_environment_http(handler)
    assert setup.run_setup(tmp_path) == 2
    assert len(requests) == 1
    assert [item[0] for item in services] == ["managed"]
    output = capsys.readouterr().out
    assert "private-service-detail" not in output and read_admin_token(tmp_path) not in output


def test_managed_environment_pending_has_overall_deadline(
        tmp_path, services, managed_environment_http, monkeypatch, capsys):
    requests = []
    clock = [0.0]
    monkeypatch.setattr(setup.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(setup.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    monkeypatch.setattr(setup, "MANAGED_ENVIRONMENT_TIMEOUT_SECONDS", 1)

    def handler(request):
        requests.append(request)
        assert len(requests) <= 2, "A pending service probe must stop at the overall deadline"
        return httpx.Response(200, json={"ready": False, "checks": [
            {"id": "service", "status": "pass"}, {"id": "studio", "status": "pending"}]})

    managed_environment_http(handler)
    assert setup.run_setup(tmp_path) == 2
    assert len(requests) == 2 and clock[0] == 1
    assert "environment check timed out" in capsys.readouterr().out
    assert [item[0] for item in services] == ["managed"]


def test_foreground_failure_keeps_explicit_lifetime_in_resume_command(
        tmp_path, services, monkeypatch, capsys):
    monkeypatch.setattr(setup, "_serve", lambda *args: False)
    assert setup.run_setup(tmp_path, foreground=True, no_open=True, port=8888) == 2
    assert not services
    assert "--port 8888 --no-open --foreground" in capsys.readouterr().out


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
    assert [item[0] for item in services] == [expected, "managed"]


def test_failed_studio_readiness_prevents_lab_and_browser_launch(tmp_path, services, monkeypatch, capsys):
    monkeypatch.setattr(setup.studio_profiles, "modern_profile_status", lambda _: {"installed": False})
    monkeypatch.setattr(setup.studio_profiles, "setup_modern_profile", lambda *a, **kw: {"ready": False})
    assert setup.run_setup(tmp_path) == 2
    assert not services
    assert "not passed its readiness checks" in capsys.readouterr().out


@pytest.mark.parametrize("ssh,explicit", [(True, False), (False, True)])
def test_headless_setup_prints_guided_opening_without_credentials_or_placeholders(tmp_path, services, monkeypatch, capsys, ssh, explicit):
    if ssh:
        monkeypatch.setenv("SSH_CONNECTION", "203.0.113.2 10000 203.0.113.5 22")
    assert setup.run_setup(tmp_path, port=8888, no_open=explicit) == 0
    assert [item[0] for item in services] == ["managed"]
    output = capsys.readouterr().out
    assert "?location=vps&lab_port=8888#open-dashboard" in output
    assert "Connect this browser" in output
    assert "--show-token" not in output and "your-user@your-server" not in output
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
    from genlayer_agent_lab.runtime.container import CommandResult
    from genlayer_agent_lab.runtime.studio_stack import _run_stage

    initialize_data_dir(tmp_path)
    token = read_admin_token(tmp_path)
    root = tmp_path / "studio-modern"
    root.mkdir()
    (root / "build.log").write_text("unrelated successful image export")
    monkeypatch.setattr(setup.studio_profiles, "modern_profile_status", lambda _: {"installed": False})

    def fail(*a, **kw):
        return _run_stage(root, "compose_up", lambda: CommandResult(1,
            b"\n".join([b"old line"] * 3000),
            (token + "\nBearer secret-from-log\nactual startup diagnostic").encode()), timeout=1830)

    monkeypatch.setattr(setup.studio_profiles, "setup_modern_profile", fail)
    assert setup.run_setup(tmp_path) == 2
    assert not services
    output = capsys.readouterr().out
    assert token not in output and "secret-from-log" not in output
    assert "actual startup diagnostic" in output and "compose_up" in output
    assert "unrelated successful image export" not in output and "build.log" not in output
    assert "operation-logs" in output and len(output) < 5000
    assert "setup --data-dir" in output


def test_unrelated_setup_failure_does_not_display_previous_build_log(tmp_path, services, monkeypatch, capsys):
    root = tmp_path / "studio-modern"
    root.mkdir()
    (root / "build.log").write_text("successful old image build")
    monkeypatch.setattr(setup.studio_profiles, "modern_profile_status", lambda _: {"installed": False})

    def fail(*a, **kw):
        raise RuntimeError("Current ownership check failed")

    monkeypatch.setattr(setup.studio_profiles, "setup_modern_profile", fail)
    assert setup.run_setup(tmp_path, port=8888, no_open=True) == 2
    output = capsys.readouterr().out
    assert "Current ownership check failed" in output
    assert "successful old image build" not in output and "build.log" not in output
    assert "--port 8888 --no-open" in output


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
            raise setup.container.UnsupportedDockerEndpoint("remote context")
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


@pytest.fixture
def available_prerequisites(monkeypatch):
    monkeypatch.setattr(setup.shutil, "which", lambda name: "/bin/" + name)
    monkeypatch.setattr(setup.container, "_endpoint", lambda **kw:
                        "npipe:////./pipe/dockerDesktopLinuxEngine")
    monkeypatch.setattr(setup.container, "_linux_info", lambda endpoint: {
        "OSType": "linux", "Architecture": "x86_64"})

    def command(endpoint, args, **kwargs):
        assert endpoint == "npipe:////./pipe/dockerDesktopLinuxEngine"
        assert args == ["compose", "version", "--short"]
        assert kwargs == {"timeout": 8}
        return types.SimpleNamespace(returncode=0, stdout=b"5.0.1\n")

    monkeypatch.setattr(setup.container, "_command", command)


@pytest.mark.parametrize("probe,code,action", [
    ("_endpoint", "docker_context_probe_failed", "docker context inspect"),
    ("_linux_info", "docker_engine_probe_failed", "docker info"),
    ("_command", "docker_compose_probe_failed", "docker compose version"),
])
@pytest.mark.parametrize("error", [
    RuntimeError("Docker command timed out; private-test-value"),
    RuntimeError("Docker command left an unclosed child pipe; private-test-value"),
    OSError("Cannot launch command; private-test-value"),
    ValueError("Invalid metadata; private-test-value"),
])
def test_prerequisite_probe_failures_are_not_missing_or_unsupported(
        monkeypatch, available_prerequisites, probe, code, action, error):
    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(setup.container, probe, fail)
    result = setup.prerequisites()
    assert result["ready"] is False
    failures = [item for item in result["checks"] if not item["ready"]]
    assert len(failures) == 1 and failures[0]["code"] == code
    assert action in failures[0]["message"]
    text = json.dumps(result).lower()
    assert all(value not in text for value in (
        "private-test-value", "install", "unsupported", "start docker", "select a local"))


@pytest.mark.parametrize("returncode,stdout", [(1, b"5.0.1"), (0, b""), (0, b"private-test-value")])
def test_compose_failed_or_invalid_response_does_not_recommend_installation(
        monkeypatch, available_prerequisites, returncode, stdout):
    monkeypatch.setattr(setup.container, "_command", lambda *a, **kw: types.SimpleNamespace(
        returncode=returncode, stdout=stdout))
    result = setup.prerequisites()
    assert result["ready"] is False
    assert result["checks"][-1]["code"] == "docker_compose_probe_failed"
    assert "install" not in json.dumps(result).lower()
    assert "private-test-value" not in json.dumps(result)


def test_supported_prerequisites_are_ready(available_prerequisites):
    result = setup.prerequisites()
    assert result["ready"] is True
    assert all(item["ready"] for item in result["checks"])


def test_confirmed_old_compose_version_recommends_upgrade(monkeypatch, available_prerequisites):
    monkeypatch.setattr(setup.container, "_command", lambda *a, **kw: types.SimpleNamespace(
        returncode=0, stdout=b"1.29.2"))
    result = setup.prerequisites()
    assert result["ready"] is False
    assert result["checks"][-1]["code"] == "docker_compose_version_unsupported"
    assert "Update the Compose plugin" in result["checks"][-1]["message"]


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
    assert [item[0] for item in services] == ["managed"]


def test_port_claimed_during_studio_start_never_opens_or_starts_another_service(tmp_path, services, monkeypatch):
    states = iter(["free", "occupied"])
    monkeypatch.setattr(setup, "_port_state", lambda *a: next(states))
    assert setup.run_setup(tmp_path) == 2
    assert not services


def test_failed_service_start_does_not_open_browser(tmp_path, services, monkeypatch):
    monkeypatch.setattr(setup.service, "install", lambda *a, **kw: {**MANAGED, "ready": False})
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
    line = setup._cli()
    executable = str(console if console_available else python).replace("'", "''")
    assert line.startswith("& '" + executable + "'")
    assert line.endswith("'") if console_available else line.endswith(" -m genlayer_agent_lab.cli")


def test_failed_studio_inspection_is_not_treated_as_permission_to_rebuild(tmp_path, services, monkeypatch, capsys):
    monkeypatch.setattr(setup.studio_profiles, "modern_profile_status", lambda _: {
        "installed": True, "ready": False, "error": "modern_runtime_inspection_failed"})
    target = tmp_path / "new"
    assert setup.run_setup(target) == 2
    assert not target.exists() and not services
    assert "could not be inspected safely" in capsys.readouterr().out
