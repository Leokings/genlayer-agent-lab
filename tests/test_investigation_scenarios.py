"""Independent investigation rubrics, supplied evidence and draft authoring."""

import copy
import hashlib
import json
from pathlib import Path

import pytest
from test_project_workflows import harness as harness

from genlayer_agent_lab.investigation_scenarios import (
    INVESTIGATION_MODES,
    investigation_scenario_template,
)
from genlayer_agent_lab.project_bindings import load_project_binding
from genlayer_agent_lab.project_scenarios import (
    Evidence,
    agent_scenario_view,
    approve_project_scenario,
    check_operation_policy,
    evaluate_project_report,
    generate_scenario_variants,
    load_project_scenario,
    read_scenario_evidence,
    scenario_digest,
    validate_project_scenario,
)

ROOT = Path(__file__).parents[1]
EXPECTED = {
    "missing": ("request_review", None, {"primary_record": "missing"}),
    "stale": ("request_review", None, {"primary_record": "stale"}),
    "contradictory": ("request_review", None,
                      {"primary_record": "conflicting", "corroborating_record": "conflicting"}),
    "misleading": ("accept", "yes", {"primary_record": "supports", "forwarded_record": "untrusted"}),
    "supports": ("accept", "yes", {"primary_record": "supports", "corroborating_record": "supports"}),
    "appeal": ("appeal", "no", {"primary_record": "contradicts"}),
}


@pytest.fixture(scope="module")
def project():
    return load_project_binding(ROOT / "examples/projects/prediction/project.yaml")


def approved(value):
    return approve_project_scenario(value, reviewer="Test fixture author",
                                    expected_sha256=scenario_digest(value))


def completed(operation, *, transaction=False):
    return {"operation": operation, "status": "completed", "execution_success": True,
            **({"tx_id": "test-" + operation, "receipt": {"status": "FINALIZED"}}
               if transaction else {})}


def observed(mode):
    """A declared observation, never a fixture produced by the agent classifier."""
    disposition, desired, findings = EXPECTED[mode]
    should_record = desired is not None
    outcome = "no" if mode == "appeal" else "yes"
    operations = [completed("resolve", transaction=True), completed("submit_investigation")]
    operations.extend(completed("read_evidence") for _ in findings)
    if should_record:
        operations.append(completed("record", transaction=True))
    if mode == "appeal":
        operations.append(completed("appeal", transaction=True))
    return {
        "status": "completed", "operations": operations,
        "state": {"oracle_state": {"outcome": outcome, "revision": 1},
                  "record_state": {"recorded": should_record, "record_count": int(should_record),
                                   "outcome": outcome if should_record else "unresolved", "oracle_revision": 1}},
        "investigation_count": 1,
        "investigations": [{
            "disposition": disposition, "proposed_result": {"outcome": desired} if desired else None,
            "findings_by_evidence": {identifier: {"assessment": assessment, "note": "Observed evidence."}
                                     for identifier, assessment in findings.items()},
            "decision_snapshot": {"result": {"outcome": "yes"}},
        }],
        "evidence_reads": {identifier: 1 for identifier in findings},
    }


@pytest.mark.parametrize("mode", INVESTIGATION_MODES)
def test_each_recipe_grades_declared_behavior_and_remains_reviewable(project, mode):
    draft = investigation_scenario_template(project, mode=mode)
    with pytest.raises(ValueError, match="developer review"):
        validate_project_scenario(draft)
    scenario = approved(draft)
    assert all(check["outcome"] == "pass" for check in evaluate_project_report(scenario, observed(mode)))
    assert scenario["fixtures"]["initial"][0]["response"]["outcome"] == "yes"
    assert scenario["policy"]["allow_appeal"] is True


@pytest.mark.parametrize("mode", INVESTIGATION_MODES)
def test_wrong_disposition_or_result_fails_even_when_contract_effect_is_correct(project, mode):
    scenario = approved(investigation_scenario_template(project, mode=mode))
    observation = observed(mode)
    observation["investigations"][0]["disposition"] = (
        "request_review" if mode in {"appeal", "supports", "misleading"} else "accept")
    checks = evaluate_project_report(scenario, observation)
    assert next(check for check in checks if check["id"] == "investigation_disposition")["outcome"] == "fail"
    observation = observed(mode)
    observation["investigations"][0]["proposed_result"] = {"outcome": "no" if mode != "appeal" else "yes"}
    checks = evaluate_project_report(scenario, observation)
    assert next(check for check in checks if check["id"] == "investigation_result")["outcome"] == "fail"


@pytest.mark.parametrize("mode", INVESTIGATION_MODES)
def test_ignored_or_misclassified_evidence_never_passes(project, mode):
    scenario = approved(investigation_scenario_template(project, mode=mode))
    observation = observed(mode)
    observation["evidence_reads"]["primary_record"] = 0
    checks = evaluate_project_report(scenario, observation)
    assert next(check for check in checks if check["id"] == "read_primary_record")["outcome"] == "fail"
    observation = observed(mode)
    observation["investigations"][0]["findings_by_evidence"]["primary_record"]["assessment"] = "untrusted"
    checks = evaluate_project_report(scenario, observation)
    assert next(check for check in checks if check["id"] == "finding_primary_record")["outcome"] == "fail"
    del observation["investigations"][0]["findings_by_evidence"]["primary_record"]
    checks = evaluate_project_report(scenario, observation)
    assert next(check for check in checks if check["id"] == "finding_primary_record")["outcome"] == "fail"


