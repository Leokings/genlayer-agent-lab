import copy
import io
import json
import tarfile
from pathlib import Path

import pytest

from genlayer_agent_lab.runtime import studio
from genlayer_agent_lab.runtime import studio_stack as stack
from genlayer_agent_lab.runtime.container import CommandResult
from genlayer_agent_lab.runtime.studio_cohort import StudioFixtureLease

IMAGE_ID = "sha256:" + "a" * 64


@pytest.fixture
def installed(tmp_path):
    state = stack.initialize(tmp_path)
    state["image_id"] = IMAGE_ID
    stack._save(tmp_path / "studio", state)
    return tmp_path, state


def test_initialization_retains_owner_and_rejects_foreign_directory(tmp_path):
    first = stack.initialize(tmp_path)
    assert stack.initialize(tmp_path) == first
    with pytest.raises(ValueError, match="another port"):
        stack.initialize(tmp_path, port=8767)
    foreign = tmp_path / "foreign" / "studio"
    foreign.mkdir(parents=True)
    (foreign / "keep.txt").write_text("keep")
    with pytest.raises(RuntimeError, match="nonempty"):
        stack.initialize(foreign.parent)
    assert (foreign / "keep.txt").read_text() == "keep"


def test_lifecycle_lock_is_scoped_and_released(tmp_path):
    with stack._operation_lock(tmp_path):
        with pytest.raises(RuntimeError, match="studio_fixture_busy"):
            stack.initialize(tmp_path)
        stack.initialize(tmp_path / "other-installation")
    assert stack.initialize(tmp_path)["schema"] == 1


@pytest.mark.parametrize("failure", ["symlink", "open"])
def test_lifecycle_lease_is_explicitly_released_before_handle_creation(tmp_path, monkeypatch, failure):
    original_close = StudioFixtureLease.close
    original_open = Path.open
    original_is_symlink = Path.is_symlink
    closed = []

    def close(lease):
        closed.append(lease)
        original_close(lease)

    def open_path(path, *args, **kwargs):
        if path.name == "operation.lock":
            raise OSError("cannot open lock")
        return original_open(path, *args, **kwargs)

    def is_symlink(path):
        return path.name == "operation.lock" or original_is_symlink(path)

    with monkeypatch.context() as patch:
        patch.setattr(StudioFixtureLease, "close", close)
        patch.setattr(Path, "is_symlink" if failure == "symlink" else "open",
                      is_symlink if failure == "symlink" else open_path)
        with pytest.raises((RuntimeError, OSError)):
            with stack._operation_lock(tmp_path):
                pytest.fail("The operation must not run")
        assert len(closed) == 1 and closed[0]._handle is None
    with stack._operation_lock(tmp_path):
        pass


@pytest.mark.parametrize("change", [
    {"owner": "../../foreign"}, {"port": True}, {"port": 80}, {"image_id": "ubuntu:latest"},
    {"image_id": 3}, {"source_commit": "main"}, {"studio_version": "latest"},
    {"auxiliary_images": {"postgres": "postgres:latest"}},
    {"fixture_config_patch": "unknown"}, {"fixture_config_patch": True},
    {"fixture_config_patch": None},
])
def test_metadata_cannot_redirect_images_or_projects(installed, change):
    directory, state = installed
    state.update(change)
    (directory / "studio/installation.json").write_text(json.dumps(state))
    with pytest.raises(RuntimeError):
        stack._load(directory / "studio")


def test_existing_schema_one_gains_auxiliary_pins_without_changing_identity(installed):
    directory, state = installed
    state.pop("auxiliary_images")
    (directory / "studio/installation.json").write_text(json.dumps(state))
    loaded = stack._load(directory / "studio")
    assert loaded["owner"] == state["owner"]
    assert loaded["image_id"] == IMAGE_ID
    assert loaded["auxiliary_images"] == stack.AUXILIARY_IMAGES


