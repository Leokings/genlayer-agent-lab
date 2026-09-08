import copy
import json

import pytest
from test_studio_cohort import FakeClient

from genlayer_agent_lab.api import initialize_data_dir
from genlayer_agent_lab.project_journal import ProjectVault
from genlayer_agent_lab.runtime import studio_profiles
from genlayer_agent_lab.runtime.project_cohort import ProjectCohort
from genlayer_agent_lab.runtime.studio import StudioError
from genlayer_agent_lab.runtime.studio_cohort import StudioFixtureLease


@pytest.fixture
def lab(tmp_path, monkeypatch):
    state = {"owner": "a" * 32, "source_commit": "test-commit"}
    status = {**state, "ready": True, "runtime_verified": True, "network_internal": True,
              "fixture_only": True, "endpoint": "http://127.0.0.1:8766"}
    monkeypatch.setattr(studio_profiles, "load_profile", lambda _: state)
    monkeypatch.setattr(studio_profiles, "modern_profile_status", lambda _: status)
    saved = []
    return tmp_path, FakeClient(), saved, status


def test_partial_fixture_update_recovers_exact_target_then_original(lab):
    directory, client, saved, _ = lab
    original = copy.deepcopy(client.records)
    first = ProjectCohort(directory, client, persist=lambda value: saved.append(copy.deepcopy(value)))
    first.__enter__()
    client.update_error_at = 4
    client.commit_before_error = True
    with pytest.raises(StudioError, match="fixture_update_failed"):
        first.apply({"CASE\n": {"outcome": "yes"}})
    assert saved[-1]["attempted"] is not None
    assert "never-retain-this-key" not in json.dumps(saved)
    first.abandon()
    client.update_error_at = None
    resumed = ProjectCohort(directory, client, state=saved[-1],
                            persist=lambda value: saved.append(copy.deepcopy(value)))
    resumed.__enter__()
    assert all(r["plugin_config"]["mock_response"]["response"] == {"^CASE\n": {"outcome": "yes"}}
               for r in client.records)
    with pytest.raises(StudioError, match="studio_fixture_busy"):
        StudioFixtureLease(directory, subdirectory="studio-modern").acquire()
    resumed.close()
    assert client.records == original
    assert saved[-1] is None


def test_journal_failure_prevents_any_validator_mutation(lab):
    directory, client, _, _ = lab
    def fail(_):
        raise OSError("disk unavailable")
    cohort = ProjectCohort(directory, client, persist=fail)
    with pytest.raises(OSError):
        cohort.__enter__()
    assert client.update_count == 0
    with StudioFixtureLease(directory, subdirectory="studio-modern"):
        pass


def test_journal_commit_precedes_update_and_unknown_changes_block_resume(lab):
    directory, client, saved, _ = lab
    cohort = ProjectCohort(directory, client, persist=lambda value: saved.append(copy.deepcopy(value)))
    cohort.__enter__()
    rpc = client._rpc
    def checked(method, params):
        if method == "sim_updateValidator":
            assert saved[-1]["attempted"] is not None
        return rpc(method, params)
    client._rpc = checked
    cohort.apply({"CASE\n": {"answer": "known"}})
    cohort.abandon()
    client.records[0]["plugin_config"]["mock_response"]["response"] = {"foreign": "config"}
    before = client.update_count
    resumed = ProjectCohort(directory, client, state=saved[-1], persist=lambda _: None)
    with pytest.raises(StudioError, match="fixture_cohort_changed"):
        resumed.__enter__()
    assert client.update_count == before


def test_vault_requires_same_installation_credential_and_rejects_tampering(tmp_path):
    first = initialize_data_dir(tmp_path / "one")
    second = initialize_data_dir(tmp_path / "two")
    vault = ProjectVault(first)
    account = vault.new_account()
    token = vault.seal(account)
    assert account["private_key"] not in token
    assert ProjectVault(first).open(token) == account
    with pytest.raises(ValueError, match="installation credential"):
        ProjectVault(second).open(token)
    with pytest.raises(ValueError, match="installation credential"):
        vault.open(token[:40] + "!" + token[41:])
