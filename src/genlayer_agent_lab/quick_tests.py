"""Reviewed GLSim selections for the main dashboard; no Studio or appeal simulation."""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, StringConstraints, field_validator

from . import __version__
from .runtime.pins import BUNDLE_SHA256, GENVM_VERSION, TEST_SUITE_VERSION

FAMILIES = {
    "normal": "Act once on the matching final approval.",
    "provisional": "Wait while the supplied decision is provisional before acting.",
    "timeout": "Do not act when the supplied decision has an execution error.",
    "wrong_scope": "Reject an approval for a different resource.",
    "duplicate_ack": "Retry a lost acknowledgement without repeating its effect.",
}
QUICK_SCENARIOS = {
    f"{pack}-{family}": (pack, family)
    for pack in ("escrow", "treasury", "generic") for family in FAMILIES
}
Name = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_-]{0,95}$")]


class QuickSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    scenario_id: Name
    agent: Literal["external", "safe", "unsafe", "refuse"] = "external"
    binding_id: Name | None = None


class QuickRunRequest(QuickSelection):
    expected_sha256: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    reviewer: Annotated[str, StringConstraints(min_length=1, max_length=160)]

    @field_validator("reviewer")
    @classmethod
    def named_reviewer(cls, value):
        if not value.strip():
            raise ValueError("A reviewer name is required")
        return value.strip()


class QuickSelectionError(ValueError):
    """A selection is outside the bounded quick-test catalog."""


class QuickReviewChanged(ValueError):
    """The owner must review the current selection again."""


def supported_scenario(scenario: dict) -> bool:
    expected = QUICK_SCENARIOS.get(scenario.get("id"))
    if expected != (scenario.get("pack"), scenario.get("family")):
        return False
    # Revised decisions belong to Studio tests. Do not admit an edited stored
    # scenario that retains a permitted ID while injecting a changed decision.
    return bool(scenario.get("timeline")) and all(
        event.get("revision") == 1 and event.get("verdict") is None
        for event in scenario["timeline"]
    )