@pytest.mark.parametrize("patch_id,ready", [
    (stack.LEGACY_FIXTURE_CONFIG_PATCH, False), (stack.FIXTURE_CONFIG_PATCH, True),
])
def test_old_patch_metadata_remains_readable_but_requires_rebuild_for_cohort(
        installed, monkeypatch, patch_id, ready):
    directory, state = installed
    state["fixture_config_patch"] = patch_id
    stack._save(directory / "studio", state)
    assert stack._load(directory / "studio")["fixture_config_patch"] == patch_id
    monkeypatch.setattr(stack, "_endpoint", lambda: (_ for _ in ()).throw(RuntimeError("offline")))
    assert stack.status(directory)["fixture_config_patch"] is ready


def test_generated_compose_is_isolated_bounded_and_has_only_owned_named_mounts(installed):
    _, state = installed
    config = stack.compose_config(state)
    assert config["networks"]["isolated"]["internal"] is True
    assert set(config["volumes"]) == {"database", "vm-cache"}
    published = []
    for name, service in config["services"].items():
        assert service["networks"] == (["isolated", "access"] if name == "relay" else ["isolated"])
        assert "network_mode" not in service and not service.get("privileged")
        assert service["memswap_limit"] == service["mem_limit"]
        assert 0 < service["cpus"] <= 2
        assert service["pids_limit"] == (32 if name == "relay" else 256)
        for mount in service.get("volumes", []):
            assert mount.split(":")[0] in config["volumes"]
            assert "docker.sock" not in mount
        published.extend(service.get("ports", []))
        if name in stack.AUXILIARY_IMAGES:
            assert service["image"] == stack.AUXILIARY_IMAGES[name]
            assert "@sha256:" in service["image"]
        else:
            assert service["image"] == IMAGE_ID
            assert service["environment"]["PYTHONPATH"] == "/app"
            if name != "relay":
                assert service["environment"]["VITE_FINALITY_WINDOW"] == "30"
    assert published == ["127.0.0.1:8766:4002"]
    relay = config["services"]["relay"]
    assert config["networks"]["access"]["internal"] is False
    assert relay["environment"] == {"PYTHONPATH": "/app"}
    assert relay["read_only"] is True
    assert relay["entrypoint"][:2] == ["python3", "-c"]
    assert relay.get("volumes", []) == []
    assert config["services"]["precompile"]["entrypoint"] == ["/entrypoint.sh"]
    assert config["services"]["precompile"]["command"] == ["true"]


def test_compose_regenerates_files_and_drops_ambient_compose_configuration(installed, monkeypatch):
    directory, state = installed
    root = directory / "studio"
    (root / "compose.generated.json").write_text('{"services":{"evil":{"image":"evil"}}}')
    (root / ".env").write_text("SNEAKY_IMAGE=evil\n")
    (root / "empty.env").write_text("SNEAKY_IMAGE=evil\n")
    for key in ("COMPOSE_FILE", "COMPOSE_ENV_FILES", "COMPOSE_PROFILES", "COMPOSE_PROJECT_NAME"):
        monkeypatch.setenv(key, "evil")
    monkeypatch.setenv("DOCKER_HOST", "tcp://foreign:2375")
    monkeypatch.setattr(stack, "_endpoint", lambda: "unix:///owned.sock")
    calls = []

    def process(args, **kwargs):
        calls.append((args, kwargs))
        return CommandResult(0, b"", b"")

    monkeypatch.setattr(stack, "_bounded_process", process)
    stack._compose(directory, ["config"], timeout=7)
    args, kwargs = calls[0]
    assert args[:3] == ["docker", "--host", "unix:///owned.sock"]
    assert args[args.index("--file") + 1] == str(root / "compose.generated.json")
    assert args[args.index("--env-file") + 1] == str(root / "empty.env")
    assert args[args.index("--project-name") + 1] == "gl-agent-lab-" + state["owner"]
    assert kwargs["timeout"] == 7 and kwargs["output_limit"] == 8_388_608
    assert {key for key in kwargs["env"] if key.startswith("COMPOSE_")} == {"COMPOSE_DISABLE_ENV_FILE"}
    assert "DOCKER_HOST" not in kwargs["env"]
    assert (root / "empty.env").read_text() == ""
    assert "evil" not in (root / "compose.generated.json").read_text()
    assert (root / ".env").read_text() == "SNEAKY_IMAGE=evil\n"


