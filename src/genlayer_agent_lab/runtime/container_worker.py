"""Container-only custom contract execution; output is diagnostic, not attestation."""

import contextlib
import importlib.metadata
import json
import os
import sys
from pathlib import Path

from ..bindings import extract_verdict, resolve_arguments, resolve_template, validate_snapshot
from .container_support import initialize_offline_sdk
from .pins import GENVM_VERSION, RUNNER_HASH, TEST_SUITE_VERSION


def require_public_write(instance, method_name: str) -> None:
    """Inspect the bound method, preserving the SDK's public/write decorators.

    GLSim 0.29.2 caches a CalldataProxy class whose class-level schema is empty.
    Bound methods forward to the real instance and retain decorator attributes.
    This validates the binding surface; arbitrary source is still untrusted code.
    """
    method = getattr(instance, method_name, None)
    if (not callable(method) or getattr(method, "__gl_public__", False) is not True
            or getattr(method, "__gl_readonly__", False) is not False):
        raise RuntimeError("Binding method must be a public write method")


def execute(payload: dict) -> dict:
    if sys.platform != "linux" or os.getuid() != 10001:
        raise RuntimeError("Custom contract worker requires the configured nonroot Linux container")
    if importlib.metadata.version("genlayer-test") != TEST_SUITE_VERSION:
        raise RuntimeError("Container has an incompatible GenLayer testing runtime")
    snapshot = validate_snapshot(payload["snapshot"])
    context = payload["context"]
    definition = snapshot["definition"]
    arguments = resolve_arguments(definition, context)
    response = resolve_template(definition["llm_response"], context)
    initialize_offline_sdk()
    from glsim.consensus import run_consensus
    from glsim.engine import SimEngine
    from glsim.state import StateStore
    from glsim.tx_decoder import encode_calldata_result

    source_path = Path("/tmp/lab-contract.py")
    source_path.write_text(snapshot["source"], encoding="utf-8")
    engine = SimEngine(StateStore(seed="genlayer-agent-lab-custom-v1"))
    engine.vm.warp("2026-01-01T00:00:00Z")
    engine.vm._strict_mock_mode = True
    engine.vm.strict_mocks = True
    # Regex matching is intentionally performed only inside the bounded container.
    engine.vm.mock_llm(definition["llm_pattern"], json.dumps(response))
    mock_calls = 0
    original_match = engine.vm._match_llm_mock

    def match(prompt):
        nonlocal mock_calls
        mock_calls += 1
        return original_match(prompt)

    engine.vm._match_llm_mock = match
    engine.activate()
    try:
        deployed = {}

        def deploy():
            address, instance = engine.deploy(str(source_path), args=definition["constructor_args"])
            deployed["instance"] = instance
            return address, encode_calldata_result(address)

        deployment = run_consensus(engine, deploy, num_validators=3, max_rotations=2)
        if deployment.error or deployment.status.value != "FINALIZED":
            raise RuntimeError("Custom contract deployment did not succeed")
        deployment_callbacks = len(engine.vm._captured_validators)
        address = deployment.result
        require_public_write(deployed["instance"], definition["method"])

        def invoke():
            result = engine.call_method(address, definition["method"], args=arguments)
            return result, encode_calldata_result(result)

        receipt = run_consensus(engine, invoke, num_validators=3, max_rotations=2)
        if receipt.error or receipt.status.value != "FINALIZED":
            raise RuntimeError("Custom contract method execution did not succeed")
        # JSON results preserve the declared path. Unsupported result types are
        # errors rather than a lossy repr that might accidentally match a verdict.
        json.dumps(receipt.result, allow_nan=False)
        return {
            "raw_result": receipt.result,
            "verdict": extract_verdict(receipt.result, definition),
            "execution_success": True,
            "contract_executed": True,
            "method": definition["method"],
            "genlayer_test_version": TEST_SUITE_VERSION,
            "genvm_version": GENVM_VERSION,
            "runner_hash": RUNNER_HASH,
            "mocked_io": True,
            "strict_mocks": True,
            "consensus": {
                "status": receipt.status.value,
                "votes": receipt.votes,
                "rotation": receipt.rotation,
                "captured_validators": len(engine.vm._captured_validators),
                "validator_count": 3,
                "llm_fixture_calls": mock_calls,
                "deployment_votes": deployment.votes,
                "deployment_captured_validators": deployment_callbacks,
                "mode": "GLSim simplified majority; deterministic calls use automatic votes",
                "public_network": False,
                "appeals_supported": False,
            },
        }
    finally:
        engine.deactivate()


def main() -> int:
    try:
        raw = sys.stdin.buffer.read(1_048_577)
        if len(raw) > 1_048_576:
            raise ValueError("Worker payload is too large")
        payload = json.loads(raw)
        with contextlib.redirect_stdout(sys.stderr):
            result = execute(payload)
        print(json.dumps(result, allow_nan=False))
        return 0
    except Exception as exc:
        # Exception text can be controlled by arbitrary custom source. Keep it
        # out of shared diagnostics and avoid repr/string hooks on user objects.
        print(json.dumps({"error": "Custom worker failed", "error_type": type(exc).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
