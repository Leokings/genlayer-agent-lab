"""Bounded SDK bridge to the pinned, separately managed local Studio stack.

Studio RPC receipts include validator keys and provider settings. Raw receipts
stay private; only an explicit field allowlist crosses this adapter's boundary.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import ipaddress
import json
import math
import re
import threading
import time
from contextlib import contextmanager
from importlib.metadata import version
from urllib.parse import urlsplit

import httpx
from genlayer_py import create_account
from genlayer_py.abi import calldata
from genlayer_py.chains import localnet
from genlayer_py.client.genlayer_client import GenLayerClient
from genlayer_py.types import TransactionHashVariant
from web3 import Web3
from web3.providers import BaseProvider

from ..bindings import bounded_json, resolve_arguments, validate_snapshot
from .pins import GENVM_VERSION, RUNNER_HASH

STUDIO_VERSION = "v0.121.6"
STUDIO_COMMIT = "366f085a479bb9e6028ce326c2c13f798a9752c7"
SDK_VERSION = "0.16.3"
CHAIN_ID = 61999
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
CAPABILITIES = {
    "backend": "studio", "public_chain": False, "bond_accounting": False,
    "advanced_lifecycle": False,
}
STATUSES = {
    "PENDING", "ACTIVATED", "PROPOSING", "COMMITTING", "REVEALING", "ACCEPTED",
    "FINALIZED", "UNDETERMINED", "LEADER_TIMEOUT", "VALIDATORS_TIMEOUT", "CANCELED",
}
DECIDED = {"ACCEPTED", "FINALIZED", "UNDETERMINED", "LEADER_TIMEOUT", "VALIDATORS_TIMEOUT"}
APPEALABLE = DECIDED - {"FINALIZED"}
ROUND_NAMES = {
    "Accepted", "Leader Rotation", "Undetermined", "Leader Timeout", "Validators Timeout",
    "Leader Rotation Appeal", "Validator Appeal Successful", "Validator Appeal Failed",
    "Leader Appeal Successful", "Leader Appeal Failed", "Leader Timeout Appeal Successful",
    "Leader Timeout Appeal Failed", "Validator Timeout Appeal Successful",
    "Validators Timeout Appeal Failed",
}
VOTES = {"agree", "disagree", "timeout", "deterministic_violation", "not_voted", "idle"}


class StudioError(RuntimeError):
    """Safe error codes; never include raw SDK, provider or contract messages."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(f"Local Studio operation failed: {code}")


def _endpoint(value: str) -> str:
    try:
        parsed = urlsplit(value)
        address = ipaddress.ip_address(parsed.hostname or "")
        if (parsed.scheme != "http" or not address.is_loopback or parsed.username is not None
                or parsed.password is not None or parsed.query or parsed.fragment
                or parsed.port is None or not 1 <= parsed.port <= 65535):
            raise ValueError
    except (ValueError, TypeError):
        raise StudioError("endpoint_must_be_literal_loopback_http") from None
    return value


def _hex(value, length: int, code: str) -> str:
    if type(value) is not str or re.fullmatch(r"0x[0-9a-fA-F]{" + str(length) + r"}", value) is None:
        raise StudioError(code)
    return value


def _count(value, default=0):
    return value if type(value) is int and value >= 0 else default


def _vote(value):
    return value.lower() if type(value) is str and value.lower() in VOTES else "unknown"


def _execution(receipt):
    """Return actual calldata value, never the SDK's human-readable rendering."""
    if not receipt:
        return None, None, None
    if type(receipt) is not dict:
        raise StudioError("malformed_receipt")
    if receipt.get("execution_result") != "SUCCESS":
        return False, None, "execution_error"
    encoded = receipt.get("result")
    # Raw RPC uses base64; accept its explicit raw wrapper too, without trusting
    # an accompanying readable/status string from a different SDK rendering.
    if type(encoded) is dict:
        encoded = encoded.get("raw")
    try:
        raw = base64.b64decode(encoded, validate=True)
        if not raw or len(raw) > 128 * 1024:
            raise ValueError
        if raw[0] != 0:
            return False, None, {1: "rollback", 2: "contract_error", 3: "error",
                                 4: "none", 5: "no_leaders"}.get(raw[0], "unknown")
        result = calldata.decode(raw[1:])
        bounded_json(result)
        return True, result, "return"
    except Exception:
        raise StudioError("malformed_execution_result") from None