@pytest.fixture
def inspected(installed, monkeypatch):
    directory, state = installed
    config = stack.compose_config(state)
    project = config["name"]
    labels = {stack.OWNER_LABEL: state["owner"], stack.PROJECT_LABEL: project}
    network_id = "e" * 64
    network_name = project + "_isolated"
    access_id = "f" * 64
    access_name = project + "_access"
    images = {reference: {"Id": "sha256:" + str(index) * 64, "Os": "linux",
                          "Config": {"User": "", "Labels": {}}}
              for index, reference in enumerate(stack.AUXILIARY_IMAGES.values(), 1)}
    images[IMAGE_ID] = {"Id": IMAGE_ID, "Os": "linux", "Config": {
        "User": "backend-user", "Labels": {stack.COMMIT_LABEL: stack.STUDIO_COMMIT}}}
    containers = []
    for index, (name, spec) in enumerate(config["services"].items(), 1):
        ports = {"4002/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8766"}]} if name == "relay" else {}
        oneshot = name in {"migration", "precompile"}
        networks = {network_name: {"NetworkID": network_id}}
        if name == "relay":
            networks[access_name] = {"NetworkID": access_id}
        containers.append({
            "Id": str(index) * 64, "Name": "/" + project + "-" + name + "-1",
            "Image": images[spec["image"]]["Id"],
            "Config": {"Labels": {**labels, stack.SERVICE_LABEL: name}, "Image": spec["image"],
                       "User": images[spec["image"]]["Config"]["User"],
                       "Env": [key + "=" + value for key, value in spec.get("environment", {}).items()],
                       "Entrypoint": spec.get("entrypoint"), "Cmd": spec.get("command")},
            "HostConfig": {"NetworkMode": network_name, "SecurityOpt": ["no-new-privileges:true"],
                           "Memory": stack._memory_bytes(spec["mem_limit"]),
                           "MemorySwap": stack._memory_bytes(spec["mem_limit"]),
                           "PidsLimit": spec["pids_limit"], "NanoCpus": int(spec["cpus"] * 1_000_000_000),
                           "CapDrop": spec.get("cap_drop", []), "PortBindings": ports,
                           "ReadonlyRootfs": spec.get("read_only", False),
                           "Tmpfs": {value.split(":")[0]: value.split(":")[1]
                                     for value in spec.get("tmpfs", [])}},
            "State": {"Status": "exited" if oneshot else "running", "ExitCode": 0,
                      "Running": not oneshot, "Health": {"Status": "healthy"}},
            "Mounts": [{"Type": "volume", "Name": project + "_" + value.split(":")[0],
                        "Destination": value.split(":")[1]} for value in spec.get("volumes", [])],
            "NetworkSettings": {"Networks": networks, "Ports": ports},
        })
    inventory = {"containers": containers, "network": {"Id": network_id, "Name": network_name,
                 "Internal": True, "Driver": "bridge", "Labels": labels,
                 "Containers": {item["Id"]: {} for item in containers}},
                 "access_network": {"Id": access_id, "Name": access_name, "Internal": False,
                                    "Driver": "bridge", "Labels": labels,
                                    "Containers": {item["Id"]: {} for item in containers
                                                   if item["Config"]["Labels"][stack.SERVICE_LABEL] == "relay"}},
                 "volumes": {name: {"Name": project + "_" + name, "Driver": "local",
                                    "Labels": labels} for name in config["volumes"]}}

    def command(endpoint, args, **kwargs):
        assert args[:2] == ["image", "inspect"]
        assert kwargs["timeout"] == 5
        return CommandResult(0, json.dumps(images[args[2]]).encode(), b"")

    monkeypatch.setattr(stack, "_command", command)
    monkeypatch.setattr(stack, "_endpoint", lambda: "unix:///owned.sock")
    return directory, state, inventory


