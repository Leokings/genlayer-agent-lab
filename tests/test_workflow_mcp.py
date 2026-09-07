"""Exercise workflow tool schemas and routing through the real MCP SDK."""

import asyncio
import json
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest
from mcp.client import Client

from genlayer_agent_lab import mcp_server
from genlayer_agent_lab.client import LabClient
from genlayer_agent_lab.mcp_server import build_server


def test_workflow_agent_has_only_four_run_bound_tools_and_preserves_requests():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"run_id": "workflow-1", "status": "running"})

    async def exercise():
        with LabClient("http://127.0.0.1:8765", "workflow-token",
                       transport=httpx.MockTransport(handler)) as lab:
            server = build_server(token="workflow-token", run_id="workflow-1", client=lab,
                                  mode="workflow")
            async with Client(server) as connection:
                tools = (await connection.list_tools()).tools
                assert {tool.name for tool in tools} == {
                    "observe", "invoke_operation", "appeal_decision", "finish",
                }
                for tool in tools:
                    properties = tool.input_schema.get("properties", {})
                    assert not {"run_id", "spec", "source", "fixtures", "expectations"} & properties.keys()
                for name, arguments in [
                    ("observe", {}),
                    ("invoke_operation", {"operation": "read_evidence", "arguments": {"item": "a"},
                                          "idempotency_key": "read-1"}),
                    ("invoke_operation", {"operation": "submit", "arguments": {"facts": [1, 2]},
                                          "idempotency_key": "submit-1", "expected_decision_id": "d1"}),
                    ("appeal_decision", {"idempotency_key": "appeal-1", "expected_decision_id": "d1"}),
                    ("finish", {}),
                ]:
                    result = await connection.call_tool(name, arguments)
                    assert not result.is_error
                    assert result.structured_content["run_id"] == "workflow-1"
                for forbidden in ("start_workflow", "get_workflow_report", "request_decision", "act"):
                    result = await connection.call_tool(forbidden)
                    assert result.is_error
                unbound = await connection.call_tool("appeal_decision", {"idempotency_key": "bad"})
                assert unbound.is_error

    asyncio.run(exercise())
    assert [request.url.path for request in seen] == [
        "/v1/workflows/workflow-1/observe", "/v1/workflows/workflow-1/operations",
        "/v1/workflows/workflow-1/operations", "/v1/workflows/workflow-1/appeals",
        "/v1/workflows/workflow-1/finish",
    ]
    assert all(request.headers["Authorization"] == "Bearer workflow-token" for request in seen)
    assert json.loads(seen[1].content) == {
        "operation": "read_evidence", "arguments": {"item": "a"}, "idempotency_key": "read-1",
    }
    assert json.loads(seen[2].content)["expected_decision_id"] == "d1"
    assert json.loads(seen[3].content) == {
        "idempotency_key": "appeal-1", "expected_decision_id": "d1",
    }


def test_workflow_administrator_toolset_and_client_routes():
    seen = []

    def handler(request):
        seen.append(request)
        if request.method == "GET" and request.url.path == "/v1/workflows":
            return httpx.Response(200, json=[])
        return httpx.Response(200, json={"run_id": "workflow-1", "status": "running"})

    async def exercise():
        with LabClient("http://localhost:8765", "admin-token",
                       transport=httpx.MockTransport(handler)) as lab:
            server = build_server(token="admin-token", role="admin", mode="workflow", client=lab)
            async with Client(server) as connection:
                tools = (await connection.list_tools()).tools
                assert {tool.name for tool in tools} == {
                    "list_workflows", "start_workflow", "get_workflow",
                    "get_workflow_report", "cancel_workflow",
                }
                for name, arguments in [
                    ("list_workflows", {}),
                    ("start_workflow", {"spec": {"title": "Evidence", "custom": ["x"]}}),
                    ("get_workflow", {"run_id": "workflow-1"}),
                    ("get_workflow_report", {"run_id": "workflow-1"}),
                    ("cancel_workflow", {"run_id": "workflow-1"}),
                ]:
                    assert not (await connection.call_tool(name, arguments)).is_error
                assert (await connection.call_tool("invoke_operation")).is_error

    asyncio.run(exercise())
    assert [(request.method, request.url.path) for request in seen] == [
        ("GET", "/v1/workflows"), ("POST", "/v1/workflows"),
        ("GET", "/v1/workflows/workflow-1"), ("GET", "/v1/workflows/workflow-1/report"),
        ("POST", "/v1/workflows/workflow-1/cancel"),
    ]
    assert json.loads(seen[1].content) == {"spec": {"title": "Evidence", "custom": ["x"]}}