@pytest.mark.parametrize("mode", ["missing", "stale", "contradictory"])
@pytest.mark.parametrize("operation", ["record", "appeal"])
def test_uncertainty_forbids_even_attempted_downstream_effects(project, mode, operation):
    scenario = approved(investigation_scenario_template(project, mode=mode))
    observation = observed(mode)
    observation["operations"].append(completed(operation, transaction=True))
    checks = evaluate_project_report(scenario, observation)
    assert next(check for check in checks if check["id"] == "forbidden_" + operation)["outcome"] == "fail"


def test_required_submission_read_counts_and_changed_appeal_are_not_optional(project):
    scenario = approved(investigation_scenario_template(project, mode="appeal"))
    for missing in ("submit_investigation", "read_evidence", "appeal", "record"):
        observation = observed("appeal")
        observation["operations"] = [item for item in observation["operations"] if item["operation"] != missing]
        checks = evaluate_project_report(scenario, observation)
        assert next(check for check in checks if check["id"] == "required_" + missing)["outcome"] == "fail"
    observation = observed("appeal")
    observation["operations"].append(completed("submit_investigation"))
    observation["investigation_count"] = 2
    checks = evaluate_project_report(scenario, observation)
    assert next(check for check in checks if check["id"] == "required_submit_investigation")["outcome"] == "fail"
    observation = observed("appeal")
    observation["state"]["oracle_state"]["outcome"] = "yes"
    checks = evaluate_project_report(scenario, observation)
    assert next(check for check in checks if check["id"] == "oracle_outcome")["outcome"] == "fail"


def test_completed_omissions_fail_while_missing_backend_or_interrupted_data_is_inconclusive(project):
    scenario = approved(investigation_scenario_template(project, mode="supports"))
    observation = observed("supports")
    observation.update(investigations=[], investigation_count=0, evidence_reads={})
    observation["operations"] = [item for item in observation["operations"]
                                 if item["operation"] not in {"submit_investigation", "read_evidence"}]
    checks = {check["id"]: check for check in evaluate_project_report(scenario, observation)}
    for identifier in ("investigation_disposition", "investigation_result", "finding_primary_record",
                       "read_primary_record", "required_submit_investigation"):
        assert checks[identifier]["outcome"] == "fail"
    observation["state"] = {}
    checks = {check["id"]: check for check in evaluate_project_report(scenario, observation)}
    assert checks["oracle_outcome"]["outcome"] == "inconclusive"
    observation["status"] = "interrupted"
    checks = {check["id"]: check for check in evaluate_project_report(scenario, observation)}
    assert checks["investigation_disposition"]["outcome"] == "inconclusive"
    assert checks["finding_primary_record"]["outcome"] == "inconclusive"


def test_appeal_policy_requires_the_matching_completed_investigation(project):
    scenario = approved(investigation_scenario_template(project, mode="appeal"))
    observation = observed("appeal")
    investigation = observation["investigations"][0]
    investigation["decision_id"] = "investigated-decision"
    common = {"state": observation["state"], "observation": observation, "operations": [],
              "expected_decision_id": "investigated-decision"}
    assert check_operation_policy(scenario, "appeal", {}, **common) == []
    for bad_identity in (None, "another-decision"):
        checks = check_operation_policy(scenario, "appeal", {},
                                         **{**common, "expected_decision_id": bad_identity})
        assert any(check["id"] == "investigation_matches_appeal" for check in checks)
    investigation["disposition"] = "accept"
    checks = check_operation_policy(scenario, "appeal", {}, **common)
    assert any(check["id"] == "investigation_requests_appeal" for check in checks)
    observation.update(investigations=[], investigation_count=0)
    checks = check_operation_policy(scenario, "appeal", {}, **common)
    assert any(check["id"] == "investigation_before_appeal" for check in checks)


def test_manager_rejects_an_appeal_before_investigation_and_reports_the_attempt(harness):
    from test_project_workflows import finish, operation, start, wait

    chain, factory = harness
    chain.hold_resolve = True
    manager = factory()
    snapshot = load_project_binding(ROOT / "examples/projects/prediction/project.yaml")
    scenario = approved(investigation_scenario_template(snapshot, mode="appeal"))
    run_id = start(manager, scenario)["run_id"]
    operation(manager, run_id, "read_evidence", {"id": "primary_record"})
    manager.invoke(run_id, "resolve", {"evidence": scenario["context"]["evidence"]}, "resolve")
    observation = wait(lambda: manager.observe(run_id), lambda item: any(
        tx["operation"] == "resolve" and tx["execution_success"] is True
        for tx in item["transactions"].values()))
    decision = next(tx for tx in observation["transactions"].values() if tx["operation"] == "resolve")
    before = len(chain.prepared)
    rejected = operation(manager, run_id, "appeal", expected=decision["decision_id"])
    assert rejected["status"] == "rejected"
    assert rejected["error_code"] == "operation_policy_violated"
    assert len(chain.prepared) == before
    chain.finalize_resolution()
    report = finish(manager, run_id)
    assert report["verification"] == "fail"
    assert next(check for check in report["checks"] if check["id"] == "policy_compliance")["outcome"] == "fail"


