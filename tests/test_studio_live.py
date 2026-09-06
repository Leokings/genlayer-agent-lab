"""Opt-in actual GenVM/consensus tests against an already owned Studio stack.

Set GL_AGENT_LAB_STUDIO_LIVE=1 and optionally GL_AGENT_LAB_STUDIO_DATA_DIR.
The tests do not start/stop Docker, change global validators, or mutate finality.
Only sanitized conformance reports are saved under this project's .lab directory.
"""

import json
import os
import uuid
from pathlib import Path

import pytest

from genlayer_agent_lab.runtime import studio_stack
from genlayer_agent_lab.runtime.studio_evaluator import bundled_snapshot
from genlayer_agent_lab.runtime.studio_fixtures import virtual_validators
from genlayer_agent_lab.studio_conformance import run_studio_conformance

PROJECT = Path(__file__).parents[1]
pytestmark = pytest.mark.skipif(
    os.environ.get("GL_AGENT_LAB_STUDIO_LIVE") != "1",
    reason="Actual Studio mutations require explicit GL_AGENT_LAB_STUDIO_LIVE=1",
)


@pytest.fixture(scope="module")
def owned_studio():
    data_dir = Path(os.environ.get("GL_AGENT_LAB_STUDIO_DATA_DIR", PROJECT / ".lab/demo"))
    state = studio_stack.status(data_dir)
    assert state.get("ready") is True, "Explicit live Studio test requires a ready owned stack"
    assert state.get("runtime_verified") is True
    assert state.get("network_internal") is True
    assert state["source_commit"] == studio_stack.STUDIO_COMMIT
    return state


def run_case(state, verdict, *, appeal=False):
    snapshot = bundled_snapshot()
    definition = snapshot["definition"]
    context = {"evidence": "A signed delivery receipt confirms the parcel reached its recipient.",
               "resource_id": "studio-live-escrow", "policy_version": "v1",
               "amount": 100, "fixture_verdict": verdict}
    fixtures = {"validators": virtual_validators(
        verdict, definition["llm_pattern"], definition["llm_response"], count=5)}
    result = run_studio_conformance(
        state["endpoint"], snapshot, context, sim_config=fixtures, appeal=appeal,
        timeout=180, stack_pins=state, expected_verdict=verdict,
    )
    label = f"{verdict}-appeal" if appeal else verdict
    path = PROJECT / ".lab" / f"studio-live-{label}-{uuid.uuid4().hex}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    assert result["verification"] == "pass", {
        "error_code": result.get("error_code"), "verification": result["verification"],
        "report": str(path), "transactions": result["transactions"],
    }
    assert result["verdict"] == verdict
    assert result["accepted_observed"] is True
    assert result["bond_accounting"] is False
    assert result["public_chain"] is False
    assert result["stack_pins"]["image_id"] == state["image_id"]
    final = result["observations"][-1]
    assert final["checkpoint"] == "execution_finalization_result"
    assert final["status"] == "FINALIZED"
    assert final["execution_success"] is True
    assert final["result_code"] == "return"
    assert len(final["votes"]) >= 3
    assert final["round_count"] >= 1
    # Reports must keep raw receipts, returns and provider/validator configuration private.
    serialized = json.dumps(result)
    for field in ('"raw_result"', '"private_key"', '"plugin_config"', '"sim_config"'):
        assert field not in serialized
    return result


@pytest.mark.parametrize("verdict", ["approve", "deny"])
def test_real_genvm_bundled_deployment_and_distinct_writes(owned_studio, verdict):
    run_case(owned_studio, verdict)


def test_actual_appeal_completes_a_new_consensus_round(owned_studio):
    # Both initial and global appeal cohorts approve. A failed appeal is a real
    # completed appeal, and lets us test lifecycle without rewriting global models.
    result = run_case(owned_studio, "approve", appeal=True)
    assert result["appeal"]["request_observed"] is True
    assert result["appeal"]["completed"] is True
    rounds = result["appeal"]["completed_rounds"]
    assert rounds and all("Appeal" in item["kind"] for item in rounds)
    assert any(item["kind"].endswith(("Successful", "Failed")) for item in rounds)
