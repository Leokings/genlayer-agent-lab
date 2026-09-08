"""Install and exercise an actual wheel in a fresh environment outside the checkout.

Only the Python standard library is imported by the installation driver. The
copied probe runs with the newly installed interpreter in isolated mode.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.metadata
import importlib.resources
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path


class VerificationError(RuntimeError):
    pass


def _cli_failure(stage: str, raw: bytes) -> str:
    """Return only recognized runtime failure categories, never CLI payload text."""
    if stage != "CLI doctor":
        return ""
    try:
        result = json.loads(raw)
        error = result.get("runtime", {}).get("error", "")
        if not isinstance(error, str):
            return ""
    except (ValueError, AttributeError):
        return ""
    categories = []
    for marker, label in (("timed out", "runtime-timeout"), ("checksum", "artifact-checksum"),
                          ("integrity", "artifact-integrity"), ("invalid output", "worker-output")):
        if marker in error.lower():
            categories.append(label)
    categories.extend(re.findall(r"\b[A-Za-z]{1,40}Error\b", error))
    categories.extend("OS-error-" + value for value in re.findall(r"\[(?:WinError|Errno) (\d{1,5})\]", error))
    return ": " + ", ".join(dict.fromkeys(categories)) if categories else ""


def clean_environment(root: Path, python: Path) -> dict[str, str]:
    """No source path, account credentials, proxies, or package-manager configuration."""
    root = root.resolve()
    for name in ("home", "tmp", "local", "roaming"):
        (root / name).mkdir(parents=True, exist_ok=True)
    env = {key: os.environ[key] for key in ("SYSTEMROOT", "SystemRoot", "WINDIR") if key in os.environ}
    system_path = (str(Path(env.get("SystemRoot", env.get("SYSTEMROOT", "C:/Windows"))) / "System32")
                   if os.name == "nt" else os.defpath)
    env.update({
        "PATH": str(python.parent) + os.pathsep + system_path,
        "HOME": str(root / "home"), "USERPROFILE": str(root / "home"),
        "LOCALAPPDATA": str(root / "local"), "APPDATA": str(root / "roaming"),
        "TEMP": str(root / "tmp"), "TMP": str(root / "tmp"), "TMPDIR": str(root / "tmp"),
        "PYTHONUTF8": "1", "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1",
        "PIP_CONFIG_FILE": os.devnull,
    })
    return env


def run(command: list[str], *, cwd: Path, env: dict, timeout=600, stage: str):
    print(f"Install verification: {stage}", file=sys.stderr, flush=True)
    try:
        result = subprocess.run(command, cwd=cwd, env=env, capture_output=True,
                                timeout=timeout, check=False,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    except OSError as exc:
        raise VerificationError(f"{stage}: launch failed (OS error {exc.errno})") from None
    except subprocess.TimeoutExpired:
        raise VerificationError(f"{stage}: process exceeded its deadline") from None
    if result.returncode:
        detail = _cli_failure(stage, result.stdout)
        if stage == "exercise installed package":
            try:
                report = json.loads(result.stdout)
                if report.get("verification") == "fail" and type(report.get("error")) is str:
                    detail = ": " + report["error"][:200]
            except (ValueError, AttributeError):
                pass
        raise VerificationError(f"{stage}: exit {result.returncode}{detail}")
    return result.stdout.decode("utf-8")


def choose_wheel(value: str) -> Path:
    supplied = Path(value)
    if supplied.is_file():
        selected = supplied.resolve()
    else:
        candidates = list(supplied.parent.glob(supplied.name))
        if len(candidates) != 1:
            raise VerificationError("Select exactly one built wheel")
        selected = candidates[0].resolve()
    if selected.suffix != ".whl" or not selected.name.startswith("genlayer_agent_lab-"):
        raise VerificationError("Expected a genlayer_agent_lab wheel")
    return selected


def verify(wheel: Path, *, backend="fixture", require_kit=False) -> dict:
    started = time.monotonic()
    result = {"schema_version": 1, "verification": "fail", "wheel": wheel.name,
              "wheel_sha256": None,
              "host_platform": platform.system(), "host_python": platform.python_version(),
              "source_checkout_used_by_probe": False, "inherited_environment": "system allowlist only"}
    try:
        with tempfile.TemporaryDirectory(prefix="gl-agent-lab-install-") as temporary:
            root = Path(temporary).resolve()
            artifacts = root / "artifacts"
            artifacts.mkdir()
            copied_wheel = artifacts / wheel.name
            # Builds can replace the source artifact while venv/pip are running.
            # Hash and install the same private copy, keeping its valid wheel name.
            shutil.copyfile(wheel, copied_wheel)
            with copied_wheel.open("rb") as stream:
                result["wheel_sha256"] = hashlib.file_digest(stream, "sha256").hexdigest()
            environment = root / "environment"
            work = root / "work"
            work.mkdir()
            python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            env = clean_environment(root, python)
            run([sys.executable, "-I", "-m", "venv", str(environment)], cwd=work, env=env,
                stage="create fresh virtual environment")
            run([str(python), "-I", "-m", "pip", "--isolated", "install", "--no-cache-dir",
                 "--index-url", "https://pypi.org/simple", str(copied_wheel)],
                cwd=work, env=env, stage="install built wheel and its declared dependencies")
            probe = root / "probe.py"
            shutil.copyfile(Path(__file__).resolve(), probe)
            command = [str(python), "-I", "-B", str(probe), "--_probe", "--backend", backend]
            if require_kit:
                command.append("--require-kit")
            raw = run(command, cwd=work, env=env, stage="exercise installed package", timeout=1800)
            details = json.loads(raw)
            if details.get("verification") != "pass":
                raise VerificationError("Installed package probe did not pass")
            result.update(verification="pass", installed=details)
    except (VerificationError, ValueError, OSError) as exc:
        result["error"] = str(exc) if isinstance(exc, VerificationError) else type(exc).__name__
    result["elapsed_seconds"] = round(time.monotonic() - started, 3)
    return result


def _checked_report(report):
    if (report.get("status") != "completed" or report.get("verdict") != "pass"
            or any(report.get("grades", {}).get(name, {}).get("status") != "pass"
                   for name in ("decision", "behavior", "outcome", "completion"))):
        raise VerificationError("Server-side agent report did not pass every grade")
    return {"run_id": report["run_id"], "status": report["status"], "verdict": report["verdict"],
            "backend": report["manifest"]["backend"]}


def _action(task, decision, run_id):
    if (decision.get("status") != "final" or decision.get("execution_result") != "success"
            or decision.get("verdict") != "approve"
            or decision.get("resource_id") != task["resource_id"]
            or decision.get("policy_version") != task["policy_version"]):
        raise VerificationError("Smoke decision was not a final scoped approval")
    return {"operation": task["operation"], "resource_id": task["resource_id"],
            "policy_version": task["policy_version"], "amount": task["amount"],
            "decision_id": decision["decision_id"], "revision": decision["revision"],
            "idempotency_key": run_id + ":action"}


def _onboarding_probe(web, url, token, installed_examples):
    """Check installed authoring routes without creating a project or starting Studio."""
    from genlayer_agent_lab.project_scenarios import validate_project_scenario
    from genlayer_agent_lab.project_verification import examples_root
    from genlayer_agent_lab.project_wire import decode_project_wire

    if (examples_root().resolve() != Path(installed_examples).resolve()
            or not (Path(installed_examples) / "projects/prediction/project.yaml").is_file()):
        raise VerificationError("Guided templates did not resolve from the installed kit")
    endpoint = url + "/v1/onboarding/"
    if web.get(endpoint + "templates").status_code != 401:
        raise VerificationError("Installed onboarding API accepted a missing credential")
    headers = {"Authorization": "Bearer " + token}
    response = web.get(endpoint + "templates", headers=headers)
    if response.status_code != 200:
        raise VerificationError("Installed onboarding template catalog is unavailable")
    templates = response.json().get("templates", [])
    identifiers = {item.get("id") for item in templates}
    if len(templates) != 11 or len(identifiers) != 11 or "prediction-appeal-upheld" not in identifiers:
        raise VerificationError("Installed onboarding template catalog is incomplete")

    def post(name, payload):
        response = web.post(endpoint + name, headers=headers, json=payload)
        if response.status_code != 200:
            raise VerificationError("Installed onboarding authoring request failed")
        return response.json()

    draft = post("draft", {"template_id": "prediction-appeal-upheld", "values": {
        "title": "Installed onboarding review", "final_outcome": "no", "initial_confidence_bps": 7312}})
    spec = decode_project_wire(draft["spec"])
    expected_response = {"outcome": "no", "confidence_bps": 7312}
    if (spec["review"]["status"] != "draft" or not draft.get("rules") or not draft.get("summary")
            or any(spec["fixtures"][phase][0]["response"] != expected_response
                   for phase in ("initial", "after_appeal"))):
        raise VerificationError("Installed onboarding draft changed its controlled model responses")
    preview = post("preview", {"spec": draft["spec"]})
    if preview.get("digest") != draft.get("digest") or preview.get("spec") != draft.get("spec"):
        raise VerificationError("Installed onboarding preview changed the draft")
    changed = {**preview["spec"], "task": preview["spec"]["task"] + " Changed after review."}
    stale = web.post(endpoint + "review", headers=headers, json={
        "spec": changed, "expected_sha256": preview["digest"], "reviewer": "Installed package probe"})
    if stale.status_code != 409:
        raise VerificationError("Installed onboarding review accepted a stale content digest")
    reviewed = post("review", {"spec": preview["spec"], "expected_sha256": preview["digest"],
                               "reviewer": "Installed package probe"})
    approved = decode_project_wire(reviewed["spec"])
    if (reviewed.get("digest") != preview["digest"]
            or {key: value for key, value in approved.items() if key != "review"}
            != {key: value for key, value in spec.items() if key != "review"}
            or approved["review"]["status"] != "approved"
            or approved["review"]["content_sha256"] != preview["digest"]):
        raise VerificationError("Installed onboarding review changed the inspected scenario")
    try:
        validate_project_scenario(approved, require_review=True)
    except (ValueError, TypeError):
        raise VerificationError("Installed onboarding did not return an executable reviewed scenario") from None
    return {"template_count": 11, "template_id": "prediction-appeal-upheld",
            "templates_from_installed_kit": True, "admin_required": True,
            "draft_preview_review": "pass", "stale_review_rejected": True,
            "controlled_responses_preserved": True, "project_runs_created": 0,
            "studio_started": False}


def _ready(admin, run_id):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if admin.get_run(run_id)["status"] == "running":
            return
        time.sleep(0.1)
    raise VerificationError("External fixture run did not become ready")


async def _mcp_agent(url, token, run_id):
    from mcp.client import Client
    from mcp.client.stdio import StdioServerParameters

    env = dict(os.environ)
    env.update(LAB_URL=url, LAB_TOKEN=token, LAB_ROLE="agent", LAB_RUN_ID=run_id, LAB_MODE="scenario")
    params = StdioServerParameters(command=sys.executable,
                                  args=["-I", "-B", "-m", "genlayer_agent_lab.mcp_server"], env=env)
    async with Client(params, read_timeout_seconds=20) as client:
        names = {tool.name for tool in (await client.list_tools()).tools}
        if names != {"observe", "request_decision", "read_decision", "act", "finish"}:
            raise VerificationError("Installed MCP agent tool scope is incorrect")

        async def call(name, args=None):
            response = await client.call_tool(name, args or {})
            if response.is_error or not isinstance(response.structured_content, dict):
                raise VerificationError("Installed MCP tool invocation failed")
            return response.structured_content

        task = (await call("observe"))["task"]
        await call("request_decision", {"idempotency_key": run_id + ":decision"})
        await call("observe")
        decision = await call("read_decision")
        await call("act", _action(task, decision, run_id))
        await call("finish")
        return sorted(names)


def probe(*, backend: str, require_kit: bool) -> dict:
    # These imports must only happen in the copied probe after wheel installation.
    import httpx

    import genlayer_agent_lab
    from genlayer_agent_lab.client import LabClient

    installed = Path(genlayer_agent_lab.__file__).resolve()
    prefix = Path(sys.prefix).resolve()
    work = Path.cwd().resolve()
    if sys.prefix == sys.base_prefix or not installed.is_relative_to(prefix):
        raise VerificationError("Package did not import from the fresh virtual environment")
    if any(Path(p).resolve().is_relative_to(work) for p in sys.path if p):
        raise VerificationError("Probe working directory leaked into Python import search paths")
    if os.getenv("PYTHONPATH") or os.getenv("LAB_TOKEN") or os.getenv("VIRTUAL_ENV"):
        raise VerificationError("Probe inherited source or user application configuration")
    metadata = importlib.metadata.distribution("genlayer-agent-lab")
    if metadata.version != genlayer_agent_lab.__version__:
        raise VerificationError("Package metadata and imported version disagree")
    package = importlib.resources.files("genlayer_agent_lab")
    resource_names = ["assets/index.html", "assets/app.js", "assets/styles.css",
                      "assets/workflows.html", "assets/workflows.js", "assets/onboarding.css",
                      "runtime/contracts/evidence_decision.py", "runtime/Dockerfile.worker",
                      "runtime/worker-requirements.txt", "runtime/studio_relay.py"]
    resources = {}
    for name in resource_names:
        content = package.joinpath(name).read_bytes()
        if not content:
            raise VerificationError("A required wheel resource is empty")
        resources[name] = hashlib.sha256(content).hexdigest()
    console = prefix / ("Scripts/gl-agent-lab.exe" if os.name == "nt" else "bin/gl-agent-lab")
    env = dict(os.environ)
    data = work / "state"

    def cli(*args, timeout=600):
        output = run([str(console), "--data-dir", str(data), *args], cwd=work, env=env,
                     timeout=timeout, stage="CLI " + args[0])
        return json.loads(output)

    installed_version = run([str(console), "--version"], cwd=work, env=env, stage="CLI version").strip()
    if installed_version != "gl-agent-lab " + metadata.version:
        raise VerificationError("Installed console entry point reports another version")
    setup_help = run([str(console), "setup", "--help"], cwd=work, env=env, stage="CLI setup help")
    if any(option not in setup_help for option in ("--check", "--no-open", "--port")):
        raise VerificationError("Installed guided setup command is unavailable")
    initialized = cli("init")
    if initialized.get("initialized") is not True or "admin_token" in initialized:
        raise VerificationError("CLI initialization failed or exposed its admin credential")
    doctor = cli("doctor", timeout=960)
    runtime = doctor.get("runtime", {})
    if runtime.get("ready") is not True or runtime.get("provenance", {}).get("execution_success") is not True:
        raise VerificationError("Installed trusted GLSim doctor did not execute successfully")
    suite = cli("suite", "--backend", backend, "--timeout", "600", timeout=660)
    if (suite.get("total") != 18 or suite.get("passed") != 18
            or suite.get("failed") != 0 or suite.get("inconclusive") != 0):
        raise VerificationError("Installed scenario suite did not pass all 18 cases")
    kit = None
    if require_kit:
        exported = work / "exported-kit"
        cli("kit", "--output", str(exported))
        files = list(exported.rglob("*"))
        required_kit_files = {"SKILL.md", "python_agent.py", "mcp_agent.py", "client.ts", "agent.ts",
                              "INSTALL.md", "SERVICES.md", "RECOVERY.md", "STUDIO.md",
                              "EXTERNAL_ONBOARDING.md", "STUDIO_WORKFLOWS.md", "workflow_agent.py",
                              "workflow-agent.ts", "partial-release.json", "appeal-overturn.json",
                              "service-workflow.yaml", "mcp_workflow_agent.py",
                              "PROJECT_WORKFLOWS.md", "PROJECT_BINDINGS.md", "SCENARIO_AUTHORING.md",
                              "project_agent.py", "project-agent.ts", "mcp_project_agent.py",
                              "project-repair.yaml", "prediction-messages-repair.yaml",
                              "INVESTIGATION.md", "investigation_agent.py", "mcp_investigation_agent.py"}
        nonempty = {p.name for p in files if p.is_file() and p.stat().st_size > 0}
        if not required_kit_files <= nonempty:
            raise VerificationError("Installed integration kit lacks its skill or clients")
        kit = {"exported": True, "file_count": sum(p.is_file() for p in files),
               "required_files": sorted(required_kit_files)}
        draft = work / "project.draft.json"
        authored = cli("project", "template", str(exported / "examples/projects/prediction/project.yaml"),
                       "--output", str(draft))
        checked = cli("project", "validate", str(draft))
        if (not authored.get("valid") or checked.get("review_status") != "draft"
                or checked.get("content_sha256") != authored.get("content_sha256")):
            raise VerificationError("Installed project snapshot/template validation failed")
        kit["project_authoring"] = "pass"
        investigation = work / "investigation.draft.json"
        authored = cli("project", "investigate-template",
                       str(exported / "examples/projects/prediction/project.yaml"),
                       "--mode", "contradictory", "--output", str(investigation))
        checked = cli("project", "validate", str(investigation))
        if (not authored.get("valid") or checked.get("review_status") != "draft"
                or checked.get("content_sha256") != authored.get("content_sha256")):
            raise VerificationError("Installed investigation authoring failed")
        kit["investigation_authoring"] = "pass"
        from genlayer_agent_lab.investigation_verification import CASES, reference_spec

        recipes = {name: reference_spec(name) for name in CASES}
        if len(recipes) != 8 or any(case["review"]["status"] != "approved" for case in recipes.values()):
            raise VerificationError("Installed investigation recipe catalog is incomplete")
        if not (exported / "examples/mcp_workflow_agent.py").is_file():
            raise VerificationError("Installed investigation MCP bridge is unavailable")
        kit["investigation_recipes"] = sorted(recipes)
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    server = subprocess.Popen(
        [sys.executable, "-I", "-B", "-m", "genlayer_agent_lab.cli", "--data-dir", str(data),
         "serve", "--port", str(port)], cwd=work, env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    try:
        token = (data / "admin.token").read_text(encoding="utf-8").strip()
        with LabClient(url, token, timeout=5) as admin:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                try:
                    if len(admin.list_scenarios()) == 18:
                        break
                except Exception:
                    if server.poll() is not None:
                        raise VerificationError("Installed HTTP service exited during startup") from None
                    time.sleep(0.1)
            else:
                raise VerificationError("Installed HTTP service was not ready")
            with httpx.Client(timeout=5, trust_env=False, follow_redirects=False) as web:
                if web.get(url + "/v1/scenarios").status_code != 401:
                    raise VerificationError("Installed HTTP API accepted a missing credential")
                for asset in ("/", "/assets/app.js", "/assets/styles.css",
                              "/assets/workflows.html", "/assets/workflows.js", "/assets/onboarding.css"):
                    response = web.get(url + asset)
                    if response.status_code != 200 or not response.content:
                        raise VerificationError("Installed dashboard resource was not served")
                    if asset == "/" and response.content != package.joinpath("assets/workflows.html").read_bytes():
                        raise VerificationError("Installed root did not serve the guided dashboard")
                if web.get(url + "/v1/workflows").status_code != 401:
                    raise VerificationError("Installed workflow API accepted a missing credential")
                if admin.workflow_list() != []:
                    raise VerificationError("Fresh installed workflow store is not empty")
                onboarding = _onboarding_probe(web, url, token, installed.parent / "_kit/examples")
                if admin.workflow_list() != []:
                    raise VerificationError("Installed onboarding authoring unexpectedly created a project run")
            created = admin.create_run("escrow-normal", agent="external", backend="fixture")
            run_id = created["run_id"]
            _ready(admin, run_id)
            with LabClient(url, created["agent_token"]) as agent:
                task = agent.observe(run_id)["task"]
                agent.request_decision(run_id, run_id + ":decision")
                agent.observe(run_id)
                agent.act(run_id, _action(task, agent.read_decision(run_id), run_id))
                agent.finish(run_id)
            python_result = _checked_report(admin.report(run_id))
            created = admin.create_run("escrow-normal", agent="external", backend="fixture")
            run_id = created["run_id"]
            _ready(admin, run_id)
            names = asyncio.run(asyncio.wait_for(
                _mcp_agent(url, created["agent_token"], run_id), timeout=30))
            mcp_result = {**_checked_report(admin.report(run_id)), "tools": names, "transport": "stdio"}
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=10)
    return {"verification": "pass", "version": metadata.version, "platform": platform.system(),
            "python": platform.python_version(), "imported_from_fresh_venv": True,
            "installed_distributions": dict(sorted(
                (dist.metadata["Name"], dist.version) for dist in importlib.metadata.distributions()
            )),
            "resources": resources, "console_version": installed_version, "setup_help": "pass",
            "doctor": {"ready": True, "backend": "glsim", "runner_hash": runtime["runner_hash"],
                       "bundle_sha256": runtime["bundle_sha256"], "execution_success": True},
            "suite": {"backend": backend, "total": 18, "passed": 18},
            "python_http": python_result, "mcp": mcp_result, "kit": kit, "onboarding": onboarding}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", help="Wheel path or a quoted glob matching exactly one wheel")
    parser.add_argument("--output", type=Path, help="Sanitized JSON verification report")
    parser.add_argument("--backend", choices=["fixture", "glsim"], default="fixture")
    parser.add_argument("--require-kit", action="store_true")
    parser.add_argument("--_probe", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args._probe:
        try:
            result = probe(backend=args.backend, require_kit=args.require_kit)
        except Exception as exc:
            # Do not export third-party exception text, request headers or MCP
            # payloads. VerificationError messages are fixed strings authored here.
            result = {"verification": "fail", "error": str(exc) if isinstance(exc, VerificationError)
                      else type(exc).__name__}
    else:
        if not args.wheel:
            parser.error("--wheel is required")
        result = verify(choose_wheel(args.wheel), backend=args.backend, require_kit=args.require_kit)
    serialized = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized, encoding="utf-8")
    print(serialized, end="")
    return 0 if result["verification"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
