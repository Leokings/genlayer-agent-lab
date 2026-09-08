"""Bounded raw-transaction bridge to the owned Studio v0.123 profile.

The v0.6 ABI is deliberately encoded here, independently of the legacy SDK's
dispatch. Source of the wire schema: tagged Studio transactions_parser.py.
Preparing signs but never submits; callers must durably journal the prepared
envelope before submitting. Replaying identical bytes is idempotent upstream.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import re
import threading

import rlp
from eth_abi import encode as abi_encode
from eth_account import Account
from genlayer_py.abi import calldata
from genlayer_py.contracts.utils import make_calldata_object
from web3 import Web3

from ..bindings import bounded_json
from .studio import StudioClient, StudioError, _endpoint, _hex, _LoopbackProvider, _snapshot
from .studio_profiles import CHAIN_ID, GENVM_VERSION, PROFILE, STUDIO_COMMIT, STUDIO_VERSION

ZERO = "0x" + "00" * 20
DISTRIBUTION_TYPE = "(uint256,uint256,uint256,uint256,uint256,uint256,uint256[],uint256,uint256,uint256)"
MESSAGE_TYPE = "(uint8,bool,uint256,address,bytes32,uint256,bytes)[]"
PARAMS_TYPE = f"(address,address,uint256,uint256,uint256,uint256,uint256,{DISTRIBUTION_TYPE},bytes,{MESSAGE_TYPE})"
DISTRIBUTION_FIELDS = (
    "leaderTimeunitsAllocation", "validatorTimeunitsAllocation", "appealRounds",
    "executionBudgetPerRound", "executionConsumed", "totalMessageFees", "rotations",
    "maxPriceGenPerTimeUnit", "storageFeeMaxGasPrice", "receiptFeeMaxGasPrice",
)
ACCOUNTING_FIELDS = (
    "version", "status", "paid_fee_value", "required_fee_value", "primary_fee_budget",
    "primary_fee_spent", "primary_fee_refunded", "execution_fee_consumed",
    "message_fee_budget", "message_fee_consumed", "message_fee_refunded",
    "appeal_bonds", "appeal_bonds_total", "appeal_bonds_payout_total",
    "appeal_bond_sender_refunded", "appeal_funding_total", "appeal_charge_surplus_refunded",
    "total_refunded", "refunds", "fees_distribution",
)
BOND_FIELDS = ("appealer", "amount", "submittedAmount", "funding", "requiredCharge",
               "surplusRefund", "sourceRound", "appealRound", "juryCount", "status",
               "minimumRequired", "topUpAndSubmit", "extendsSchedule")
REFUND_FIELDS = ("recipient", "amount", "source", "primary", "message", "appealBond")


def _public_accounting(accounting):
    if type(accounting) is not dict:
        raise StudioError("malformed_fee_accounting")
    result = {}
    for key in ACCOUNTING_FIELDS:
        if key not in accounting:
            continue
        value = accounting[key]
        if key in {"appeal_bonds", "refunds"}:
            if type(value) is not list or len(value) > 256:
                raise StudioError("malformed_fee_accounting")
            fields = BOND_FIELDS if key == "appeal_bonds" else REFUND_FIELDS
            if any(type(item) is not dict for item in value):
                raise StudioError("malformed_fee_accounting")
            result[key] = [{name: copy.deepcopy(item[name]) for name in fields if name in item}
                           for item in value]
        elif key == "fees_distribution":
            _distribution(value)
            result[key] = copy.deepcopy(value)
        elif key == "status":
            if type(value) is not str or len(value) > 64:
                raise StudioError("malformed_fee_accounting")
            result[key] = value
        else:
            result[key] = _uint(value, "malformed_fee_accounting")
    bounded_json(result)
    return result


def _uint(value, code="invalid_fee_value"):
    if type(value) is str and re.fullmatch(r"[0-9]+", value):
        value = int(value)
    if type(value) is not int or not 0 <= value < 2**256:
        raise StudioError(code)
    return value


def _distribution(value):
    if type(value) is not dict or set(value) != set(DISTRIBUTION_FIELDS):
        raise StudioError("invalid_fee_distribution")
    result = []
    for name in DISTRIBUTION_FIELDS:
        if name == "rotations":
            rotations = value[name]
            if type(rotations) is not list or not 1 <= len(rotations) <= 32:
                raise StudioError("invalid_fee_distribution")
            result.append([_uint(n) for n in rotations])
        else:
            result.append(_uint(value[name]))
    return tuple(result)


def _messages(values):
    if type(values) is not list or len(values) > 20:
        raise StudioError("invalid_message_allocations")
    result = []
    for value in values:
        if type(value) is not dict or type(value.get("onAcceptance")) is not bool:
            raise StudioError("invalid_message_allocations")
        result.append((_uint(value["messageType"]), value["onAcceptance"],
            _uint(value["parentIndex"]), _hex(value["recipient"], 40, "invalid_message_recipient"),
            bytes.fromhex(_hex(value["callKey"], 64, "invalid_message_key")[2:]),
            _uint(value["budget"]), bytes.fromhex(value["feeParams"].removeprefix("0x"))))
    return result


def _encode(name, types, args):
    signature = name + "(" + ",".join(types) + ")"
    return Web3.keccak(text=signature)[:4] + abi_encode(types, args)


def _payload(method, args, code=None, *, read=False):
    bounded_json(args)
    if type(args) is not list:
        raise StudioError("invalid_contract_arguments")
    encoded = calldata.encode(make_calldata_object(method=method, args=args))
    # gen_call's legacy read envelope identifies the flag by the literal zero
    # byte. RLP's integer/boolean zero is empty and is treated as bare calldata.
    return rlp.encode([code, encoded, False] if code is not None
                      else [encoded, b"\x00" if read else False])


class StudioModernClient(StudioClient):
    def __init__(self, endpoint, *, account_private_key=None, account=None, timeout=180,
                 cancel_event=None):
        endpoint = _endpoint(endpoint)
        if type(timeout) not in (int, float) or not 0 < timeout <= 1800:
            raise StudioError("invalid_timeout")
        self.timeout = float(timeout)
        self.cancel_event = cancel_event or threading.Event()
        self._deadline = None
        self._lock = threading.RLock()
        self._closed = False
        self._provider = _LoopbackProvider(endpoint, self._remaining, request_timeout=120)
        self.account = account or (Account.from_key(account_private_key)
                                   if account_private_key is not None else Account.create())
        self.address = self.account.address
        self._consensus = None
        self._fee_config = None

    def _compatible(self):
        if self._rpc("eth_chainId", []) != hex(CHAIN_ID):
            raise StudioError("chain_id_mismatch")
        contract = self._rpc("sim_getConsensusContract", ["ConsensusMain"])
        if type(contract) is not dict or type(contract.get("abi")) is not list:
            raise StudioError("consensus_abi_mismatch")
        address = _hex(contract.get("address"), 40, "consensus_abi_mismatch")
        signatures = {(entry.get("name"), tuple(i.get("type") for i in entry.get("inputs", [])))
            for entry in contract["abi"] if type(entry) is dict and entry.get("type") == "function"}
        if not {("topUpAndSubmitAppeal", ("bytes32", "uint256", "tuple")),
                ("submitAppeal", ("bytes32", "uint256"))} <= signatures:
            raise StudioError("modern_consensus_abi_required")
        config = self._rpc("sim_getFeeConfig", [])
        if type(config) is not dict or config.get("enabled") is not True:
            raise StudioError("fee_enabled_profile_required")
        _distribution(config["defaultFees"]["distribution"])
        _uint(config["defaultFees"]["feeValue"])
        self._fee_config = config
        self._consensus = Web3.to_checksum_address(address)
        return contract

    def doctor(self):
        result = {"backend": "studio-modern", "profile": PROFILE, "ready": False,
            "public_chain": False, "bond_accounting": False,
            "studio_version_expected": STUDIO_VERSION, "studio_commit_expected": STUDIO_COMMIT,
            "genvm_version_expected": GENVM_VERSION, "release_verified": False}
        try:
            with self._operation():
                self._compatible()
                window = _uint(self._rpc("sim_getFinalityWindowTime", []))
                if not 1 <= window <= 86400:
                    raise StudioError("invalid_finality_window")
                result.update(ready=True, rpc_compatible=True, chain_id=CHAIN_ID,
                    finality_window_seconds=window, bond_accounting=True, advanced_lifecycle=True)
        except StudioError as exc:
            result["error_code"] = exc.code
        return result

    def balance(self, address=None):
        with self._operation():
            address = _hex(address or self.address, 40, "invalid_account")
            value = self._rpc("eth_getBalance", [address, "latest"])
            if type(value) is not str or re.fullmatch(r"0x[0-9a-fA-F]+", value) is None:
                raise StudioError("invalid_balance")
            return int(value, 16)

    def fund(self, amount):
        """Explicit local fixture funding; never invoked by transaction submission."""
        with self._operation():
            self._compatible()
            return _hex(self._rpc("sim_fundAccount", [self.address, str(_uint(amount))]),
                        64, "invalid_transaction_id")

    def ensure_test_balance(self, minimum=10**21):
        """Top up only the missing local test allowance; resume never resets it."""
        with self._operation():
            minimum = _uint(minimum)
            before = self.balance()
            funded = max(0, minimum - before)
            receipt = self.fund(funded) if funded else None
            return {"balance_before": before, "funded": funded, "funding_tx_id": receipt,
                    "balance_after": self.balance(), "test_units_only": True}

    def _estimate(self, kind, address, method, args, *, code=None, user_value=0):
        with self._operation():
            self._compatible()
            payload = _payload(method, args, code)
            params = {"type": kind, "to": address, "from": self.address,
                "data": "0x" + payload.hex(), "value": hex(_uint(user_value)),
                "transaction_hash_variant": "latest-nonfinal"}
            result = self._rpc("sim_estimateTransactionFees", [params])
            if type(result) is not dict or type(result.get("recommendedPreset")) is not dict:
                raise StudioError("malformed_fee_estimate")
            receipt = result.get("receipt", {})
            if receipt.get("execution_result") != "SUCCESS":
                raise StudioError("fee_estimate_execution_failed")
            preset = result["recommendedPreset"]
            distribution = preset.get("distribution")
            _distribution(distribution)
            allocations = preset.get("messageAllocations", [])
            _messages(allocations)
            # Retain fee facts, never validator/provider data from estimate receipt.
            quote = {"fee_value": _uint(preset.get("feeValue")),
                "distribution": copy.deepcopy(distribution),
                "message_allocations": copy.deepcopy(allocations),
                "num_validators": _uint(preset.get("numOfInitialValidators", 5)),
                "call_sha256": hashlib.sha256(payload).hexdigest(), "kind": kind,
                "recipient": address, "sender": self.address, "user_value": _uint(user_value),
                "source": "studio_execution_estimate"}
            bounded_json(quote)
            return quote

    def estimate_deploy(self, code: bytes, args: list, *, user_value=0):
        if type(code) is not bytes or not 1 <= len(code) <= 2 * 1024 * 1024:
            raise StudioError("invalid_contract_source")
        return self._estimate("deploy", ZERO, None, args, code=code, user_value=user_value)

    def estimate_write(self, address, method, args, *, user_value=0):
        address = _hex(address, 40, "invalid_contract_address")
        if type(method) is not str or re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,127}", method) is None:
            raise StudioError("invalid_contract_method")
        return self._estimate("write", address, method, args, user_value=user_value)

    def _sign(self, kind, data, fee_value, *, quote, target_tx_id=None):
        nonce_raw = self._rpc("eth_getTransactionCount", [self.address, "pending"])
        nonce = int(nonce_raw, 16) if type(nonce_raw) is str else _uint(nonce_raw)
        tx = {"to": self._consensus, "nonce": nonce, "gas": 30_000_000,
              "gasPrice": 0, "value": _uint(fee_value), "data": data, "chainId": CHAIN_ID}
        signed = self.account.sign_transaction(tx)
        raw = bytes(signed.raw_transaction)
        return {"schema": 1, "profile": PROFILE, "kind": kind,
            "tx_id": "0x" + Web3.keccak(raw).hex(), "raw_transaction": "0x" + raw.hex(),
            "sender": self.address, "nonce": nonce, "fee_value": fee_value,
            "quote": copy.deepcopy(quote), "target_tx_id": target_tx_id}

    def _prepare(self, kind, address, method, args, *, code=None, quote=None, user_value=0):
        with self._operation():
            self._compatible()
            payload = _payload(method, args, code)
            quote = quote or self._estimate(kind, address, method, args, code=code,
                                            user_value=user_value)
            if (quote.get("call_sha256") != hashlib.sha256(payload).hexdigest()
                    or quote.get("recipient") != address or quote.get("sender") != self.address
                    or quote.get("kind") != kind or quote.get("user_value") != user_value):
                raise StudioError("fee_quote_identity_mismatch")
            distribution = _distribution(quote["distribution"])
            rotations = max(distribution[6])
            params = (self.address, address, _uint(quote["num_validators"]), rotations,
                0, 0, _uint(user_value), distribution, payload,
                _messages(quote["message_allocations"]))
            data = _encode("addTransaction", [PARAMS_TYPE], [params])
            return self._sign(kind, data, _uint(quote["fee_value"]) + user_value, quote=quote)

    def prepare_deploy(self, code, args, *, quote=None, user_value=0):
        if type(code) is not bytes or not 1 <= len(code) <= 2 * 1024 * 1024:
            raise StudioError("invalid_contract_source")
        return self._prepare("deploy", ZERO, None, args, code=code, quote=quote,
                             user_value=user_value)

    def prepare_write(self, address, method, args, *, quote=None, user_value=0):
        address = _hex(address, 40, "invalid_contract_address")
        return self._prepare("write", address, method, args, quote=quote, user_value=user_value)

    def appeal_quote(self, tx_id):
        with self._operation():
            self._compatible()
            _hex(tx_id, 64, "invalid_transaction_id")
            quote = self._rpc("gen_estimateLatestAppealCharge", [{"txId": tx_id}])
            if type(quote) is not dict:
                raise StudioError("malformed_appeal_quote")
            return {"target_tx_id": tx_id, "decision_id": _uint(quote["decisionId"]),
                "bond": _uint(quote["bond"]), "funding": _uint(quote["funding"]),
                "fee_value": _uint(quote["bond"]) + _uint(quote["funding"]),
                "appeal_deadline": _uint(quote["appealDeadline"]), "source": "studio_live_quote"}

    def prepare_appeal(self, tx_id, *, quote=None):
        with self._operation():
            self._compatible()
            _hex(tx_id, 64, "invalid_transaction_id")
            quote = quote or self.appeal_quote(tx_id)
            if quote.get("target_tx_id") != tx_id:
                raise StudioError("appeal_quote_identity_mismatch")
            raw = self._raw_transaction(tx_id)
            accounting = (raw.get("data") or {}).get("fee_accounting") or {}
            distribution = _distribution(accounting.get("fees_distribution"))
            data = _encode("topUpAndSubmitAppeal", ["bytes32", "uint256", DISTRIBUTION_TYPE],
                [bytes.fromhex(tx_id[2:]), _uint(quote["decision_id"]), distribution])
            return self._sign("appeal", data, _uint(quote["fee_value"]), quote=quote,
                              target_tx_id=tx_id)

    def submit(self, prepared):
        with self._operation():
            self._compatible()
            if type(prepared) is not dict or prepared.get("profile") != PROFILE:
                raise StudioError("invalid_prepared_transaction")
            raw = prepared.get("raw_transaction")
            if type(raw) is not str or re.fullmatch(r"0x[0-9a-fA-F]+", raw) is None:
                raise StudioError("invalid_prepared_transaction")
            data = bytes.fromhex(raw[2:])
            expected = "0x" + Web3.keccak(data).hex()
            if (len(data) > 3 * 1024 * 1024 or prepared.get("tx_id") != expected
                    or Account.recover_transaction(data) != self.address):
                raise StudioError("prepared_transaction_identity_mismatch")
            envelope = rlp.decode(data)
            if (len(envelope) != 9 or (int.from_bytes(envelope[6]) - 35) // 2 != CHAIN_ID
                    or "0x" + envelope[3].hex() != self._consensus.lower()
                    or int.from_bytes(envelope[4]) != prepared.get("fee_value")
                    or int.from_bytes(envelope[0]) != prepared.get("nonce")):
                raise StudioError("prepared_transaction_identity_mismatch")
            result = self._rpc("eth_sendRawTransaction", [raw])
            if result != expected:
                raise StudioError("submitted_transaction_identity_mismatch")
            return result

    def read(self, address, method, args, *, finalized=True):
        with self._operation():
            self._compatible()
            address = _hex(address, 40, "invalid_contract_address")
            result = self._rpc("gen_call", [{"type": "read", "to": address,
                "from": self.address, "data": "0x" + _payload(method, args, read=True).hex(),
                "transaction_hash_variant": "latest-final" if finalized else "latest-nonfinal"}])
            if type(result) is not str or len(result) > 256 * 1024:
                raise StudioError("malformed_contract_result")
            value = calldata.decode(bytes.fromhex(result.removeprefix("0x")))
            bounded_json(value)
            return value

    def verify_contract(self, address, code: bytes):
        with self._operation():
            address = _hex(address, 40, "invalid_contract_address")
            if type(code) is not bytes:
                raise StudioError("invalid_contract_source")
            encoded = self._rpc("gen_getContractCode", [address])
            try:
                actual = base64.b64decode(encoded, validate=True)
            except Exception:
                raise StudioError("malformed_deployed_source") from None
            if hashlib.sha256(actual).digest() != hashlib.sha256(code).digest():
                raise StudioError("deployed_source_mismatch")
            return {"contract_address": address, "source_sha256": hashlib.sha256(actual).hexdigest(),
                    "verified": True}

    def _raw_transaction(self, tx_id):
        _hex(tx_id, 64, "invalid_transaction_id")
        raw = self._rpc("gen_getStudioTransactionByHash", [tx_id, True])
        if raw is None:
            raise StudioError("transaction_not_found")
        return raw

    def transaction(self, tx_id):
        with self._operation():
            self._compatible()
            raw = self._raw_transaction(tx_id)
            result = _snapshot(raw, tx_id)
            accounting = (raw.get("data") or {}).get("fee_accounting") or {}
            public_accounting = _public_accounting(accounting)
            children = [_hex(value, 64, "invalid_child_transaction")
                        for value in raw.get("triggered_transactions", [])]
            result.update(backend="studio-modern", profile=PROFILE, bond_accounting=True,
                advanced_lifecycle=True, fee_accounting=public_accounting,
                fees=public_accounting, child_transaction_ids=children,
                child_transactions=[{"tx_id": tx} for tx in children],
                execution_result_name=raw.get("txExecutionResultName"))
            return result

    def envelope(self, tx_id):
        with self._operation():
            _hex(tx_id, 64, "invalid_transaction_id")
            raw = self._rpc("eth_getTransactionReceipt", [tx_id])
            if raw is None:
                return None
            if type(raw) is not dict or raw.get("transactionHash") != tx_id:
                raise StudioError("receipt_identity_mismatch")
            status = raw.get("status")
            if status not in ("0x0", "0x1"):
                raise StudioError("malformed_envelope_status")
            return {"tx_id": tx_id, "success": status == "0x1",
                    "error_code": None if status == "0x1" else "protocol_submission_reverted"}

    def lifecycle(self, tx_id):
        with self._operation():
            _hex(tx_id, 64, "invalid_transaction_id")
            raw = self._rpc("gen_getTransactionLifecycle", [{"txId": tx_id}])
            if type(raw) is not dict:
                raise StudioError("malformed_lifecycle")
            allowed = ("storedStatus", "projectedStatus", "resolutionAction", "resolutionSource",
                       "decisionId", "decisionActive", "evaluatedAt")
            result = {key: raw[key] for key in allowed if key in raw}
            bounded_json(result)
            return result
