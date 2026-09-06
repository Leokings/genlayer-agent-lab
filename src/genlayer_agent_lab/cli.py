"""Command-line installation, execution and report management."""

from __future__ import annotations

import argparse
import ipaddress
import json
import math
import os
import platform
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from . import __version__
from .api import (
    DEFAULT_PORT,
    create_app,
    default_data_dir,
    initialize_data_dir,
    read_admin_token,
    validate_loopback_url,
)
from .reports import export_report

TERMINAL = {"completed", "cancelled", "interrupted", "inconclusive"}


def _common(parser: argparse.ArgumentParser, *, child: bool = False) -> None:
    parser.add_argument("--data-dir", type=Path,
                        default=argparse.SUPPRESS if child else default_data_dir(),
                        help="Installation data directory (or LAB_DATA_DIR)")
    parser.add_argument("--url", default=argparse.SUPPRESS if child else os.getenv("LAB_URL"),
                        help="Use a running loopback service instead of an offline engine")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gl-agent-lab",
        description="Self-hosted tests for agents consuming GenLayer decisions.",
        epilog="Exit status: 0 pass/success, 1 evaluation failure, 2 infrastructure/input error.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    _common(parser)
    sub = parser.add_subparsers(dest="command", required=True)

    def command(name: str, help: str) -> argparse.ArgumentParser:
        child = sub.add_parser(name, help=help)
        _common(child, child=True)
        return child

    init = command("init", "Initialize local state without replacing configuration or credentials")
    init.add_argument("--show-token", action="store_true", help="Explicitly print the admin secret")
    doctor = command("doctor", "Inspect the runtime and local prerequisites")
    doctor.add_argument("--timeout", type=float, default=900,
                        help="First-time preparation deadline in seconds (default 900, maximum 1800)")
    serve = command("serve", "Run the persistent local service in the foreground")
    serve.add_argument("--host", default="127.0.0.1", help="Loopback bind address only")
    serve.add_argument("--port", type=int, default=DEFAULT_PORT)
    kit = command("kit", "Export bundled setup instructions and agent examples")
    kit.add_argument("--output", type=Path, required=True, help="New directory for the installation kit")
    backup = command("backup", "Back up a stopped Lab installation's database")
    backup.add_argument("--output", type=Path, required=True, help="New backup archive path")
    restore = command("restore", "Restore a verified Lab backup into a new data directory")
    restore.add_argument("archive", type=Path)
    service = command("service", "Manage this user's automatic startup service")
    operations = service.add_subparsers(dest="service_operation", required=True)
    for name in ("install", "status", "start", "stop", "uninstall"):
        operation = operations.add_parser(name)
        _common(operation, child=True)
        if name == "install":
            operation.add_argument("--port", type=int, default=DEFAULT_PORT)
            operation.add_argument("--start", action="store_true", help="Start after installation")
    command("scenarios", "List installed scenario summaries")
    command("bindings", "List imported custom contract bindings without source or fixtures")
    worker = command("worker", "Inspect or explicitly build the local isolated contract worker")
    worker.add_argument("operation", choices=["doctor", "build"])
    studio = command("studio", "Manage the owned local Studio stack and verify observed consensus")
    studio_operations = studio.add_subparsers(dest="studio_operation", required=True)
    for name, description in (("build", "Build the pinned Studio image"),
                              ("up", "Start this installation's Studio services"),
                              ("down", "Stop this installation's services, preserving data"),
                              ("status", "Inspect the owned stack's readiness"),
                              ("verify", "Deploy and execute a contract using real Studio checkpoints")):
        operation = studio_operations.add_parser(name, help=description)
        _common(operation, child=True)
        if name == "build":
            operation.add_argument("--port", type=int, default=8766,
                                   help="Loopback RPC port fixed for this installation")
        elif name == "verify":
            operation.add_argument("--binding", type=Path, help="Local binding YAML; default bundled contract")
            operation.add_argument("--scenario", default="escrow-normal",
                                   help="Built-in scenario supplying evidence and an independent expected decision")
            operation.add_argument("--appeal", action="store_true", help="Require a completed observed appeal")
            operation.add_argument("--timeout", type=float, default=180, help="Total workflow deadline")
            operation.add_argument("--expected-verdict", choices=["approve", "deny"],
                                   help="Override the selected scenario's expected decision")
            operation.add_argument("--output", type=Path, help="Write sanitized conformance evidence")
    run = command("run", "Run one scenario; offline runs wait for their result")
    run.add_argument("scenario_id")
    run.add_argument("--agent", choices=["safe", "unsafe", "refuse", "external"], default="safe")
    run.add_argument("--backend", choices=["glsim", "fixture", "container-glsim", "studio"], default=None,
                     help="Default: glsim, or container-glsim with --binding; fixture executes no contract")
    run.add_argument("--binding", help="Imported custom contract ID; requires container-glsim or studio")
    run.add_argument("--wait", action="store_true", help="Wait for a remote run to complete")
    run.add_argument("--timeout", type=float, default=180, help="Wait limit in seconds")
    run.add_argument("--show-agent-token", action="store_true",
                     help="Explicitly print the new external run's agent secret")
    suite = command("suite", "Run all scenarios with a scripted reference agent")
    suite.add_argument("--agent", choices=["safe", "unsafe", "refuse"], default="safe")
    suite.add_argument("--backend", choices=["glsim", "fixture", "container-glsim", "studio"], default=None,
                       help="Default: glsim, or container-glsim with --binding")
    suite.add_argument("--binding", help="Use this imported custom contract for every scenario")
    suite.add_argument("--timeout", type=float, default=900, help="Overall suite wait limit")
    status = command("status", "List runs or inspect one run")
    status.add_argument("run_id", nargs="?")
    report = command("report", "Export a saved report")
    report.add_argument("run_id")
    report.add_argument("--format", choices=["json", "html", "junit"], default="json")
    report.add_argument("--output", type=Path)
    cancel = command("cancel", "Cancel a run on a persistent service")
    cancel.add_argument("run_id")
    imported = command("import-scenario", "Validate and import a local declarative YAML scenario")
    imported.add_argument("path", type=Path)
    binding = command("import-binding", "Snapshot a local declarative contract binding and its source")
    binding.add_argument("path", type=Path)
    return parser


