import copy
import json
import subprocess
import sys
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from genlayer_agent_lab.runtime import studio_cohort as cohort
from genlayer_agent_lab.runtime.studio import StudioError
from genlayer_agent_lab.runtime.studio_fixtures import validator_config


def validators(count=12):
    result = []
    for index in range(count):
        record = validator_config()
        record.pop("amount")
        record.update(address=f"0x{index + 1:040x}", private_key="never-retain-this-key")
        result.append(record)
    return result


class FakeClient:
    def __init__(self):
        self._provider = SimpleNamespace(url="http://127.0.0.1:8766")
        self.records = validators()
        self.calls = []
        self.active = False
        self.update_error_at = None
        self.fail_all_updates = False
        self.commit_before_error = False
        self.update_count = 0

    @contextmanager
    def _operation(self):
        assert not self.active
        self.active = True
        try:
            yield
        finally:
            self.active = False

    def _rpc(self, method, params):
        assert self.active
        self.calls.append((method, copy.deepcopy(params)))
        if method == "sim_getAllValidators":
            return copy.deepcopy(self.records)
        assert method == "sim_updateValidator"
        assert set(params) == {"validator_address", "stake", "provider", "model", "config",
                               "plugin", "plugin_config"}
        self.update_count += 1
        failing = self.fail_all_updates or self.update_count == self.update_error_at
        if failing and not self.commit_before_error:
            raise RuntimeError("private RPC details")
        for record in self.records:
            if record["address"] == params["validator_address"]:
                record.update({key: copy.deepcopy(value) for key, value in params.items()
                               if key != "validator_address"})
                break
        else:
            raise AssertionError("unexpected identity")
        if failing:
            raise RuntimeError("private RPC details")
        return copy.deepcopy(record)


@pytest.fixture
def owned(tmp_path, monkeypatch):
    state = {"owner": "a" * 32, "port": 8766}
    status = {"installed": True, "ready": True, "runtime_verified": True,
              "network_internal": True, "fixture_only": True, "fixture_config_patch": True,
              "public_chain": False, "source_commit": cohort.studio_stack.STUDIO_COMMIT,
              "project": "gl-agent-lab-" + state["owner"], "endpoint": "http://127.0.0.1:8766"}
    monkeypatch.setattr(cohort.studio_stack, "_load", lambda root: copy.deepcopy(state))
    monkeypatch.setattr(cohort.studio_stack, "status", lambda root: copy.deepcopy(status))
    return tmp_path, FakeClient(), status


def test_apply_keeps_identity_config_and_restores_before_releasing_lease(owned):
    directory, client, _ = owned
    original = copy.deepcopy(client.records)
    with cohort.StudioCohort(directory, client) as managed:
        assert "never-retain" not in json.dumps(managed._original)
        result = managed.apply({"AGENT_LAB_EVIDENCE_V1\n": {"verdict": "deny"}})
        assert result == {"validator_count": 12, "stored_configuration_verified": True}
        for before, after in zip(original, client.records):
            assert {key: before[key] for key in cohort._IDENTITY} == {
                key: after[key] for key in cohort._IDENTITY}
            assert after["plugin_config"]["mock_response"]["response"] == {
                "^AGENT_LAB_EVIDENCE_V1\n": {"verdict": "deny"}}
        with pytest.raises(StudioError, match="studio_fixture_busy"):
            cohort.StudioFixtureLease(directory).acquire()
    assert client.records == original
    assert managed._original is None
    with cohort.StudioFixtureLease(directory):
        pass
    assert [method for method, _ in client.calls].count("sim_updateValidator") == 24


@pytest.mark.parametrize("field,value", [
    ("ready", False), ("runtime_verified", False), ("network_internal", False),
    ("fixture_only", False), ("fixture_config_patch", False), ("public_chain", True),
    ("source_commit", "foreign"), ("project", "foreign"),
    ("endpoint", "https://studio.genlayer.com/api"),
])
def test_unowned_or_unpatched_runtime_rejected_before_rpc(owned, field, value):
    directory, client, status = owned
    status[field] = value
    with pytest.raises(StudioError, match="owned_fixture_stack_required"):
        with cohort.StudioCohort(directory, client):
            pass
    assert not client.calls
    with cohort.StudioFixtureLease(directory):
        pass


def test_client_cannot_point_to_another_loopback_stack(owned):
    directory, client, _ = owned
    client._provider.url = "http://127.0.0.1:8767"
    with pytest.raises(StudioError, match="owned_fixture_stack_required"):
        with cohort.StudioCohort(directory, client):
            pass
    assert not client.calls


