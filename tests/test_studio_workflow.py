"""Workflow bridge tests with fake SDK/RPC responses; no Studio or model calls."""

import base64
import copy
import json
from types import SimpleNamespace

import httpx
import pytest
from genlayer_py.abi import calldata
from genlayer_py.types import TransactionHashVariant

from genlayer_agent_lab.runtime import studio
from genlayer_agent_lab.workflow_bindings import bundled_workflow_snapshot

TX = "0x" + "a" * 64
ADDRESS = "0x" + "b" * 40
ENDPOINT = "http://127.0.0.1:4099/api"
SECRET = "private-provider-material"


def state(**changes):
    return {"decision": "partial", "resource_id": "service-001", "policy_version": "v1",
            "evidence": "Delivery receipt", "unit": "test_units", "amount": 100,
            "authorized_amount": 40, "released_amount": 0, "remaining_amount": 100,
            "revision": 1, **changes}


class FakeSDK:
    def __init__(self):
        self.calls = []
        self.result = state()

    def write_contract(self, **kwargs):
        self.calls.append(("write", kwargs))
        return TX

    def deploy_contract(self, **kwargs):
        self.calls.append(("deploy", kwargs))
        return TX

    def read_contract(self, **kwargs):
        self.calls.append(("read", kwargs))
        return copy.deepcopy(self.result)


@pytest.fixture
def workflow_bridge(monkeypatch):
    snapshot = bundled_workflow_snapshot()
    rpc = SimpleNamespace(
        code=base64.b64encode(snapshot["source"].encode()).decode(), calls=[],
        schema={"methods": {"get_state": {"readonly": True},
                            "evaluate": {"readonly": False}, "release": {"readonly": False}}},
    )
    sdk = FakeSDK()
    with studio.StudioClient(ENDPOINT, timeout=2) as client:
        # Compatibility has its own real-SDK coverage in test_studio.py. Here
        # source/schema verification remains real; only its RPC responses vary.
        monkeypatch.setattr(client, "_compatible", lambda: None)
        monkeypatch.setattr(client, "_sdk", sdk)

        def rpc_call(method, params):
            rpc.calls.append((method, params))
            assert params == [ADDRESS]
            if method == "gen_getContractCode":
                return rpc.code
            assert method == "gen_getContractSchema"
            return copy.deepcopy(rpc.schema)

        monkeypatch.setattr(client, "_rpc", rpc_call)
        yield client, snapshot, rpc, sdk


@pytest.mark.parametrize(("code", "error"), [
    (base64.b64encode(b"different deployed implementation").decode(), "deployed_source_mismatch"),
    ("!not-base64", "malformed_deployed_source"),
])
def test_workflow_source_mismatch_prevents_any_sdk_action(workflow_bridge, code, error):
    client, snapshot, rpc, sdk = workflow_bridge
    rpc.code = code
    with pytest.raises(studio.StudioError, match=error):
        client.write_workflow(ADDRESS, snapshot, "release", {
            "requested_amount": 40, "resource_id": "service-001", "policy_version": "v1",
        })
    assert not sdk.calls
    assert [name for name, _ in rpc.calls] == ["gen_getContractCode"]


@pytest.mark.parametrize("schema", [
    None, [], {}, {"methods": []}, {"methods": {}}, {"methods": {"release": []}},
    {"methods": {"release": {}}}, {"methods": {"release": {"readonly": True}}},
    {"methods": {"release": {"readonly": 0}}},
])
def test_workflow_schema_mismatch_prevents_write(workflow_bridge, schema):
    client, snapshot, rpc, sdk = workflow_bridge
    rpc.schema = schema
    with pytest.raises(studio.StudioError, match="workflow_method_schema_mismatch"):
        client.write_workflow(ADDRESS, snapshot, "release", {
            "requested_amount": 40, "resource_id": "service-001", "policy_version": "v1",
        })
    assert not sdk.calls


