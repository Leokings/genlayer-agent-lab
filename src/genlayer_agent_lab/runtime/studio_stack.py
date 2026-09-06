"""Explicitly managed, local-only Studio dependency with an owned Compose project.

This is a fixture-only development stack. Its RPC has no authentication and is
published on loopback only; developers must not expose it through a proxy.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tarfile
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from .container import _bounded_process, _endpoint, _json_output, _linux_info

STUDIO_VERSION = "0.121.6"
STUDIO_COMMIT = "366f085a479bb9e6028ce326c2c13f798a9752c7"
STUDIO_REPOSITORY = "https://github.com/genlayerlabs/genlayer-studio.git"
IMAGE_TAG = f"genlayer-agent-lab-studio:{STUDIO_VERSION}"
COMMIT_LABEL = "io.genlayer.agent-lab.studio.commit"
OWNER_LABEL = "io.genlayer.agent-lab.studio.owner"
PROJECT_LABEL = "com.docker.compose.project"
SERVICE_LABEL = "com.docker.compose.service"
# Docker Hub manifest-list digests resolved 2026-09-06 through registry-1.docker.io.
# These pin auxiliary image contents. Upstream Ubuntu/apt and GenVM release URLs
# still prevent a claim of fully reproducible backend builds.
AUXILIARY_IMAGES = {
    "postgres": "postgres:16-alpine@sha256:cf78e76683b9ca8c5733cbbdce6c9262b45b6767934dd0a95e671f9a0fc20685",
    "redis": "redis:8-alpine@sha256:becdda6c7f4b3fb42e42fd7f120bbf5c54c4caaaf16f26da24e4563d2c1f0576",
}
SOURCE_PATHS = ["backend", "asgi.py", "uvicorn_config.py", "LICENSE",
                "docker/Dockerfile.backend", "docker/entrypoint-backend.sh"]
MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_SOURCE_BYTES = 256 * 1024 * 1024


def _command(endpoint, args, **kwargs):
    # Compose also consumes COMPOSE_FILE, COMPOSE_ENV_FILES, profiles and other
    # process variables even when an explicit generated file is supplied.
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith("COMPOSE_")
           and key.upper() not in {"DOCKER_CONTEXT", "DOCKER_HOST", "DOCKER_DEFAULT_PLATFORM"}}
    env["COMPOSE_DISABLE_ENV_FILE"] = "1"
    command = ["docker", "--host", endpoint, *args]
    return _bounded_process(command, env=env, **kwargs)


def _checked(result, stage):
    if result.returncode:
        raise RuntimeError(f"Studio {stage} failed (exit {result.returncode})")
    return result


def _load(root: Path) -> dict:
    path = root / "installation.json"
    if path.is_symlink() or path.stat().st_size > 16384:
        raise RuntimeError("Invalid Studio installation metadata")
    return _validated_state(json.loads(path.read_text(encoding="utf-8")))


def _validated_state(data: dict) -> dict:
    if (type(data) is not dict or data.get("schema") != 1
            or data.get("source_commit") != STUDIO_COMMIT
            or data.get("studio_version") != STUDIO_VERSION
            or type(data.get("owner")) is not str
            or not re.fullmatch(r"[0-9a-f]{32}", data.get("owner", ""))
            or type(data.get("port")) is not int or not 1024 <= data["port"] <= 65535):
        raise RuntimeError("Invalid Studio installation metadata")
    if "image_id" in data and (type(data["image_id"]) is not str
                               or not re.fullmatch(r"sha256:[0-9a-f]{64}", data["image_id"])):
        raise RuntimeError("Invalid Studio image identity")
    if data.get("auxiliary_images", AUXILIARY_IMAGES) != AUXILIARY_IMAGES:
        raise RuntimeError("Studio auxiliary image pins do not match this release")
    return {**data, "auxiliary_images": dict(AUXILIARY_IMAGES)}


def _save(root: Path, data: dict) -> None:
    data = _validated_state(data)
    pending = root / f"installation.{uuid.uuid4().hex}.tmp"
    pending.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    pending.replace(root / "installation.json")


def _root(data_dir: Path) -> Path:
    root = Path(data_dir).resolve() / "studio"
    if root.is_symlink():
        raise RuntimeError("Studio installation directory must not be a symlink")
    return root


@contextmanager
def _operation_lock(data_dir: Path):
    """Serialize only this installation's lifecycle, without taking the Lab DB lock."""
    root = _root(data_dir)
    root.mkdir(parents=True, exist_ok=True)
    path = root / "operation.lock"
    if path.is_symlink():
        raise RuntimeError("Studio lock must not be a symlink")
    handle = path.open("a+b")
    try:
        handle.seek(0, 2)
        if handle.tell() == 0:
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
        except OSError as exc:
            raise RuntimeError("Another Studio lifecycle operation is using this installation") from exc
        yield
    finally:
        handle.close()


