"""Credential-free diagnostics for the explicitly managed Studio image build."""

from __future__ import annotations

import json
import math
import os
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path

from .build_diagnostics import MESSAGES as BUILD_MESSAGES
from .build_diagnostics import _classify

MESSAGES = {
    **BUILD_MESSAGES,
    "timeout": ("The Studio image build exceeded its time limit.",
                "Check Docker readiness and network access, then retry studio build."),
    "output_limit": ("The Studio image build exceeded its bounded output limit.",
                     "Inspect Docker and dependency installation before retrying studio build."),
    "docker_unavailable": ("The local Docker engine could not be reached for the Studio build.",
                           "Start the local Linux Docker engine, then retry studio build."),
    "build_failure": ("The Studio image build failed.",
                      "Check the reported stage and Docker exit code, then retry studio build."),
}


class StudioBuildFailure(RuntimeError):
    """Only fixed diagnostic text and the owned log path reach the CLI error handler."""

    def __init__(self, diagnostic: dict, log_path: Path | None):
        self.diagnostic = diagnostic
        self.log_path = log_path
        details = f"stage={diagnostic['stage']}, category={diagnostic['category']}"
        if diagnostic["docker_exit_code"] is not None:
            details += f", exit={diagnostic['docker_exit_code']}"
        saved = (f"Diagnostics: {log_path}" if log_path is not None
                 else "Structured diagnostics could not be saved.")
        super().__init__(f"Studio image build failed ({details}). "
                         f"{diagnostic['summary']} {diagnostic['hint']} {saved}")

    def as_result(self) -> dict:
        return {"ready": False, "status": "error", "backend": "studio",
                "stage": self.diagnostic["stage"], "category": self.diagnostic["category"],
                "error": self.diagnostic["summary"], "hint": self.diagnostic["hint"],
                "diagnostic_log": str(self.log_path) if self.log_path is not None else None}


def _write_log(log_dir: Path, diagnostic: dict) -> Path | None:
    try:
        directory = Path(log_dir)
        if directory.is_symlink():
            return None
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = directory / f"studio-{uuid.uuid4().hex}.json"
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(diagnostic, ensure_ascii=True, indent=2) + "\n")
        return path.resolve()
    except (OSError, ValueError):
        # A log failure must not mask the image-build failure.
        return None


def studio_build_failure(elapsed: float, image_tag: str, *, log_dir: Path,
                         docker_exit: int | None = None, stdout: bytes = b"",
                         stderr: bytes = b"", error: Exception | None = None) -> StudioBuildFailure:
    # The shared classifier samples bounded text; none of that text is retained.
    category = _classify("build_image", stdout, stderr, error)
    summary, hint = MESSAGES[category]
    diagnostic = {
        "schema_version": 1, "at": datetime.now(UTC).isoformat(), "backend": "studio",
        "stage": "build_image",
        "elapsed_seconds": round(min(86_400, max(0, elapsed)), 2)
        if math.isfinite(elapsed) else None,
        "image_tag": image_tag if re.fullmatch(
            r"genlayer-agent-lab-studio:[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}(?:-[0-9a-f]{32})?",
            image_tag) else "genlayer-agent-lab-studio",
        "docker_exit_code": docker_exit
        if type(docker_exit) is int and -(2**31) <= docker_exit < 2**32 else None,
        "category": category, "summary": summary, "hint": hint,
        "output_policy": "Recognized failure categories only; no raw output, source, fixtures or environment.",
    }
    return StudioBuildFailure(diagnostic, _write_log(log_dir, diagnostic))
