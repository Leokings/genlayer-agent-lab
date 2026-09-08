import json
from pathlib import Path

import pytest
import yaml

from genlayer_agent_lab import project_cli
from genlayer_agent_lab.cli import build_parser, main
from genlayer_agent_lab.project_scenarios import load_project_scenario

ROOT = Path(__file__).parents[1]
MANIFEST = ROOT / "examples/projects/prediction/project.yaml"
EXAMPLE = ROOT / "examples/project-scenarios/prediction-finalize.yaml"


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    monkeypatch.delenv("LAB_URL", raising=False)
    monkeypatch.delenv("LAB_TOKEN", raising=False)


def test_terminal_author_export_validate_and_review_roundtrip(tmp_path, capsys):
    snapshot = tmp_path / "project.json"
    assert main(["project", "snapshot", str(MANIFEST), "--output", str(snapshot)]) == 0
    assert json.loads(capsys.readouterr().out)["project"]["contracts"] == ["oracle", "recorder"]
    draft = tmp_path / "draft.json"
    assert main(["project", "template", str(snapshot), "--mode", "appeal_changed", "--output", str(draft)]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["review_status"] == "draft" and summary["executable"] is False
    assert "project_snapshot" in json.loads(draft.read_text())
    assert main(["project", "validate", str(draft)]) == 0
    checked = json.loads(capsys.readouterr().out)
    assert checked["content_sha256"] == summary["content_sha256"]
    output = tmp_path / "approved.json"
    args = ["project", "approve", str(draft), "--reviewer", "Developer", "--expected-sha256",
            checked["content_sha256"], "--output", str(output)]
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out)["executable"] is True
    assert load_project_scenario(output)["review"]["reviewer"] == "Developer"
    original = output.read_bytes()
    assert main(args) == 2
    assert output.read_bytes() == original
    capsys.readouterr()
    bad = args.copy()
    bad[bad.index("--expected-sha256") + 1] = "0" * 64
    bad[-1] = str(tmp_path / "bad.json")
    assert main(bad) == 2
    assert "digest does not match" in capsys.readouterr().err
    assert not (tmp_path / "bad.json").exists()