def _snapshot(raw: dict, tx_id: str) -> dict:
    if type(raw) is not dict or raw.get("hash", raw.get("tx_id")) != tx_id:
        raise StudioError("receipt_identity_mismatch")
    status = raw.get("status")
    if status not in STATUSES:
        raise StudioError("unknown_transaction_status")
    if raw.get("execution_mode", "NORMAL") != "NORMAL" or raw.get("leader_only") is True:
        raise StudioError("normal_consensus_required")
    consensus = raw.get("consensus_data") or {}
    if type(consensus) is not dict:
        raise StudioError("malformed_receipt")
    leaders = consensus.get("leader_receipt") or []
    if type(leaders) is dict:
        leaders = [leaders]
    if type(leaders) is not list:
        raise StudioError("malformed_receipt")
    success, result, result_code = _execution(leaders[0] if leaders else None)
    history = raw.get("consensus_history") or {}
    if type(history) is not dict:
        raise StudioError("malformed_receipt")
    rounds = []
    for item in history.get("consensus_results") or []:
        if type(item) is not dict:
            raise StudioError("malformed_receipt")
        name = item.get("consensus_round")
        validators = item.get("validator_results") or []
        if type(validators) is not list:
            raise StudioError("malformed_receipt")
        round_data = {
            "index": len(rounds), "kind": name if name in ROUND_NAMES else "Unknown",
            "validator_votes": [_vote(v.get("vote")) for v in validators if type(v) is dict],
        }
        history_leader = item.get("leader_result")
        if history_leader:
            if type(history_leader) is list:
                history_leader = history_leader[0]
            history_success, history_result, history_code = _execution(history_leader)
            round_data.update(execution_success=history_success, raw_result=history_result,
                              result_code=history_code)
        rounds.append(round_data)
    votes = consensus.get("votes") or {}
    if type(votes) is not dict:
        raise StudioError("malformed_receipt")
    address = raw.get("to_address", raw.get("recipient"))
    if address is not None:
        _hex(address, 40, "malformed_contract_address")
    return {
        **CAPABILITIES, "tx_id": tx_id, "contract_address": address, "status": status,
        "execution_success": success, "raw_result": result, "result_code": result_code,
        "votes": [_vote(v) for v in votes.values()], "rounds": rounds,
        "round_count": len(rounds), "appealed": raw.get("appealed") is True,
        "timestamp_appeal": _count(raw.get("timestamp_appeal"), None),
        "timestamp_awaiting_finalization": _count(raw.get("timestamp_awaiting_finalization"), None),
        "appeal_failed": _count(raw.get("appeal_failed")),
        "appeal_processing_time": _count(raw.get("appeal_processing_time")),
        "rotation_count": _count(raw.get("rotation_count")),
    }


