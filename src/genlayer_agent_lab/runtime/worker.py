"""Single-use GLSim worker. No server, live providers or arbitrary contract paths."""

from __future__ import annotations

import contextlib
import hashlib
import importlib.metadata
import json
import os
import sys
from pathlib import Path

from .artifacts import prepare_sdk
from .compat import cleanup_windows_stdin_fix, install_windows_stdin_fix
from .pins import BUNDLE_SHA256, GENVM_VERSION, RUNNER_HASH, TEST_SUITE_VERSION

CONTRACT = Path(__file__).parent / "contracts" / "evidence_decision.py"


def execute(request: dict) -> dict:
    installed = importlib.metadata.version("genlayer-test")
    if installed != TEST_SUITE_VERSION:
        raise RuntimeError(f"Unsupported genlayer-test {installed}; require {TEST_SUITE_VERSION}")
    evidence = request.get("evidence")
    if not isinstance(evidence, str) or not 1 <= len(evidence) <= 16000:
        raise ValueError("Invalid evidence")
    if set(request) != {"evidence", "fixture_response"}:
        raise ValueError("Worker accepts only evidence and an explicit IO fixture")

    prepare_sdk(CONTRACT)
    from glsim.consensus import run_consensus
    from glsim.engine import SimEngine
    from glsim.state import StateStore
    from glsim.tx_decoder import encode_calldata_result

    paths, original = install_windows_stdin_fix()
    engine = SimEngine(StateStore(seed="genlayer-agent-lab-v1"))
    engine.vm.warp("2026-01-01T00:00:00Z")
    engine.vm._strict_mock_mode = True
    engine.vm.strict_mocks = True
    engine.vm.mock_llm(r"^AGENT_LAB_EVIDENCE_V1\n", json.dumps(request["fixture_response"]))
    llm_calls: list[str] = []
    match_mock = engine.vm._match_llm_mock

    def count_mock(prompt):
        response = match_mock(prompt)
        llm_calls.append(hashlib.sha256(prompt.encode()).hexdigest())
        return response

    engine.vm._match_llm_mock = count_mock
    engine.activate()
    try:
        address, _ = engine.deploy(str(CONTRACT))
        initial = engine.call_method(address, "get_state")
        if initial != {"verdict": "pending", "evidence": ""}:
            raise RuntimeError("Worker did not start with clean contract state")

        def call():
            verdict = engine.call_method(address, "evaluate", [evidence])
            return verdict, encode_calldata_result(verdict)

        receipt = run_consensus(engine, call, num_validators=3, max_rotations=2)
        if receipt.error:
            raise RuntimeError(f"Contract execution failed: {receipt.error}")
        if receipt.status.value != "FINALIZED" or receipt.result not in ("approve", "deny"):
            raise RuntimeError("Contract did not reach a successful simulated consensus result")
        captured = len(engine.vm._captured_validators)
        if captured != 1 or len(llm_calls) < 4:
            raise RuntimeError("Expected leader and validator assessment functions were not exercised")
        state = engine.call_method(address, "get_state")
        if state != {"verdict": receipt.result, "evidence": evidence}:
            raise RuntimeError("Contract state does not match execution receipt")
        result = {
            "verdict": receipt.result,
            "provenance": {
                "backend": "glsim",
                "genlayer_test_version": installed,
                "genvm_version": GENVM_VERSION,
                "runner_hash": RUNNER_HASH,
                "bundle_sha256": BUNDLE_SHA256,
                "contract_sha256": hashlib.sha256(CONTRACT.read_bytes()).hexdigest(),
                "evidence_sha256": hashlib.sha256(evidence.encode()).hexdigest(),
                "fixture_sha256": hashlib.sha256(
                    json.dumps(request["fixture_response"], sort_keys=True).encode()
                ).hexdigest(),
                "contract_address": address,
                "worker_pid": os.getpid(),
                "fresh_process": True,
                "mocked_io": True,
                "strict_mocks": True,
                "execution_success": True,
                "initial_state": initial,
                "final_state": state,
                "result_bytes_hex": receipt.result_bytes.hex(),
                "consensus": {
                    "status": receipt.status.value,
                    "votes": receipt.votes,
                    "rotation": receipt.rotation,
                    "validator_count": 3,
                    "captured_validators": captured,
                    "llm_fixture_calls": len(llm_calls),
                    "mode": "GLSim simplified majority; leader plus 3 validator callbacks",
                    "public_network": False,
                    "appeals_supported": False,
                },
                "windows_stdin_compatibility_fix": os.name == "nt",
            },
        }
    finally:
        try:
            engine.deactivate()
        finally:
            cleanup_windows_stdin_fix(paths, original)
    return result


def main() -> int:
    try:
        request = json.load(sys.stdin)
        if not isinstance(request, dict):
            raise ValueError("Expected object")
        # Upstream SDK setup and simulated IO may print; stdout is JSON-only.
        with contextlib.redirect_stdout(sys.stderr):
            result = execute(request)
        print(json.dumps(result))
        return 0
    except Exception as exc:
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