def _json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def _worker_progress(stage: str) -> None:
    messages = {
        "prepare_sdk": "[1/3] Checking Docker and preparing the pinned SDK...",
        "build_image": "[2/3] Building the isolated worker image (up to 10 minutes)...",
        "readiness_probe": "[3/3] Verifying the image and running its contract readiness probe...",
    }
    if stage in messages:
        print(messages[stage], file=sys.stderr, flush=True)


def _studio_command(args) -> int:
    if args.url:
        raise ValueError("Studio commands manage this installation only. Omit --url and LAB_URL.")
    from .runtime import studio_stack

    if args.studio_operation == "build":
        result = studio_stack.build(args.data_dir, port=args.port,
                                    progress=lambda message: print(message, file=sys.stderr, flush=True))
        _json(result)
        return 0 if result.get("image_id") else 2
    if args.studio_operation == "up":
        result = studio_stack.up(args.data_dir)
        _json(result)
        return 0 if result.get("ready") is True else 2
    if args.studio_operation == "down":
        result = studio_stack.down(args.data_dir)
        _json(result)
        return 0 if result.get("stopped") is True else 2
    stack = studio_stack.status(args.data_dir)
    if args.studio_operation == "status":
        _json(stack)
        return 0 if stack.get("ready") is True else 2
    if stack.get("ready") is not True or stack.get("network_internal") is not True:
        _json({"verification": "inconclusive", "backend": "studio", "error_code": "owned_studio_not_ready"})
        return 2
    from .bindings import load_binding, resolve_template
    from .runtime.studio_evaluator import bundled_snapshot
    from .runtime.studio_fixtures import virtual_validators
    from .scenarios import bundled_scenarios
    from .studio_conformance import run_studio_conformance

    scenario = bundled_scenarios()[args.scenario].model_dump()
    snapshot = load_binding(args.binding) if args.binding else bundled_snapshot()
    definition = snapshot["definition"]
    context = {key: scenario[key] for key in
               ("evidence", "resource_id", "policy_version", "amount", "fixture_verdict")}
    config = {"validators": virtual_validators(context["fixture_verdict"], definition["llm_pattern"],
                                               resolve_template(definition["llm_response"], context))}
    result = run_studio_conformance(stack["endpoint"], snapshot, context, sim_config=config,
                                    appeal=args.appeal, timeout=args.timeout, stack_pins=stack,
                                    expected_verdict=args.expected_verdict or scenario["expected_decision"])
    if args.output:
        args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    _json(result)
    return {"pass": 0, "fail": 1}.get(result.get("verification"), 2)


