"""Verify independent clients against an already running custom-contract worker.

This is a development check, not an installer. Start the local HTTP service,
import delivery-assessment, and prepare the container worker or owned Studio
stack before running it. Use --backend studio to verify observed Studio execution.
Only the orchestrator reads the administrator token. Child examples receive
run-scoped credentials, and server reports are the source of truth.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import re
import runpy
import shutil
import subprocess
import sys
import time
from pathlib import Path

from genlayer_agent_lab.client import LabClient, LabError, validate_base_url

ROOT = Path(__file__).resolve().parents[1]
CLIENTS = ("python", "typescript", "mcp")
GRADE_NAMES = ("decision", "behavior", "outcome", "completion")


def child_environment(base_url: str, token: str, run_id: str) -> dict[str, str]:
    """Deliberate allowlist: no admin token, model keys, proxies or Python/Node options."""
    allowed = ("SYSTEMROOT", "SystemRoot", "WINDIR", "TEMP", "TMP", "TMPDIR")
    result = {key: os.environ[key] for key in allowed if key in os.environ}
    result.update({"LAB_URL": base_url, "LAB_TOKEN": token, "LAB_RUN_ID": run_id,
                   "LAB_ROLE": "agent", "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"})
    return result


def summary(client: str, report: dict) -> dict:
    """Never include full fixtures, raw output, source, credentials or arbitrary findings."""
    manifest = report.get("manifest") or {}
    binding = manifest.get("binding") or {}
    runtime = manifest.get("runtime") or {}
    image_id = runtime.get("image_id")
    if manifest.get("backend") == "studio":
        image_id = ((runtime.get("studio_evidence") or {}).get("stack_pins") or {}).get("image_id")
    return {"client": client, "run_id": report.get("run_id"), "scenario": report.get("scenario"),
            "status": report.get("status"), "verdict": report.get("verdict"),
            "backend": manifest.get("backend"), "binding_id": binding.get("id"),
            "binding_sha256": binding.get("binding_sha256"),
            "source_sha256": binding.get("source_sha256"), "image_id": image_id}


def _check_studio_evidence(evidence: dict) -> dict:
    """Validate actual Studio checkpoints; never treat scripted consumer events as receipts."""
    if evidence.get("backend") != "studio" or evidence.get("verification") != "pass":
        raise RuntimeError("The report lacks passing Studio execution conformance")
    if evidence.get("verdict") not in {"approve", "deny"}:
        raise RuntimeError("The Studio contract result was not mapped to a decision")
    stack = evidence.get("stack_pins") or {}
    expected = evidence.get("expected_pins") or {}
    if (stack.get("provenance") != "owner_reported"
            or stack.get("rpc_release_verified") is not False
            or stack.get("network_internal") is not True
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", str(stack.get("image_id", "")))
            or not re.fullmatch(r"[0-9a-f]{40}", str(stack.get("source_commit", "")))
            or stack.get("source_commit") != expected.get("studio_commit")):
        raise RuntimeError("The report lacks consistent owner-reported immutable Studio stack pins")
    transactions = evidence.get("transactions") or {}
    observations = evidence.get("observations") or []
    for name, checkpoint in (("deployment", "deployment_finalization_result"),
                             ("execution", "execution_finalization_result")):
        tx_id = transactions.get(name)
        if not re.fullmatch(r"0x[0-9a-fA-F]{64}", str(tx_id or "")):
            raise RuntimeError("The Studio evidence is missing a submitted transaction identifier")
        final = next((item for item in reversed(observations)
                      if item.get("checkpoint") == checkpoint), {})
        if (final.get("tx_id") != tx_id or final.get("status") != "FINALIZED"
                or final.get("execution_success") is not True):
            raise RuntimeError("The Studio transaction lacks a matching successful finalized observation")
    return evidence.get("binding") or {}


def check_report(report: dict, binding: dict, backend: str = "container-glsim") -> None:
    if backend not in {"container-glsim", "studio"}:
        raise RuntimeError("Unsupported custom contract verification backend")
    if report.get("status") != "completed" or report.get("verdict") != "pass":
        raise RuntimeError("The server did not record a completed passing run")
    if report.get("agent") != "external" or any(
        report.get("grades", {}).get(name, {}).get("status") != "pass" for name in GRADE_NAMES
    ):
        raise RuntimeError("The external agent did not pass all four server-side grades")
    manifest = report.get("manifest") or {}
    saved_binding = manifest.get("binding") or {}
    runtime = manifest.get("runtime") or {}
    if manifest.get("backend") != backend or runtime.get("backend") != backend:
        raise RuntimeError("The report did not use the requested custom contract backend")
    runtime_binding = (_check_studio_evidence(runtime.get("studio_evidence") or {})
                       if backend == "studio" else runtime)
    if saved_binding.get("id") != binding["id"]:
        raise RuntimeError("The report refers to a different contract binding")
    if backend == "studio" and runtime_binding.get("id") != binding["id"]:
        raise RuntimeError("The Studio execution refers to a different contract binding")
    for key in ("source_sha256", "binding_sha256"):
        if (not re.fullmatch(r"[0-9a-f]{64}", str(binding.get(key, "")))
                or saved_binding.get(key) != binding[key] or runtime_binding.get(key) != binding[key]):
            raise RuntimeError("Contract snapshot hashes do not match the imported binding")
    if backend == "container-glsim" and not re.fullmatch(
            r"sha256:[0-9a-f]{64}", str(runtime.get("image_id", ""))):
        raise RuntimeError("The report is missing an immutable worker image identifier")
    required_flags = ("contract_executed", "execution_success", "mocked_io")
    if backend == "container-glsim":
        required_flags += ("isolated",)
    if any(runtime.get(key) is not True for key in
           required_flags):
        raise RuntimeError("The report lacks successful contract execution evidence")


def _mcp_agent() -> None:
    # Reuse the existing example policy without invoking its administrator CLI.
    example = runpy.run_path(str(ROOT / "examples/mcp_agent.py"), run_name="lab_mcp_example")
    result = asyncio.run(asyncio.wait_for(example["run_agent"](
        os.environ["LAB_URL"], os.environ["LAB_TOKEN"], os.environ["LAB_RUN_ID"]
    ), timeout=120))
    print(json.dumps({"run_id": result.get("run_id"), "verdict": result.get("verdict")}))


def wait_until_running(admin: LabClient, run_id: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = admin.get_run(run_id)["status"]
        if status == "running":
            return
        if status not in {"queued", "preparing"}:
            raise RuntimeError("Custom contract preparation ended before an agent could connect")
        time.sleep(0.25)
    raise TimeoutError("Custom contract preparation exceeded the verification deadline")


def _commands(node: str) -> dict[str, list[str]]:
    return {
        "python": [sys.executable, "-I", "-B", str(ROOT / "examples/python_agent.py")],
        "typescript": [node, str(ROOT / "examples/typescript/agent.ts")],
        "mcp": [sys.executable, "-I", "-B", str(Path(__file__).resolve()), "--_mcp-agent"],
    }


def verify(base_url: str, admin_token: str, binding_id: str, scenario: str,
           timeout: float, node: str, backend: str = "container-glsim") -> tuple[list[dict], int]:
    if backend not in {"container-glsim", "studio"}:
        raise RuntimeError("Unsupported custom contract verification backend")
    results = []
    with LabClient(base_url, admin_token) as admin:
        binding = next((item for item in admin.list_bindings() if item["id"] == binding_id), None)
        if binding is None:
            raise RuntimeError("Import the requested contract binding before running this check")
        if not any(item["id"] == scenario for item in admin.list_scenarios()):
            raise RuntimeError("The requested scenario is not installed")
        for client_name, command in _commands(node).items():
            created = admin.create_run(scenario, agent="external", backend=backend,
                                       binding_id=binding_id)
            run_id = created["run_id"]
            report = None
            try:
                wait_until_running(admin, run_id, timeout)
                process = subprocess.run(
                    command, cwd=ROOT, env=child_environment(base_url, created["agent_token"], run_id),
                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                    timeout=timeout, check=False,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
                # Example output is captured and discarded. Never use its claimed verdict.
                report = admin.report(run_id)
                if process.returncode:
                    raise RuntimeError("An example process failed; inspect the server's saved run")
                check_report(report, binding, backend)
            except (RuntimeError, TimeoutError, subprocess.TimeoutExpired, OSError, LabError):
                try:
                    admin.cancel_run(run_id)
                    report = admin.report(run_id)
                except LabError:
                    report = {"run_id": run_id, "status": "unavailable", "verdict": "inconclusive"}
                result = summary(client_name, report)
                result["verification"] = "fail"
                results.append(result)
                return results, 1 if report.get("verdict") == "fail" else 2
            except KeyboardInterrupt:
                admin.cancel_run(run_id)
                raise
            result = summary(client_name, report)
            result["verification"] = "pass"
            results.append(result)
    return results, 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=os.getenv("LAB_URL", "http://127.0.0.1:8765"))
    parser.add_argument("--data-dir", type=Path, default=Path(os.getenv("LAB_DATA_DIR", ROOT / ".lab/demo")))
    parser.add_argument("--binding", default="delivery-assessment")
    parser.add_argument("--scenario", default="escrow-normal")
    parser.add_argument("--backend", choices=("container-glsim", "studio"), default="container-glsim")
    parser.add_argument("--timeout", type=float, default=180, help="Separate preparation and client time limits")
    parser.add_argument("--_mcp-agent", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args._mcp_agent:
        _mcp_agent()
        return 0
    try:
        base_url = validate_base_url(args.url)
        if not math.isfinite(args.timeout) or not 1 <= args.timeout <= 1800:
            raise RuntimeError("Verification timeout must be between 1 and 1800 seconds")
        node = shutil.which("node")
        if node is None:
            raise RuntimeError("Node.js 24 or newer is required for the TypeScript example")
        version = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=10,
                                 env=child_environment(base_url, "version-probe", "version-probe"),
                                 creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        if version.returncode or not re.match(r"v(?:2[4-9]|[3-9][0-9])\.", version.stdout.strip()):
            raise RuntimeError("Node.js 24 or newer is required for the TypeScript example")
        token = (args.data_dir / "admin.token").read_text(encoding="utf-8").strip()
        results, code = verify(base_url, token, args.binding, args.scenario, args.timeout, node,
                               backend=args.backend)
        evidence = {"verification": "pass" if code == 0 else "fail", "runs": results}
        output_dir = ROOT / ".lab"
        output_dir.mkdir(exist_ok=True)
        (output_dir / "custom-client-verification.json").write_text(
            json.dumps(evidence, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps(evidence, indent=2))
        return code
    except KeyboardInterrupt:
        print(json.dumps({"verification": "interrupted"}))
        return 130
    except (RuntimeError, ValueError, OSError, LabError, subprocess.TimeoutExpired):
        # Credentials and subprocess stderr are deliberately excluded, including preflight failures.
        print(json.dumps({"verification": "error", "detail":
                          "Check the local service, imported binding, worker readiness and Node.js 24+."}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
