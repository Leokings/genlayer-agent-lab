"""Separately owned, fee-enabled Studio profile for extensible project runs.

Never upgrades or reuses the legacy Studio database. All execution services are
on an internal Docker network; only the fixed RPC relay publishes on loopback.
The upstream release is a prerelease, pinned by commit and GenVM release.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tarfile
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path

from . import studio_stack as legacy
from .container import _bounded_process, _endpoint, _json_output, _linux_info

PROFILE = "studio-v0123"
STUDIO_VERSION = "0.123.0-rc.6"
STUDIO_COMMIT = "6551995be232d093144f2c32b6775757a010ab3c"
GENVM_VERSION = "v0.6.0-rc3"
RUNNER_PINS = {
    "py-genlayer": "5jycge4q8k23462jtb0b9fyey1s9qz928sz2nbrd9mg4sxqg2qng",
    "py-genlayer-multi": "faykzar6hr5ehfm07jatm69erx6nv96wmz1ab1dszjthbcgre3m0",
    "py-lib-genlayer-std": "kzr02ndm9et4qkmbqpq5djjt5sme2yt76n7sz1qbzax0knt6mam0",
}
CHAIN_ID = 61127
FIXTURE_SUPPORT = "upstream-validator-config-v0123"
JSON_FIXTURE_WIRE = "genvm-v03-json-text-v1"
DEFAULT_PORT = 8796
STARTUP_TIMEOUT_SECONDS = 1800
STARTUP_TIMEOUT_ENV = "LAB_STUDIO_STARTUP_TIMEOUT_SECONDS"
SOURCE_PATHS = ["backend", "asgi.py", "uvicorn_config.py", "LICENSE", "docker",
                "third_party/genvm/version", "examples", "tests", ".e2e-genvm-prebuilt",
                ".genvm-nix-closure"]


def profile_root(data_dir: Path) -> Path:
    parent = Path(data_dir).resolve()
    root = parent / "studio-modern"
    if root.is_symlink():
        raise RuntimeError("Modern Studio directory must not be a symlink")
    return root


def _validate(state):
    if (type(state) is not dict or state.get("schema") != 1
            or state.get("profile") != PROFILE or state.get("source_commit") != STUDIO_COMMIT
            or state.get("studio_version") != STUDIO_VERSION
            or state.get("genvm_version") != GENVM_VERSION
            or type(state.get("owner")) is not str
            or re.fullmatch(r"[0-9a-f]{32}", state["owner"]) is None
            or type(state.get("port")) is not int or not 1024 <= state["port"] <= 65535
            or state.get("auxiliary_images") != legacy.AUXILIARY_IMAGES
            or state.get("fixture_config_patch") != FIXTURE_SUPPORT):
        raise RuntimeError("Invalid modern Studio profile metadata")
    if "image_id" in state and (type(state["image_id"]) is not str
            or re.fullmatch(r"sha256:[0-9a-f]{64}", state["image_id"]) is None):
        raise RuntimeError("Invalid modern Studio image identity")
    return state


def load_profile(data_dir: Path) -> dict:
    path = profile_root(data_dir) / "installation.json"
    if path.is_symlink() or path.stat().st_size > 16384:
        raise RuntimeError("Invalid modern Studio profile metadata")
    return _validate(json.loads(path.read_text(encoding="utf-8")))


def _save(root, state):
    _validate(state)
    pending = root / ("installation." + uuid.uuid4().hex + ".tmp")
    pending.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    pending.replace(root / "installation.json")


@contextmanager
def _lock(data_dir):
    from .studio_cohort import StudioFixtureLease
    with StudioFixtureLease(data_dir, subdirectory="studio-modern"):
        yield


def _initialize(data_dir, port):
    root = profile_root(data_dir)
    if (root / "installation.json").exists():
        state = load_profile(data_dir)
        if state["port"] != port:
            raise ValueError("Modern Studio already uses another port")
        return state
    if any(p.name != "fixture.lock" for p in root.iterdir()):
        raise RuntimeError("Modern Studio directory is nonempty without ownership metadata")
    state = {"schema": 1, "profile": PROFILE, "source_commit": STUDIO_COMMIT,
             "studio_version": STUDIO_VERSION, "genvm_version": GENVM_VERSION,
             "owner": uuid.uuid4().hex, "port": port,
             "auxiliary_images": dict(legacy.AUXILIARY_IMAGES),
             "fixture_config_patch": FIXTURE_SUPPORT}
    state["json_fixture_wire"] = JSON_FIXTURE_WIRE
    _validate(state)
    with (root / "installation.json").open("x", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2)
    return state


def compose_config(state):
    state = _validate(state)
    config = legacy.compose_services(state)
    for service in config["services"].values():
        env = service.get("environment", {})
        if "GENLAYER_CHAIN_ID" in env:
            env.update(GENLAYER_CHAIN_ID=str(CHAIN_ID),
                       GENLAYER_STUDIO_GEN_PER_TIME_UNIT="1",
                       GENLAYER_STUDIO_STORAGE_UNIT_PRICE="250000000",
                       GENLAYER_STUDIO_RECEIPT_GAS_PRICE="250000000")
    return config


def _archive(source, root, context):
    archive = root / ("source." + uuid.uuid4().hex + ".tar")
    try:
        legacy._checked(_bounded_process(["git", "-c", "core.autocrlf=false", "-C", str(source),
            "archive", "--format=tar", f"--output={archive}", STUDIO_COMMIT,
            *SOURCE_PATHS], timeout=45), "modern source archive")
        if archive.stat().st_size > legacy.MAX_ARCHIVE_BYTES:
            raise RuntimeError("Modern Studio source archive too large")
        with tarfile.open(archive) as src:
            members = src.getmembers()
            if len(members) > 20000 or sum(m.size for m in members) > legacy.MAX_SOURCE_BYTES:
                raise RuntimeError("Modern Studio source archive exceeds limits")
            for member in members:
                name = member.name.rstrip("/")
                parts = name.split("/")
                if (not name or "\\" in name or ":" in name
                        or any(p in {"", ".", ".."} for p in parts)
                        or not (member.isfile() or member.isdir())):
                    raise RuntimeError("Unsafe modern Studio source archive")
            src.extractall(context, members=members, filter="data")
    finally:
        archive.unlink(missing_ok=True)


def _json_fixture_patch(source: str) -> str:
    # This pinned Studio prerelease returns Lua tables for JSON fixtures, while
    # its GenVM v0.3 decoder requires JSON text (as the real provider returns).
    # Adapt only that serialization boundary; never alter decisions or votes.
    start = '\t\t\tif mapped_prompt.format == "json" then\n'
    end = '\n\t\t\telse\n\t\t\t\t-- For text format, convert tables to JSON string'
    if source.count(start) != 1 or source.count(end) != 1:
        raise RuntimeError("Pinned JSON fixture adapter source mismatch")
    before, remaining = source.split(start, 1)
    old, after = remaining.split(end, 1)
    if 'data_value = mock_data.data' not in old or 'lib.rs.json_parse(mock_data.data)' not in old:
        raise RuntimeError("Pinned JSON fixture adapter source mismatch")
    return (before + start + '\t\t\t\t-- Lab: GenVM v0.3 expects JSON text.\n'
            + '\t\t\t\tdata_value = lib.rs.json_stringify(mock_data.data)' + end + after)


def build_profile(data_dir: Path, *, port=DEFAULT_PORT, checkout=None, progress=None):
    with _lock(data_dir):
        state = _initialize(data_dir, port)
        root = profile_root(data_dir)
        endpoint = _endpoint()
        _linux_info(endpoint)
        source = Path(checkout).resolve() if checkout else root / "source"
        if not source.exists():
            if progress:
                progress("Downloading pinned fee-enabled Studio source...")
            legacy._run_stage(root, "source_download", lambda: _bounded_process([
                "git", "clone", "--depth", "1", "--branch", "v" + STUDIO_VERSION,
                legacy.STUDIO_REPOSITORY, str(source)], timeout=300), timeout=300,
                progress=progress, description="download pinned Studio source")
        actual = legacy._checked(_bounded_process(["git", "-C", str(source), "rev-parse", "HEAD"]),
                                  "modern source identity").stdout.decode().strip()
        if actual != STUDIO_COMMIT:
            raise RuntimeError("Modern Studio source commit mismatch")
        tag = f"genlayer-agent-lab-studio:{STUDIO_VERSION}-{state['owner']}"
        with tempfile.TemporaryDirectory(prefix="build-", dir=root) as directory:
            context = Path(directory)
            _archive(source, root, context)
            lua = (context / "backend/node/llm.lua").read_text(encoding="utf-8")
            (context / "lab-llm.lua").write_text(_json_fixture_patch(lua), encoding="utf-8")
            if (context / "third_party/genvm/version").read_text().strip() != GENVM_VERSION:
                raise RuntimeError("Modern GenVM pin mismatch")
            with (context / "docker/Dockerfile.backend").open("a", encoding="utf-8") as handle:
                handle.write("\nCOPY LICENSE /app/GENLAYER_STUDIO_LICENSE\n")
                for name, pin in RUNNER_PINS.items():
                    handle.write(f"RUN test -f /genvm/runners/{name}/{pin[:2]}/{pin[2:]}.zip\n")
                handle.write("COPY lab-llm.lua /app/backend/node/llm.lua\n")
            if progress:
                progress("Building fee-enabled Studio and downloading the pinned GenVM release...")
            legacy._run_stage(root, "build_image", lambda: legacy._command(endpoint,
                ["build", "--target", "prod", "--label",
                f"{legacy.COMMIT_LABEL}={STUDIO_COMMIT}", "--label",
                f"{legacy.PATCH_LABEL}={FIXTURE_SUPPORT}", "--label",
                f"{legacy.OWNER_LABEL}={state['owner']}", "--tag", tag,
                "--file", str(context / "docker/Dockerfile.backend"), str(context)],
                timeout=1800, output_limit=8_388_608), timeout=1800, progress=progress,
                description="build image and download pinned GenVM")
        image = _json_output(legacy._command(endpoint,
            ["image", "inspect", tag, "--format", "{{json .}}"]), "modern image inspection")
        labels = image.get("Config", {}).get("Labels", {})
        if labels.get(legacy.COMMIT_LABEL) != STUDIO_COMMIT:
            raise RuntimeError("Modern Studio image source mismatch")
        state["image_id"] = image["Id"]
        state["json_fixture_wire"] = JSON_FIXTURE_WIRE
        _save(root, state)
    return modern_profile_status(data_dir)


def _compose(data_dir, args, *, timeout=30, progress=None):
    state = load_profile(data_dir)
    root = profile_root(data_dir)
    path = root / "compose.generated.json"
    empty = root / "empty.env"
    if path.is_symlink() or empty.is_symlink():
        raise RuntimeError("Invalid generated modern Studio configuration")
    path.write_text(json.dumps(compose_config(state), indent=2).replace("$", "$$"), encoding="utf-8")
    empty.write_text("", encoding="utf-8")
    stage = "compose_up" if args[0] == "up" else "compose_down"
    description = ("prepare runtime cache and start services" if stage == "compose_up"
                   else "stop owned services")
    return legacy._run_stage(root, stage, lambda: legacy._command(_endpoint(),
        ["compose", "--env-file", str(empty),
        "--project-directory", str(root), "--project-name", "gl-agent-lab-" + state["owner"],
        "--file", str(path), *args], timeout=timeout, output_limit=8_388_608),
        timeout=timeout, progress=progress, description=description)


def startup_timeout():
    value = os.environ.get(STARTUP_TIMEOUT_ENV, str(STARTUP_TIMEOUT_SECONDS))
    if not re.fullmatch(r"[0-9]{1,4}", value) or not 60 <= int(value) <= 3600:
        raise ValueError(f"{STARTUP_TIMEOUT_ENV} must be a whole number from 60 to 3600 seconds.")
    return int(value)


def start_profile(data_dir, *, progress=None):
    wait_seconds = startup_timeout()
    with _lock(data_dir):
        state = load_profile(data_dir)
        legacy._assert_owned(legacy._inventory(_endpoint(), state), state,
                             config=compose_config(state))
        if progress:
            progress(f"Cold preparation and service readiness have a {wait_seconds}s wait budget. "
                     "Completed precompilation is reused; keep this setup running while it prepares.")
        _compose(data_dir, ["up", "--detach", "--wait", "--wait-timeout", str(wait_seconds)],
                 timeout=wait_seconds + 30, progress=progress)
    if progress:
        progress("Studio stage: verify owned runtime, RPC and validator cohort.")
    return modern_profile_status(data_dir)


def stop_profile(data_dir):
    with _lock(data_dir):
        state = load_profile(data_dir)
        legacy._assert_owned(legacy._inventory(_endpoint(), state), state,
                             config=compose_config(state))
        _compose(data_dir, ["down", "--timeout", "15"], timeout=45)
    return {"stopped": True, "data_preserved": True}


def modern_profile_status(data_dir):
    if not (profile_root(data_dir) / "installation.json").exists():
        return {"installed": False, "ready": False, "profile": PROFILE}
    state = load_profile(data_dir)
    result = {**state, "installed": True, "ready": False, "runtime_verified": False,
              "endpoint": f"http://127.0.0.1:{state['port']}",
              "project": "gl-agent-lab-" + state["owner"], "fixture_only": False,
              "fixture_ready": False,
              "public_chain": False, "bond_accounting": False, "network_internal": False}
    if not state.get("image_id") or state.get("json_fixture_wire") != JSON_FIXTURE_WIRE:
        result["error"] = "modern_profile_build_required"
        return result
    try:
        endpoint = _endpoint()
        inventory = legacy._inventory(endpoint, state)
        config = compose_config(state)
        result["configuration_sha256"] = hashlib.sha256(
            json.dumps(config, sort_keys=True).encode()).hexdigest()
        result["network_internal"] = (inventory["network"] or {}).get("Internal") is True
        diagnostic = {}
        result["runtime_verified"] = legacy._verify_runtime(endpoint, state, inventory,
            config=config, source_commit=STUDIO_COMMIT, diagnostic=diagnostic)
        if not result["runtime_verified"]:
            result["verification_failure"] = diagnostic
            return result
        from .studio_modern import StudioModernClient
        with StudioModernClient(result["endpoint"], timeout=10) as client:
            result["rpc"] = client.doctor()
            if result["rpc"].get("ready"):
                from .studio import StudioError
                from .studio_cohort import _records
                try:
                    with client._operation():
                        validators = _records(client._rpc("sim_getAllValidators", []))
                    result.update(fixture_ready=True, fixture_only=True,
                                  validator_count=len(validators))
                except StudioError:
                    result["fixture_error"] = "invalid_fixture_cohort"
        result["ready"] = result["rpc"].get("ready") is True
        result["bond_accounting"] = result["ready"] and result["rpc"].get("bond_accounting") is True
    except (RuntimeError, OSError, ValueError, TypeError, KeyError):
        result["error"] = "modern_runtime_inspection_failed"
    return result


def setup_modern_profile(data_dir, *, port=DEFAULT_PORT, progress=None, checkout=None):
    startup_timeout()  # Reject an invalid wait budget before downloading or building.
    build_profile(data_dir, port=port, progress=progress, checkout=checkout)
    result = start_profile(data_dir, progress=progress)
    if result.get("ready"):
        from .studio_fixtures import validator_config
        from .studio_modern import StudioModernClient
        with _lock(data_dir), StudioModernClient(result["endpoint"], timeout=120) as client:
            with client._operation():
                existing = client._rpc("sim_getAllValidators", [])
                if type(existing) is not list or len(existing) > 64:
                    raise RuntimeError("Invalid modern Studio validator inventory")
                if not existing:
                    fixture = validator_config(prompts={"AGENT_LAB_PROJECT_V1": {"decision": "approve"}})
                    fixture.pop("amount")
                    for _ in range(12):
                        client._rpc("sim_createValidator", fixture)
                else:
                    from .studio_cohort import _records
                    _records(existing)  # Refuse to repair a changed or foreign cohort.
    return modern_profile_status(data_dir)
