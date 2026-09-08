"""Protocol boundary checks; real Studio evidence is recorded separately."""
import base64
import copy

import pytest
import rlp
from eth_abi import decode
from eth_account import Account
from genlayer_py.abi import calldata
from web3 import Web3

from genlayer_agent_lab.runtime.studio import StudioError
from genlayer_agent_lab.runtime.studio_modern import (
    DISTRIBUTION_FIELDS,
    PARAMS_TYPE,
    ZERO,
    StudioModernClient,
    _payload,
)
from genlayer_agent_lab.runtime.studio_profiles import CHAIN_ID, PROFILE

ADDRESS = "0x" + "11" * 20
TX_ID = "0x" + "22" * 32


def client(monkeypatch):
    result = StudioModernClient("http://127.0.0.1:8796", account=Account.from_key(b"\x07" * 32))
    result._consensus = ADDRESS
    monkeypatch.setattr(result, "_compatible", lambda: None)
    return result


def distribution():
    result = dict.fromkeys(DISTRIBUTION_FIELDS, 1)
    result.update(rotations=[0, 0], executionConsumed=0, totalMessageFees=0)
    return result


def quote(client, code):
    import hashlib
    return {"fee_value": 1234, "distribution": distribution(), "message_allocations": [],
        "num_validators": 5, "call_sha256": hashlib.sha256(_payload(None, [], code)).hexdigest(),
        "kind": "deploy", "recipient": ZERO, "sender": client.address, "user_value": 0}


def test_prepare_is_network_read_only_and_signs_full_fee_envelope(monkeypatch):
    instance = client(monkeypatch)
    calls = []

    def rpc(method, params):
        calls.append((method, params))
        assert method == "eth_getTransactionCount"
        return "0x9"

    monkeypatch.setattr(instance, "_rpc", rpc)
    prepared = instance.prepare_deploy(b"contract", [], quote=quote(instance, b"contract"))
    assert [method for method, _ in calls] == ["eth_getTransactionCount"]
    raw = bytes.fromhex(prepared["raw_transaction"][2:])
    assert prepared["tx_id"] == "0x" + Web3.keccak(raw).hex()
    assert Account.recover_transaction(raw) == instance.address
    envelope = rlp.decode(raw)
    assert int.from_bytes(envelope[0]) == 9
    assert int.from_bytes(envelope[4]) == 1234
    assert (int.from_bytes(envelope[6]) - 35) // 2 == CHAIN_ID
    params, = decode([PARAMS_TYPE], envelope[5][4:])
    assert params[2] == 5
    assert params[7][6] == (0, 0)
    assert rlp.decode(params[8])[0] == b"contract"


def test_quote_cannot_be_reused_for_different_contract(monkeypatch):
    instance = client(monkeypatch)
    with pytest.raises(StudioError, match="fee_quote_identity_mismatch"):
        instance.prepare_deploy(b"different", [], quote=quote(instance, b"contract"))


def test_submit_replays_same_bytes_and_checks_prepared_fee(monkeypatch):
    instance = client(monkeypatch)
    monkeypatch.setattr(instance, "_rpc", lambda *_: "0x0")
    prepared = instance.prepare_deploy(b"contract", [], quote=quote(instance, b"contract"))
    seen = []

    def rpc(method, params):
        assert method == "eth_sendRawTransaction"
        seen.append(params[0])
        return prepared["tx_id"]

    monkeypatch.setattr(instance, "_rpc", rpc)
    assert instance.submit(prepared) == instance.submit(prepared)
    assert seen == [prepared["raw_transaction"]] * 2
    corrupt = {**prepared, "fee_value": 0}
    with pytest.raises(StudioError, match="identity_mismatch"):
        instance.submit(corrupt)
    assert len(seen) == 2


def test_appeal_is_bound_to_exact_decision_and_has_separate_envelope(monkeypatch):
    instance = client(monkeypatch)
    monkeypatch.setattr(instance, "_raw_transaction", lambda _: {
        "data": {"fee_accounting": {"fees_distribution": distribution()}}})
    monkeypatch.setattr(instance, "_rpc", lambda *_: "0x3")
    request = {"target_tx_id": TX_ID, "decision_id": 27, "bond": 5, "funding": 7,
               "fee_value": 12}
    prepared = instance.prepare_appeal(TX_ID, quote=request)
    assert prepared["tx_id"] != TX_ID
    assert prepared["target_tx_id"] == TX_ID
    assert prepared["kind"] == "appeal"
    raw = rlp.decode(bytes.fromhex(prepared["raw_transaction"][2:]))
    assert int.from_bytes(raw[4]) == 12
    from genlayer_agent_lab.runtime.studio_modern import DISTRIBUTION_TYPE
    values = decode(["bytes32", "uint256", DISTRIBUTION_TYPE], raw[5][4:])
    assert values[:2] == (bytes.fromhex(TX_ID[2:]), 27)