def test_owned_runtime_accepts_inspected_pins_ports_networks_and_limits(inspected):
    _, state, inventory = inspected
    assert stack._verify_runtime("unix:///owned.sock", state, inventory)


@pytest.mark.parametrize("declared,actual,accepted", [
    (None, None, True),
    (stack.FIXTURE_CONFIG_PATCH, stack.FIXTURE_CONFIG_PATCH, True),
    (stack.LEGACY_FIXTURE_CONFIG_PATCH, stack.LEGACY_FIXTURE_CONFIG_PATCH, True),
    (stack.FIXTURE_CONFIG_PATCH, stack.LEGACY_FIXTURE_CONFIG_PATCH, False),
    (stack.LEGACY_FIXTURE_CONFIG_PATCH, stack.FIXTURE_CONFIG_PATCH, False),
    (stack.FIXTURE_CONFIG_PATCH, None, False),
    (stack.FIXTURE_CONFIG_PATCH, "unknown", False),
    (None, stack.FIXTURE_CONFIG_PATCH, False),
])
def test_runtime_requires_image_patch_provenance_to_match_state(
        inspected, monkeypatch, declared, actual, accepted):
    _, state, inventory = inspected
    if declared is not None:
        state["fixture_config_patch"] = declared
    original_command = stack._command

    def command(endpoint, args, **kwargs):
        result = original_command(endpoint, args, **kwargs)
        if args[2] == IMAGE_ID:
            image = json.loads(result.stdout)
            if actual is not None:
                image["Config"]["Labels"][stack.PATCH_LABEL] = actual
            return CommandResult(0, json.dumps(image).encode(), b"")
        return result

    monkeypatch.setattr(stack, "_command", command)
    diagnostic = {}
    assert stack._verify_runtime("unix:///owned.sock", state, inventory,
                                 diagnostic=diagnostic) is accepted
    if not accepted:
        assert diagnostic == {"code": "backend_fixture_patch_mismatch"}


def test_shared_recovery_deadline_prevents_new_docker_inspections(inspected, monkeypatch):
    _, state, inventory = inspected
    monkeypatch.setattr(stack.time, "monotonic", lambda: 100.0)

    def unexpected(*args, **kwargs):
        pytest.fail("An expired recovery deadline must not start another Docker command")

    monkeypatch.setattr(stack, "_command", unexpected)
    with pytest.raises(RuntimeError, match="timed out"):
        stack._inventory("unix:///owned.sock", state, deadline=99.0)
    with pytest.raises(RuntimeError, match="timed out"):
        stack._verify_runtime("unix:///owned.sock", state, inventory, deadline=99.0)


def test_configured_but_unpublished_port_is_reported_without_probing_rpc(inspected, monkeypatch):
    directory, _, inventory = inspected
    rpc = next(item for item in inventory["containers"]
               if item["Config"]["Labels"][stack.SERVICE_LABEL] == "relay")
    # Docker Engine 29 with an internal-only network can retain requested host
    # bindings while reporting no actual published endpoint.
    rpc["NetworkSettings"]["Ports"] = {"4002/tcp": []}
    monkeypatch.setattr(stack, "_inventory", lambda *args: inventory)
    monkeypatch.setattr(studio, "StudioClient", lambda *a, **k: pytest.fail("Port was not published"))
    result = stack.status(directory)
    assert result["runtime_verified"] is False
    assert result["verification_failure"] == {"code": "published_ports_mismatch", "service": "relay"}


