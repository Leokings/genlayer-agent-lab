"""Public tool discovery and rejected-call recovery through MCP and the journal."""

import asyncio
import copy
import json

import httpx
import pytest
from mcp.client import Client
from test_project_workflows import complete_record, finish, operation, scenario, start, wait
from test_project_workflows import harness as harness

from genlayer_agent_lab.client import LabClient
from genlayer_agent_lab.mcp_server import build_server
from genlayer_agent_lab.project_builtin_tools import ARGUMENT_MODELS, builtin_argument_error
from genlayer_agent_lab.project_scenarios import agent_scenario_view


def all_builtins(draft):
    draft["policy"]["operations"]["submit_investigation"] = {"max_calls": 4}


def test_discovery_covers_permitted_builtins_without_private_scenario_data():
    reviewed = scenario("appeal_changed", change=all_builtins)
    before = copy.deepcopy(reviewed)
    public = agent_scenario_view(reviewed)
    contracts = public["builtin_operations"]
    assert set(contracts) == set(ARGUMENT_MODELS)
    for name, contract in contracts.items():
        assert contract["arguments_schema"]["additionalProperties"] is False
        assert contract["example"]["operation"] == name
        assert builtin_argument_error(reviewed, name, contract["example"]["arguments"]) is None
    evidence = contracts["read_evidence"]
    assert evidence["arguments_schema"]["required"] == ["id"]
    assert evidence["example"]["arguments"] == {"id": reviewed["evidence"][0]["id"]}
    assert contracts["submit_investigation"]["arguments_schema"] == public["investigation_submission_schema"]
    assert "expected_decision_id" in contracts["submit_investigation"]["example"]
    assert contracts["appeal"]["example"]["arguments"] == {}
    encoded = json.dumps(contracts)
    for private in (reviewed["review"]["content_sha256"], reviewed["evidence"][0]["content"],
                    reviewed["fixtures"]["initial"][0]["prefix"]):
        assert private not in encoded
    assert reviewed == before
    public["builtin_operations"]["read_evidence"]["example"]["arguments"]["id"] = "mutated"
    fresh = agent_scenario_view(reviewed)["builtin_operations"]["read_evidence"]
    assert fresh["example"]["arguments"] == {"id": reviewed["evidence"][0]["id"]}
    disabled = agent_scenario_view(scenario())["builtin_operations"]
    assert "appeal" not in disabled and "submit_investigation" not in disabled


@pytest.mark.parametrize("name,arguments,field", [
    ("read_evidence", {"evidence_id": "private-input-value"}, "arguments.id"),
    ("read_evidence", {"id": "settlement_record", "extra": "private-input-value"}, "arguments.extra"),
    ("read_evidence", {"id": ["private-input-value"]}, "arguments.id"),
    ("inspect_fees", {"method": "private-input-value"}, "arguments.operation"),
    ("inspect_appeal", {"expected_decision_id": "private-input-value"}, "arguments.decision_id"),
    ("appeal", {"decision_id": "private-input-value"}, "arguments.decision_id"),
    ("submit_investigation", {"summary": "private-input-value"}, "arguments.findings"),
])
def test_argument_errors_identify_fields_without_echoing_values(name, arguments, field):
    detail = builtin_argument_error(scenario("appeal_changed", change=all_builtins), name, arguments)
    assert field in detail
    assert "private-input-value" not in detail
    assert "builtin_operations" in detail and "new idempotency key" in detail


def test_fee_inspection_describes_nested_contract_arguments():
    reviewed = scenario()
    detail = builtin_argument_error(reviewed, "inspect_fees", {"operation": "resolve"})
    assert "arguments.arguments" in detail and "evidence" in detail
    assert builtin_argument_error(reviewed, "inspect_fees", {
        "operation": "record", "arguments": {"expected_revision": "1", "outcome": "yes"}}) is None
    assert "binding.operations" in builtin_argument_error(reviewed, "inspect_fees", {
        "operation": "not_declared", "arguments": {}})