class _LoopbackProvider(BaseProvider):
    def __init__(self, endpoint: str, remaining, *, request_timeout=10.0):
        super().__init__()
        self.url = endpoint
        self._remaining = remaining
        self._request_timeout = request_timeout
        self._http = httpx.Client(trust_env=False, follow_redirects=False)
        self._sequence = 0
        self.on_submission = None
        self.on_submission_attempt = None

    def make_request(self, method, params):
        remaining = self._remaining()
        self._sequence += 1
        payload = {"jsonrpc": "2.0", "id": self._sequence, "method": str(method), "params": params}
        try:
            body = bytearray()
            timeout = httpx.Timeout(min(remaining, self._request_timeout))
            if method == "eth_sendRawTransaction" and self.on_submission_attempt is not None:
                self.on_submission_attempt()
            with self._http.stream("POST", self.url, json=payload, timeout=timeout) as response:
                if response.status_code != 200:
                    raise StudioError("rpc_http_error")
                for chunk in response.iter_bytes():
                    self._remaining()
                    body.extend(chunk)
                    if len(body) > MAX_RESPONSE_BYTES:
                        raise StudioError("rpc_response_too_large")
            decoded = json.loads(body)
            if (type(decoded) is not dict or decoded.get("jsonrpc") != "2.0"
                    or decoded.get("id") != self._sequence):
                raise StudioError("malformed_rpc_response")
            if "error" in decoded:
                error = decoded["error"]
                code = error.get("code") if type(error) is dict else None
                if code == -32601:
                    raise StudioError("rpc_method_unavailable")
                raise StudioError("rpc_rejected")
            if "result" not in decoded:
                raise StudioError("malformed_rpc_response")
            if method == "eth_sendRawTransaction" and self.on_submission is not None:
                self.on_submission(_hex(decoded["result"], 64, "invalid_transaction_id"))
            return decoded
        except StudioError:
            raise
        except httpx.TimeoutException:
            self._remaining()
            raise StudioError("rpc_timeout") from None
        except Exception:
            raise StudioError("rpc_transport_error") from None

    def close(self):
        self._http.close()


