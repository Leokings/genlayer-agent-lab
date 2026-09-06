"""Exercise the real SDK against an isolated fake RPC and reject unsafe receipts."""

import base64
import copy
import json
import threading
import time

import httpx
import pytest
from eth_account import Account
from genlayer_py.abi import calldata
from genlayer_py.chains import localnet
from web3 import Web3

from genlayer_agent_lab.bindings import _definition, _snapshot
from genlayer_agent_lab.runtime import studio
from genlayer_agent_lab.runtime.worker import CONTRACT

TX = "0x" + "a" * 64
ADDRESS = "0x" + "b" * 40
SENDER = "0x" + "c" * 40
ENDPOINT = "http://127.0.0.1:4099/api"
SECRET = "sk-private-provider-and-validator-material"


def raw_receipt(status="ACCEPTED", value="approve", *, success=True):
    return {
        "hash": TX, "to_address": ADDRESS, "status": status, "execution_mode": "NORMAL",
        "leader_only": False, "timestamp_appeal": None, "appealed": False,
        "sim_config": {"api_key": SECRET},
        "consensus_data": {
            "leader_receipt": [{
                "execution_result": "SUCCESS" if success else "ERROR",
                "result": base64.b64encode(b"\0" + calldata.encode(value)).decode(),
                "node_config": {"private_key": SECRET, "plugin_config": {"key": SECRET}},
            }], "votes": {SENDER: "agree"},
        },
        "consensus_history": {"consensus_results": [{
            "consensus_round": "Accepted", "validator_results": [
                {"vote": "agree", "node_config": {"private_key": SECRET}},
            ],
        }]},
    }


class FakeRPC:
    def __init__(self):
        self.calls = []
        self.receipts = [raw_receipt()]
        self.chain_id = hex(studio.CHAIN_ID)
        self.abi = copy.deepcopy(localnet.consensus_main_contract["abi"])
        for item in self.abi:
            if item.get("name") == "addTransaction":
                item["inputs"] = item["inputs"][:5]
        self.readonly = False
        self.deployed_code = CONTRACT.read_bytes()
        self.appeal_effect = True
        self.sent = []
        self.override = None

    def handle(self, request):
        payload = json.loads(request.content)
        method, params = payload["method"], payload["params"]
        self.calls.append((method, params))
        if self.override:
            overridden = self.override(payload)
            if overridden is not None:
                return overridden
        if method == "eth_chainId":
            result = self.chain_id
        elif method == "sim_getConsensusContract":
            result = {"address": ADDRESS, "abi": self.abi, "bytecode": ""}
        elif method == "sim_getFinalityWindowTime":
            result = "30"
        elif method == "gen_getContractSchema":
            result = {"methods": {"evaluate": {"readonly": self.readonly}}}
        elif method == "gen_getContractCode":
            result = base64.b64encode(self.deployed_code).decode()
        elif method == "eth_getTransactionCount":
            result = "0x0"
        elif method == "eth_estimateGas":
            result = "0x7a120"
        elif method == "eth_sendRawTransaction":
            signed = params[0]
            self.sent.append(signed)
            # Signatures must really be recoverable; this isn't a fake SDK client.
            assert Account.recover_transaction(signed)
            if len(self.sent) > 1 and self.appeal_effect:
                self.receipts[-1]["timestamp_appeal"] = 123
                self.receipts[-1]["appealed"] = True
            result = TX
        elif method == "eth_getTransactionReceipt":
            result = {
                "transactionHash": TX, "transactionIndex": "0x0", "blockHash": TX,
                "blockNumber": "0x0", "from": SENDER, "to": ADDRESS,
                "cumulativeGasUsed": "0x1", "gasUsed": "0x1", "status": "0x1",
                "logs": [{
                    "address": ADDRESS, "topics": [
                        Web3.to_hex(Web3.keccak(text="NewTransaction(bytes32,address,address)")),
                        TX, "0x" + "0" * 24 + ADDRESS[2:], "0x" + "0" * 24 + SENDER[2:],
                    ], "data": "0x", "blockNumber": 0, "transactionHash": TX,
                    "transactionIndex": 0, "blockHash": TX, "logIndex": 0, "removed": False,
                }],
            }
        elif method == "eth_getTransactionByHash":
            result = copy.deepcopy(self.receipts[0])
            if len(self.receipts) > 1:
                self.receipts.pop(0)
        else:
            raise AssertionError(f"Unexpected method {method}")
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": payload["id"], "result": result})


@pytest.fixture
def rpc_client():
    rpc = FakeRPC()
    with studio.StudioClient(ENDPOINT, timeout=2) as client:
        client._provider._http.close()
        client._provider._http = httpx.Client(transport=httpx.MockTransport(rpc.handle),
                                            trust_env=False, follow_redirects=False)
        yield rpc, client