def initialize(data_dir: Path, port: int = 8766) -> dict:
    with _operation_lock(data_dir):
        return _initialize(data_dir, port)


def _initialize(data_dir: Path, port: int = 8766) -> dict:
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError("Studio port must be between 1024 and 65535")
    root = _root(data_dir)
    root.mkdir(parents=True, exist_ok=True)
    if (root / "installation.json").exists():
        state = _load(root)
        if state["port"] != port:
            raise ValueError("Studio is already initialized on another port")
        return state
    if any(path.name != "operation.lock" for path in root.iterdir()):
        raise RuntimeError("Studio directory is nonempty and has no ownership record")
    state = {"schema": 1, "owner": uuid.uuid4().hex, "port": port,
             "source_commit": STUDIO_COMMIT, "studio_version": STUDIO_VERSION,
             "auxiliary_images": dict(AUXILIARY_IMAGES)}
    # Exclusive creation prevents two installers from assigning different owners.
    with (root / "installation.json").open("x", encoding="utf-8") as out:
        json.dump(state, out, indent=2)
    (root / "empty.env").write_text("", encoding="utf-8")
    return state


def build(data_dir: Path, *, port: int = 8766, progress=None,
          checkout: Path | None = None) -> dict:
    """Fetch the pinned upstream dependency; archive committed allowlisted files only."""
    with _operation_lock(data_dir):
        _build(data_dir, port=port, progress=progress, checkout=checkout)
    # The mutation lock is released before the HTTP readiness probe.
    return status(data_dir)


def _extract_source(archive: Path, context: Path) -> None:
    if archive.stat().st_size > MAX_ARCHIVE_BYTES:
        raise RuntimeError("Studio source archive exceeds its size limit")
    with tarfile.open(archive) as src:
        total = 0
        members = []
        seen = set()
        for member in src:
            name = member.name.rstrip("/")
            parts = name.split("/")
            allowed = any(name == item or name.startswith(item + "/")
                          or (member.isdir() and item.startswith(name + "/"))
                          for item in SOURCE_PATHS)
            if (not allowed or not name or "\\" in name or ":" in name
                    or any(part in {"", ".", ".."} for part in parts)
                    or not (member.isfile() or member.isdir())
                    or name.casefold() in seen):
                raise RuntimeError("Studio source archive contains an unsafe or unexpected entry")
            total += member.size
            if total > MAX_SOURCE_BYTES or len(members) >= 20000:
                raise RuntimeError("Studio source archive exceeds its structural limit")
            seen.add(name.casefold())
            members.append(member)
        src.extractall(context, members=members, filter="data")


