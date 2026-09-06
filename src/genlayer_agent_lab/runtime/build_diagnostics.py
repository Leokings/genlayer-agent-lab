"""Structured worker-build diagnostics without persisting arbitrary process output."""

from __future__ import annotations

import json
import math
import os
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path

STAGES = {"prepare_sdk", "build_image", "readiness_probe"}
MESSAGES = {
    "timeout": ("A worker-build operation exceeded its time limit.",
                "Check Docker readiness and network access, then retry the explicit build."),
    "output_limit": ("A worker-build operation exceeded its bounded output limit.",
                     "Inspect Docker and dependency installation before retrying."),
    "dns_failure": ("Docker or a dependency download could not resolve a host.",
                    "Check DNS and connectivity for Docker registries and package downloads."),
    "tls_failure": ("A registry or package connection failed certificate or TLS validation.",
                    "Check the system clock and configured trust certificates; keep verification enabled."),
    "disk_full": ("The build ran out of available storage.",
                  "Check the Docker engine's disk allocation and available host storage."),
    "dependency_integrity": ("A dependency failed its expected integrity check.",
                             "Retry the verified download; do not bypass the pinned hashes."),
    "package_resolution": ("The pinned build dependencies could not be resolved.",
                           "Check package-index connectivity and the supported release requirements."),
    "registry_access": ("A container registry rejected or limited the image download.",
                        "Check Docker's registry access and rate limits before retrying."),
    "docker_unavailable": ("A compatible local Docker engine could not be reached.",
                           "Start the local Linux Docker engine and run worker doctor."),
    "sdk_preparation": ("The pinned SDK could not be prepared for the worker build.",
                        "Run the native runtime doctor and check its readiness result."),
    "readiness_failure": ("The built image did not pass its contract readiness probe.",
                          "Run worker doctor and inspect the installed toolkit's supported runtime versions."),
    "build_failure": ("The worker image build failed.",
                      "Check the reported stage and Docker exit code, then retry the explicit build."),
}


class BuildFailure(RuntimeError):
    """A fixed-message error with an optional path to a credential-free structured log."""

    def __init__(self, diagnostic: dict, log_path: Path | None):
        self.diagnostic = diagnostic
        self.log_path = log_path
        super().__init__(diagnostic["summary"])

    def as_result(self) -> dict:
        return {"ready": False, "status": "error", "backend": "container-glsim",
                "stage": self.diagnostic["stage"], "category": self.diagnostic["category"],
                "error": self.diagnostic["summary"], "hint": self.diagnostic["hint"],
                "diagnostic_log": str(self.log_path) if self.log_path is not None else None}


def _classify(stage: str, stdout: bytes, stderr: bytes, error: Exception | None) -> str:
    # Raw text is used only to recognize known causes. It is never returned or written.
    samples = [value[:16_384] + value[-16_384:] for value in (stdout, stderr)]
    text = b"\n".join(samples).decode("utf-8", errors="replace").casefold()
    text += "\n" + (str(error)[:4096].casefold() if error is not None else "")
    categories = (
        ("output_limit", ("output limit", "unclosed child pipe")),
        ("dns_failure", ("no such host", "temporary failure in name resolution",
                         "name or service not known", "could not resolve")),
        ("tls_failure", ("certificate verify failed", "certificate_verify_failed", "x509:",
                         "tls handshake", "certificate signed by unknown authority")),
        ("disk_full", ("no space left on device", "enospc")),
        ("dependency_integrity", ("hashes do not match", "packages do not match the hashes",
                                  "checksum mismatch", "hash mismatch")),
        ("package_resolution", ("no matching distribution found", "could not find a version that satisfies",
                                "resolutionimpossible")),
        ("registry_access", ("unauthorized", "authentication required", "pull access denied",
                             "insufficient_scope", "toomanyrequests", "429 too many requests")),
        ("docker_unavailable", ("cannot connect to the docker daemon", "error during connect",
                                 "docker probe failed", "docker context inspection failed",
                                 "running linux docker engine", "select a local docker",
                                 "cannot launch docker command")),
        ("timeout", ("timed out", "timeout", "deadline exceeded")),
    )
    for category, patterns in categories:
        if any(pattern in text for pattern in patterns):
            return category
    return {"prepare_sdk": "sdk_preparation", "readiness_probe": "readiness_failure"}.get(
        stage, "build_failure"
    )


def _write_log(log_dir: Path | None, diagnostic: dict) -> Path | None:
    if log_dir is None:
        return None
    try:
        directory = Path(log_dir)
        if directory.is_symlink():
            return None
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = directory / f"worker-{uuid.uuid4().hex}.json"
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(diagnostic, ensure_ascii=True, indent=2) + "\n")
        return path.resolve()
    except (OSError, ValueError):
        # Diagnostics must never replace the original build failure.
        return None


def build_failure(stage: str, elapsed: float, image_tag: str, *, docker_exit: int | None = None,
                  stdout: bytes = b"", stderr: bytes = b"", error: Exception | None = None,
                  log_dir: Path | None = None) -> BuildFailure:
    stage = stage if stage in STAGES else "unknown"
    category = _classify(stage, stdout, stderr, error)
    summary, hint = MESSAGES[category]
    diagnostic = {
        "schema_version": 1, "at": datetime.now(UTC).isoformat(), "stage": stage,
        "elapsed_seconds": round(max(0, elapsed), 2) if math.isfinite(elapsed) else None,
        "image_tag": image_tag if re.fullmatch(r"genlayer-agent-lab-worker:[A-Za-z0-9._-]+", image_tag)
        else "genlayer-agent-lab-worker",
        "docker_exit_code": docker_exit if type(docker_exit) is int else None,
        "category": category, "summary": summary, "hint": hint,
        "output_policy": "Recognized failure categories only; no raw output, source, fixtures or environment.",
    }
    return BuildFailure(diagnostic, _write_log(log_dir, diagnostic))