@pytest.fixture
def binding():
    definition = _definition({
        "id": "studio-escrow", "title": "Studio escrow", "source": "custom.py",
        "method": "evaluate", "arguments": [{"from_field": "evidence"}],
        "llm_pattern": "^AGENT_LAB_EVIDENCE_V1", "llm_response": {"verdict": "$fixture_verdict"},
    })
    return _snapshot(definition, CONTRACT.read_text(encoding="utf-8"))


@pytest.mark.parametrize("endpoint", [
    "https://studio.genlayer.com/api", "http://localhost:4000/api", "http://10.0.0.1:4000/api",
    "http://127.0.0.1:4000/api?secret=x", "http://user:pass@127.0.0.1:4000/api",
    "http://127.0.0.1/api", "http://127.0.0.1:4000/api#x", "file:///tmp/api",
])
def test_rejects_non_literal_loopback_or_credential_endpoints(endpoint):
    with pytest.raises(studio.StudioError, match="endpoint_must_be_literal_loopback_http"):
        studio.StudioClient(endpoint)


def test_doctor_checks_rpc_and_distinguishes_compatibility_from_release_identity(rpc_client):
    rpc, client = rpc_client
    result = client.doctor()
    assert result["ready"] is True
    assert result["chain_id"] == 61999
    assert result["finality_window_seconds"] == 30
    assert result["release_verified"] is False
    assert result["bond_accounting"] is False
    assert result["public_chain"] is False
    assert not rpc.sent


def test_chain_mismatch_prevents_signing_and_submission(rpc_client, binding):
    rpc, client = rpc_client
    rpc.chain_id = "0x1"
    with pytest.raises(studio.StudioError, match="chain_id_mismatch"):
        client.deploy(binding)
    assert rpc.sent == []


def test_abi_mismatch_never_falls_back_to_installed_sdk_abi(rpc_client, binding):
    rpc, client = rpc_client
    rpc.abi = []
    assert client.doctor()["error_code"] == "consensus_abi_mismatch"
    with pytest.raises(studio.StudioError, match="consensus_abi_mismatch"):
        client.deploy(binding)
    assert rpc.sent == []


def test_real_sdk_signs_deployment_and_write_with_distinct_contract_payloads(rpc_client, binding):
    rpc, client = rpc_client
    original = copy.deepcopy(localnet)
    assert client.deploy(binding, sim_config={"validators": []}) == TX
    assert client.write(Web3.to_checksum_address(ADDRESS), binding, {"evidence": "delivery"}) == TX
    assert len(rpc.sent) == 2
    assert rpc.sent[0] != rpc.sent[1]
    assert rpc.calls[[m for m, _ in rpc.calls].index("eth_sendRawTransaction")][1][1] == {"validators": []}
    assert localnet == original


def test_readonly_schema_prevents_write(rpc_client, binding):
    rpc, client = rpc_client
    rpc.readonly = True
    with pytest.raises(studio.StudioError, match="public_write_method_required"):
        client.write(ADDRESS, binding, {"evidence": "delivery"})
    assert rpc.sent == []


def test_deployed_source_must_match_the_bound_snapshot(rpc_client, binding):
    rpc, client = rpc_client
    rpc.deployed_code = b"different contract"
    with pytest.raises(studio.StudioError, match="deployed_source_mismatch"):
        client.write(ADDRESS, binding, {"evidence": "delivery"})
    assert rpc.sent == []


def test_snapshot_exposes_actual_value_and_no_provider_or_validator_secrets(rpc_client):
    rpc, client = rpc_client
    result = client.transaction(TX)
    assert result["raw_result"] == "approve"
    assert result["execution_success"] is True
    assert result["rounds"] == [{"index": 0, "kind": "Accepted", "validator_votes": ["agree"]}]
    assert SECRET not in json.dumps(result)
    rpc.receipts = [raw_receipt(value={"decision": "deny"})]
    assert client.transaction(TX)["raw_result"] == {"decision": "deny"}


def test_official_idle_votes_remain_distinct_from_unknown_values(rpc_client):
    rpc, client = rpc_client
    rpc.receipts[0]["consensus_data"]["votes"] = {SENDER: "idle"}
    rpc.receipts[0]["consensus_history"]["consensus_results"][0]["validator_results"] = [
        {"vote": "idle"}, {"vote": "unrecognized"},
    ]
    snapshot = client.transaction(TX)
    assert snapshot["votes"] == ["idle"]
    assert snapshot["rounds"][0]["validator_votes"] == ["idle", "unknown"]


def test_accepted_error_is_never_mistaken_for_success(rpc_client):
    rpc, client = rpc_client
    rpc.receipts = [raw_receipt(success=False)]
    result = client.wait(TX)
    assert result["status"] == "ACCEPTED"
    assert result["execution_success"] is False
    assert result["raw_result"] is None