def _build(data_dir: Path, *, port: int, progress, checkout: Path | None) -> None:
    state = _initialize(data_dir, port)
    root = _root(data_dir)
    endpoint = _endpoint()
    _linux_info(endpoint)
    if progress:
        progress("Fetching the pinned Studio dependency...")
    source = Path(checkout).resolve() if checkout else root / "source"
    if not source.exists():
        _checked(_bounded_process([
            "git", "clone", "--depth", "1", "--branch", f"v{STUDIO_VERSION}",
            STUDIO_REPOSITORY, str(source)], timeout=300), "source download")
    actual = _checked(_bounded_process(
        ["git", "-C", str(source), "rev-parse", "HEAD"]), "source identity").stdout.decode().strip()
    if actual != STUDIO_COMMIT:
        raise RuntimeError("Studio source commit does not match the pinned release")
    with tempfile.TemporaryDirectory(prefix="studio-build-", dir=root) as directory:
        scratch = Path(directory)
        archive = scratch / "source.tar"
        # git archive otherwise honors the host's autocrlf and can turn Linux
        # entrypoint shebangs into /bin/bash\r on Windows. Keep committed LF bytes.
        _checked(_bounded_process(["git", "-c", "core.autocrlf=false", "-C", str(source), "archive", "--format=tar",
                 f"--output={archive}", STUDIO_COMMIT, *SOURCE_PATHS], timeout=30), "source archive")
        context = scratch / "context"
        context.mkdir()
        _extract_source(archive, context)
        # Packaging-only overlay on the temporary committed build context. Never
        # edit the checkout or vendored application code; retain upstream's notice.
        dockerfile = context / "docker/Dockerfile.backend"
        with dockerfile.open("a", encoding="utf-8") as out:
            out.write("\n# Agent Lab packaging: preserve the upstream MIT license notice.\n"
                      "COPY LICENSE /app/GENLAYER_STUDIO_LICENSE\n")
        if progress:
            progress("Building Studio and downloading GenVM (first build can take several minutes)...")
        _checked(_command(endpoint, ["build", "--quiet", "--target", "prod", "--label",
            f"{COMMIT_LABEL}={STUDIO_COMMIT}", "--tag", f"{IMAGE_TAG}-{state['owner']}", "--file",
            str(context / "docker/Dockerfile.backend"), str(context)],
            timeout=1200, output_limit=8_388_608), "image build")
    image = _json_output(_command(endpoint, ["image", "inspect", f"{IMAGE_TAG}-{state['owner']}",
                         "--format", "{{json .}}"]), "Studio image inspection")
    if (image.get("Config", {}).get("Labels", {}).get(COMMIT_LABEL) != STUDIO_COMMIT
            or image.get("Os") != "linux"):
        raise RuntimeError("Studio image source label is missing")
    state["image_id"] = image["Id"]
    state["packaging_overlay"] = "upstream-license-notice-v1"
    _save(root, state)


