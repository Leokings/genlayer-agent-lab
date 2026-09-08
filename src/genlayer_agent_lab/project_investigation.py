"""Bounded agent-authored findings attached to an observed GenLayer decision.

This records observable claims and evidence citations. It does not judge prose,
fetch websites, disclose private reasoning or submit a dossier to GenLayer.
"""

from __future__ import annotations

import copy
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from .bindings import _hash, bounded_json

MAX_INVESTIGATIONS = 4


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    evidence_id: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_]{0,95}$")
    assessment: Literal["supports", "contradicts", "missing", "stale", "conflicting", "untrusted"]
    note: str = Field(min_length=1, max_length=400)

    @field_validator("note")
    @classmethod
    def meaningful_note(cls, value):
        if not value.strip():
            raise ValueError("A finding needs a concise note")
        return value


class InvestigationSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    disposition: Literal["accept", "appeal", "request_review"]
    proposed_result: JsonValue
    findings: list[Finding] = Field(min_length=1, max_length=32)
    summary: str = Field(min_length=1, max_length=1200)

    @model_validator(mode="before")
    @classmethod
    def bounded_submission(cls, value):
        bounded_json(value, max_nodes=512)
        if len(json.dumps(value, ensure_ascii=True, allow_nan=False).encode()) > 24000:
            raise ValueError("Investigation artifact exceeds its byte limit")
        return value

    @model_validator(mode="after")
    def distinct_citations(self):
        ids = [item.evidence_id for item in self.findings]
        if len(ids) != len(set(ids)):
            raise ValueError("Investigation citations must be unique")
        if not self.summary.strip():
            raise ValueError("An investigation needs a concise summary")
        return self


def investigation_view(operations):
    """Derive artifacts from committed intents, so retries cannot append copies."""
    artifacts, reads = [], {}
    for intent in operations:
        if intent.get("status") != "completed" or intent.get("execution_success") is not True:
            continue
        result = intent.get("result")
        if intent.get("operation") == "submit_investigation" and type(result) is dict:
            artifacts.append(copy.deepcopy(result))
        if intent.get("operation") == "read_evidence" and type(result) is dict:
            identity = result.get("id")
            if type(identity) is str:
                reads[identity] = reads.get(identity, 0) + 1
    return {"investigations": artifacts, "investigation_count": len(artifacts), "evidence_reads": reads}


def build_investigation(arguments, *, intent_key, decision, operations, declared_evidence):
    """Validate citations against completed reads and freeze the decision used.

    Structural validity does not establish that an agent's findings are correct.
    The scenario's independent expectations grade the recorded findings/actions.
    """
    submission = InvestigationSubmission.model_validate(arguments).model_dump()
    if not decision or decision.get("execution_success") is not True:
        raise ValueError("Investigation requires a successfully executed current decision")
    if investigation_view(operations)["investigation_count"] >= MAX_INVESTIGATIONS:
        raise ValueError("Investigation limit exceeded")
    read_results = {item["result"]["id"]: item["result"] for item in operations
                    if item.get("operation") == "read_evidence" and item.get("status") == "completed"
                    and item.get("execution_success") is True and type(item.get("result")) is dict
                    and type(item["result"].get("id")) is str}
    known = {item["id"] for item in declared_evidence}
    ids = [item["evidence_id"] for item in submission["findings"]]
    if not set(ids) <= known or not set(ids) <= read_results.keys():
        raise ValueError("Investigation must cite evidence already read in this run")
    return {**submission, "submission_id": intent_key, "decision_id": decision["decision_id"],
            "decision_snapshot": copy.deepcopy({key: decision.get(key) for key in
                ("decision_id", "tx_id", "operation", "status", "execution_success", "result")}),
            "findings_by_evidence": {item["evidence_id"]: copy.deepcopy(item) for item in submission["findings"]},
            "citations": [{"evidence_id": identity, "sha256": _hash(read_results[identity])} for identity in ids],
            "artifact_scope": "agent_authored_lab_report_only"}
