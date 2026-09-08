# v0.3.0
# { "Depends": "py-genlayer:5jycge4q8k23462jtb0b9fyey1s9qz928sz2nbrd9mg4sxqg2qng" }

import genlayer as gl
from genlayer.types import Address, u256


class PredictionAudit(gl.contract.Contract):
    """An observable downstream effect with an operator-controlled repair path."""

    owner: Address
    market_id: str
    authorized_emitter: str
    reject_delivery: bool
    recorded: bool
    outcome: str
    oracle_revision: u256
    delivery_count: u256
    repair_count: u256

    def __init__(self, market_id: str, reject_delivery: bool):
        self.owner = gl.message.sender_address
        self.market_id = market_id
        self.authorized_emitter = "0x" + "00" * 20
        self.reject_delivery = reject_delivery
        self.recorded = False
        self.outcome = "unresolved"
        self.oracle_revision = u256(0)
        self.delivery_count = u256(0)
        self.repair_count = u256(0)

    def _state(self) -> dict:
        return {"market_id": self.market_id, "authorized_emitter": self.authorized_emitter,
                "recorded": self.recorded, "outcome": self.outcome,
                "oracle_revision": int(self.oracle_revision),
                "delivery_count": int(self.delivery_count), "repair_count": int(self.repair_count)}

    def _require_owner(self):
        if gl.message.sender_address != self.owner:
            raise gl.vm.UserError("[EXPECTED] Only the audit operator is authorized")

    def _confirm_record(self, revision: int, outcome: str):
        if self.recorded:
            raise gl.vm.UserError("[EXPECTED] Audit already recorded")
        record = gl.contract.get_at(Address(self.authorized_emitter)).view().get_state()
        if (record["market_id"] != self.market_id or not record["recorded"]
                or record["oracle_revision"] != revision or record["outcome"] != outcome):
            raise gl.vm.UserError("[EXPECTED] Supplied audit does not match the source record")

    @gl.public.view
    def get_state(self) -> dict:
        return self._state()

    @gl.public.write
    def authorize(self, emitter: str) -> dict:
        self._require_owner()
        if self.authorized_emitter != "0x" + "00" * 20:
            raise gl.vm.UserError("[EXPECTED] Audit emitter already authorized")
        self.authorized_emitter = Address(emitter).as_hex
        return self._state()

    @gl.public.write
    def append(self, revision: int, outcome: str) -> dict:
        if gl.message.sender_address != Address(self.authorized_emitter):
            raise gl.vm.UserError("[EXPECTED] Caller is not the authorized emitter")
        if self.reject_delivery:
            raise gl.vm.UserError("[EXPECTED] Controlled downstream delivery rejection")
        self._confirm_record(revision, outcome)
        self.recorded = True
        self.outcome = outcome
        self.oracle_revision = u256(revision)
        self.delivery_count = u256(self.delivery_count + 1)
        return self._state()

    @gl.public.write
    def repair(self, revision: int, outcome: str) -> dict:
        self._require_owner()
        self._confirm_record(revision, outcome)
        self.recorded = True
        self.outcome = outcome
        self.oracle_revision = u256(revision)
        self.repair_count = u256(self.repair_count + 1)
        return self._state()