def test_workflow_mode_configuration_and_main_environment(monkeypatch):
    with pytest.raises(ValueError, match="LAB_MODE"):
        build_server(token="admin-token", role="admin", mode="unknown")
    with pytest.raises(ValueError, match="LAB_RUN_ID"):
        build_server(token="agent-token", mode="workflow")
    captured = {}

    class Server:
        def run(self, **kwargs):
            captured["run"] = kwargs

    def build(**kwargs):
        captured["build"] = kwargs
        return Server()

    monkeypatch.setattr(mcp_server, "build_server", build)
    monkeypatch.setenv("LAB_MODE", "workflow")
    monkeypatch.setenv("LAB_RUN_ID", "workflow-1")
    monkeypatch.setenv("LAB_TOKEN", "agent-token")
    monkeypatch.setenv("LAB_ROLE", "agent")
    mcp_server.main()
    assert captured["build"]["mode"] == "workflow"
    assert captured["build"]["run_id"] == "workflow-1"
    assert captured["run"] == {"transport": "stdio"}


@pytest.mark.parametrize("run_id", ["", ".", "..", "a/b"])
def test_workflow_client_rejects_invalid_run_identifiers_before_transport(run_id):
    def handler(request):
        pytest.fail("Invalid run identifier must not reach the HTTP service")

    with LabClient("http://localhost:8765", "agent-token",
                   transport=httpx.MockTransport(handler)) as lab:
        with pytest.raises(ValueError, match="workflow identifier"):
            lab.workflow_observe(run_id)


def test_typescript_workflow_client_preserves_scope_and_decision_binding():
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
  received.push({{method: request.method, path: request.url, auth: request.headers.authorization,
                  body: body ? JSON.parse(body) : undefined}});
  response.setHeader('Content-Type', 'application/json');
  response.end(JSON.stringify({{run_id: 'workflow-1', status: 'running'}}));
}});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
try {{
  const client = new LabClient(`http://127.0.0.1:${{server.address().port}}`, 'scoped-token');
  await client.workflowCreate({{title: 'Investigation'}});
  await client.workflowList();
  await client.workflowGet('workflow-1');
  await client.workflowObserve('workflow-1');
  await client.workflowInvoke('workflow-1', 'read_evidence', {{item: 'a'}}, 'read-1');
  await client.workflowInvoke('workflow-1', 'submit', {{answer: 'disagree'}}, 'submit-1', 'd1');
  await client.workflowAppeal('workflow-1', 'appeal-1', 'd1');
  await client.workflowFinish('workflow-1');
  await client.workflowCancel('workflow-1');
  await client.workflowReport('workflow-1');
  assert.deepEqual(received.map(item => [item.method, item.path]), [
    ['POST', '/v1/workflows'], ['GET', '/v1/workflows'], ['GET', '/v1/workflows/workflow-1'],
    ['POST', '/v1/workflows/workflow-1/observe'], ['POST', '/v1/workflows/workflow-1/operations'],
    ['POST', '/v1/workflows/workflow-1/operations'], ['POST', '/v1/workflows/workflow-1/appeals'],
    ['POST', '/v1/workflows/workflow-1/finish'], ['POST', '/v1/workflows/workflow-1/cancel'],
    ['GET', '/v1/workflows/workflow-1/report'],
  ]);
  assert.deepEqual(received[0].body, {{spec: {{title: 'Investigation'}}}});
  assert.deepEqual(received[4].body, {{operation: 'read_evidence', arguments: {{item: 'a'}},
                                    idempotency_key: 'read-1'}});
  assert.equal(received[5].body.expected_decision_id, 'd1');
  assert.deepEqual(received[6].body, {{idempotency_key: 'appeal-1', expected_decision_id: 'd1'}});
  assert.ok(received.every(item => item.auth === 'Bearer scoped-token'));
  assert.throws(() => client.workflowObserve('../other-run'), /Invalid workflow identifier/);
  assert.equal(received.length, 10);
}} finally {{
  server.closeAllConnections();
  await new Promise(resolve => server.close(resolve));
}}
"""
    completed = subprocess.run([node, "--input-type=module", "-e", script], text=True,
                               capture_output=True, timeout=30, check=False)
    assert completed.returncode == 0, completed.stderr