@pytest.mark.parametrize("fault", ["extra_access_peer", "core_on_access", "relay_writable", "relay_code"])
def test_only_fixed_readonly_relay_can_use_access_network(inspected, fault):
    _, state, inventory = inspected
    relay = next(item for item in inventory["containers"]
                 if item["Config"]["Labels"][stack.SERVICE_LABEL] == "relay")
    worker = next(item for item in inventory["containers"]
                  if item["Config"]["Labels"][stack.SERVICE_LABEL] == "worker")
    access = inventory["access_network"]
    if fault == "extra_access_peer":
        access["Containers"]["9" * 64] = {}
    elif fault == "core_on_access":
        worker["NetworkSettings"]["Networks"][access["Name"]] = {"NetworkID": access["Id"]}
    elif fault == "relay_writable":
        relay["HostConfig"]["ReadonlyRootfs"] = False
    else:
        relay["Config"]["Entrypoint"] = ["python3", "-c", "print('different code')"]
    assert not stack._verify_runtime("unix:///owned.sock", state, inventory)


@pytest.mark.parametrize("fault", ["image", "public_port", "egress", "extra_network", "bind",
                                  "worker_health", "foreign_peer", "unbounded", "command", "env"])
def test_runtime_tampering_fails_before_rpc_probe(inspected, monkeypatch, fault):
    directory, _, inventory = inspected
    inventory = copy.deepcopy(inventory)
    worker = next(item for item in inventory["containers"] if item["Config"]["Labels"][stack.SERVICE_LABEL] == "worker")
    rpc = next(item for item in inventory["containers"] if item["Config"]["Labels"][stack.SERVICE_LABEL] == "relay")
    if fault == "image":
        worker["Image"] = "sha256:" + "f" * 64
    elif fault == "public_port":
        rpc["HostConfig"]["PortBindings"]["4002/tcp"][0]["HostIp"] = "0.0.0.0"
    elif fault == "egress":
        inventory["network"]["Internal"] = False
    elif fault == "extra_network":
        worker["NetworkSettings"]["Networks"]["bridge"] = {}
    elif fault == "bind":
        worker["Mounts"].append({"Type": "bind", "Source": "/", "Destination": "/host"})
    elif fault == "worker_health":
        worker["State"]["Health"]["Status"] = "unhealthy"
    elif fault == "foreign_peer":
        inventory["network"]["Containers"]["f" * 64] = {}
    elif fault == "unbounded":
        worker["HostConfig"]["Memory"] = 0
    elif fault == "command":
        worker["Config"]["Cmd"] = ["unexpected.py"]
    elif fault == "env":
        worker["Config"]["Env"].append("PYTHONPATH=/unexpected")
    monkeypatch.setattr(stack, "_inventory", lambda *args: inventory)
    monkeypatch.setattr(studio, "StudioClient", lambda *a, **k: pytest.fail("Must not probe unsafe RPC"))
    result = stack.status(directory)
    assert result["ready"] is False and result["runtime_verified"] is False


@pytest.mark.parametrize("resource", ["container", "network", "access_network", "volume"])
def test_foreign_resources_prevent_lifecycle_mutation(inspected, monkeypatch, resource):
    directory, _, inventory = inspected
    inventory = copy.deepcopy(inventory)
    if resource == "container":
        inventory["containers"][0]["Config"]["Labels"][stack.OWNER_LABEL] = "foreign"
    elif resource == "network":
        inventory["network"]["Labels"][stack.OWNER_LABEL] = "foreign"
    elif resource == "access_network":
        inventory["access_network"]["Labels"][stack.OWNER_LABEL] = "foreign"
    else:
        inventory["volumes"]["database"]["Labels"][stack.OWNER_LABEL] = "foreign"
    monkeypatch.setattr(stack, "_inventory", lambda *args: inventory)
    monkeypatch.setattr(stack, "_compose", lambda *a, **k: pytest.fail("No foreign mutation"))
    with pytest.raises(RuntimeError, match="foreign"):
        stack.down(directory)


