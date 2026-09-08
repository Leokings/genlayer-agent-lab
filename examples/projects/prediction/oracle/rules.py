"""Shared contract module, executed inside GenVM rather than on the Lab host."""

import hashlib

import genlayer as gl


def evidence_digest(evidence: str) -> str:
    return hashlib.sha256(evidence.encode("utf-8")).hexdigest()


def normalize_decision(response) -> dict:
    if not isinstance(response, dict) or set(response) != {"outcome", "confidence_bps"}:
        raise gl.vm.UserError("[LLM_ERROR] Expected outcome and confidence_bps")
    if response["outcome"] not in ("yes", "no", "void"):
        raise gl.vm.UserError("[LLM_ERROR] Invalid prediction outcome")
    confidence = response["confidence_bps"]
    if type(confidence) is not int or not 0 <= confidence <= 10000:
        raise gl.vm.UserError("[LLM_ERROR] Invalid prediction confidence")
    return {"outcome": response["outcome"], "confidence_bps": confidence}
