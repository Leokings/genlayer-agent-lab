"""GenVM verdicts for agent scenarios; consumer event schedules remain scripted."""

import copy
from pathlib import Path

from ..bindings import _definition, _snapshot, resolve_template, validate_snapshot
from .studio_fixtures import virtual_validators


class StudioEvaluationError(RuntimeError):
    """Carry only the conformance helper's sanitized evidence into a failed run."""

    def __init__(self, provenance: dict):
        self.provenance = copy.deepcopy(provenance)
        super().__init__("Studio execution did not produce a successfully finalized mapped result")


def bundled_snapshot() -> dict:
    source = Path(__file__).with_name("contracts").joinpath("evidence_decision.py").read_text(encoding="utf-8")
    definition = _definition({
        "id": "bundled-evidence-decision", "title": "Bundled evidence decision",
        "source": "evidence_decision.py", "method": "evaluate",
        "arguments": [{"from_field": "evidence"}],
        "llm_pattern": "^AGENT_LAB_EVIDENCE_V1\\n", "llm_response": {"verdict": "$fixture_verdict"},
    })
    return _snapshot(definition, source)


def evaluate(data_dir: Path, snapshot: dict | None, context: dict, *, timeout=180,
             cancel_event=None) -> dict:
    from ..studio_conformance import run_studio_conformance
    from .studio_cohort import StudioFixtureLease
    from .studio_stack import status

    checked = validate_snapshot(snapshot) if snapshot is not None else bundled_snapshot()
    definition = checked["definition"]
    fixtures = {"validators": virtual_validators(
        context["fixture_verdict"], definition["llm_pattern"],
        resolve_template(definition["llm_response"], context))}
    stack = status(data_dir)
    if not stack.get("ready"):
        raise RuntimeError("Owned Studio is not ready; run studio status")
    with StudioFixtureLease(data_dir):
        evidence = run_studio_conformance(
            stack["endpoint"], checked, context, sim_config=fixtures, timeout=timeout,
            stack_pins=stack, cancel_event=cancel_event,
        )
    successful = evidence["verification"] == "pass" and evidence.get("verdict") in {"approve", "deny"}
    # Submission alone is not evidence of contract execution. Preserve an unknown
    # outcome if observation ended before Studio reported an execution result.
    execution_observations = [item for item in evidence.get("observations", [])
                              if item.get("tx_id") == evidence.get("transactions", {}).get("execution")
                              and type(item.get("execution_success")) is bool]
    execution_success = True if successful else (
        execution_observations[-1]["execution_success"] if execution_observations else None)
    provenance = {
        "backend": "studio", "contract_executed": True if successful or execution_observations else None,
        "execution_success": execution_success,
        "mocked_io": True, "public_chain": False, "bond_accounting": False,
        "contract_execution": "GenVM in the owned local Studio stack",
        "agent_lifecycle": "scripted consumer events after Studio contract execution",
        "studio_evidence": evidence,
    }
    if not successful:
        raise StudioEvaluationError(provenance)
    return {"verdict": evidence["verdict"], "provenance": provenance}