def test_workflow_wrong_operation_direction_and_snapshot_tampering_fail_before_rpc(workflow_bridge):
    client, snapshot, rpc, sdk = workflow_bridge
    for operation in ("get_state", "undeclared"):
        with pytest.raises(studio.StudioError, match="unsupported_workflow_operation"):
            client.write_workflow(ADDRESS, snapshot, operation, {})
    with pytest.raises(studio.StudioError, match="unsupported_workflow_operation"):
        client.read_workflow(ADDRESS, snapshot, "release", {})
    snapshot["source"] += "\n# " + SECRET
    with pytest.raises(studio.StudioError) as error:
        client.write_workflow(ADDRESS, snapshot, "release", {})
    assert error.value.code == "sdk_operation_failed"
    assert SECRET not in str(error.value)
    assert not sdk.calls and not rpc.calls


@pytest.mark.parametrize(("finalized", "variant"), [
    (None, TransactionHashVariant.LATEST_FINAL),
    (True, TransactionHashVariant.LATEST_FINAL),
    (False, TransactionHashVariant.LATEST_NONFINAL),
])
def test_workflow_read_selects_snapshot_and_preserves_partial_structured_result(
        workflow_bridge, finalized, variant):
    client, snapshot, _, sdk = workflow_bridge
    options = {} if finalized is None else {"finalized": finalized}
    result = client.read_workflow(ADDRESS, snapshot, "get_state", {}, **options)
    assert result == state()
    assert sdk.calls == [("read", {"address": ADDRESS, "function_name": "get_state", "args": [],
                                    "transaction_hash_variant": variant})]
    result["decision"] = "deny"
    assert sdk.result["decision"] == "partial"


def test_workflow_read_rejects_write_method_and_invalid_domain_result(workflow_bridge):
    client, snapshot, rpc, sdk = workflow_bridge
    rpc.schema["methods"]["get_state"]["readonly"] = False
    with pytest.raises(studio.StudioError, match="workflow_method_schema_mismatch"):
        client.read_workflow(ADDRESS, snapshot, "get_state", {})
    assert not sdk.calls
    rpc.schema["methods"]["get_state"]["readonly"] = True
    sdk.result = {"decision": "approve", "source": SECRET}
    with pytest.raises(studio.StudioError) as error:
        client.read_workflow(ADDRESS, snapshot, "get_state", {})
    assert error.value.code == "sdk_operation_failed"
    assert SECRET not in str(error.value)


def test_workflow_deploy_and_write_use_pinned_source_declared_arguments_and_normal_consensus(
        workflow_bridge):
    client, snapshot, _, sdk = workflow_bridge
    assert client.deploy_workflow(snapshot) == TX
    assert client.write_workflow(ADDRESS, snapshot, "release", {
        "requested_amount": 40, "resource_id": "service-001", "policy_version": "v1",
        "irrelevant_secret": SECRET,
    }) == TX
    assert sdk.calls == [
        ("deploy", {"code": snapshot["source"].encode(), "args": ["service-001", "v1", 100],
                    "leader_only": False, "consensus_max_rotations": 3}),
        ("write", {"address": ADDRESS, "function_name": "release", "args": [40, "service-001", "v1"],
                   "leader_only": False, "consensus_max_rotations": 3}),
    ]


def leader(value, *, success=True):
    return {"execution_result": "SUCCESS" if success else "ERROR",
            "result": base64.b64encode(b"\0" + calldata.encode(value)).decode(),
            "node_config": {"private_key": SECRET}}


@pytest.mark.parametrize("successful", [True, False])
def test_receipt_history_uses_execution_entry_zero_never_validation_entry_one(successful):
    prior, current = state(decision="deny", authorized_amount=0), state()
    raw = {
        "hash": TX, "to_address": ADDRESS, "status": "ACCEPTED",
        "consensus_data": {"leader_receipt": [leader(current, success=successful), leader(True)]},
        "consensus_history": {"consensus_results": [
            {"consensus_round": "Accepted", "leader_result": [leader(prior), leader(True)],
             "validator_results": [{"vote": "agree", "node_config": {"private_key": SECRET}}]},
            {"consensus_round": "Validator Appeal Successful",
             "leader_result": [leader(current, success=successful), leader(True)]},
        ]},
    }
    result = studio._snapshot(raw, TX)
    assert result["execution_success"] is successful
    assert result["raw_result"] == (current if successful else None)
    assert result["rounds"][0]["raw_result"] == prior
    assert result["rounds"][1]["raw_result"] == (current if successful else None)
    assert result["rounds"][1]["execution_success"] is successful
    assert result["rounds"][0]["validator_votes"] == ["agree"]
    assert SECRET not in json.dumps(result)