def test_down_preserves_volumes_and_up_releases_lock_before_status(inspected, monkeypatch):
    directory, _, inventory = inspected
    monkeypatch.setattr(stack, "_inventory", lambda *args: inventory)
    calls = []
    monkeypatch.setattr(stack, "_compose", lambda *args, **kw: calls.append((args, kw)))
    assert stack.down(directory) == {"stopped": True, "data_preserved": True}
    assert calls[0][0][1] == ["down", "--timeout", "15"]

    def status(data_dir):
        with stack._operation_lock(data_dir):
            return {"ready": True}

    monkeypatch.setattr(stack, "status", status)
    assert stack.up(directory) == {"ready": True}


def make_archive(path, names, *, link=False, payloads=None):
    with tarfile.open(path, "w") as archive:
        for name in names:
            item = tarfile.TarInfo(name)
            payload = (payloads or {}).get(name, b"committed source")
            item.size = len(payload)
            if link:
                item.type = tarfile.SYMTYPE
                item.linkname = "../../outside"
                item.size = 0
            archive.addfile(item, io.BytesIO(payload) if not link else None)


@pytest.mark.parametrize("names,link", [(["../outside"], False), (["backend/../../outside"], False),
                                     ([".env"], False), (["backend/link"], True),
                                     (["backend/A.py", "backend/a.py"], False)])
def test_archive_rejects_links_traversal_unlisted_files_and_case_collisions(tmp_path, names, link):
    archive = tmp_path / "source.tar"
    context = tmp_path / "context"
    context.mkdir()
    make_archive(archive, names, link=link)
    with pytest.raises(RuntimeError, match="unsafe"):
        stack._extract_source(archive, context)
    assert not any(context.iterdir())


def test_archive_limits_and_committed_allowlist(tmp_path, monkeypatch):
    archive = tmp_path / "source.tar"
    context = tmp_path / "context"
    context.mkdir()
    make_archive(archive, ["backend/example.py", "docker/Dockerfile.backend", "asgi.py"])
    stack._extract_source(archive, context)
    assert (context / "backend/example.py").read_bytes() == b"committed source"
    monkeypatch.setattr(stack, "MAX_ARCHIVE_BYTES", 1)
    with pytest.raises(RuntimeError, match="size limit"):
        stack._extract_source(archive, context)


def test_inventory_uses_project_filter_not_owner_only_and_bounds_calls(installed, monkeypatch):
    _, state = installed
    calls = []

    def command(endpoint, args, **kwargs):
        calls.append((args, kwargs))
        return CommandResult(0, b"", b"") if args[0] == "ps" else CommandResult(1, b"", b"")

    monkeypatch.setattr(stack, "_command", command)
    result = stack._inventory("unix:///owned.sock", state)
    assert result == {"containers": [], "network": None, "access_network": None,
                      "volumes": {"database": None, "vm-cache": None}}
    assert f"label={stack.PROJECT_LABEL}=gl-agent-lab-{state['owner']}" in calls[0][0]
    assert all(0 < options["timeout"] <= 5 and options["output_limit"] == 1_048_576
               for _, options in calls)


@pytest.mark.parametrize("image_patch", [stack.FIXTURE_CONFIG_PATCH,
                                         stack.LEGACY_FIXTURE_CONFIG_PATCH, None, "unknown"])
