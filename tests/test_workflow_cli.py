import json

from genlayer_agent_lab import workflow_cli
from genlayer_agent_lab.cli import main


def test_workflow_cli_keeps_session_in_service_and_run_token_opt_in(tmp_path, monkeypatch, capsys):
    calls = []

    class Client:
        def __init__(self, url, token):
            assert url == "http://127.0.0.1:8775" and token == "admin-test"

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def workflow_create(self, spec):
            calls.append(spec)
            return {"run_id": "workflow-test", "agent_token": "private-run-test", "status": "preparing"}

    monkeypatch.setattr(workflow_cli, "LabClient", Client)
    monkeypatch.setenv("LAB_TOKEN", "admin-test")
    monkeypatch.delenv("LAB_URL", raising=False)
    path = tmp_path / "case.json"
    path.write_text('{"schema_version":1}', encoding="utf-8")
    args = ["workflow", "create", str(path), "--data-dir", str(tmp_path)]
    assert main(args) == 2
    assert "running Lab service" in capsys.readouterr().err
    assert calls == []
    args += ["--url", "http://127.0.0.1:8775"]
    assert main(args) == 0
    output = capsys.readouterr().out
    assert "private-run-test" not in output and json.loads(output)["run_id"] == "workflow-test"
    assert main(args + ["--show-agent-token"]) == 0
    assert json.loads(capsys.readouterr().out)["agent_token"] == "private-run-test"


def test_invalid_workflow_json_is_not_sent_to_service(tmp_path, monkeypatch, capsys):
    class Client:
        def __init__(self, *args):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def workflow_create(self, spec):
            raise AssertionError("Invalid file must not be submitted")

    monkeypatch.setattr(workflow_cli, "LabClient", Client)
    monkeypatch.setenv("LAB_TOKEN", "admin-test")
    path = tmp_path / "bad.json"
    for content in ('[]', '{"value":NaN}', '{"value":"' + 'x' * 50000 + '"}'):
        path.write_text(content, encoding="utf-8")
        assert main(["workflow", "create", str(path), "--url", "http://127.0.0.1:8775",
                     "--data-dir", str(tmp_path)]) == 2
        assert "Error:" in capsys.readouterr().err
