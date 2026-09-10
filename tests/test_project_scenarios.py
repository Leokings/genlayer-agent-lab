"""Behavioral authoring gates and independent checks, without model/network calls."""

import copy
import json
from pathlib import Path

import pytest
import yaml

from genlayer_agent_lab.project_bindings import (
    GENVM_EXECUTOR_VERSION,
    SINGLE_RUNNER,
    _definition,
    _snapshot,
)
from genlayer_agent_lab.project_scenarios import (
    ValueRef,
    agent_scenario_view,
    approve_project_scenario,
    check_operation_policy,
    evaluate_project_report,
    evaluate_rule,
    generate_scenario_variants,
    load_project_scenario,
    prediction_message_scenario_template,
    prediction_scenario_template,
    project_scenario_document,
    read_scenario_evidence,
    scenario_authoring_prompt,
    scenario_digest,
    scenario_json_schema,
    validate_project_scenario,
)


def project():
    """A confined source snapshot; authoring never executes this Python."""
    source = (f"# {GENVM_EXECUTOR_VERSION}\n"
              '# {"Depends":"' + SINGLE_RUNNER + '"}\nraise RuntimeError("DO NOT EXECUTE")\n')
    oracle = {"type": "object", "fields": {
        "outcome": {"type": "string", "enum": ["unresolved", "yes", "no", "void"]},
        "revision": {"type": "integer", "minimum": 0},
        "confidence_bps": {"type": "integer", "minimum": 0, "maximum": 10000},
    }}
    recorder = {"type": "object", "fields": {
        "recorded": {"type": "boolean"}, "outcome": {"type": "string"},
        "oracle_revision": {"type": "integer"}, "record_count": {"type": "integer"},
    }}
    definition = {
        "schema_version": 2, "kind": "project", "id": "authoring_test",
        "title": "Authoring test project",
        "contracts": {name: {"source_project": {"entrypoint": "contract.py", "files": ["contract.py"],
                                               "runner": SINGLE_RUNNER}}
                      for name in ("oracle", "recorder")},
        "operations": {
            "read_oracle": {"contract": "oracle", "method": "get_state", "readonly": True,
                            "result": oracle},
            "resolve": {"contract": "oracle", "method": "resolve", "readonly": False,
                        "arguments": [{"name": "evidence", "type": {"type": "string"}}], "result": oracle},
            "read_record": {"contract": "recorder", "method": "get_state", "readonly": True,
                            "result": recorder},
            "record": {"contract": "recorder", "method": "record", "readonly": False,
                       "arguments": [{"name": "expected_revision", "type": {"type": "integer"}},
                                     {"name": "outcome", "type": {"type": "string"}}], "result": recorder},
        }, "state_reads": {"oracle_state": "read_oracle", "record_state": "read_record"},
    }
    return _snapshot(_definition(definition), {name: {"contract.py": source}
                                             for name in ("oracle", "recorder")})


def draft(mode="finalize"):
    return prediction_scenario_template(project(), mode=mode)


def approved(value=None):
    value = draft() if value is None else value
    return approve_project_scenario(value, reviewer="Test author", expected_sha256=scenario_digest(value))


def successful(operation="resolve", **changes):
    return {"operation": operation, "status": "completed", "execution_success": True,
            "tx_id": "test-" + operation, "receipt": {"status": "FINALIZED"}, "fee": 5,
            **changes}


def observation(**changes):
    return {
        "status": "completed",
        "state": {"oracle_state": {"outcome": "yes", "revision": 2, "confidence_bps": 9100},
                  "record_state": {"recorded": True, "outcome": "yes", "oracle_revision": 2,
                                   "record_count": 1}},
        "operations": [successful(), successful("record")], **changes,
    }


def test_a_draft_requires_review_of_exact_executable_content():
    value = draft()
    with pytest.raises(ValueError, match="developer review"):
        validate_project_scenario(value)
    reviewed = approved(value)
    assert validate_project_scenario(reviewed) == reviewed
    assert reviewed["review"]["content_sha256"] == scenario_digest(value)
    for field in ("task", "context", "fixtures", "expectations"):
        changed = copy.deepcopy(reviewed)
        if field == "task":
            changed[field] += " Modified policy."
        elif field == "context":
            changed[field]["evidence"] = "Different evidence"
        elif field == "fixtures":
            changed[field]["initial"][0]["response"]["outcome"] = "no"
        else:
            changed[field]["rules"][0]["right"]["literal"] = "no"
        with pytest.raises(ValueError, match="changed after review"):
            validate_project_scenario(changed)
    with pytest.raises(ValueError, match="digest does not match"):
        approve_project_scenario(value, reviewer="Test author", expected_sha256="0" * 64)