@pytest.mark.parametrize("operation", ["deploy", "write"])
def test_submission_callback_persists_hash_before_sdk_receipt_timeout(workflow_bridge, operation):
    client, snapshot, _, sdk = workflow_bridge
    order = []
    persisted = []

    def transport(request):
        payload = json.loads(request.content)
        method = payload["method"]
        order.append(method)
        if method == "eth_sendRawTransaction":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": payload["id"], "result": TX})
        assert method == "eth_getTransactionReceipt"
        assert persisted == [TX], "Submission must be recorded before receipt retrieval"
        raise httpx.ReadTimeout(SECRET)

    def persist(tx_id):
        persisted.append(tx_id)
        order.append("persist")

    client._provider._http.close()
    client._provider._http = httpx.Client(transport=httpx.MockTransport(transport), trust_env=False)
    client._provider.on_submission = persist

    def submit_and_wait(**kwargs):
        client._provider.make_request("eth_sendRawTransaction", ["signed-test-envelope"])
        client._provider.make_request("eth_getTransactionReceipt", [TX])
        pytest.fail("The fake SDK receipt query must time out")

    sdk.deploy_contract = submit_and_wait
    sdk.write_contract = submit_and_wait
    with pytest.raises(studio.StudioError) as error:
        if operation == "deploy":
            client.deploy_workflow(snapshot)
        else:
            client.write_workflow(ADDRESS, snapshot, "release", {
                "requested_amount": 40, "resource_id": "service-001", "policy_version": "v1",
            })
    assert error.value.code == "rpc_timeout"
    assert SECRET not in str(error.value)
    assert persisted == [TX]
    assert order == ["eth_sendRawTransaction", "persist", "eth_getTransactionReceipt"]


def test_dispatch_attempt_is_recorded_before_transport_but_not_for_reads():
    provider = studio._LoopbackProvider(ENDPOINT, lambda: 5)
    order = []
    provider.on_submission_attempt = lambda: order.append("dispatch")

    def transport(request):
        payload = json.loads(request.content)
        order.append(payload["method"])
        if payload["method"] == "eth_sendRawTransaction":
            assert order[-2:] == ["dispatch", "eth_sendRawTransaction"]
            raise httpx.ReadTimeout("unknown submission outcome")
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": payload["id"], "result": None})

    provider._http.close()
    provider._http = httpx.Client(transport=httpx.MockTransport(transport), trust_env=False)
    try:
        provider.make_request("eth_getTransactionByHash", [TX])
        assert order == ["eth_getTransactionByHash"]
        with pytest.raises(studio.StudioError, match="rpc_timeout"):
            provider.make_request("eth_sendRawTransaction", ["signed-test-envelope"])
        assert order == ["eth_getTransactionByHash", "dispatch", "eth_sendRawTransaction"]
    finally:
        provider.close()


@pytest.mark.parametrize("outcome", [
    {"result": "not-a-transaction-id"},
    {"error": {"code": -32000, "message": SECRET}},
])
def test_submission_callback_does_not_record_rejected_or_malformed_acknowledgement(outcome):
    def transport(request):
        payload = json.loads(request.content)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": payload["id"], **outcome})

    provider = studio._LoopbackProvider(ENDPOINT, lambda: 2)
    provider._http.close()
    provider._http = httpx.Client(transport=httpx.MockTransport(transport), trust_env=False)
    persisted = []
    provider.on_submission = persisted.append
    try:
        with pytest.raises(studio.StudioError) as error:
            provider.make_request("eth_sendRawTransaction", ["signed-test-envelope"])
        assert SECRET not in str(error.value)
        assert not persisted
    finally:
        provider.close()
