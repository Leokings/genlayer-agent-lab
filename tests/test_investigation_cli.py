"""Exercise exported investigation authoring and verifier dispatch boundaries."""

import json
from pathlib import Path

import pytest

from genlayer_agent_lab.cli import main

MANIFEST = Path(__file__).parents[1] / "examples/projects/prediction/project.yaml"


@pytest.fixture(autouse=True)
def local_environment(monkeypatch):
    monkeypatch.delenv("LAB_URL", raising=False)
    monkeypatch.delenv("LAB_TOKEN", raising=False)


@pytest.mark.parametrize("mode", ["missing", "stale", "contradictory", "misleading", "supports", "appeal"])
def test_investigation_draft_export_remains_reviewable(tmp_path, capsys, mode):
    draft = tmp_path / "case.json"
    assert main(["project", "investigate-template", str(MANIFEST), "--mode", mode,
                 "--output", str(draft)]) == 0
    authored = json.loads(capsys.readouterr().out)
    assert authored["review_status"] == "draft" and authored["executable"] is False
    assert main(["project", "validate", str(draft)]) == 0
    validated = json.loads(capsys.readouterr().out)
    assert validated["content_sha256"] == authored["content_sha256"]
    assert "submit_investigation" in json.loads(draft.read_text())["policy"]["operations"]


def test_live_verifier_uses_requested_transport_and_never_overwrites_evidence(tmp_path, monkeypatch, capsys):
    from genlayer_agent_lab import investigation_verification

    monkeypatch.setenv("LAB_TOKEN", "test-admin")
    calls = []

    def verify(url, token, **options):
        calls.append((url, token, options))
        return {"verification": "pass", "cases": [{"case": "misleading"}],
                "reports": {"misleading": {"expectations": "private-test-marker"}}}

    monkeypatch.setattr(investigation_verification, "verify_investigations", verify)
    output = tmp_path / "evidence.json"
    arguments = ["project", "verify-investigation", "--case", "misleading", "--transport", "mcp",
                 "--url", "http://127.0.0.1:8795", "--output", str(output)]
    assert main(arguments) == 0
    assert "private-test-marker" not in capsys.readouterr().out
    assert json.loads(output.read_text())["reports"]["misleading"]["expectations"] == "private-test-marker"
    assert calls[0] == ("http://127.0.0.1:8795", "test-admin",
                       {"transport": "mcp", "cases": ["misleading"], "timeout_seconds": 900})
    assert main(arguments) == 2
    capsys.readouterr()
    assert len(calls) == 1