def compose_config(state: dict) -> dict:
    from .studio_fixtures import validator_config

    state = _validated_state(state)
    image = state.get("image_id")
    if not image:
        raise RuntimeError("Build Studio before starting it")
    relay_source = Path(__file__).with_name("studio_relay.py").read_text(encoding="utf-8")
    owner = state["owner"]
    env = {
        "DBHOST": "postgres", "DBPORT": "5432", "DBNAME": "genlayer_state",
        "DBUSER": "postgres", "DBPASSWORD": "fixture-only",
        "REDIS_URL": "redis://redis:6379/0", "LOG_LEVEL": "INFO", "LOGCONFIG": "dev",
        "PYTHONPATH": "/app",
        "GENVMROOT": "/genvm", "GENVM_BIN": "/genvm/bin", "GENLAYER_CHAIN_ID": "61999",
        "RPCPORT": "4000", "WORKER_PORT": "4001", "WEB_CONCURRENCY": "1",
        "WORKER_ID": "lab-worker", "MAX_PARALLEL_TXS_PER_WORKER": "1",
        "VITE_FINALITY_WINDOW": "30", "DEFAULT_NUM_INITIAL_VALIDATORS": "5",
        "DEFAULT_CONSENSUS_MAX_ROTATIONS": "3", "VITE_MAX_ROTATIONS": "3",
        "VITE_FINALITY_WINDOW_APPEAL_FAILED_REDUCTION": "0.2",
        "LAB_STUDIO_FIXTURE_KEY": "fixture-only", "WEBDRIVERHOST": "127.0.0.1",
        "WEBDRIVERPORT": "9", "HARDHAT_URL": "http://127.0.0.1", "HARDHAT_PORT": "9",
        "CONSENSUS_CONTRACT_ADDRESS": "0x0000000000000000000000000000000000000000",
        "USAGE_METRICS_API_URL": "", "USAGE_METRICS_API_KEY": "",
        "VALIDATORS_CONFIG_JSON": json.dumps([validator_config(prompts={
            "AGENT_LAB_EVIDENCE_V1": {"verdict": "approve"},
            "DELIVERY_ASSESSMENT_V1": {"decision": "approve"},
        })]),
    }
    common = {"image": image, "pull_policy": "never", "environment": env,
              "init": True, "restart": "no", "networks": ["isolated"],
              "cap_drop": ["ALL"], "security_opt": ["no-new-privileges:true"],
              "pids_limit": 256, "labels": {OWNER_LABEL: owner},
              "logging": {"driver": "json-file", "options": {"max-size": "5m", "max-file": "2"}}}
    db_dep = {"postgres": {"condition": "service_healthy"}}
    app_dep = {"migration": {"condition": "service_completed_successfully"},
               "precompile": {"condition": "service_completed_successfully"},
               "redis": {"condition": "service_healthy"}}
    config = {
        "name": f"gl-agent-lab-{owner}",
        "services": {
            "postgres": {"image": AUXILIARY_IMAGES["postgres"], "environment": {
                "POSTGRES_DB": "genlayer_state", "POSTGRES_USER": "postgres",
                "POSTGRES_PASSWORD": "fixture-only"}, "networks": ["isolated"],
                "volumes": ["database:/var/lib/postgresql/data"], "mem_limit": "384m",
                "labels": {OWNER_LABEL: owner}, "healthcheck": {"test": ["CMD-SHELL",
                    "pg_isready -U postgres -d genlayer_state"], "interval": "2s", "retries": 30}},
            "redis": {"image": AUXILIARY_IMAGES["redis"], "networks": ["isolated"],
                "command": ["redis-server", "--save", "", "--appendonly", "no"],
                "tmpfs": ["/data:rw,noexec,nosuid,size=64m"],
                "labels": {OWNER_LABEL: owner}, "mem_limit": "128m", "healthcheck": {
                    "test": ["CMD", "redis-cli", "ping"], "interval": "2s", "retries": 30}},
            "migration": {**common, "entrypoint": ["alembic"],
                "command": ["upgrade", "head"], "working_dir": "/app/backend/database_handler",
                "depends_on": db_dep, "mem_limit": "512m", "healthcheck": {"disable": True}},
            "precompile": {**common, "entrypoint": ["/entrypoint.sh"],
                "command": ["true"],
                "volumes": ["vm-cache:/genvm-cache"], "mem_limit": "3g",
                "healthcheck": {"disable": True}},
            "jsonrpc": {**common, "entrypoint": ["python3", "-m", "backend.protocol_rpc.run_server"],
                "command": [], "depends_on": app_dep, "volumes": ["vm-cache:/genvm-cache"],
                "mem_limit": "1536m", "cpus": 1.5},
            "worker": {**common, "entrypoint": ["python3", "-m", "backend.consensus.run_worker"],
                "command": [], "depends_on": {**app_dep, "jsonrpc": {"condition": "service_healthy"}},
                "volumes": ["vm-cache:/genvm-cache"], "mem_limit": "3g", "cpus": 2,
                "healthcheck": {"test": ["CMD", "python3", "-c",
                    "import urllib.request; urllib.request.urlopen('http://127.0.0.1:4001/health')"],
                    "interval": "5s", "timeout": "3s", "start_period": "120s", "retries": 20}},
            # Docker does not publish ports on an internal-only network. The
            # fixed-target relay is the sole member of the separate access bridge.
            # Core GenVM/DB services retain no direct external network attachment.
            "relay": {**common, "entrypoint": ["python3", "-c", relay_source], "command": [],
                "environment": {"PYTHONPATH": "/app"}, "networks": ["isolated", "access"],
                "depends_on": {"jsonrpc": {"condition": "service_healthy"}},
                "ports": [f"127.0.0.1:{state['port']}:4002"], "read_only": True,
                "tmpfs": ["/tmp:rw,noexec,nosuid,size=64m"], "pids_limit": 32,
                "mem_limit": "128m", "cpus": 0.5,
                "healthcheck": {"test": ["CMD", "python3", "-c",
                    "import urllib.request; urllib.request.urlopen('http://127.0.0.1:4002/health', timeout=2)"],
                    "interval": "5s", "timeout": "3s", "start_period": "15s", "retries": 10}},
        },
        "networks": {"isolated": {"internal": True, "labels": {OWNER_LABEL: owner}},
                     "access": {"internal": False, "labels": {OWNER_LABEL: owner}}},
        "volumes": {name: {"labels": {OWNER_LABEL: owner}} for name in ["database", "vm-cache"]},
    }
    for service in config["services"].values():
        service["memswap_limit"] = service["mem_limit"]
        service.setdefault("cpus", 1)
        service.setdefault("pids_limit", 256)
        service.setdefault("security_opt", ["no-new-privileges:true"])
        service.setdefault("restart", "no")
        service.setdefault("logging", common["logging"])
    return config