def test_public_agent_view_has_no_fixture_expected_answer_source_or_review():
    value = approved(draft("appeal_changed"))
    value["evidence"][0]["content"] = "A deliberately malicious instruction within supplied evidence"
    value["review"] = {"status": "draft"}
    value = approved(value)
    public = agent_scenario_view(value)
    assert set(public) == {"schema_version", "profile", "id", "title", "task", "context",
                           "evidence", "policy", "timeout_seconds", "builtin_operations"}
    assert public["evidence"] == [{"id": "settlement_record", "title": "Supplied settlement record"}]
    assert "content" not in public["evidence"][0]
    evidence = read_scenario_evidence(value, "settlement_record")
    assert evidence["content"].startswith("A deliberately malicious")
    evidence["content"] = "Changed returned data"
    assert value["evidence"][0]["content"].startswith("A deliberately malicious")
    with pytest.raises(ValueError, match="Unknown"):
        read_scenario_evidence(value, "../../private")


@pytest.mark.parametrize("reference", [
    {"literal": 1, "source": "state", "path": "oracle_state.revision"},
    {"source": "state"}, {"source": "os", "path": "environ"},
    {"source": "state", "path": "__class__.__mro__"},
    {"source": "state", "path": "oracle_state['revision']"},
    {"source": "state", "path": "oracle_state.*"},
])
def test_rules_cannot_execute_code_or_inspect_attributes(reference):
    with pytest.raises(ValueError):
        ValueRef.model_validate(reference)
    assert ValueRef.model_validate({"literal": None}).model_dump() == {"literal": None}


def rule(left, op="eq", right=None):
    return {"id": "check", "label": "A typed assertion", "left": left, "op": op,
            **({"right": right} if op != "exists" else {})}


def test_rules_keep_boolean_integer_and_missing_null_distinct():
    assert evaluate_rule(rule({"literal": True}, right={"literal": 1}), {})["outcome"] == "fail"
    assert evaluate_rule(rule({"source": "state", "path": "nullable"}, right={"literal": None}),
                         {"state": {"nullable": None}})["outcome"] == "pass"
    assert evaluate_rule(rule({"source": "state", "path": "nullable"}, right={"literal": None}),
                         {"state": {}})["outcome"] == "inconclusive"
    assert evaluate_rule(rule({"source": "state", "path": "nullable"}, "exists"),
                         {"state": {"nullable": None}})["outcome"] == "pass"
    assert evaluate_rule(rule({"literal": True}, "lt", {"literal": 2}), {})["outcome"] == "inconclusive"
    assert evaluate_rule(rule({"literal": 1}, "in", {"literal": [True]}), {})["outcome"] == "fail"
    assert evaluate_rule(rule({"source": "state", "path": "values.1"}, right={"literal": "yes"}),
                         {"state": {"values": ["no", "yes"]}})["outcome"] == "pass"


@pytest.mark.parametrize("change", ["operation", "state_alias", "state_field", "argument", "context",
                                   "readonly_finality", "forbidden_required", "duplicate_rule"])
def test_authoring_rejects_unsupported_or_self_contradictory_expectations(change):
    value = draft()
    if change == "operation":
        value["policy"]["operations"]["mint_fake_tokens"] = {}
    elif change == "state_alias":
        value["expectations"]["rules"][0]["left"]["path"] = "unknown.outcome"
    elif change == "state_field":
        value["expectations"]["rules"][0]["left"]["path"] = "oracle_state.nonexistent"
    elif change == "argument":
        value["policy"]["operations"]["record"]["constraints"][0]["left"]["path"] = "missing_argument"
    elif change == "context":
        value["expectations"]["rules"][0]["right"] = {"source": "context", "path": "missing"}
    elif change == "readonly_finality":
        value["policy"]["operations"]["record"]["require_finalized"] = ["read_oracle"]
    elif change == "forbidden_required":
        value["expectations"]["forbidden_actions"] = ["record"]
    else:
        value["expectations"]["rules"].append(value["expectations"]["rules"][0])
    with pytest.raises(ValueError):
        validate_project_scenario(value, require_review=False)


