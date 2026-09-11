"""Approving owner sign-in must not initialize state or leak owner credentials."""

import httpx
import pytest
from fastapi.testclient import TestClient

from genlayer_agent_lab import dashboard_cli
from genlayer_agent_lab.api import create_app, initialize_data_dir, read_admin_token
from genlayer_agent_lab.cli import main


def test_missing_or_foreign_installation_never_initializes_or_sends_credential(tmp_path, monkeypatch, capsys):
    path = tmp_path / "missing"
    monkeypatch.setattr(dashboard_cli, "_port_state", lambda *a: "occupied")
    monkeypatch.setattr(dashboard_cli.httpx, "Client", lambda **kw: pytest.fail("credential sent"))
    assert main(["dashboard", "approve", "ABCD-EFGH-2345", "--data-dir", str(path)]) == 2
    assert not path.exists()
    assert "not reachable" in capsys.readouterr().err


def test_approval_delivers_browser_access_but_never_displays_credentials(tmp_path, monkeypatch, capsys):
    app = create_app(tmp_path)
    token = read_admin_token(tmp_path)
    original = httpx.Client
    # In-process API exercises real app wiring while no Studio is started.
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        pairing = client.post("/v1/dashboard/connect", json={}).json()
        seen = []

        def handler(request):
            seen.append(request)
            response = client.post(request.url.path, content=request.content, headers=dict(request.headers))
            return httpx.Response(response.status_code, json=response.json())

        monkeypatch.setattr(dashboard_cli, "_port_state", lambda *a: "same_installation")
        monkeypatch.setattr(dashboard_cli.httpx, "Client", lambda **kw: original(
            transport=httpx.MockTransport(handler), **kw))
        assert main(["dashboard", "approve", pairing["code"].lower(), "--data-dir", str(tmp_path)]) == 0
        assert len(seen) == 1 and seen[0].headers["authorization"] == "Bearer " + token
        grant = client.post("/v1/dashboard/claim", json={
            "request_id":pairing["request_id"], "claim_secret":pairing["claim_secret"]}).json()
        assert grant["token"] != token
        owner = {"Authorization":"Bearer " + grant["token"]}
        assert client.get("/v1/onboarding/templates", headers=owner).status_code == 200
        assert client.get("/v1/onboarding/templates").status_code == 401
        another = client.post("/v1/dashboard/connect", json={}).json()
        assert client.post("/v1/dashboard/approve", json={"code":another["code"]}, headers=owner).status_code == 401
        output = capsys.readouterr()
        assert token not in output.out + output.err and grant["token"] not in output.out + output.err
        assert "Browser approved" in output.out


@pytest.mark.parametrize("failure", [401, 400, 500, "timeout"])
def test_approval_failure_is_safe_and_does_not_reset_installation(tmp_path, monkeypatch, capsys, failure):
    initialize_data_dir(tmp_path)
    token = read_admin_token(tmp_path)
    monkeypatch.setattr(dashboard_cli, "_port_state", lambda *a: "same_installation")
    original = httpx.Client

    def handler(request):
        if failure == "timeout":
            raise httpx.ReadTimeout("private error " + token)
        return httpx.Response(failure, text="private error " + token)

    monkeypatch.setattr(dashboard_cli.httpx, "Client", lambda **kw: original(
        transport=httpx.MockTransport(handler), **kw))
    assert main(["dashboard", "approve", "ABCD-EFGH-2345", "--data-dir", str(tmp_path)]) == 2
    output = capsys.readouterr()
    assert token not in output.out + output.err
    assert read_admin_token(tmp_path) == token
