"""Modern installation boundaries, including legacy coexistence."""
import copy
import json

import pytest

from genlayer_agent_lab.runtime import studio_profiles as profiles
from genlayer_agent_lab.runtime import studio_stack
from genlayer_agent_lab.runtime.container import CommandResult
from genlayer_agent_lab.runtime.studio import StudioError
from genlayer_agent_lab.runtime.studio_cohort import StudioFixtureLease


def initialize(path):
    with profiles._lock(path):
        state = profiles._initialize(path, 8796)
    return state


def test_modern_installation_is_separate_from_legacy_metadata(tmp_path):
    before = studio_stack.initialize(tmp_path, 8766)
    legacy_bytes = (tmp_path / "studio/installation.json").read_bytes()
    modern = initialize(tmp_path)
    assert modern["owner"] != before["owner"]
    assert profiles.load_profile(tmp_path)["port"] == 8796
    assert (tmp_path / "studio/installation.json").read_bytes() == legacy_bytes
    assert studio_stack._load(tmp_path / "studio")["port"] == 8766


def test_modern_lifecycle_and_project_cohort_share_exclusive_lease(tmp_path):
    initialize(tmp_path)
    with profiles._lock(tmp_path):
        with pytest.raises(StudioError, match="studio_fixture_busy"):
            StudioFixtureLease(tmp_path, subdirectory="studio-modern").acquire()
        # Legacy test runs can use a different stack while this profile is leased.
        with StudioFixtureLease(tmp_path):
            pass


def test_modern_profile_rejects_unknown_release_before_generating_compose(tmp_path):
    state = initialize(tmp_path)
    state.update(image_id="sha256:" + "1" * 64, source_commit=studio_stack.STUDIO_COMMIT)
    with pytest.raises(RuntimeError, match="profile metadata"):
        profiles.compose_config(state)


def test_modern_profile_never_silently_reuses_another_port(tmp_path):
    initialize(tmp_path)
    with profiles._lock(tmp_path), pytest.raises(ValueError, match="another port"):
        profiles._initialize(tmp_path, 8766)


def test_fee_profile_uses_internal_execution_network_and_only_loopback_relay(tmp_path):
    state = initialize(tmp_path)
    state["image_id"] = "sha256:" + "1" * 64
    config = profiles.compose_config(state)
    services = config["services"]
    assert config["networks"]["isolated"]["internal"] is True
    assert services["relay"]["ports"] == ["127.0.0.1:8796:4002"]
    for name, service in services.items():
        if name != "relay":
            assert service["networks"] == ["isolated"]
            assert "ports" not in service
        assert not service.get("privileged")
    for name in ("jsonrpc", "worker"):
        env = services[name]["environment"]
        assert env["GENLAYER_CHAIN_ID"] == "61127"
        assert env["GENLAYER_STUDIO_GEN_PER_TIME_UNIT"] == "1"
        assert env["GENLAYER_STUDIO_STORAGE_UNIT_PRICE"] == "250000000"
        assert not env["USAGE_METRICS_API_URL"]


def test_unowned_nonempty_modern_directory_is_preserved(tmp_path):
    root = profiles.profile_root(tmp_path)
    root.mkdir(parents=True)
    (root / "important.txt").write_text("keep")
    with profiles._lock(tmp_path), pytest.raises(RuntimeError, match="nonempty"):
        profiles._initialize(tmp_path, 8796)
    assert (root / "important.txt").read_text() == "keep"
    assert not (root / "installation.json").exists()


def test_profile_cannot_claim_ready_without_built_image(tmp_path):
    initialize(tmp_path)
    status = profiles.modern_profile_status(tmp_path)
    assert status["installed"]
    assert not status["ready"]
    assert not status["runtime_verified"]
    assert not status["bond_accounting"]


def test_cross_profile_runtime_generation_does_not_modify_persisted_state(tmp_path):
    state = initialize(tmp_path)
    state["image_id"] = "sha256:" + "1" * 64
    original = copy.deepcopy(state)
    config = profiles.compose_config(state)
    assert state == original
    assert profiles.STUDIO_COMMIT in json.dumps(state)
    assert studio_stack.STUDIO_COMMIT not in json.dumps(config)


def test_cold_start_uses_bounded_budget_and_preserves_cache_on_ordinary_retry(tmp_path, monkeypatch):
    state = initialize(tmp_path)
    state["image_id"] = "sha256:" + "1" * 64
    root = profiles.profile_root(tmp_path)
    profiles._save(root, state)
    original = (root / "installation.json").read_bytes()
    (root / "cache-marker").write_text("completed precompile")
    commands, checks = [], []
    monkeypatch.delenv(profiles.STARTUP_TIMEOUT_ENV, raising=False)
    monkeypatch.setattr(profiles, "_endpoint", lambda: "unix:///test.sock")
    monkeypatch.setattr(studio_stack, "_inventory", lambda *a: {"owned": True})
    monkeypatch.setattr(studio_stack, "_assert_owned", lambda *a, **kw: checks.append(kw["config"]))
    monkeypatch.setattr(profiles, "modern_profile_status", lambda _: {"ready": True})

    def command(endpoint, args, **kwargs):
        commands.append((args, kwargs))
        return CommandResult(1 if len(commands) == 1 else 0, b"precompile progress",
                             b"actual compose failure" if len(commands) == 1 else b"")

    monkeypatch.setattr(studio_stack, "_command", command)
    with pytest.raises(studio_stack.StudioOperationFailure) as error:
        profiles.start_profile(tmp_path)
    assert error.value.diagnostic["stage"] == "compose_up"
    assert "actual compose failure" in error.value.diagnostic["output_tail"]
    assert profiles.start_profile(tmp_path)["ready"] is True
    assert len(checks) == 2
    assert all(check["networks"]["isolated"]["internal"] for check in checks)
    assert all(args[-5:] == ["up", "--detach", "--wait", "--wait-timeout", "1800"]
               and kwargs["timeout"] == 1830 for args, kwargs in commands)
    assert (root / "installation.json").read_bytes() == original
    assert (root / "cache-marker").read_text() == "completed precompile"
    assert len(list((root / "operation-logs").glob("compose_up-*.json"))) == 2


