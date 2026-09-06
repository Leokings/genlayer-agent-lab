"""Versioned, declarative inputs. No scenario can supply executable code."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator


class DecisionEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tick: StrictInt = Field(ge=0, le=90)
    status: Literal["pending", "provisional", "final"] = "final"
    verdict: Literal["approve", "deny"] | None = None
    resource_id: str | None = Field(default=None, max_length=128)
    policy_version: str | None = Field(default=None, max_length=64)
    revision: StrictInt = Field(default=1, ge=1, le=100)
    execution_result: Literal["success", "error"] = "success"


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,95}$")
    pack: Literal["escrow", "treasury", "generic"]
    family: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=160)
    task: str = Field(min_length=1, max_length=4000)
    operation: Literal["release_payment", "transfer", "apply_decision"]
    resource_id: str = Field(min_length=1, max_length=128)
    policy_version: str = Field(default="v1", min_length=1, max_length=64)
    amount: StrictInt = Field(default=100, ge=1, le=10**12)
    initial_balance: StrictInt = Field(default=1000, ge=1, le=10**12)
    evidence: str = Field(min_length=1, max_length=16000)
    fixture_verdict: Literal["approve", "deny"] = "approve"
    expected_decision: Literal["approve", "deny"] = "approve"
    expected_effect: Literal["execute", "hold"] = "execute"
    timeline: list[DecisionEvent] = Field(min_length=1, max_length=20)
    lose_first_ack: bool = False
    max_calls: StrictInt = Field(default=100, ge=5, le=1000)
    timeout_seconds: StrictInt = Field(default=600, ge=5, le=3600)

    @model_validator(mode="after")
    def consistent(self):
        ticks = [event.tick for event in self.timeline]
        if ticks[0] != 0 or ticks != sorted(set(ticks)):
            raise ValueError("Timeline must start at tick 0 and use strictly increasing ticks")
        if self.amount > self.initial_balance:
            raise ValueError("Amount must fit the initial balance")
        if self.pack == "escrow" and self.operation != "release_payment":
            raise ValueError("Escrow scenarios require release_payment")
        if self.pack == "treasury" and self.operation != "transfer":
            raise ValueError("Treasury scenarios require transfer")
        if self.pack == "generic" and self.operation != "apply_decision":
            raise ValueError("Generic scenarios require apply_decision")
        return self

    def summary(self) -> dict:
        return {key: getattr(self, key) for key in ("id", "pack", "family", "title", "task")}
