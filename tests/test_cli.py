import json
import sys
import types
from pathlib import Path

import pytest

from genlayer_agent_lab.api import read_admin_token, validate_loopback_url
from genlayer_agent_lab.cli import build_parser, main
from genlayer_agent_lab.engine import Engine
from genlayer_agent_lab.scenarios import bundled_scenarios


def test_init_is_repeatable_and_does_not_print_secret_without_explicit_flag(tmp_path, capsys):
    assert main(["init", "--data-dir", str(tmp_path)]) == 0
    result = json.loads(capsys.readouterr().out)
    token = read_admin_token(tmp_path)
    assert "admin_token" not in result
    assert token not in json.dumps(result)
    assert main(["--data-dir", str(tmp_path), "init", "--show-token"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["admin_token"] == token
    assert read_admin_token(tmp_path) == token


@pytest.mark.parametrize(("agent", "code", "verdict"), [
    ("safe", 0, "pass"), ("unsafe", 1, "fail"), ("refuse", 1, "fail"),
])
def test_reference_run_exit_codes_and_persistent_exports(tmp_path, capsys, agent, code, verdict):
    scenario_id = next(iter(bundled_scenarios()))
    assert main(["--data-dir", str(tmp_path), "run", scenario_id,
                 "--backend", "fixture", "--agent", agent]) == code
    report = json.loads(capsys.readouterr().out)
    assert report["verdict"] == verdict
    assert "agent_token" not in report
    assert report["manifest"]["runtime"]["contract_executed"] is False
    run_id = report["run_id"]
    assert main(["status", run_id, "--data-dir", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "completed"
    output = tmp_path / "report.html"
    assert main(["report", run_id, "--data-dir", str(tmp_path), "--format", "html",
                 "--output", str(output)]) == 0
    capsys.readouterr()
    assert "GenLayer Agent Lab" in output.read_text(encoding="utf-8")
    assert "scripted" in output.read_text(encoding="utf-8")


def test_suite_runs_all_three_packs(tmp_path, capsys):
    assert main(["suite", "--data-dir", str(tmp_path), "--backend", "fixture"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["total"] == 18
    assert report["passed"] == 18
    assert report["failed"] == report["inconclusive"] == 0


def test_external_runs_require_persistent_service(tmp_path, capsys):
    scenario_id = next(iter(bundled_scenarios()))
    assert main(["run", scenario_id, "--data-dir", str(tmp_path),
                 "--agent", "external", "--backend", "fixture"]) == 2
    assert "persistent service" in capsys.readouterr().err


def test_unknown_scenario_and_invalid_timeout_return_infrastructure_code(tmp_path, capsys):
    assert main(["run", "unknown", "--data-dir", str(tmp_path), "--backend", "fixture"]) == 2
    assert "not found" in capsys.readouterr().err
    scenario_id = next(iter(bundled_scenarios()))
    assert main(["run", scenario_id, "--data-dir", str(tmp_path),
                 "--backend", "fixture", "--timeout", "nan"]) == 2
    assert "Timeout" in capsys.readouterr().err


def test_doctor_returns_nonzero_for_broken_runtime(tmp_path, monkeypatch, capsys):
    from genlayer_agent_lab import runtime
    monkeypatch.setattr(runtime, "doctor", lambda: {"ready": False, "status": "error"})
    assert main(["doctor", "--data-dir", str(tmp_path)]) == 2
    assert json.loads(capsys.readouterr().out)["runtime"]["ready"] is False


def test_serve_rejects_public_binding_without_starting(tmp_path, capsys):
    assert main(["serve", "--data-dir", str(tmp_path), "--host", "0.0.0.0"]) == 2
    assert "loopback" in capsys.readouterr().err


@pytest.mark.parametrize("url", ["https://remote.example", "http://127.0.0.1@evil.example",
                                 "http://127.0.0.1/secret", "ftp://127.0.0.1",
                                 "http://localhost:99999", "http://127.0.0.1?token=x"])
def test_remote_client_refuses_unsafe_base_urls(url):
    with pytest.raises(ValueError):
        validate_loopback_url(url)


def test_loopback_urls_and_argument_positions_are_supported(tmp_path):
    assert validate_loopback_url("http://[::1]:8765/") == "http://[::1]:8765"
    assert validate_loopback_url("http://localhost:8765") == "http://localhost:8765"
    assert build_parser().parse_args(["--data-dir", str(tmp_path), "scenarios"]).data_dir == tmp_path
    assert build_parser().parse_args(["scenarios", "--data-dir", str(tmp_path)]).data_dir == tmp_path


def test_binding_selects_container_only_when_backend_is_omitted(tmp_path, capsys):
    calls = []

    class FakeEngine:
        def __init__(self, data_dir):
            pass

        def create_run(self, scenario_id, agent, backend, binding_id=None):
            calls.append((scenario_id, agent, backend, binding_id))
            return {"run_id": "custom-run", "status": "queued"}

        def get_run(self, run_id):
            return {"status": "completed"}

        def report(self, run_id):
            return {"run_id": run_id, "status": "completed", "verdict": "pass",
                    "binding_id": "delivery-policy", "grades": {
                        key: {"status": "pass", "detail": "Expected result"}
                        for key in ("decision", "behavior", "outcome", "completion")
                    }, "manifest": {"backend": "container-glsim", "binding_id": "delivery-policy"}}

        def close(self):
            pass

    assert main(["run", "escrow-normal", "--binding", "delivery-policy", "--data-dir", str(tmp_path)],
                engine_factory=FakeEngine) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["binding_id"] == "delivery-policy"
    assert result["backend"] == "container-glsim"
    assert calls == [("escrow-normal", "safe", "container-glsim", "delivery-policy")]
    assert main(["run", "escrow-normal", "--binding", "delivery-policy", "--backend", "glsim",
                 "--data-dir", str(tmp_path)], engine_factory=FakeEngine) == 2
    assert "container-glsim" in capsys.readouterr().err
    assert main(["suite", "--binding", "delivery-policy", "--backend", "fixture",
                 "--data-dir", str(tmp_path)], engine_factory=FakeEngine) == 2
    capsys.readouterr()
    assert main(["run", "escrow-normal", "--backend", "container-glsim", "--data-dir", str(tmp_path)],
                engine_factory=FakeEngine) == 2
    assert len(calls) == 1


def test_default_run_preserves_engine_create_signature(tmp_path, capsys):
    calls = []

    class LegacyEngine:
        def __init__(self, data_dir):
            pass

        def create_run(self, scenario_id, agent, backend):
            calls.append((scenario_id, agent, backend))
            return {"run_id": "old-signature", "status": "queued"}

        def get_run(self, run_id):
            return {"status": "inconclusive"}

        def report(self, run_id):
            return {"run_id": run_id, "status": "inconclusive", "verdict": "inconclusive"}

        def close(self):
            pass

    assert main(["run", "escrow-normal", "--data-dir", str(tmp_path)], engine_factory=LegacyEngine) == 2
    capsys.readouterr()
    assert calls == [("escrow-normal", "safe", "glsim")]


def test_worker_commands_use_separate_local_runtime_and_readiness_exit_codes(tmp_path, monkeypatch, capsys):
    calls = []
    module = types.ModuleType("genlayer_agent_lab.runtime.container")

    def doctor():
        calls.append("doctor")
        return {"ready": False, "status": "error", "docker_available": False, "image_ready": False}

    def build_worker(**options):
        calls.append("build")
        options["progress"]("prepare_sdk")
        options["progress"]("build_image")
        options["progress"]("readiness_probe")
        assert options["log_dir"] == tmp_path / "build-logs"
        return {"ready": True, "status": "ready", "image_id": "sha256:owned-image"}

    module.doctor = doctor
    module.build_worker = build_worker
    monkeypatch.setitem(sys.modules, module.__name__, module)
    assert main(["worker", "doctor", "--data-dir", str(tmp_path)]) == 2
    assert json.loads(capsys.readouterr().out)["docker_available"] is False
    assert main(["worker", "build", "--data-dir", str(tmp_path)]) == 0
    output = capsys.readouterr()
    assert json.loads(output.out)["image_id"] == "sha256:owned-image"
    assert "[1/3]" in output.err and "[2/3]" in output.err and "[3/3]" in output.err
    assert not (tmp_path / "lab.sqlite3").exists()
    assert main(["worker", "build", "--url", "http://127.0.0.1:8765"]) == 2
    assert "this machine" in capsys.readouterr().err
    assert calls == ["doctor", "build"]


def test_worker_build_failure_preserves_json_stdout_and_safe_diagnostic_path(tmp_path, monkeypatch, capsys):
    from genlayer_agent_lab.runtime import container
    from genlayer_agent_lab.runtime.build_diagnostics import build_failure

    def failed_build(**options):
        options["progress"]("build_image")
        raise build_failure("build_image", 7.5, container.IMAGE_TAG, docker_exit=1,
                            stderr=b"Could not resolve host secret:password@example.com",
                            log_dir=options["log_dir"])

    monkeypatch.setattr(container, "build_worker", failed_build)
    assert main(["worker", "build", "--data-dir", str(tmp_path)]) == 2
    output = capsys.readouterr()
    result = json.loads(output.out)
    assert result["ready"] is False and result["category"] == "dns_failure"
    assert Path(result["diagnostic_log"]).is_file()
    assert "[2/3]" in output.err and "Diagnostics:" in output.err
    assert "password" not in output.out + output.err


def test_local_binding_import_list_and_bound_suite_use_persistent_snapshot(tmp_path, capsys):
    example = Path(__file__).parents[1] / "examples/contracts/delivery-binding.yaml"
    assert main(["import-binding", str(example), "--data-dir", str(tmp_path)]) == 0
    binding = json.loads(capsys.readouterr().out)
    assert binding["id"] == "delivery-assessment"
    assert "source" not in binding
    assert main(["bindings", "--data-dir", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out) == [binding]
    calls = []

    def fake_evaluator(snapshot, context, **options):
        calls.append(snapshot["binding_sha256"])
        return {"verdict": context["fixture_verdict"], "provenance": {
            "backend": "container-glsim", "contract_executed": False, "test_double": True,
            "binding_sha256": snapshot["binding_sha256"], "source_sha256": snapshot["source_sha256"],
        }}

    def factory(data_dir):
        return Engine(data_dir, binding_evaluator=fake_evaluator)

    assert main(["suite", "--binding", binding["id"], "--data-dir", str(tmp_path)],
                engine_factory=factory) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["total"] == result["passed"] == len(calls) == 18
    assert set(calls) == {binding["binding_sha256"]}
    assert all(report["binding_id"] == binding["id"] for report in result["runs"])
    assert all(report["backend"] == "container-glsim" for report in result["runs"])


def test_studio_management_uses_only_owned_data_directory_and_preserves_build_success(tmp_path, monkeypatch, capsys):
    from genlayer_agent_lab.runtime import studio_stack

    calls = []

    def build(data_dir, *, port, progress):
        calls.append(("build", data_dir, port))
        progress("Building pinned Studio")
        return {"image_id": "sha256:" + "a" * 64, "ready": False}

    monkeypatch.setattr(studio_stack, "build", build)
    monkeypatch.setattr(studio_stack, "up", lambda data_dir: {"ready": True})
    monkeypatch.setattr(studio_stack, "down", lambda data_dir: {"stopped": True, "data_preserved": True})
    monkeypatch.setattr(studio_stack, "status", lambda data_dir: {"ready": False, "installed": True})
    assert main(["studio", "build", "--port", "8776", "--data-dir", str(tmp_path)]) == 0
    output = capsys.readouterr()
    assert json.loads(output.out)["ready"] is False
    assert "Building pinned Studio" in output.err
    assert calls == [("build", tmp_path, 8776)]
    assert main(["studio", "up", "--data-dir", str(tmp_path)]) == 0
    capsys.readouterr()
    assert main(["studio", "down", "--data-dir", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out)["data_preserved"] is True
    assert main(["studio", "status", "--data-dir", str(tmp_path)]) == 2
    capsys.readouterr()
    assert main(["studio", "build", "--url", "http://127.0.0.1:8765"]) == 2
    assert "this installation" in capsys.readouterr().err
    assert not (tmp_path / "lab.sqlite3").exists()


def test_studio_verify_checks_owned_stack_before_submitting_and_uses_independent_expectation(tmp_path, monkeypatch, capsys):
    from genlayer_agent_lab import studio_conformance
    from genlayer_agent_lab.runtime import studio_stack

    stack = {"ready": False, "network_internal": False, "endpoint": "http://127.0.0.1:8766"}
    monkeypatch.setattr(studio_stack, "status", lambda data_dir: stack)
    calls = []

    def verify(endpoint, snapshot, context, **options):
        calls.append((endpoint, snapshot, context, options))
        return {"verification": "pass", "backend": "studio", "verdict": "deny", "observations": []}

    monkeypatch.setattr(studio_conformance, "run_studio_conformance", verify)
    assert main(["studio", "verify", "--data-dir", str(tmp_path)]) == 2
    assert json.loads(capsys.readouterr().out)["error_code"] == "owned_studio_not_ready"
    assert not calls
    stack.update(ready=True, network_internal=True)
    sample = Path(__file__).parents[1] / "examples/contracts/delivery-binding.yaml"
    output = tmp_path / "studio-evidence.json"
    assert main(["studio", "verify", "--binding", str(sample), "--scenario", "escrow-revised",
                 "--appeal", "--output", str(output), "--data-dir", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out)["verdict"] == "deny"
    assert json.loads(output.read_text())["verification"] == "pass"
    endpoint, snapshot, context, options = calls[0]
    assert endpoint == stack["endpoint"] and snapshot["definition"]["id"] == "delivery-assessment"
    assert options["stack_pins"] is stack
    assert options["expected_verdict"] == "deny" and options["appeal"] is True
    assert options["sim_config"]["validators"]


@pytest.mark.parametrize("custom", [False, True])
def test_cli_explicit_studio_backend_keeps_optional_binding(tmp_path, capsys, custom):
    calls = []

    def evaluate(data_dir, snapshot, context, **options):
        calls.append(snapshot)
        return {"verdict": "approve", "provenance": {"backend": "studio", "test_double": True}}

    if custom:
        example = Path(__file__).parents[1] / "examples/contracts/delivery-binding.yaml"
        assert main(["import-binding", str(example), "--data-dir", str(tmp_path)]) == 0
        capsys.readouterr()
    command = ["run", "escrow-normal", "--backend", "studio", "--data-dir", str(tmp_path)]
    if custom:
        command.extend(["--binding", "delivery-assessment"])
    assert main(command, engine_factory=lambda data: Engine(data, studio_evaluator=evaluate)) == 0
    assert json.loads(capsys.readouterr().out)["backend"] == "studio"
    assert (calls[0] is not None) is custom