class StudioClient:
    def __init__(self, endpoint: str, timeout: float = 180, cancel_event=None, account=None):
        endpoint = _endpoint(endpoint)
        if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
                or not math.isfinite(timeout) or not 0 < timeout <= 3600):
            raise StudioError("invalid_timeout")
        if version("genlayer-py") != SDK_VERSION:
            raise StudioError("sdk_version_mismatch")
        self.timeout = float(timeout)
        self.cancel_event = cancel_event or threading.Event()
        self._deadline = None
        self._lock = threading.RLock()
        self._closed = False
        self._provider = _LoopbackProvider(endpoint, self._remaining)
        chain = copy.deepcopy(localnet)
        chain.rpc_urls["default"]["http"] = [endpoint]
        self._sdk = GenLayerClient(chain, account if account is not None else create_account())
        # Constructing the SDK class does no network IO. Replace both references
        # before its first initialization/signing request; no global SDK patch.
        self._sdk.provider = self._provider
        self._sdk.w3.provider = self._provider

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        self._provider.close()
        self._closed = True

    def _remaining(self):
        if self._closed:
            raise StudioError("client_closed")
        if self.cancel_event.is_set():
            raise StudioError("canceled")
        left = (self._deadline or time.monotonic()) - time.monotonic()
        if left <= 0:
            raise StudioError("deadline_exceeded")
        return left

    @contextmanager
    def _operation(self):
        started = time.monotonic()
        if not self._lock.acquire(timeout=self.timeout):
            raise StudioError("client_busy")
        try:
            outer = self._deadline is None
            if outer:
                self._deadline = started + self.timeout
            try:
                self._remaining()
                yield
            except StudioError:
                raise
            except Exception:
                raise StudioError("sdk_operation_failed") from None
            finally:
                if outer:
                    self._deadline = None
        finally:
            self._lock.release()

    def _rpc(self, method, params):
        return self._provider.make_request(method, params)["result"]

    def _compatible(self):
        if self._rpc("eth_chainId", []) != hex(CHAIN_ID):
            raise StudioError("chain_id_mismatch")
        contract = self._rpc("sim_getConsensusContract", ["ConsensusMain"])
        if type(contract) is not dict:
            raise StudioError("consensus_abi_mismatch")
        _hex(contract.get("address"), 40, "consensus_abi_mismatch")
        abi = contract.get("abi")
        if type(abi) is not list:
            raise StudioError("consensus_abi_mismatch")
        signatures = {(item.get("name"), tuple(a.get("type") for a in item.get("inputs", [])))
                      for item in abi if type(item) is dict and item.get("type") == "function"}
        required = {("addTransaction", ("address", "address", "uint256", "uint256", "bytes")),
                    ("submitAppeal", ("bytes32",))}
        if not required <= signatures:
            raise StudioError("consensus_abi_mismatch")
        events = {(item.get("name"), tuple(a.get("type") for a in item.get("inputs", [])))
                  for item in abi if type(item) is dict and item.get("type") == "event"}
        if ("NewTransaction", ("bytes32", "address", "address")) not in events:
            raise StudioError("consensus_abi_mismatch")
        contract = copy.deepcopy(contract)
        contract["address"] = Web3.to_checksum_address(contract["address"])
        self._sdk.chain.consensus_main_contract = contract
        # SDK0.16.3 normally fetches this again with a broad fallback. This
        # instance-only replacement retains mandatory checks for every write.
        self._sdk.initialize_consensus_smart_contract = self._compatible

    def doctor(self):
        result = {**CAPABILITIES, "ready": False, "rpc_compatible": False,
                  "studio_version_expected": STUDIO_VERSION, "studio_commit_expected": STUDIO_COMMIT,
                  "sdk_version": SDK_VERSION, "genvm_version_expected": GENVM_VERSION,
                  "runner_hash_expected": RUNNER_HASH, "release_verified": False}
        try:
            with self._operation():
                self._compatible()
                window = self._rpc("sim_getFinalityWindowTime", [])
                if type(window) is str and window.isdecimal():
                    window = int(window)
                if type(window) is not int or not 1 <= window <= 86400:
                    raise StudioError("invalid_finality_window")
                result.update(ready=True, rpc_compatible=True, chain_id=CHAIN_ID,
                              finality_window_seconds=window)
        except StudioError as exc:
            result["error_code"] = exc.code
        return result

    @staticmethod
    def _sim_config(config):
        if config is not None:
            bounded_json(config)
            if type(config) is not dict or set(config) - {"validators", "genvm_datetime"}:
                raise StudioError("invalid_sim_config")
        return copy.deepcopy(config)

    def deploy(self, snapshot: dict, sim_config=None) -> str:
        with self._operation():
            checked = validate_snapshot(snapshot)
            config = self._sim_config(sim_config)
            self._compatible()
            tx_id = self._sdk.deploy_contract(code=checked["source"].encode("utf-8"),
                args=checked["definition"]["constructor_args"], leader_only=False,
                consensus_max_rotations=3, sim_config=config)
            return _hex(tx_id, 64, "invalid_transaction_id")

    def write(self, address: str, snapshot: dict, context: dict, sim_config=None) -> str:
        with self._operation():
            _hex(address, 40, "invalid_contract_address")
            checked = validate_snapshot(snapshot)
            bounded_json(context)
            args = resolve_arguments(checked["definition"], context)
            config = self._sim_config(sim_config)
            self._compatible()
            deployed_code = self._rpc("gen_getContractCode", [address])
            try:
                actual_code = base64.b64decode(deployed_code, validate=True)
            except Exception:
                raise StudioError("malformed_deployed_source") from None
            if hashlib.sha256(actual_code).hexdigest() != checked["source_sha256"]:
                raise StudioError("deployed_source_mismatch")
            schema = self._rpc("gen_getContractSchema", [address])
            method = checked["definition"]["method"]
            if (type(schema) is not dict or type(schema.get("methods")) is not dict
                    or type(schema["methods"].get(method)) is not dict
                    or schema["methods"][method].get("readonly") is not False):
                raise StudioError("public_write_method_required")
            tx_id = self._sdk.write_contract(address=address, function_name=method, args=args,
                leader_only=False, consensus_max_rotations=3, sim_config=config)
            return _hex(tx_id, 64, "invalid_transaction_id")

    def _verify_workflow_method(self, address, snapshot, operation, readonly):
        from ..workflow_bindings import validate_workflow_snapshot
        checked = validate_workflow_snapshot(snapshot)
        _hex(address, 40, "invalid_contract_address")
        definition = checked["definition"]["operations"].get(operation)
        if definition is None or definition["readonly"] is not readonly:
            raise StudioError("unsupported_workflow_operation")
        self._compatible()
        encoded = self._rpc("gen_getContractCode", [address])
        try:
            actual = base64.b64decode(encoded, validate=True)
        except Exception:
            raise StudioError("malformed_deployed_source") from None
        if hashlib.sha256(actual).hexdigest() != checked["source_sha256"]:
            raise StudioError("deployed_source_mismatch")
        schema = self._rpc("gen_getContractSchema", [address])
        method = definition["method"]
        if (type(schema) is not dict or type(schema.get("methods")) is not dict
                or type(schema["methods"].get(method)) is not dict
                or schema["methods"][method].get("readonly") is not readonly):
            raise StudioError("workflow_method_schema_mismatch")
        return checked, method

    def deploy_workflow(self, snapshot: dict) -> str:
        from ..workflow_bindings import validate_workflow_snapshot
        with self._operation():
            checked = validate_workflow_snapshot(snapshot)
            self._compatible()
            tx_id = self._sdk.deploy_contract(code=checked["source"].encode("utf-8"),
                args=checked["definition"]["constructor_args"], leader_only=False,
                consensus_max_rotations=3)
            return _hex(tx_id, 64, "invalid_transaction_id")

    def write_workflow(self, address, snapshot, operation, context) -> str:
        from ..workflow_bindings import resolve_workflow_arguments
        with self._operation():
            checked, method = self._verify_workflow_method(address, snapshot, operation, False)
            arguments = resolve_workflow_arguments(checked, operation, context)
            tx_id = self._sdk.write_contract(address=address, function_name=method, args=arguments,
                leader_only=False, consensus_max_rotations=3)
            return _hex(tx_id, 64, "invalid_transaction_id")

    def read_workflow(self, address, snapshot, operation, context, *, finalized=True):
        from ..workflow_bindings import resolve_workflow_arguments, validate_workflow_result
        with self._operation():
            checked, method = self._verify_workflow_method(address, snapshot, operation, True)
            arguments = resolve_workflow_arguments(checked, operation, context)
            result = self._sdk.read_contract(address=address, function_name=method, args=arguments,
                transaction_hash_variant=(TransactionHashVariant.LATEST_FINAL if finalized
                                          else TransactionHashVariant.LATEST_NONFINAL))
            bounded_json(result)
            return validate_workflow_result(checked, operation, result)

    def _raw_transaction(self, tx_id: str) -> dict:
        _hex(tx_id, 64, "invalid_transaction_id")
        result = self._rpc("eth_getTransactionByHash", [tx_id])
        if result is None:
            raise StudioError("transaction_not_found")
        return result

    def transaction(self, tx_id: str) -> dict:
        with self._operation():
            return _snapshot(self._raw_transaction(tx_id), tx_id)

    def _pause(self):
        self.cancel_event.wait(min(0.25, self._remaining()))
        self._remaining()

    def wait(self, tx_id: str, until: str = "accepted") -> dict:
        if until not in {"accepted", "finalized"}:
            raise StudioError("invalid_wait_status")
        with self._operation():
            while True:
                result = self.transaction(tx_id)
                if result["status"] == "CANCELED" or (
                    until == "accepted" and result["status"] in DECIDED
                ) or result["status"] == "FINALIZED":
                    return result
                self._pause()

    def appeal(self, tx_id: str) -> dict:
        with self._operation():
            before = self.transaction(tx_id)
            if before["status"] not in APPEALABLE or before["appealed"]:
                raise StudioError("transaction_not_appealable")
            self._compatible()
            returned = self._sdk.appeal_transaction(transaction_id=tx_id, value=0)
            if returned != tx_id:
                raise StudioError("appeal_transaction_id_mismatch")
            while True:
                after = self.transaction(tx_id)
                new_rounds = after["rounds"][before["round_count"]:]
                completed = [r for r in new_rounds if r["kind"].endswith(("Successful", "Failed"))]
                observed = (after["appealed"] or bool(new_rounds)
                            or after["timestamp_appeal"] != before["timestamp_appeal"])
                if observed:
                    return {"transaction": after, "request_observed": True,
                            "appeal_completed": bool(completed), "completed_rounds": completed}
                if after["status"] == "FINALIZED":
                    raise StudioError("appeal_not_observed_before_finalization")
                self._pause()
