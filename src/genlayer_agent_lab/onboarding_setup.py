"""Guided local setup using the existing owned project Studio and Lab service.

Prerequisite checks are read-only. Setup never installs system software, changes
the user's Docker context, resets an installation, or prints a credential.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import platform
import re
import secrets
import shlex
import shutil
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path
from urllib.parse import quote

import httpx

from . import __version__
from .runtime import container, studio_profiles

DOCKER_ENGINE_URL = "https://docs.docker.com/engine/install/"
DOCKER_DESKTOP_URL = "https://docs.docker.com/desktop/setup/install/"
COMPOSE_URL = "https://docs.docker.com/compose/install/linux/"
GIT_URL = "https://git-scm.com/downloads"
SETUP_SKILL_URL = "https://github.com/Leokings/genlayer-agent-lab/blob/main/skills/setup-genlayer-agent-lab/SKILL.md"
SETUP_PAGE_URL = "https://genlayer-agent-lab-setup.vercel.app/"


def installation_identity(data_dir, token):
    """Public fingerprint distinguishing installations without sending a bearer."""
    identity = "genlayer-agent-lab\0" + os.path.normcase(str(Path(data_dir).expanduser().resolve())) + "\0" + token
    return hashlib.sha256(identity.encode()).hexdigest()


def installation_proof(token, nonce):
    """Answer a fresh setup challenge without exposing the installation token."""
    if type(nonce) is not str or re.fullmatch(r"[0-9a-f]{64}", nonce) is None:
        raise ValueError("Setup nonce must contain exactly 64 lowercase hexadecimal characters")
    message = b"genlayer-agent-lab/setup/v1\0" + nonce.encode("ascii")
    return hmac.new(token.encode("utf-8"), message, hashlib.sha256).hexdigest()


def _say(message):
    print(message, flush=True)


def _prerequisite_handoff():
    _say("Your setup agent can install missing prerequisites and resume this installation.")
    _say("Give it the setup instructions: " + SETUP_SKILL_URL)
    _say("On supported Ubuntu, those instructions use bootstrap-ubuntu.sh --install; "
         "a required password or new login is an explicit resume step.")


def prerequisites():
    """Inspect the selected local Docker daemon and Compose with bounded commands."""
    checks = []
    git_available = shutil.which("git") is not None
    if shutil.which("docker") is None:
        url = DOCKER_ENGINE_URL if platform.system() == "Linux" else DOCKER_DESKTOP_URL
        checks.append({"ready": False, "message": "Docker is not installed or is absent from PATH. "
                       f"Install Docker {'Engine' if platform.system() == 'Linux' else 'Desktop'}: {url}"})
        return {"ready": False, "checks": checks, "git_available": git_available}
    try:
        endpoint = container._endpoint(timeout=8)
    except (RuntimeError, OSError, ValueError):
        checks.append({"ready": False, "message": "Select a local Docker context with a Unix socket or "
                       "Windows named pipe. Inspect it with `docker context ls`; remote daemons are unsupported."})
        return {"ready": False, "checks": checks, "git_available": git_available}
    try:
        info = container._linux_info(endpoint)
        architecture = info.get("Architecture")
        ready = architecture in {"x86_64", "amd64"}
        checks.append({"ready": ready, "message": "Docker is running Linux x86-64 containers." if ready else
                       "This pinned Studio runtime requires a Linux x86-64 Docker engine. "
                       "Use an x86-64 host; an ARM or Windows-container daemon is unsupported."})
    except (RuntimeError, OSError, ValueError):
        checks.append({"ready": False, "message": "Docker's Linux engine is unavailable to this user. "
                       "Start Docker, select Linux containers, and run `docker info`. On Linux, check "
                       f"daemon access using the Docker Engine instructions: {DOCKER_ENGINE_URL}"})
    try:
        result = container._command(endpoint, ["compose", "version", "--short"], timeout=8)
        version = result.stdout.decode("utf-8", errors="replace").strip()
        match = re.match(r"v?(\d+)\.", version)
        ready = result.returncode == 0 and match is not None and int(match[1]) >= 2
    except (RuntimeError, OSError, ValueError):
        ready = False
    checks.append({"ready": ready, "message": "Docker Compose is available." if ready else
                   "Install the Docker Compose plugin, then check `docker compose version`: " + COMPOSE_URL})
    return {"ready": all(item["ready"] for item in checks), "checks": checks,
            "git_available": git_available}


def _port_state(data_dir, port):
    """Recognize only this installation; never send a token to an occupied port."""
    from .api import read_admin_token

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(.4)
        if probe.connect_ex(("127.0.0.1", port)) != 0:
            return "free"
    if not (data_dir / "admin.token").is_file() or (data_dir / "admin.token").is_symlink():
        return "occupied"
    token = read_admin_token(data_dir)
    expected = installation_identity(data_dir, token)
    nonce = secrets.token_hex(32)
    expected_proof = installation_proof(token, nonce)
    try:
        with httpx.Client(timeout=2, follow_redirects=False, trust_env=False) as client:
            with client.stream("GET", f"http://127.0.0.1:{port}/health", params={"setup_nonce": nonce}) as response:
                if response.status_code != 200:
                    return "occupied"
                chunks = bytearray()
                for chunk in response.iter_bytes():
                    chunks.extend(chunk)
                    if len(chunks) > 4096:
                        return "occupied"
                body = json.loads(chunks)
        proof = body.get("setup_proof") if type(body) is dict else None
        if (type(body) is dict and body.get("status") == "ok" and body.get("version") == __version__
                and body.get("installation_id") == expected and type(proof) is str
                and re.fullmatch(r"[0-9a-f]{64}", proof) is not None
                and hmac.compare_digest(proof, expected_proof)):
            return "same_installation"
    except (httpx.HTTPError, ValueError):
        pass
    return "occupied"


def _shell_word(value):
    return "'" + str(value).replace("'", "''") + "'" if os.name == "nt" else shlex.quote(str(value))


def _cli():
    command = Path(sys.executable).parent / ("gl-agent-lab.exe" if os.name == "nt" else "gl-agent-lab")
    invocation = (_shell_word(command) if command.is_file()
                  else _shell_word(sys.executable) + " -m genlayer_agent_lab.cli")
    return ("& " if os.name == "nt" else "") + invocation


def _connection_instructions(data_dir, port, *, headless):
    if headless:
        _say("Your Lab is running. Next: open your dashboard on your computer.")
        _say(f"Guided opening steps: {SETUP_PAGE_URL}?location=vps&lab_port={port}#open-dashboard")
        _say("Your setup agent will guide you through Termius or PowerShell on your laptop.")
        _say("It will give the exact fields or a ready-to-paste command, one step at a time. Reuse a working connection.")
    else:
        _say(f"Open your dashboard: http://127.0.0.1:{port}/")
    _say("In the dashboard, choose 'Connect this browser' and paste its sign-in request into your setup agent.")
    _say("Your setup agent approves the request; the browser signs in automatically. No workspace key needs copying.")


def _open_dashboard(data_dir, port, *, headless):
    if headless:
        _connection_instructions(data_dir, port, headless=True)
        return
    from .api import read_admin_token

    dashboard = f"http://127.0.0.1:{port}/assets/workflows.html"
    # A fragment never reaches HTTP logs. The dashboard consumes and removes it
    # before fetching data. Neither this URL nor browser exceptions are printed.
    secret_url = dashboard + "#token=" + quote(read_admin_token(data_dir), safe="")
    try:
        opened = webbrowser.open(secret_url, new=2)
    except Exception:
        opened = False
    if opened:
        _say(f"Dashboard opened: {dashboard}")
    else:
        _say("The browser could not open automatically.")
        _connection_instructions(data_dir, port, headless=False)


def _redact(text, token=None):
    secrets = [token] if token else []
    secrets.extend(value for key, value in os.environ.items() if value and len(value) >= 6
                   and re.search(r"(?:TOKEN|PASSWORD|SECRET|API_KEY|PRIVATE_KEY)", key, re.I))
    for secret in sorted(set(secrets), key=len, reverse=True):
        text = text.replace(secret, "[redacted]")
    text = re.sub(r"(?i)(bearer\s+)[^\s]+", r"\1[redacted]", text)
    text = re.sub(r"(?i)(https?://)[^/@\s]+:[^/@\s]+@", r"\1[redacted]@", text)
    return text


def _resume_command(data_dir, port, no_open):
    return (f"{_cli()} setup --data-dir {_shell_word(data_dir)} --port {port}"
            + (" --no-open" if no_open else ""))


def _failure(exc, data_dir, *, port=8765, no_open=False):
    from .api import read_admin_token

    try:
        token = read_admin_token(data_dir)
    except (OSError, RuntimeError):
        token = None
    _say("Setup could not complete: " + _redact(str(exc), token)[:1000])
    # Only the exception's current stage may supply output. An old successful
    # image build is unrelated evidence when a later Compose/startup step fails.
    from .runtime.studio_stack import StudioOperationFailure

    if isinstance(exc, StudioOperationFailure):
        tail = "\n".join(exc.diagnostic["output_tail"].splitlines()[-20:])
        if tail:
            _say("Recent output from the failed stage:\n" + _redact(tail, token))
    _say("Inspect the reported stage, then resume this installation; saved data and runtime cache are preserved:")
    _say("  " + _resume_command(data_dir, port, no_open))


def _serve(data_dir, port, on_ready):
    """Run the standard Lab app in the foreground; open UI only after startup."""
    import uvicorn

    from .api import create_app

    server = uvicorn.Server(uvicorn.Config(create_app(data_dir), host="127.0.0.1", port=port,
                                          access_log=False, server_header=False))
    stopped = threading.Event()

    def wait_for_start():
        deadline = time.monotonic() + 60
        while not stopped.wait(.1):
            if server.started:
                on_ready()
                return
            if time.monotonic() >= deadline:
                _say("The Lab is taking longer to start; inspect the service output before opening the dashboard.")
                return

    opener = threading.Thread(target=wait_for_start, daemon=True, name="lab-setup-browser")
    opener.start()
    try:
        server.run()
    except SystemExit:
        return False
    finally:
        stopped.set()
        opener.join(timeout=1)
    return server.started


def run_setup(data_dir, *, port=8765, no_open=False, check=False):
    """Check, initialize, reuse/start project Studio, and launch the foreground Lab."""
    from .api import initialize_data_dir

    data_dir = Path(data_dir).expanduser().resolve()
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError("Setup requires a Lab port from 1024 to 65535.")
    if (data_dir / "admin.token").is_symlink():
        raise ValueError("Setup cannot use a linked admin.token. Select an owned Lab data directory.")
    _say("Checking this machine for GenLayer Agent Lab...")
    _say(f"Installation: {data_dir}")
    required = prerequisites()
    for item in required["checks"]:
        _say(("Ready: " if item["ready"] else "Needed: ") + item["message"])
    if not required["ready"]:
        _prerequisite_handoff()
        return 2
    try:
        state = studio_profiles.modern_profile_status(data_dir)
        if state.get("error") and state["error"] != "modern_profile_build_required":
            raise RuntimeError("Project Studio could not be inspected safely. "
                               "Run `gl-agent-lab project studio-status` with this data directory for diagnostics.")
        studio_port = state.get("port", studio_profiles.DEFAULT_PORT)
        if port == studio_port:
            raise ValueError(f"Lab port {port} conflicts with this installation's Studio RPC. Choose another --port.")
        running = _port_state(data_dir, port)
        if running == "occupied":
            raise ValueError(f"Port {port} belongs to another service or an installation setup cannot identify. "
                             "Choose another --port, or inspect and stop that service yourself.")
        needs_build = not state.get("image_id") or state.get("error") == "modern_profile_build_required"
        if needs_build and not required["git_available"]:
            _say("Needed: Git is required for the first pinned Studio build. Install it from " + GIT_URL)
            _prerequisite_handoff()
            return 2
        if check:
            _say("Project Studio: " + ("ready" if state.get("ready") else "installed; startup needed"
                                       if state.get("installed") else "first build needed"))
            _say("Lab: " + ("already running for this installation" if running == "same_installation"
                            else f"port {port} is available"))
            _say("Prerequisites passed. Run setup without --check to open the Lab.")
            return 0
        studio_profiles.startup_timeout()
        initialize_data_dir(data_dir)
        headless = (no_open or bool(os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_TTY"))
                    or platform.system() == "Linux" and not any(
                        os.environ.get(key) for key in ("DISPLAY", "WAYLAND_DISPLAY")))
        if not state.get("ready"):
            _say("Setup runs in this foreground terminal. To return later, resume with:")
            _say("  " + _resume_command(data_dir, port, headless))
            if headless and platform.system() == "Linux":
                _say("For unattended SSH setup, start it inside tmux. Detach with Ctrl+B, then D; "
                     "reconnect with `tmux attach`. Closing a plain SSH terminal can interrupt setup.")
        if state.get("ready") is True and state.get("runtime_verified") is True:
            _say("Project Studio is already ready; continuing with this owned stack.")
        elif needs_build:
            _say("Building this installation's project Studio. The first build can take tens of minutes;")
            _say("downloads and image build have bounded deadlines. Progress will appear below.")
            state = studio_profiles.setup_modern_profile(data_dir, port=studio_port, progress=_say)
        else:
            _say("Starting this installation's existing project Studio services...")
            state = studio_profiles.start_profile(data_dir, progress=_say)
        if any(state.get(key) is not True for key in ("ready", "runtime_verified", "network_internal", "fixture_ready")):
            raise RuntimeError("Project Studio has not passed its readiness checks. "
                               "Run `gl-agent-lab project studio-status` with this data directory for diagnostics.")
        if state.get("validator_count", 0) == 0:
            raise RuntimeError("Project Studio has no configured validator cohort. "
                               "Inspect `project studio-status`, then resume setup after startup completes.")
        running = _port_state(data_dir, port)
        if running == "same_installation":
            _say("The Lab is already running for this installation.")
            _open_dashboard(data_dir, port, headless=headless)
            return 0
        if running == "occupied":
            raise RuntimeError(f"Port {port} became occupied while Studio started. Run setup with another --port.")
        _say(f"Starting the Lab at http://127.0.0.1:{port}. Keep this terminal open; Ctrl+C stops the Lab.")
        started = _serve(data_dir, port, lambda: _open_dashboard(data_dir, port, headless=headless))
        if not started:
            raise RuntimeError("The Lab did not finish starting. Inspect the service output above.")
        _say("The foreground Lab has stopped. Use `project studio-down` when you also want to stop Studio.")
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        _failure(exc, data_dir, port=port, no_open=no_open)
        return 2