def test_schema_prompt_and_variations_are_local_authoring_commands(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(project_cli, "LabClient", lambda *args: pytest.fail("No network client during authoring"))
    schema = tmp_path / "schema.json"
    prompt = tmp_path / "prompt.txt"
    assert main(["project", "schema", "--output", str(schema)]) == 0
    capsys.readouterr()
    assert json.loads(schema.read_text())["properties"]["profile"]["const"] == "project"
    assert main(["project", "author-prompt", str(MANIFEST), "--output", str(prompt)]) == 0
    capsys.readouterr()
    assert "Do not approve your own draft" in prompt.read_text()
    changes = tmp_path / "changes.yaml"
    changes.write_text(yaml.safe_dump([{"id": "variant-one", "title": "Different evidence",
                                      "replacements": {"context.evidence": "New controlled evidence"}}]))
    directory = tmp_path / "variants"
    assert main(["project", "variants", str(EXAMPLE), str(changes), "--output", str(directory)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["review_required"] is True
    variant = load_project_scenario(directory / "variant-one.json", require_review=False)
    assert variant["review"]["status"] == "draft"
    assert variant["context"]["evidence"] == "New controlled evidence"


def test_create_rejects_unreviewed_draft_and_hides_scoped_token_by_default(tmp_path, monkeypatch, capsys):
    calls = []

    class Client:
        def __init__(self, url, token):
            assert url == "http://127.0.0.1:8785" and token == "admin-test"

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def workflow_create(self, spec):
            calls.append(spec)
            return {"run_id": "project-test", "profile": "project", "agent_token": "run-secret"}

    monkeypatch.setattr(project_cli, "LabClient", Client)
    monkeypatch.setenv("LAB_TOKEN", "admin-test")
    args = ["project", "create", str(EXAMPLE), "--data-dir", str(tmp_path)]
    assert main(args) == 2
    assert "running Lab service" in capsys.readouterr().err
    args.extend(["--url", "http://127.0.0.1:8785"])
    assert main(args) == 2
    assert "developer review" in capsys.readouterr().err and not calls
    case = load_project_scenario(EXAMPLE, require_review=False)
    reviewed = project_cli.approve_project_scenario(case, reviewer="Developer",
                                                   expected_sha256=project_cli.scenario_digest(case))
    approved_path = tmp_path / "approved.json"
    approved_path.write_text(json.dumps(reviewed))
    args[2] = str(approved_path)
    assert main(args) == 0
    assert "run-secret" not in capsys.readouterr().out
    assert calls[0] == reviewed
    assert main(args + ["--show-agent-token"]) == 0
    assert json.loads(capsys.readouterr().out)["agent_token"] == "run-secret"


def test_cli_validation_error_does_not_echo_private_source_or_supplied_secret(tmp_path, capsys):
    value = load_project_scenario(EXAMPLE, require_review=False)
    value["api_key"] = "private-test-marker"
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(value))
    assert main(["project", "validate", str(path)]) == 2
    error = capsys.readouterr().err
    assert "private-test-marker" not in error and "api_key" in error


@pytest.mark.parametrize(("operation", "method", "result", "code"), [
    ("studio-build", "setup_modern_profile", {"ready": True}, 0),
    ("studio-up", "start_profile", {"ready": False}, 2),
    ("studio-status", "modern_profile_status", {"ready": True}, 0),
    ("studio-down", "stop_profile", {"stopped": True, "data_preserved": True}, 0),
])
def test_studio_commands_target_only_the_modern_owned_profile(tmp_path, monkeypatch, capsys,
                                                            operation, method, result, code):
    from genlayer_agent_lab.runtime import studio_profiles

    calls = []

    def invoke(directory, **kwargs):
        calls.append((directory, kwargs))
        return result

    monkeypatch.setattr(studio_profiles, method, invoke)
    assert main(["project", operation, "--data-dir", str(tmp_path)]) == code
    assert json.loads(capsys.readouterr().out) == result
    assert calls[0][0] == tmp_path
    if operation == "studio-build":
        assert calls[0][1]["port"] == 8796
    assert main(["project", operation, "--data-dir", str(tmp_path), "--url", "http://127.0.0.1:8765"]) == 2
    assert "manage this installation" in capsys.readouterr().err
    assert len(calls) == 1


def test_project_common_arguments_work_before_and_after_subcommand(tmp_path):
    parser = build_parser()
    for args in (["--data-dir", str(tmp_path), "project", "list"],
                 ["project", "--data-dir", str(tmp_path), "list"],
                 ["project", "list", "--data-dir", str(tmp_path)]):
        assert parser.parse_args(args).data_dir == tmp_path


def test_verify_reserves_new_evidence_file_before_starting_and_keeps_reports_off_terminal(tmp_path, monkeypatch, capsys):
    from genlayer_agent_lab import project_verification

    calls = []
    monkeypatch.setenv("LAB_TOKEN", "admin-test")

    def verify(url, token, **options):
        calls.append((url, token, options))
        assert type(options["timeout_seconds"]) is int
        return {"verification": "pass", "cases": [{"case": "prediction"}], "reports": [{"private": "expected-rubric"}]}

    monkeypatch.setattr(project_verification, "verify_projects", verify)
    output = tmp_path / "evidence.json"
    args = ["project", "verify", "--case", "prediction", "--timeout", "900", "--output", str(output),
            "--url", "http://127.0.0.1:8765"]
    assert main(args) == 0
    assert "reports" not in json.loads(capsys.readouterr().out)
    assert json.loads(output.read_text())["reports"][0]["private"] == "expected-rubric"
    assert calls[0][2] == {"cases": ["prediction"], "timeout_seconds": 900}
    assert main(args) == 2
    capsys.readouterr()
    assert len(calls) == 1


def test_message_template_cli_uses_private_fault_manifest(tmp_path, capsys):
    manifest = ROOT / "examples/projects/prediction-messages/project-repair.yaml"
    output = tmp_path / "repair.json"
    assert main(["project", "template", str(manifest), "--mode", "repair", "--output", str(output)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["review_status"] == "draft"
    case = load_project_scenario(output, require_review=False)
    assert "reject_delivery" not in case["context"]
    assert "repair_audit" in case["policy"]["operations"]