@pytest.mark.parametrize("value", ["", "0", "59", "3601", "-1", "infinite", "60.5", "1" * 100])
def test_startup_budget_rejects_invalid_values_before_mutation(tmp_path, monkeypatch, value):
    monkeypatch.setenv(profiles.STARTUP_TIMEOUT_ENV, value)
    target = tmp_path / "new-installation"
    with pytest.raises(ValueError, match="60 to 3600"):
        profiles.start_profile(target)
    assert not target.exists()


@pytest.mark.parametrize("value", ["60", "1800", "3600"])
def test_startup_budget_is_configurable_with_a_finite_maximum(monkeypatch, value):
    monkeypatch.setenv(profiles.STARTUP_TIMEOUT_ENV, value)
    assert profiles.startup_timeout() == int(value)


def test_stage_failure_redacts_bounded_output_on_disk_and_in_exception(tmp_path, monkeypatch):
    root = profiles.profile_root(tmp_path)
    root.mkdir()
    (tmp_path / "admin.token").write_text("admin-key-from-file")
    monkeypatch.setenv("SOME_API_KEY", "environment-key")
    raw = ("x" * 30000 + "\nadmin-key-from-file environment-key Bearer bearer-key\n"
           '"PASSWORD": "literal-password"\npostgres://user:db-password@database:5432/lab\n'
           "https://example.test?token=query-key\nError: address already in use")
    with pytest.raises(studio_stack.StudioOperationFailure) as caught:
        studio_stack._run_stage(root, "compose_up", lambda: CommandResult(17, raw.encode(), b""), timeout=1830)
    error = caught.value
    logged = error.log_path.read_text()
    diagnostic = json.loads(logged)
    for secret in ("admin-key-from-file", "environment-key", "bearer-key", "literal-password",
                   "db-password", "query-key"):
        assert secret not in logged + str(error)
    assert len(diagnostic["output_tail"].encode()) <= studio_stack.OPERATION_OUTPUT_BYTES + 3
    assert "address already in use" in diagnostic["output_tail"]
    assert diagnostic["exit_code"] == 17 and diagnostic["status"] == "failed"
    assert "actual" not in str(error)  # Command output is not put in exception messages.


def test_stage_timeout_keeps_captured_output_and_specific_guidance(tmp_path):
    root = profiles.profile_root(tmp_path)
    root.mkdir()

    def timeout():
        error = RuntimeError("Docker command timed out")
        error.stdout, error.stderr, error.returncode = b"precompile still running", b"", -9
        raise error

    with pytest.raises(studio_stack.StudioOperationFailure) as caught:
        studio_stack._run_stage(root, "compose_up", timeout, timeout=1830)
    diagnostic = json.loads(caught.value.log_path.read_text())
    assert diagnostic["category"] == "timeout"
    assert diagnostic["timeout_seconds"] == 1830
    assert "precompile still running" in diagnostic["output_tail"]
    assert "ordinary setup retry" in diagnostic["hint"]
    assert profiles.STARTUP_TIMEOUT_ENV in diagnostic["hint"]


def test_successful_cold_preparation_records_elapsed_time_without_false_failure(tmp_path, monkeypatch):
    root = profiles.profile_root(tmp_path)
    root.mkdir()
    ticks = iter([0, 840])
    monkeypatch.setattr(studio_stack.time, "monotonic", lambda: next(ticks))
    progress = []
    studio_stack._run_stage(root, "compose_up", lambda: CommandResult(0, b"precompile exited 0", b""),
                            timeout=1830, progress=progress.append)
    diagnostic = json.loads(next((root / "operation-logs").glob("*.json")).read_text())
    assert diagnostic["status"] == "completed" and diagnostic["elapsed_seconds"] == 840
    assert any("completed" in line and "840s" in line for line in progress)


def test_failed_diagnostic_write_does_not_hide_command_failure(tmp_path):
    root = profiles.profile_root(tmp_path)
    root.mkdir()
    (root / "operation-logs").write_text("existing unrelated file")
    with pytest.raises(studio_stack.StudioOperationFailure) as caught:
        studio_stack._run_stage(root, "compose_up", lambda: CommandResult(42, b"error", b""), timeout=1830)
    assert caught.value.log_path is None
    assert "exit 42" in str(caught.value)


def test_interrupted_stage_is_not_left_marked_running(tmp_path):
    root = profiles.profile_root(tmp_path)
    root.mkdir()

    def interrupt():
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        studio_stack._run_stage(root, "compose_up", interrupt, timeout=1830)
    diagnostic = json.loads(next((root / "operation-logs").glob("*.json")).read_text())
    assert diagnostic["status"] == "interrupted"