@pytest.mark.parametrize("malformed", ["", "not-base64-!", base64.b64encode(b"\0broken").decode()])
def test_malformed_success_result_is_rejected(rpc_client, malformed):
    rpc, client = rpc_client
    rpc.receipts[0]["consensus_data"]["leader_receipt"][0]["result"] = malformed
    with pytest.raises(studio.StudioError, match="malformed_execution_result"):
        client.transaction(TX)


def test_receipt_identity_and_execution_mode_are_verified(rpc_client):
    rpc, client = rpc_client
    rpc.receipts[0]["hash"] = "0x" + "d" * 64
    with pytest.raises(studio.StudioError, match="receipt_identity_mismatch"):
        client.transaction(TX)
    rpc.receipts = [raw_receipt()]
    rpc.receipts[0]["execution_mode"] = "LEADER_ONLY"
    with pytest.raises(studio.StudioError, match="normal_consensus_required"):
        client.transaction(TX)


def test_finality_wait_does_not_stop_at_acceptance(rpc_client):
    rpc, client = rpc_client
    rpc.receipts = [raw_receipt(), raw_receipt("FINALIZED")]
    assert client.wait(TX, until="finalized")["status"] == "FINALIZED"
    assert [m for m, _ in rpc.calls].count("eth_getTransactionByHash") == 2


def test_wait_deadline_and_cancellation_are_bounded(rpc_client):
    rpc, client = rpc_client
    rpc.receipts = [raw_receipt("PENDING")]
    client.timeout = 0.05
    start = time.monotonic()
    with pytest.raises(studio.StudioError, match="deadline_exceeded"):
        client.wait(TX)
    assert time.monotonic() - start < 1
    client.cancel_event.set()
    count = len(rpc.calls)
    with pytest.raises(studio.StudioError, match="canceled"):
        client.wait(TX)
    assert len(rpc.calls) == count


def test_appeal_observation_is_not_claimed_as_completed_round(rpc_client, binding):
    rpc, client = rpc_client
    client.deploy(binding)
    result = client.appeal(TX)
    assert result["request_observed"] is True
    assert result["appeal_completed"] is False
    assert result["transaction"]["timestamp_appeal"] == 123


def test_appeal_rpc_ack_alone_cannot_pass(rpc_client, binding):
    rpc, client = rpc_client
    client.deploy(binding)
    rpc.appeal_effect = False
    client.timeout = 0.05
    with pytest.raises(studio.StudioError, match="deadline_exceeded"):
        client.appeal(TX)


def test_finalized_transaction_cannot_be_appealed(rpc_client):
    rpc, client = rpc_client
    rpc.receipts = [raw_receipt("FINALIZED")]
    with pytest.raises(studio.StudioError, match="transaction_not_appealable"):
        client.appeal(TX)
    assert not rpc.sent


def test_errors_and_redirects_do_not_expose_or_follow_provider_data(rpc_client):
    rpc, client = rpc_client
    rpc.override = lambda p: httpx.Response(200, json={
        "jsonrpc": "2.0", "id": p["id"], "error": {"code": -32000, "message": SECRET}})
    result = client.doctor()
    assert result["error_code"] == "rpc_rejected"
    assert SECRET not in json.dumps(result)
    rpc.override = lambda p: httpx.Response(302, headers={"Location": "https://example.com/"})
    before = len(rpc.calls)
    assert client.doctor()["error_code"] == "rpc_http_error"
    assert len(rpc.calls) == before + 1


def test_wrong_json_rpc_id_is_rejected(rpc_client):
    rpc, client = rpc_client
    rpc.override = lambda p: httpx.Response(200, json={"jsonrpc": "2.0", "id": -1, "result": "0xf22f"})
    assert client.doctor()["error_code"] == "malformed_rpc_response"


def test_oversized_rpc_response_is_bounded(rpc_client, monkeypatch):
    rpc, client = rpc_client
    monkeypatch.setattr(studio, "MAX_RESPONSE_BYTES", 1024)
    rpc.override = lambda p: httpx.Response(200, content=b"x" * 1025)
    assert client.doctor()["error_code"] == "rpc_response_too_large"


def test_transport_disables_environment_proxy_and_rejects_sdk_version_drift(monkeypatch):
    calls = []
    original = httpx.Client

    def record(*args, **kwargs):
        calls.append(kwargs)
        return original(*args, **kwargs)

    monkeypatch.setattr(studio.httpx, "Client", record)
    with studio.StudioClient(ENDPOINT, cancel_event=threading.Event()):
        pass
    assert calls == [{"trust_env": False, "follow_redirects": False}]
    monkeypatch.setattr(studio, "version", lambda _: "0.19.0rc2")
    with pytest.raises(studio.StudioError, match="sdk_version_mismatch"):
        studio.StudioClient(ENDPOINT)