def _compose(data_dir: Path, args: list[str], *, timeout=30, endpoint=None):
    root = _root(data_dir)
    state = _load(root)
    # Regenerate from validated metadata; never execute an arbitrary edited compose file.
    payload = json.dumps(compose_config(state), indent=2).replace("$", "$$")
    path = root / "compose.generated.json"
    if path.is_symlink() or (root / "empty.env").is_symlink():
        raise RuntimeError("Generated Studio configuration must not be a symlink")
    path.write_text(payload, encoding="utf-8")
    (root / "empty.env").write_text("", encoding="utf-8")
    return _checked(_command(endpoint or _endpoint(), ["compose", "--env-file", str(root / "empty.env"),
        "--project-directory", str(root), "--project-name", f"gl-agent-lab-{state['owner']}",
        "--file", str(path), *args], timeout=timeout, output_limit=8_388_608), "compose operation")


def up(data_dir: Path) -> dict:
    with _operation_lock(data_dir):
        state = _load(_root(data_dir))
        _assert_owned(_inventory(_endpoint(), state), state)
        _compose(data_dir, ["up", "--detach", "--wait", "--wait-timeout", "300"], timeout=330)
    return status(data_dir)


def down(data_dir: Path) -> dict:
    """Stop this installation's services and network; preserve database and VM cache."""
    with _operation_lock(data_dir):
        state = _load(_root(data_dir))
        _assert_owned(_inventory(_endpoint(), state), state)
        _compose(data_dir, ["down", "--timeout", "15"], timeout=45)
    return {"stopped": True, "data_preserved": True}


def _inventory(endpoint: str, state: dict, *, deadline=None) -> dict:
    """Read actual resources by Compose project, including foreign-owner conflicts."""
    project = f"gl-agent-lab-{state['owner']}"
    deadline = min(time.monotonic() + 25, deadline) if deadline is not None else time.monotonic() + 25

    def command(args):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("Studio resource inspection timed out")
        return _command(endpoint, args, timeout=min(5, remaining), output_limit=1_048_576)

    rows = _checked(command(["ps", "--all", "--filter", f"label={PROJECT_LABEL}={project}",
                             "--format", "{{json .}}"]), "container status")
    listed = [json.loads(line) for line in rows.stdout.decode().splitlines() if line.strip()]
    if len(listed) > 7:
        raise RuntimeError("Unexpected number of Studio project containers")
    containers = []
    for row in listed:
        identifier = row.get("ID", "")
        if not re.fullmatch(r"[0-9a-f]{12,64}", identifier):
            raise RuntimeError("Invalid Studio container identity")
        containers.append(_json_output(command(
            ["inspect", identifier, "--format", "{{json .}}"]), "container inspection"))

    def optional(kind, name):
        result = command([kind, "inspect", name])
        if result.returncode:
            return None
        decoded = json.loads(result.stdout)
        if type(decoded) is not list or len(decoded) != 1 or type(decoded[0]) is not dict:
            raise RuntimeError("Invalid Studio resource metadata")
        return decoded[0]

    return {"containers": containers, "network": optional("network", project + "_isolated"),
            "access_network": optional("network", project + "_access"),
            "volumes": {name: optional("volume", project + "_" + name)
                        for name in ("database", "vm-cache")}}


def _assert_owned(inventory: dict, state: dict) -> None:
    project = f"gl-agent-lab-{state['owner']}"
    services = set(compose_config(state)["services"])
    seen = set()
    for container in inventory["containers"]:
        labels = container.get("Config", {}).get("Labels") or {}
        service = labels.get(SERVICE_LABEL)
        if (labels.get(OWNER_LABEL) != state["owner"] or labels.get(PROJECT_LABEL) != project
                or service not in services or service in seen):
            raise RuntimeError("Studio project contains a foreign or duplicate container")
        seen.add(service)
    resources = [inventory["network"], inventory.get("access_network"),
                 *inventory["volumes"].values()]
    for resource in resources:
        if resource is None:
            continue
        labels = resource.get("Labels") or {}
        if labels.get(OWNER_LABEL) != state["owner"] or labels.get(PROJECT_LABEL) != project:
            raise RuntimeError("Studio project contains a foreign network or volume")


def _memory_bytes(value: str) -> int:
    match = re.fullmatch(r"(\d+)([mg])", value)
    if match is None:
        raise RuntimeError("Invalid Studio memory limit")
    return int(match[1]) * (1024 ** (2 if match[2] == "m" else 3))


