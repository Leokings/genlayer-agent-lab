# v0.3.0
# { "Depends": "py-genlayer:5jycge4q8k23462jtb0b9fyey1s9qz928sz2nbrd9mg4sxqg2qng" }

import genlayer as gl
from genlayer.types import Address, u256


class PredictionRecordWithMessage(gl.contract.Contract):
    """A finalized record is distinct from its asynchronously emitted audit."""

    owner: Address
    oracle: Address
    audit: Address
    market_id: str
    emission_stage: str
    recorded: bool
    outcome: str
    oracle_revision: u256
    record_count: u256

    def __init__(self, oracle: str, audit: str, market_id: str, emission_stage: str):
        if emission_stage not in ("decided", "finalized"):
            raise gl.vm.UserError("[EXPECTED] Invalid message emission stage")
        self.owner = gl.message.sender_address
        self.oracle = Address(oracle)
        self.audit = Address(audit)
        self.market_id = market_id
        self.emission_stage = emission_stage
        self.recorded = False
        self.outcome = "unresolved"
        self.oracle_revision = u256(0)
        self.record_count = u256(0)

    def _state(self) -> dict:
        return {"market_id": self.market_id, "recorded": self.recorded,
                "outcome": self.outcome, "oracle_revision": int(self.oracle_revision),
                "record_count": int(self.record_count), "audit_address": self.audit.as_hex,
                "emission_stage": self.emission_stage}

    @gl.public.view
    def get_state(self) -> dict:
        return self._state()

    @gl.public.write
    def record(self, expected_revision: int, outcome: str) -> dict:
        if gl.message.sender_address != self.owner:
            raise gl.vm.UserError("[EXPECTED] Only the record operator can write")
        if self.recorded:
            raise gl.vm.UserError("[EXPECTED] Prediction already recorded")
        state = gl.contract.get_at(self.oracle).view().get_state()
        if state["market_id"] != self.market_id:
            raise gl.vm.UserError("[EXPECTED] Oracle market mismatch")
        if type(expected_revision) is not int or expected_revision < 1:
            raise gl.vm.UserError("[EXPECTED] Invalid oracle revision")
        if state["revision"] != expected_revision:
            raise gl.vm.UserError("[EXPECTED] Stale oracle revision")
        if outcome not in ("yes", "no", "void") or state["outcome"] != outcome:
            raise gl.vm.UserError("[EXPECTED] Outcome does not match the oracle")
        self.recorded = True
        self.outcome = outcome
        self.oracle_revision = u256(expected_revision)
        self.record_count = u256(self.record_count + 1)
        gl.contract.get_at(self.audit).emit(on=self.emission_stage).append(expected_revision, outcome)
        return self._state()