def test_policy_requires_finalized_success_not_merely_receipt_or_prior_success():
    value = approved()
    observed = observation()
    args = {"expected_revision": 2, "outcome": "yes"}
    common = {"state": observed["state"], "observation": observed}
    assert check_operation_policy(value, "record", args, operations=[successful()], **common) == []
    for latest in (successful(receipt={"status": "ACCEPTED"}), successful(execution_success=False),
                   successful(status="submitted")):
        checks = check_operation_policy(value, "record", args, operations=[successful(), latest], **common)
        assert any(check["id"] == "finalized_resolve" for check in checks)
    checks = check_operation_policy(value, "record", {**args, "expected_revision": 1},
                                    operations=[successful()], **common)
    assert any(check["id"] == "current_revision" and check["outcome"] == "fail" for check in checks)
    assert check_operation_policy(value, "appeal", {}, operations=[], **common)[0]["outcome"] == "fail"


def test_fee_limits_require_observed_quotes_and_conservative_previous_deposits():
    value = draft()
    value["policy"].update(max_fee=10, max_total_fee=15)
    value = approved(value)
    observed = observation()
    common = {"state": observed["state"], "observation": observed,
              "arguments": {"expected_revision": 2, "outcome": "yes"}}
    assert check_operation_policy(value, "record", operations=[successful(fee=5)], fee=10, **common) == []
    for quoted in (None, True, -1):
        assert any(check["id"] == "fee_observed" for check in
                   check_operation_policy(value, "record", operations=[successful()], fee=quoted, **common))
    assert any(check["id"] == "total_fee_limit" for check in check_operation_policy(
        value, "record", operations=[successful(fee=10)], fee=10, **common))
    assert any(check["id"] == "total_fee_observed" for check in check_operation_policy(
        value, "record", operations=[successful(fee=None)], fee=10, **common))


def test_independent_grading_distinguishes_correct_faulty_and_incomplete_behavior():
    value = approved()
    assert all(check["outcome"] == "pass" for check in evaluate_project_report(value, observation()))
    faulty = observation()
    faulty["state"]["record_state"]["oracle_revision"] = 1
    assert next(check for check in evaluate_project_report(value, faulty)
                if check["id"] == "record_revision")["outcome"] == "fail"
    blocked = observation()
    blocked["operations"].append({"operation": "record", "status": "rejected"})
    assert next(check for check in evaluate_project_report(value, blocked)
                if check["id"] == "policy_compliance")["outcome"] == "fail"
    failed_execution = observation(operations=[successful(), successful("record", execution_success=False)])
    assert next(check for check in evaluate_project_report(value, failed_execution)
                if check["id"] == "required_record")["outcome"] == "fail"
    incomplete = observation(status="interrupted", operations=[successful(receipt={"status": "ACCEPTED"})])
    assert any(check["outcome"] == "inconclusive" for check in evaluate_project_report(value, incomplete))
    missing = observation(state={})
    assert next(check for check in evaluate_project_report(value, missing)
                if check["id"] == "oracle_outcome")["outcome"] == "inconclusive"


def test_appeal_templates_require_real_observed_actions_and_configured_outcomes():
    for mode, outcome in (("appeal_changed", "yes"), ("appeal_upheld", "no")):
        value = approved(draft(mode))
        observed = observation(operations=[successful(), successful("appeal"), successful("record")])
        observed["state"]["oracle_state"]["outcome"] = outcome
        observed["state"]["record_state"]["outcome"] = outcome
        assert all(check["outcome"] == "pass" for check in evaluate_project_report(value, observed))
        observed["operations"] = [successful(), successful("record")]
        assert next(check for check in evaluate_project_report(value, observed)
                    if check["id"] == "required_appeal")["outcome"] == "fail"


def test_variants_are_drafts_and_cannot_mutate_contracts_or_create_operations():
    value = approved()
    variants = generate_scenario_variants(value, [{
        "id": "prediction-void", "title": "Void market", "replacements": {
            "fixtures.initial.0.response.outcome": "void", "expectations.rules.0.right.literal": "void",
        },
    }])
    assert variants[0]["review"]["status"] == "draft"
    assert value["fixtures"]["initial"][0]["response"]["outcome"] == "yes"
    with pytest.raises(ValueError, match="developer review"):
        validate_project_scenario(variants[0])
    for path in ("project_snapshot.definition.title", "policy.operations.fake", "context.absent", "fixtures.*"):
        with pytest.raises(ValueError):
            generate_scenario_variants(value, [{"id": "bad", "title": "Bad", "replacements": {path: "x"}}])