def _safe_run(value: dict[str, Any], *, show_token: bool = False) -> dict[str, Any]:
    return {key: item for key, item in value.items() if show_token or key != "agent_token"}


def _report_exit(report: dict[str, Any]) -> int:
    grades = report.get("grades", {})
    statuses = [grades.get(name, {}).get("status", "inconclusive")
                for name in ("decision", "behavior", "outcome", "completion")]
    if report.get("status") != "completed" or "inconclusive" in statuses:
        return 2
    if report.get("verdict") == "fail" or "fail" in statuses:
        return 1
    return 0 if report.get("verdict") == "pass" and all(s == "pass" for s in statuses) else 2


def _summary(report: dict[str, Any]) -> dict[str, Any]:
    result = {key: report.get(key) for key in
            ("run_id", "scenario", "agent", "status", "verdict", "grades", "findings")}
    manifest = report.get("manifest", {})
    runtime = manifest.get("runtime", {})
    result["manifest"] = {"backend": manifest.get("backend"),
                          "lifecycle": manifest.get("lifecycle"),
                          "runtime": {key: runtime[key] for key in
                                      ("backend", "contract_executed", "execution_success", "mocked_io")
                                      if key in runtime}}
    binding = manifest.get("binding") or {}
    binding_id = (report.get("binding_id") or manifest.get("binding_id") or binding.get("id")
                  or binding.get("definition", {}).get("id"))
    result["backend"] = report.get("backend") or manifest.get("backend")
    if binding_id:
        result["binding_id"] = binding_id
        result["manifest"]["binding_id"] = binding_id
        result["manifest"]["binding_sha256"] = binding.get("binding_sha256") or runtime.get("binding_sha256")
        result["manifest"]["source_sha256"] = binding.get("source_sha256") or runtime.get("source_sha256")
    return result


class _Remote:
    def __init__(self, url: str, token: str):
        self.client = httpx.Client(base_url=validate_loopback_url(url), timeout=20,
                                   headers={"Authorization": f"Bearer {token}"}, trust_env=False,
                                   follow_redirects=False)

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self.client.request(method, path, **kwargs)
        if response.status_code >= 300:
            try:
                detail = response.json().get("detail", "Request failed")
            except (ValueError, AttributeError):
                detail = "Request failed"
            raise RuntimeError(f"Service returned HTTP {response.status_code}: {detail}")
        return response.json()

    def list_scenarios(self) -> list[dict]:
        return self._request("GET", "/v1/scenarios")

    def list_bindings(self) -> list[dict]:
        return self._request("GET", "/v1/bindings")

    def list_runs(self) -> list[dict]:
        return self._request("GET", "/v1/runs")

    def create_run(self, scenario_id: str, agent: str, backend: str,
                   binding_id: str | None = None) -> dict:
        payload = {"scenario_id": scenario_id, "agent": agent, "backend": backend}
        if binding_id is not None:
            payload["binding_id"] = binding_id
        return self._request("POST", "/v1/runs", json=payload)

    def get_run(self, run_id: str) -> dict:
        return self._request("GET", f"/v1/runs/{quote(run_id, safe='')}")

    def report(self, run_id: str) -> dict:
        return self._request("GET", f"/v1/runs/{quote(run_id, safe='')}/report")

    def cancel_run(self, run_id: str) -> dict:
        return self._request("POST", f"/v1/runs/{quote(run_id, safe='')}/cancel")

    def close(self) -> None:
        self.client.close()