def _verify_runtime(endpoint: str, state: dict, inventory: dict,
                    *, diagnostic: dict | None = None, deadline=None) -> bool:
    """Fail closed on actual runtime configuration, not ps display strings."""
    def fail(code: str, service: str | None = None) -> bool:
        if diagnostic is not None:
            diagnostic.update(code=code)
            if service is not None:
                diagnostic["service"] = service
        return False

    _assert_owned(inventory, state)
    config = compose_config(state)
    project = config["name"]
    network_name = project + "_isolated"
    network = inventory["network"]
    if network is None or network.get("Internal") is not True:
        return fail("network_missing_or_external")
    if network.get("Name") != network_name or network.get("Driver") != "bridge":
        return fail("network_identity_mismatch")
    access_name = project + "_access"
    access_network = inventory.get("access_network")
    if (access_network is None or access_network.get("Name") != access_name
            or access_network.get("Internal") is not False
            or access_network.get("Driver") != "bridge"):
        return fail("access_network_identity_mismatch")
    if any(value is None for value in inventory["volumes"].values()):
        return fail("owned_volume_missing")
    for name, volume in inventory["volumes"].items():
        if (volume.get("Name") != project + "_" + name or volume.get("Driver") != "local"
                or volume.get("Options")):
            return fail("volume_configuration_mismatch")
    if len(inventory["containers"]) != len(config["services"]):
        return fail("service_inventory_incomplete")
    identifiers = {item.get("Id") for item in inventory["containers"]}
    if set(network.get("Containers", {})) - identifiers:
        return fail("unexpected_network_peer")
    relay_ids = {item.get("Id") for item in inventory["containers"]
                 if item.get("Config", {}).get("Labels", {}).get(SERVICE_LABEL) == "relay"}
    if set(access_network.get("Containers", {})) != relay_ids:
        return fail("unexpected_access_network_peer")
    images = {}
    for reference in {state["image_id"], *AUXILIARY_IMAGES.values()}:
        remaining = 5 if deadline is None else min(5, deadline - time.monotonic())
        if remaining <= 0:
            raise RuntimeError("Studio runtime verification timed out")
        image = _json_output(_command(endpoint, ["image", "inspect", reference,
                            "--format", "{{json .}}"], timeout=remaining), "image inspection")
        if image.get("Os") != "linux" or not re.fullmatch(r"sha256:[0-9a-f]{64}", image.get("Id", "")):
            return fail("image_identity_invalid")
        if reference == state["image_id"] and (
            image["Id"] != reference
            or (image.get("Config", {}).get("Labels") or {}).get(COMMIT_LABEL) != STUDIO_COMMIT
        ):
            return fail("backend_source_mismatch")
        images[reference] = image
    for item in inventory["containers"]:
        actual = item.get("Config", {})
        host = item.get("HostConfig", {})
        service = actual["Labels"][SERVICE_LABEL]
        spec = config["services"][service]
        runtime = item.get("State", {})
        reference = spec["image"]
        expected_networks = {network_name, access_name} if service == "relay" else {network_name}
        if item.get("Image") != images[reference]["Id"] or actual.get("Image") != reference:
            return fail("container_image_mismatch", service)
        if (host.get("Privileged") or host.get("Devices") or host.get("DeviceRequests")
                or host.get("CapAdd") or host.get("PidMode") or host.get("UTSMode")
                or host.get("IpcMode") == "host" or host.get("NetworkMode") not in expected_networks
                or "no-new-privileges:true" not in (host.get("SecurityOpt") or [])
                or host.get("Memory") != _memory_bytes(spec["mem_limit"])
                or host.get("MemorySwap") != _memory_bytes(spec["memswap_limit"])
                or host.get("PidsLimit") != spec["pids_limit"]
                or host.get("NanoCpus") != int(spec["cpus"] * 1_000_000_000)):
            return fail("container_isolation_mismatch", service)
        if "cap_drop" in spec and "ALL" not in (host.get("CapDrop") or []):
            return fail("container_capabilities_mismatch", service)
        if service == "relay" and host.get("ReadonlyRootfs") is not True:
            return fail("relay_writable_root", service)
        if actual.get("User", "") != images[reference].get("Config", {}).get("User", ""):
            return fail("container_user_mismatch", service)
        for key, inspect_key in (("entrypoint", "Entrypoint"), ("command", "Cmd")):
            if key in spec and (actual.get(inspect_key) or []) != spec[key]:
                return fail("container_command_mismatch", service)
        actual_env = dict(value.split("=", 1) for value in actual.get("Env", []) if "=" in value)
        if any(actual_env.get(key) != value for key, value in spec.get("environment", {}).items()):
            return fail("container_environment_mismatch", service)
        expected_ports = ({"4002/tcp": [{"HostIp": "127.0.0.1", "HostPort": str(state["port"])}]}
                          if service == "relay" else {})
        if (host.get("PortBindings") or {}) != expected_ports or host.get("PublishAllPorts"):
            return fail("configured_ports_mismatch", service)
        expected_mounts = {
            ("volume", project + "_" + volume.split(":")[0], volume.split(":")[1])
            for volume in spec.get("volumes", [])
        }
        actual_mounts = {(mount.get("Type"), mount.get("Name"), mount.get("Destination"))
                         for mount in item.get("Mounts", []) if mount.get("Type") != "tmpfs"}
        if actual_mounts != expected_mounts:
            return fail("container_mounts_mismatch", service)
        expected_tmpfs = {"/data"} if service == "redis" else {"/tmp"} if service == "relay" else set()
        if set(host.get("Tmpfs") or {}) != expected_tmpfs:
            return fail("container_tmpfs_mismatch", service)
        if service in {"migration", "precompile"}:
            if runtime.get("Status") != "exited" or runtime.get("ExitCode") != 0:
                return fail("oneshot_not_completed", service)
        else:
            if runtime.get("Running") is not True or runtime.get("Health", {}).get("Status") != "healthy":
                return fail("service_not_healthy", service)
            networks = item.get("NetworkSettings", {}).get("Networks") or {}
            if (set(networks) != expected_networks
                    or networks[network_name].get("NetworkID") != network.get("Id")
                    or (service == "relay" and networks[access_name].get("NetworkID") != access_network.get("Id"))):
                return fail("container_network_mismatch", service)
            ports = {key: value for key, value in item.get("NetworkSettings", {}).get("Ports", {}).items()
                     if value is not None}
            if ports != expected_ports:
                return fail("published_ports_mismatch", service)
    return True