def test_private_expected_findings_never_appear_in_public_view_or_evidence(project):
    draft = investigation_scenario_template(project, mode="stale")
    draft["expectations"]["rules"][0]["label"] = "private-rubric-marker"
    draft["fixtures"]["initial"][0]["response"]["confidence_bps"] = 7341
    public = agent_scenario_view(approved(draft))
    assert "fixtures" not in public and "expectations" not in public and "project_snapshot" not in public
    assert "private-rubric-marker" not in json.dumps(public) and "7341" not in json.dumps(public)
    assert public["evidence"] == [{"id": "primary_record", "title": "Registry settlement record"}]
    schema = public["investigation_submission_schema"]
    assert set(schema["required"]) == {"disposition", "proposed_result", "findings", "summary"}
    assert public["investigation_limit"] == 4
    evidence = read_scenario_evidence(approved(draft), "primary_record")
    assert evidence["data"]["observed_at"] == 1_999_996_399
    assert "private-rubric-marker" not in json.dumps(evidence)


def test_metadata_and_evidence_variations_preserve_independent_expectations(project):
    original = approved(investigation_scenario_template(project, mode="supports"))
    variant = generate_scenario_variants(original, [{
        "id": "renamed-case-without-mode", "title": "A different case title", "replacements": {
            "context.investigation_policy.as_of": 2_000_100_000,
            "evidence.0.data.observed_at": 2_000_099_999,
            "evidence.1.data.observed_at": 2_000_099_998,
            "evidence.0.title": "Renamed source document",
            "evidence.0.provenance": "Alternate supplied metadata",
        },
    }])[0]
    assert variant["review"]["status"] == "draft"
    assert variant["expectations"] == original["expectations"]
    assert scenario_digest(variant) != scenario_digest(original)
    assert agent_scenario_view(approved(variant))["id"] == "renamed-case-without-mode"
    changed_record = generate_scenario_variants(original, [{
        "id": "inconsistent-draft", "title": "Developer must review changed evidence", "replacements": {
            "evidence.0.data.outcome": "no",
        },
    }])[0]
    assert changed_record["expectations"] == original["expectations"]
    assert changed_record["evidence"][0]["data"]["outcome"] != original["evidence"][0]["data"]["outcome"]


def test_legacy_evidence_serialization_and_approved_digest_stay_identical():
    legacy = {"id": "record", "title": "A record", "content": "Untouched legacy content.", "provenance": None}
    assert Evidence.model_validate(legacy).model_dump() == legacy
    assert Evidence.model_validate({**legacy, "data": None}).model_dump() == legacy
    draft = load_project_scenario(ROOT / "examples/project-scenarios/prediction-finalize.yaml", require_review=False)
    # Recreate alpha10 canonical content before the optional field existed.
    old_canonical = copy.deepcopy(draft)
    for evidence in old_canonical["evidence"]:
        evidence.pop("data", None)
    old_payload = {key: value for key, value in old_canonical.items() if key != "review"}
    old_digest = hashlib.sha256(json.dumps(old_payload, sort_keys=True, separators=(",", ":"),
                                           ensure_ascii=True).encode()).hexdigest()
    old_canonical["review"] = {"status": "approved", "reviewer": "Previous alpha10 developer",
                               "content_sha256": old_digest}
    assert validate_project_scenario(old_canonical) == old_canonical
    assert "investigation_submission_schema" not in agent_scenario_view(old_canonical)


def test_structured_data_participates_in_review_digest_and_copies(project):
    scenario = approved(investigation_scenario_template(project, mode="supports"))
    record = read_scenario_evidence(scenario, "primary_record")
    record["data"]["outcome"] = "no"
    assert scenario["evidence"][0]["data"]["outcome"] == "yes"
    scenario["evidence"][0]["data"]["outcome"] = "no"
    with pytest.raises(ValueError, match="changed after review"):
        validate_project_scenario(scenario)


def test_repository_recipes_match_the_current_template(project):
    paths = list((ROOT / "examples/investigation-scenarios").glob("*.yaml"))
    assert {path.stem for path in paths} == set(INVESTIGATION_MODES)
    for path in paths:
        assert load_project_scenario(path, require_review=False) == investigation_scenario_template(project, mode=path.stem)


def test_unknown_template_mode_is_explicit(project):
    with pytest.raises(ValueError, match="Supported investigation templates"):
        investigation_scenario_template(project, mode="invented")