@pytest.mark.parametrize("name,arguments,field", [
    ("inspect_fees", {"operation": "resolve"}, "evidence"),
    ("inspect_appeal", {"expected_decision_id": "d1"}, "arguments.decision_id"),
    ("appeal", {"decision_id": "d1"}, "arguments.decision_id"),
    ("submit_investigation", {"summary": "Read evidence first"}, "arguments.findings"),
])
def test_malformed_builtin_is_actionable_and_never_submits_transaction(harness, name, arguments, field):
    chain, factory = harness
    manager = factory()
    run_id = start(manager, scenario("appeal_changed", change=all_builtins))["run_id"]
    before = len(chain.prepared)
    rejected = operation(manager, run_id, name, arguments)
    assert rejected["status"] == "rejected" and field in rejected["error_detail"]
    assert len(chain.prepared) == before
    assert manager.get(run_id)["operations"][0]["error_detail"] == rejected["error_detail"]


def test_real_mcp_discovery_corrects_evidence_shape_but_preserves_failed_attempt(harness):
    _, factory = harness
    manager = factory()
    run_id = start(manager)["run_id"]

    def handler(request):
        if request.url.path.endswith("/observe"):
            return httpx.Response(200, json=manager.observe(run_id))
        assert request.url.path.endswith("/operations")
        supplied = json.loads(request.content)
        return httpx.Response(202, json=manager.invoke(run_id, supplied["operation"],
            supplied["arguments"], supplied["idempotency_key"], supplied.get("expected_decision_id")))

    async def exercise():
        with LabClient("http://127.0.0.1:8765", "run-token", transport=httpx.MockTransport(handler)) as lab:
            server = build_server(token="run-token", run_id=run_id, client=lab, mode="workflow")
            async with Client(server) as connection:
                descriptions = {tool.name: tool.description for tool in (await connection.list_tools()).tools}
                assert 'arguments={"id":' in descriptions["invoke_operation"]
                bad = {"operation": "read_evidence", "arguments": {"evidence_id": "settlement_record"},
                       "idempotency_key": "original-bad-read"}
                await connection.call_tool("invoke_operation", bad)
                rejected = wait(lambda: manager.observe(run_id)["operations"],
                                lambda ops: any(item["status"] == "rejected" for item in ops))[0]
                assert rejected["error_code"] == "invalid_evidence_request"
                assert "id, not evidence_id" in rejected["error_detail"]
                observed = (await connection.call_tool("observe", {})).structured_content
                assert observed["operations"][0]["error_detail"] == rejected["error_detail"]
                corrected = observed["builtin_operations"]["read_evidence"]["example"]
                corrected["idempotency_key"] = "corrected-read"
                await connection.call_tool("invoke_operation", corrected)
                wait(lambda: manager.observe(run_id)["evidence_reads"], lambda reads: reads.get("settlement_record") == 1)
                retried = (await connection.call_tool("invoke_operation", bad)).structured_content
                assert retried["status"] == "rejected" and retried["error_detail"] == rejected["error_detail"]
                assert len(manager.observe(run_id)["operations"]) == 2
                return rejected["error_detail"]

    detail = asyncio.run(exercise())
    manager.close()
    manager = factory()
    wait(lambda: manager.observe(run_id)["status"], lambda value: value == "running")
    assert manager.observe(run_id)["operations"][0]["error_detail"] == detail
    operation(manager, run_id, "resolve", {"evidence": "Public evidence was read."})
    complete_record(manager, run_id)
    report = finish(manager, run_id)
    checks = {check["id"]: check for check in report["checks"]}
    assert report["verification"] == "fail"
    assert checks["transactions_finalized"]["outcome"] == "pass"
    assert checks["policy_compliance"]["outcome"] == "fail"
    assert checks["behavior"]["detail"] == ["invalid_evidence_request"]
    assert report["operations"][0]["error_detail"] == detail
    assert report["operations"][1]["status"] == "completed"