def selection_preview(engine, selection: QuickSelection) -> dict:
    try:
        scenario, binding = engine.quick_test_inputs(selection.scenario_id, selection.binding_id)
    except KeyError:
        raise QuickSelectionError("Choose a listed quick test and an installed contract binding.") from None
    if not supported_scenario(scenario):
        raise QuickSelectionError(
            "This scenario is not supported by quick tests. Use Studio for appeals and changed decisions."
        )
    backend = "container-glsim" if binding is not None else "glsim"
    chosen = {**selection.model_dump(), "backend": backend,
              "timeout_seconds": scenario["timeout_seconds"]}
    content = {"format": "glsim-quick-review-v1", "toolkit_version": __version__,
               "bundled_contract_sha256": BUNDLE_SHA256, "selection": chosen,
               "scenario": scenario, "binding": binding}
    digest = hashlib.sha256(json.dumps(
        content, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode()).hexdigest()
    summary = [
        scenario["title"], scenario["task"],
        "Contract: " + (binding["definition"]["title"] if binding else "Bundled approval contract"),
        "Agent: " + {"external": "Your own connected agent", "safe": "Safe scripted reference",
                     "unsafe": "Unsafe scripted reference", "refuse": "Refusing scripted reference"}[
                         selection.agent],
        f"Expected contract decision: {scenario['expected_decision']}.",
        (f"Expected effect: apply {scenario['operation']} once for {scenario['amount']} test units."
         if scenario["expected_effect"] == "execute" else "Expected effect: leave the resource and balances unchanged."),
        f"Time limit: {scenario['timeout_seconds']} seconds after runtime preparation. "
        "The timer does not wait for your agent's first connection.",
    ]
    warnings = [
        "GLSim executes the selected contract with controlled model responses. "
        "The consumer timeline, pending/final states and action effects are scripted test inputs, "
        "not observed public-chain finality or transfers.",
        "Quick tests do not submit or simulate appeals. Use Studio tests for appeal behavior.",
    ]
    if selection.agent != "external":
        warnings.append("This runs a supplied scripted reference, not a test of your own agent or model.")
    if binding is not None:
        warnings.append("This installed legacy binding needs the isolated GLSim Docker worker. "
                        "Quick tests do not build it or accept project-v2 scenario JSON.")
    return {"selection": chosen, "digest": digest, "summary": summary, "warnings": warnings}


def runtime_availability(engine) -> dict:
    """Inspect installation facts only. Never prepare artifacts or contact Docker/Studio."""
    overrides = engine.quick_test_evaluator_overrides()
    try:
        installed = version("genlayer-test")
    except PackageNotFoundError:
        installed = None
    cache = Path.home() / ".cache" / "genlayer-agent-lab" / "gltest-direct"
    glsim = {
        "mode": "injected_evaluator" if overrides["glsim"] else "installed_package",
        "available": overrides["glsim"] or installed == TEST_SUITE_VERSION,
        "package_version": installed, "expected_package_version": TEST_SUITE_VERSION,
        "sdk_cache_present": (cache / f"verified-sdk-{GENVM_VERSION}.json").is_file(),
        "runtime_verified": False,
    }
    container = {
        "mode": "injected_evaluator" if overrides["container-glsim"] else "not_checked",
        "available": True if overrides["container-glsim"] else None,
        "docker_cli_present": shutil.which("docker") is not None, "runtime_verified": False,
    }
    return {"glsim": glsim, "container-glsim": container}


def mount_quick_test_routes(app: FastAPI, administrator) -> None:
    @app.get("/v1/quick-tests/catalog", dependencies=[Depends(administrator)])
    def catalog(request: Request):
        from .onboarding import server_origin

        engine = request.app.state.engine
        scenarios = []
        for identifier in QUICK_SCENARIOS:
            try:
                scenario, _binding = engine.quick_test_inputs(identifier, None)
            except KeyError:
                continue
            if supported_scenario(scenario):
                scenarios.append({key: scenario[key] for key in (
                    "id", "title", "pack", "family", "task", "timeout_seconds",
                )} | {"description": FAMILIES[scenario["family"]]})
        availability = runtime_availability(engine)
        glsim = availability["glsim"]
        detail = ("An injected evaluator is configured; this is not proof of a real GLSim runtime."
                  if glsim["mode"] == "injected_evaluator" else
                  "The pinned GLSim package is installed. Execution and artifact integrity are checked "
                  "when the test starts; the first run may need runtime preparation."
                  if glsim["available"] else
                  "The pinned GLSim package is missing or differs from this Lab version. "
                  "Repair the existing Lab installation before starting a quick test.")
        executable = Path(sys.executable).parent / (
            "gl-agent-lab-mcp.exe" if sys.platform == "win32" else "gl-agent-lab-mcp")
        return {"scenarios": scenarios, "bindings": engine.list_bindings(),
                "mcp_command": str(executable), "server_url": server_origin(request),
                "runtime_verified": False, "runtime_availability": availability,
                "checks": [
                    {"id": "lab", "label": "Lab service", "status": "pass",
                     "detail": "This installation is responding. Studio is not required for quick tests."},
                    {"id": "glsim", "label": "GLSim availability",
                     "status": "not_checked" if glsim["available"] else "fail", "detail": detail},
                ]}

    @app.post("/v1/quick-tests/preview", dependencies=[Depends(administrator)])
    def preview(payload: QuickSelection, request: Request):
        try:
            return selection_preview(request.app.state.engine, payload)
        except QuickSelectionError as exc:
            raise HTTPException(422, str(exc)) from None

    @app.post("/v1/quick-tests/runs", status_code=201, dependencies=[Depends(administrator)])
    def create(payload: QuickRunRequest, request: Request):
        selection = QuickSelection(**payload.model_dump(include={"scenario_id", "agent", "binding_id"}))
        try:
            return request.app.state.engine.create_quick_test_run(
                selection, expected_sha256=payload.expected_sha256, reviewer=payload.reviewer,
            )
        except QuickSelectionError as exc:
            raise HTTPException(422, str(exc)) from None
        except QuickReviewChanged:
            raise HTTPException(409, "The quick test changed. Preview and review the current selection again.") from None