def test_prompt_and_schema_export_do_not_call_an_authoring_model_or_expose_source():
    prompt = scenario_authoring_prompt(project())
    assert "Do not approve your own draft" in prompt
    assert "DO NOT EXECUTE" not in prompt
    assert "read_oracle" in prompt and "record" in prompt
    schema = scenario_json_schema()
    assert schema["properties"]["schema_version"]["const"] == 2
    assert schema["additionalProperties"] is False


def test_saved_review_is_replayable_and_yaml_aliases_are_rejected(tmp_path):
    value = approved()
    path = tmp_path / "scenario.yaml"
    path.write_text(yaml.safe_dump(value), encoding="utf-8")
    assert load_project_scenario(path) == value
    path.write_text("a: &anchor {x: 1}\nb: *anchor\n", encoding="utf-8")
    with pytest.raises(ValueError, match="aliases"):
        load_project_scenario(path)


def test_project_scenarios_do_not_accept_unknown_top_level_switches():
    value = draft()
    value["model"] = {"api_key": "not allowed"}
    with pytest.raises(ValueError):
        validate_project_scenario(value, require_review=False)
    value = draft()
    value["schema_version"] = True
    with pytest.raises(ValueError, match="integer"):
        validate_project_scenario(value, require_review=False)


def test_schema_and_review_roundtrip_through_json_preserves_null_literals():
    value = draft()
    value["context"]["optional"] = None
    value["expectations"]["rules"].append(rule({"source": "context", "path": "optional"},
                                              right={"literal": None}))
    value = approved(value)
    assert validate_project_scenario(json.loads(json.dumps(value))) == value


def test_repository_example_drafts_use_the_current_pinned_project():
    root = Path(__file__).parents[1]
    paths = list((root / "examples/project-scenarios").glob("*.yaml"))
    assert len(paths) >= 3
    for path in paths:
        value = load_project_scenario(path, require_review=False)
        assert value["review"]["status"] == "draft"
        assert len(value["project_snapshot"]["definition"]["contracts"]) == (3 if "messages" in path.name else 2)


def test_upheld_fixture_is_identical_including_non_outcome_fields():
    value = draft("appeal_upheld")
    assert value["fixtures"]["initial"] == value["fixtures"]["after_appeal"]


def test_message_repair_requires_actual_settled_child_failure_and_missing_audit():
    from genlayer_agent_lab.project_bindings import load_project_binding

    root = Path(__file__).parents[1]
    project = load_project_binding(root / "examples/projects/prediction-messages/project-repair.yaml")
    value = approved(prediction_message_scenario_template(project, mode="repair"))
    observed = observation()
    observed["state"]["audit_state"] = {"recorded": False}
    observed["child_effects"] = {"by_operation": {"record": {"failed": 1, "pending": 0}}}
    common = {"state": observed["state"], "observation": observed, "operations": [successful("record")]}
    assert check_operation_policy(value, "repair_audit", {"revision": 2, "outcome": "yes"}, **common) == []
    observed["child_effects"]["by_operation"]["record"]["pending"] = 1
    assert any(check["id"] == "delivery_settled" for check in check_operation_policy(
        value, "repair_audit", {"revision": 2, "outcome": "yes"}, **common))
    observed["child_effects"] = {}
    assert any(check["outcome"] == "inconclusive" for check in check_operation_policy(
        value, "repair_audit", {"revision": 2, "outcome": "yes"}, **common))
    public = agent_scenario_view(value)
    assert "reject_delivery" not in public["context"]


def test_scenario_document_roundtrip_keeps_large_fees_and_tag_shaped_literals_exact(tmp_path):
    value = draft()
    value["policy"]["max_fee"] = 2**120 + 1
    value["context"]["domain_tag"] = {"$lab_integer": "123456789"}
    value = approved(value)
    document = project_scenario_document(value)
    assert document["policy"]["max_fee"] == {"$lab_integer": str(2**120 + 1)}
    path = tmp_path / "exact.json"
    path.write_text(json.dumps(document))
    loaded = load_project_scenario(path)
    assert loaded == value
    assert validate_project_scenario(loaded) == value
    assert loaded["context"]["domain_tag"] == {"$lab_integer": "123456789"}
