import json
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest

from genlayer_agent_lab.client import LabClient, LabError, validate_base_url


def test_client_retries_preserve_action_and_scoped_authentication():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"status": "unknown" if len(requests) == 1 else "applied"})

    action = {"operation": "transfer", "amount": 100, "idempotency_key": "once"}
    with LabClient("http://127.0.0.1:8765", "run-secret", transport=httpx.MockTransport(handler)) as lab:
        assert lab.act("run-123", action)["status"] == "unknown"
        assert lab.act("run-123", action)["status"] == "applied"
    assert all(request.headers["Authorization"] == "Bearer run-secret" for request in requests)
    assert all(request.url.path == "/v1/runs/run-123/actions" for request in requests)
    assert json.loads(requests[0].content) == json.loads(requests[1].content) == action


@pytest.mark.parametrize("url", [
    "https://example.com", "http://127.0.0.1.evil.example", "file:///tmp/a",
    "http://user:password@localhost:8765", "http://localhost:8765/a",
    "http://localhost:8765?secret=1", "http://localhost:8765#fragment",
])
def test_nonlocal_or_ambiguous_origins_rejected_before_sending_credentials(url):
    with pytest.raises(ValueError):
        validate_base_url(url)


def test_redirect_not_followed_and_server_echoed_token_redacted():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(302, headers={"Location": "https://example.com"}, json={"detail": "bad run-secret"})

    with LabClient("http://localhost:8765", "run-secret", transport=httpx.MockTransport(handler)) as lab:
        with pytest.raises(LabError) as caught:
            lab.observe("run-123")
    assert caught.value.status_code == 302
    assert "run-secret" not in str(caught.value)
    assert len(requests) == 1


def test_report_and_decision_routes_are_distinct():
    paths = []

    def handler(request):
        paths.append((request.method, request.url.path))
        return httpx.Response(200, json={})

    with LabClient("http://127.0.0.1:8765", "test-token", transport=httpx.MockTransport(handler)) as lab:
        lab.request_decision("run-a", "decision-key")
        lab.read_decision("run-a")
        lab.finish("run-a")
        lab.report("run-a")
    assert paths == [("POST", "/v1/runs/run-a/decision"), ("GET", "/v1/runs/run-a/decision"),
                     ("POST", "/v1/runs/run-a/finish"), ("GET", "/v1/runs/run-a/report")]


def test_custom_binding_request_mapping_and_default_compatibility():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=[] if request.method == "GET" else {"run_id": "queued"})

    with LabClient("http://127.0.0.1:8765", "admin-secret", transport=httpx.MockTransport(handler)) as lab:
        lab.list_bindings()
        lab.create_run("escrow-normal")
        lab.create_run("escrow-normal", "safe", "container-glsim", binding_id="delivery-policy")
    assert requests[0].url.path == "/v1/bindings"
    assert json.loads(requests[1].content) == {
        "scenario_id": "escrow-normal", "agent": "external", "backend": "glsim",
    }
    assert json.loads(requests[2].content) == {
        "scenario_id": "escrow-normal", "agent": "safe", "backend": "container-glsim",
        "binding_id": "delivery-policy",
    }


@pytest.mark.parametrize(("backend", "binding"), [
    ("fixture", "delivery-policy"), ("glsim", "delivery-policy"), ("container-glsim", None),
])
def test_custom_binding_never_falls_back_to_native_or_fixture(backend, binding):
    def handler(request):
        pytest.fail("Invalid backend combination must be rejected before sending credentials")

    with LabClient("http://localhost:8765", "admin-secret", transport=httpx.MockTransport(handler)) as lab:
        with pytest.raises(ValueError, match="container-glsim"):
            lab.create_run("escrow-normal", backend=backend, binding_id=binding)


@pytest.mark.parametrize("binding", [None, "delivery-assessment"])
def test_studio_backend_accepts_optional_binding_without_changing_default(binding):
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(201, json={"run_id": "studio-run"})

    with LabClient("http://127.0.0.1:8765", "admin-secret", transport=httpx.MockTransport(handler)) as lab:
        lab.create_run("escrow-normal", backend="studio", binding_id=binding)
    assert seen[0]["backend"] == "studio"
    assert seen[0].get("binding_id") == binding
    assert ("binding_id" in seen[0]) is (binding is not None)


def test_typescript_client_sends_binding_fields_to_real_loopback_http_server():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for the TypeScript client integration test")
    version = subprocess.run([node, "--version"], text=True, capture_output=True, check=True)
    if int(version.stdout.strip().lstrip("v").split(".")[0]) < 24:
        pytest.skip("Node.js 24 or newer is required for the build-free TypeScript example")
    module = (Path(__file__).parents[1] / "examples/typescript/client.ts").as_uri()
    script = f"""
import {{createServer}} from 'node:http';
import assert from 'node:assert/strict';
import {{LabClient}} from {json.dumps(module)};
const received = [];
const server = createServer(async (request, response) => {{
  let body = '';
  for await (const part of request) body += part;
  received.push({{path: request.url, auth: request.headers.authorization,
                  body: body ? JSON.parse(body) : undefined}});
  response.setHeader('Content-Type', 'application/json');
  response.end(JSON.stringify(request.method === 'GET' ? [] : {{run_id: 'created'}}));
}});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
try {{
  const client = new LabClient(`http://127.0.0.1:${{server.address().port}}`, 'admin-secret');
  await client.listBindings();
  await client.createRun('escrow-normal');
  await client.createRun('escrow-normal', 'safe', 'container-glsim', 'delivery-policy');
  await client.createRun('escrow-normal', 'safe', 'studio');
  await client.createRun('escrow-normal', 'safe', 'studio', 'delivery-policy');
  assert.equal(received[0].path, '/v1/bindings');
  assert.deepEqual(received[1].body, {{scenario_id:'escrow-normal',agent:'external',backend:'glsim'}});
  assert.deepEqual(received[2].body, {{scenario_id:'escrow-normal',agent:'safe',
                                    backend:'container-glsim',binding_id:'delivery-policy'}});
  assert.ok(received.every(item => item.auth === 'Bearer admin-secret'));
  assert.deepEqual(received[3].body, {{scenario_id:'escrow-normal',agent:'safe',backend:'studio'}});
  assert.equal(received[4].body.binding_id, 'delivery-policy');
  assert.equal(received[4].body.backend, 'studio');
  assert.throws(() => client.createRun('escrow-normal','safe','glsim','delivery-policy'), /container-glsim/);
  assert.equal(received.length, 5);
}} finally {{
  server.closeAllConnections();
  await new Promise(resolve => server.close(resolve));
}}
"""
    completed = subprocess.run([node, "--input-type=module", "-e", script], text=True,
                               capture_output=True, timeout=30, check=False)
    assert completed.returncode == 0, completed.stderr