def status(data_dir: Path) -> dict:
    root = _root(data_dir)
    if not (root / "installation.json").exists():
        return {"installed": False, "ready": False}
    state = _load(root)
    result = {"installed": True, "ready": False, "source_commit": STUDIO_COMMIT,
              "studio_version": STUDIO_VERSION, "genvm_version": "v0.2.16",
              "image_id": state.get("image_id"), "project": f"gl-agent-lab-{state['owner']}",
              "endpoint": f"http://127.0.0.1:{state['port']}", "fixture_only": True,
              "public_chain": False, "bond_accounting": False,
              "auxiliary_images": dict(AUXILIARY_IMAGES), "reproducible_build": False,
              "runtime_verified": False}
    if not state.get("image_id"):
        return result
    result["configuration_sha256"] = hashlib.sha256(
        json.dumps(compose_config(state), sort_keys=True).encode()).hexdigest()
    try:
        endpoint = _endpoint()
        inventory = _inventory(endpoint, state)
        result["services"] = [{"name": item.get("Name", "").lstrip("/"),
                               "service": item.get("Config", {}).get("Labels", {}).get(SERVICE_LABEL),
                               "state": item.get("State", {}).get("Status"),
                               "health": item.get("State", {}).get("Health", {}).get("Status")}
                              for item in inventory["containers"]]
        result["network_internal"] = (inventory["network"] or {}).get("Internal") is True
        diagnostic = {}
        result["runtime_verified"] = _verify_runtime(endpoint, state, inventory,
                                                      diagnostic=diagnostic)
        if not result["runtime_verified"]:
            result["error"] = "runtime_not_verified"
            result["verification_failure"] = diagnostic
            return result
        from .studio import StudioClient
        with StudioClient(result["endpoint"], timeout=5) as client:
            result["rpc"] = client.doctor()
        result["ready"] = result["rpc"].get("ready") is True
    except (RuntimeError, OSError, ValueError, TypeError, KeyError):
        result["error"] = "runtime_inspection_failed"
    return result
