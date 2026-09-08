"""Modern installation boundaries, including legacy coexistence."""
import copy
import json

import pytest

from genlayer_agent_lab.runtime import studio_profiles as profiles
from genlayer_agent_lab.runtime import studio_stack
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