def test_receipt_separates_lifecycle_from_execution_and_removes_private_fields(monkeypatch):
    instance = client(monkeypatch)
    raw = {"tx_id": TX_ID, "status": "FINALIZED", "recipient": ADDRESS,
           "consensus_data": {"leader_receipt": [{"execution_result": "ERROR",
               "node_config": {"private_key": "secret"}}]}, "consensus_history": {},
           "data": {"fee_accounting": {"total_refunded": 200, "private_key": "secret",
               "appeal_bonds": [{"amount": 3, "admissionRollback": {"secret": "secret"}}]}},
           "triggered_transactions": ["0x" + "33" * 32]}
    monkeypatch.setattr(instance, "_raw_transaction", lambda _: raw)
    observed = instance.transaction(TX_ID)
    assert observed["status"] == "FINALIZED"
    assert observed["execution_success"] is False
    assert observed["fee_accounting"] == {"total_refunded": 200, "appeal_bonds": [{"amount": 3}]}
    assert observed["child_transactions"] == [{"tx_id": "0x" + "33" * 32}]
    assert "secret" not in str(observed)


def test_envelope_revert_is_not_reported_as_success(monkeypatch):
    instance = client(monkeypatch)
    monkeypatch.setattr(instance, "_rpc", lambda *_: {
        "transactionHash": TX_ID, "status": "0x0", "revertReason": "untrusted arbitrary text"})
    assert instance.envelope(TX_ID) == {"tx_id": TX_ID, "success": False,
                                       "error_code": "protocol_submission_reverted"}


def test_read_decodes_structured_result_and_uses_explicit_finality(monkeypatch):
    instance = client(monkeypatch)
    expected = {"decision": "partial", "amount": 40, "nested": {"requires_appeal": False}}
    calls = []
    monkeypatch.setattr(instance, "_rpc", lambda *args: calls.append(args)
                        or calldata.encode(expected).hex())
    assert instance.read(ADDRESS, "get_state", [], finalized=True) == expected
    assert calls[0][1][0]["transaction_hash_variant"] == "latest-final"
    encoded = bytes.fromhex(calls[0][1][0]["data"][2:])
    assert rlp.decode(encoded)[1] == b"\x00"


def test_recovery_verifies_actual_contract_bytes(monkeypatch):
    instance = client(monkeypatch)
    monkeypatch.setattr(instance, "_rpc", lambda *_: base64.b64encode(b"original").decode())
    assert instance.verify_contract(ADDRESS, b"original")["verified"]
    with pytest.raises(StudioError, match="deployed_source_mismatch"):
        instance.verify_contract(ADDRESS, b"different")


def test_test_balance_topup_does_not_reset_existing_balance(monkeypatch):
    instance = client(monkeypatch)
    balances = iter([80, 100, 120, 120])
    funded = []
    monkeypatch.setattr(instance, "balance", lambda: next(balances))
    monkeypatch.setattr(instance, "fund", lambda amount: funded.append(amount) or TX_ID)
    assert instance.ensure_test_balance(100)["funded"] == 20
    assert instance.ensure_test_balance(100)["funded"] == 0
    assert funded == [20]


def test_modern_client_stays_loopback_only():
    for url in ["https://studio-dev.genlayer.com/api", "http://example.com:8796",
                "http://127.0.0.1:8796/?token=test"]:
        with pytest.raises(StudioError, match="endpoint_must_be_literal_loopback"):
            StudioModernClient(url)


def test_estimate_discards_validator_receipt_data(monkeypatch):
    instance = client(monkeypatch)
    result = {"receipt": {"execution_result": "SUCCESS", "validator_key": "secret"},
        "recommendedPreset": {"distribution": distribution(), "feeValue": "123",
                              "numOfInitialValidators": 5, "messageAllocations": []}}
    monkeypatch.setattr(instance, "_rpc", lambda *_: copy.deepcopy(result))
    actual = instance.estimate_deploy(b"code", [])
    assert actual["fee_value"] == 123
    assert actual["source"] == "studio_execution_estimate"
    assert "secret" not in str(actual)
    assert PROFILE == "studio-v0123"