def _wait_for_run(engine: Any, run_id: str, timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    try:
        while True:
            result = engine.get_run(run_id)
            if result.get("status") in TERMINAL:
                return engine.report(run_id)
            if time.monotonic() >= deadline:
                engine.cancel_run(run_id)
                raise RuntimeError("Wait deadline exceeded; the run was cancelled and its trace kept.")
            time.sleep(0.1)
    except KeyboardInterrupt:
        engine.cancel_run(run_id)
        raise


def _execute(args: argparse.Namespace, engine: Any, *, remote: bool) -> int:
    if args.command == "scenarios":
        _json(engine.list_scenarios())
    elif args.command == "bindings":
        _json(engine.list_bindings())
    elif args.command == "status":
        _json(engine.get_run(args.run_id) if args.run_id else engine.list_runs())
    elif args.command == "cancel":
        _json(engine.cancel_run(args.run_id))
    elif args.command == "import-scenario":
        if remote:
            raise ValueError("Scenario import is local-only. Stop the service and omit --url.")
        _json(engine.import_scenario(args.path.expanduser().resolve()))
    elif args.command == "import-binding":
        if remote:
            raise ValueError("Contract binding import is local-only. Stop the service and omit --url.")
        _json(engine.import_binding(args.path.expanduser().resolve()))
    elif args.command == "report":
        report = engine.report(args.run_id)
        content = export_report(report, args.format)
        if args.output:
            args.output.write_text(content, encoding="utf-8")
            _json({"output": str(args.output.resolve()), "format": args.format})
        else:
            print(content, end="" if content.endswith("\n") else "\n")
    elif args.command == "run":
        if not math.isfinite(args.timeout) or args.timeout <= 0 or args.timeout > 86_400:
            raise ValueError("Timeout must be greater than zero and at most 86400 seconds.")
        if args.agent == "external" and not remote:
            raise ValueError("External agents require the persistent service: use serve and --url.")
        options = {"agent": args.agent, "backend": args.backend}
        if args.binding is not None:
            options["binding_id"] = args.binding
        created = engine.create_run(args.scenario_id, **options)
        if remote and not args.wait:
            _json(_safe_run(created, show_token=args.show_agent_token))
            return 0
        if args.show_agent_token:
            _json(_safe_run(created, show_token=True))
        report = _wait_for_run(engine, created["run_id"], args.timeout)
        _json(_summary(report))
        return _report_exit(report)
    elif args.command == "suite":
        if not math.isfinite(args.timeout) or args.timeout <= 0 or args.timeout > 86_400:
            raise ValueError("Timeout must be greater than zero and at most 86400 seconds.")
        deadline = time.monotonic() + args.timeout
        reports = []
        codes = []
        for scenario in engine.list_scenarios():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError("Suite wait deadline exceeded; completed reports are retained.")
            scenario_id = scenario.get("id") or scenario.get("scenario_id")
            if not scenario_id:
                raise RuntimeError("Invalid scenario summary: identifier missing.")
            options = {"agent": args.agent, "backend": args.backend}
            if args.binding is not None:
                options["binding_id"] = args.binding
            created = engine.create_run(scenario_id, **options)
            report = _wait_for_run(engine, created["run_id"], remaining)
            reports.append(_summary(report))
            codes.append(_report_exit(report))
        if not reports:
            raise RuntimeError("No scenarios are installed.")
        _json({"runs": reports, "total": len(reports), "passed": codes.count(0),
               "failed": codes.count(1), "inconclusive": codes.count(2)})
        return max(codes)
    return 0


def main(argv: list[str] | None = None, *, engine_factory: Any = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        args.data_dir = args.data_dir.expanduser()
        if args.command not in {"backup", "restore"}:
            args.data_dir = args.data_dir.resolve()
        if args.command in {"kit", "backup", "restore", "service"}:
            if args.url:
                raise ValueError("This command operates locally. Omit --url and LAB_URL.")
            if args.command == "kit":
                from .kit import export_kit
                result = export_kit(args.output)
            elif args.command == "backup":
                from .recovery import backup
                result = backup(args.data_dir, args.output)
            elif args.command == "restore":
                from .recovery import restore
                result = restore(args.archive, args.data_dir)
            else:
                from . import service
                if args.service_operation == "install":
                    result = service.install(args.data_dir, port=args.port, start_now=args.start)
                else:
                    result = getattr(service, args.service_operation)(args.data_dir)
            _json(result)
            if args.command == "service":
                operation = args.service_operation
                key = {"install": "installed", "start": "ready", "status": "installed",
                       "stop": "stopped", "uninstall": "uninstalled"}[operation]
                if operation == "install" and args.start:
                    key = "ready"
                return 0 if result.get(key) is True else 2
            return 0
        if args.command in {"run", "suite"}:
            if args.binding is not None and not args.binding.strip():
                raise ValueError("Binding ID must not be empty.")
            args.backend = args.backend or ("container-glsim" if args.binding else "glsim")
            if ((args.binding is not None and args.backend not in {"container-glsim", "studio"})
                    or (args.backend == "container-glsim" and not args.binding)):
                raise ValueError("--binding requires container-glsim or studio; container-glsim requires --binding.")
        if args.command == "studio":
            return _studio_command(args)
        if args.command == "worker":
            if args.url:
                raise ValueError("Worker commands operate on this machine only. Omit --url and LAB_URL.")
            from .runtime.build_diagnostics import BuildFailure
            from .runtime.container import build_worker, doctor
            # Building has its own runtime-enforced timeout; do not apply the run wait deadline.
            try:
                result = doctor() if args.operation == "doctor" else build_worker(
                    progress=_worker_progress, log_dir=args.data_dir / "build-logs"
                )
            except BuildFailure as exc:
                result = exc.as_result()
                print("Worker build failed. " + (f"Diagnostics: {exc.log_path}" if exc.log_path
                      else "Structured diagnostics could not be saved."), file=sys.stderr)
            _json(result)
            return 0 if result.get("ready") is True else 2
        if args.command == "init":
            data_dir = initialize_data_dir(args.data_dir)
            result = {"data_dir": str(data_dir), "version": __version__,
                      "token_file": str(data_dir / "admin.token"), "initialized": True}
            if args.show_token:
                result["admin_token"] = read_admin_token(data_dir)
            _json(result)
            return 0
        if args.command == "doctor":
            from .runtime import doctor
            if not math.isfinite(args.timeout) or not 0 < args.timeout <= 1800:
                raise ValueError("Doctor timeout must be positive and at most 1800 seconds.")
            print("Preparing and checking the pinned GenLayer runtime. First installation downloads "
                  "about 217 MB; this can take several minutes.", file=sys.stderr, flush=True)
            runtime = doctor(timeout=args.timeout)
            _json({"version": __version__, "python": platform.python_version(),
                   "platform": platform.platform(), "data_dir": str(args.data_dir),
                   "token_configured": (args.data_dir / "admin.token").is_file(),
                   "runtime": runtime})
            return 2 if runtime.get("ready") is False or runtime.get("ok") is False else 0
        if args.command == "serve":
            try:
                local = ipaddress.ip_address(args.host).is_loopback
            except ValueError:
                local = args.host.lower() == "localhost"
            if not local or not 1 <= args.port <= 65_535:
                raise ValueError("Serve requires a loopback host and port 1–65535.")
            import uvicorn
            uvicorn.run(create_app(args.data_dir), host=args.host, port=args.port,
                        access_log=False, server_header=False)
            return 0
        remote = bool(args.url)
        if remote:
            token = os.environ.get("LAB_TOKEN") or read_admin_token(args.data_dir)
            engine = _Remote(args.url, token)
        else:
            initialize_data_dir(args.data_dir)
            if engine_factory is None:
                from .engine import Engine
                engine_factory = Engine
            engine = engine_factory(args.data_dir)
        try:
            return _execute(args, engine, remote=remote)
        finally:
            engine.close()
    except KeyboardInterrupt:
        print("Interrupted; any active foreground run was cancelled.", file=sys.stderr)
        return 130
    except KeyError:
        print("Error: run, scenario or contract binding not found.", file=sys.stderr)
        return 2
    except httpx.HTTPError:
        print("Error: could not reach the local service. Check its URL and health.", file=sys.stderr)
        return 2
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