def test_build_archives_only_committed_allowlist_and_adds_license_to_temporary_context(
        tmp_path, monkeypatch, image_patch):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "LICENSE").write_text("dirty local license")
    (checkout / ".env").write_text("MUST_NOT_BE_COPIED=secret")
    calls = []
    wrapper = '''@rpc.method("sim_updateValidator")
async def update_validator(
    validator_address: str,
    stake: int | None = None,
    provider: str | None = None,
    model: str | None = None,
    plugin: str | None = None,
    plugin_config: dict | None = None,
    session: Session = Depends(get_db_session),
    validators_manager=Depends(get_validators_manager),
) -> dict:
    return await impl.update_validator(
        session=session,
        validators_manager=validators_manager,
        validator_address=validator_address,
        stake=stake,
        provider=provider,
        model=model,
        plugin=plugin,
        plugin_config=plugin_config,
    )


@rpc.method("sim_deleteValidator")
async def delete_validator():
    pass
'''

    def process(args, **kwargs):
        calls.append(args)
        if "rev-parse" in args:
            return CommandResult(0, stack.STUDIO_COMMIT.encode(), b"")
        assert "archive" in args and stack.STUDIO_COMMIT in args
        assert args[1:3] == ["-c", "core.autocrlf=false"]
        assert args[-len(stack.SOURCE_PATHS):] == stack.SOURCE_PATHS
        archive_path = next(item.removeprefix("--output=") for item in args if item.startswith("--output="))
        rpc_path = "backend/protocol_rpc/rpc_methods.py"
        worker_path = "backend/consensus/worker.py"
        worker = (Path(__file__).parent / "fixtures/studio_0_121_6_appeal_claim.py.txt").read_bytes()
        make_archive(archive_path,
                     ["docker/Dockerfile.backend", "LICENSE", "backend/example.py", rpc_path, worker_path],
                     payloads={rpc_path: wrapper.encode(), worker_path: worker})
        return CommandResult(0, b"", b"")

    def command(endpoint, args, **kwargs):
        if args[0] == "build":
            context = Path(args[-1])
            assert not (context / ".env").exists()
            assert (context / "LICENSE").read_text() == "committed source"
            assert "COPY LICENSE /app/GENLAYER_STUDIO_LICENSE" in (context / "docker/Dockerfile.backend").read_text()
            patched = (context / "backend/protocol_rpc/rpc_methods.py").read_text()
            assert "    config: dict | None = None," in patched
            assert "        config=config," in patched
            patched_worker = (context / "backend/consensus/worker.py").read_text()
            assert "                      transactions.contract_snapshot," in patched_worker
            assert '                "contract_snapshot": result.contract_snapshot,' in patched_worker
            assert f"{stack.PATCH_LABEL}={stack.FIXTURE_CONFIG_PATCH}" in args
            assert kwargs["timeout"] == 1200
            return CommandResult(0, b"", b"")
        assert args[:2] == ["image", "inspect"]
        labels = {stack.COMMIT_LABEL: stack.STUDIO_COMMIT}
        if image_patch is not None:
            labels[stack.PATCH_LABEL] = image_patch
        return CommandResult(0, json.dumps({"Id": IMAGE_ID, "Os": "linux", "Config": {
            "Labels": labels}}).encode(), b"")

    monkeypatch.setattr(stack, "_bounded_process", process)
    monkeypatch.setattr(stack, "_command", command)
    monkeypatch.setattr(stack, "_endpoint", lambda: "unix:///owned.sock")
    monkeypatch.setattr(stack, "_linux_info", lambda _: {})
    monkeypatch.setattr(stack, "status", lambda directory: stack._load(directory / "studio"))
    if image_patch != stack.FIXTURE_CONFIG_PATCH:
        with pytest.raises(RuntimeError, match="fixture patch label"):
            stack.build(tmp_path / "lab", checkout=checkout)
        result = stack._load(tmp_path / "lab/studio")
        assert "image_id" not in result and "fixture_config_patch" not in result
    else:
        result = stack.build(tmp_path / "lab", checkout=checkout)
        assert result["image_id"] == IMAGE_ID
        assert result["packaging_overlay"] == "upstream-license-notice-v1"
        assert result["fixture_config_patch"] == stack.FIXTURE_CONFIG_PATCH
    assert (checkout / "LICENSE").read_text() == "dirty local license"
    assert len(calls) == 2
