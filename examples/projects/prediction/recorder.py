# v0.3.0
# { "Depends": "py-genlayer:5jycge4q8k23462jtb0b9fyey1s9qz928sz2nbrd9mg4sxqg2qng" }

import genlayer as gl
from genlayer.types import Address, u256


class PredictionRecord(gl.contract.Contract):
    """Records a verified oracle observation once; no payment or token simulation.

    The caller must wait for the oracle transaction's finality. The Lab checks
    that policy independently because a cross-contract state read alone does
    not establish finality of the originating transaction.
    """

    owner: Address
    oracle: Address
    market_id: str
    recorded: bool
    outcome: str
    oracle_revision: u256
    record_count: u256

    def __init__(self, oracle: str, market_id: str):
        self.owner = gl.message.sender_address
        self.oracle = Address(oracle)
        self.market_id = market_id
        self.recorded = False
        self.outcome = "unresolved"
        self.oracle_revision = u256(0)
        self.record_count = u256(0)

    def _state(self) -> dict:
        return {"market_id": self.market_id, "recorded": self.recorded,
                "outcome": self.outcome, "oracle_revision": int(self.oracle_revision),
                "record_count": int(self.record_count)}

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
        return self._state()