@pytest.mark.parametrize("change", [
    lambda rows: rows.pop(),
    lambda rows: rows.extend(validators(53)),
    lambda rows: rows[1].update(address=rows[0]["address"]),
    lambda rows: rows[0].update(stake=True),
    lambda rows: rows[0].update(config=None),
    lambda rows: rows[0]["plugin_config"].update(api_url="https://paid.example"),
    lambda rows: rows[0]["plugin_config"]["mock_response"].update(
        eq_principle_prompt_comparative={".*": True}),
])
def test_non_fixture_or_unbounded_cohort_is_never_changed(owned, change):
    directory, client, _ = owned
    change(client.records)
    with pytest.raises(StudioError, match="invalid_fixture_cohort"):
        with cohort.StudioCohort(directory, client):
            pass
    assert not client.update_count


@pytest.mark.parametrize("commit_before_error", [False, True])
def test_partial_or_uncertain_apply_is_restored_without_leaking_rpc_error(owned, commit_before_error):
    directory, client, _ = owned
    original = copy.deepcopy(client.records)
    client.update_error_at = 4
    client.commit_before_error = commit_before_error
    with pytest.raises(StudioError, match="fixture_update_failed") as error:
        with cohort.StudioCohort(directory, client) as managed:
            managed.apply({"PROMPT": "deny"})
    assert "private RPC" not in str(error.value)
    assert client.records == original
    assert client.update_count <= 8


def test_caller_exception_restores_and_propagates(owned):
    directory, client, _ = owned
    original = copy.deepcopy(client.records)
    with pytest.raises(ValueError, match="caller failure"):
        with cohort.StudioCohort(directory, client) as managed:
            managed.apply({"PROMPT": "deny"})
            raise ValueError("caller failure")
    assert client.records == original


def test_restore_failure_is_visible_bounded_and_lease_is_released(owned):
    directory, client, _ = owned
    with pytest.raises(StudioError, match="fixture_restore_failed"):
        with cohort.StudioCohort(directory, client) as managed:
            managed.apply({"PROMPT": "deny"})
            client.fail_all_updates = True
    assert client.update_count == 24
    with cohort.StudioFixtureLease(directory):
        pass


def test_foreign_config_drift_is_not_overwritten_on_close(owned):
    directory, client, _ = owned
    with pytest.raises(StudioError, match="fixture_restore_failed"):
        with cohort.StudioCohort(directory, client) as managed:
            managed.apply({"PROMPT": "deny"})
            client.records[0]["plugin_config"]["mock_response"]["response"] = {"^OTHER": 3}
    assert client.update_count == 12


def test_bad_prompts_are_rejected_before_update(owned):
    directory, client, _ = owned
    with cohort.StudioCohort(directory, client) as managed:
        with pytest.raises(StudioError, match="invalid_cohort_prompts"):
            managed.apply({"PROMPT": 1, "PROMPT_LONGER": 2})
    assert client.update_count == 0


def test_abandon_releases_lease_without_restoring_or_making_rpc_calls(owned):
    directory, client, _ = owned
    with cohort.StudioCohort(directory, client) as managed:
        managed.apply({"PROMPT": "deny"})
        changed = copy.deepcopy(client.records)
        calls = copy.deepcopy(client.calls)
        managed.abandon()
        assert managed._original is managed._expected is managed._attempted is None
        assert managed._active is False
        with cohort.StudioFixtureLease(directory):
            pass
        with pytest.raises(StudioError, match="fixture_cohort_not_open"):
            managed.apply({"PROMPT": "approve"})
    # Context exit and repeated close must not silently restore unresolved work.
    managed.close()
    managed.abandon()
    assert client.records == changed and client.calls == calls


def test_lease_excludes_another_process(tmp_path):
    source = """
import sys
from genlayer_agent_lab.runtime.studio import StudioError
from genlayer_agent_lab.runtime.studio_cohort import StudioFixtureLease
try:
    with StudioFixtureLease(sys.argv[1]):
        raise SystemExit(2)
except StudioError as exc:
    raise SystemExit(0 if exc.code == 'studio_fixture_busy' else 3)
"""
    with cohort.StudioFixtureLease(tmp_path):
        result = subprocess.run([sys.executable, "-c", source, str(tmp_path)],
                                capture_output=True, timeout=15)
    assert result.returncode == 0
