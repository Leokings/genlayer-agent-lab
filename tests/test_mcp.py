import asyncio
import json

import httpx
import pytest
from mcp.client import Client

from genlayer_agent_lab.client import LabClient
from genlayer_agent_lab.mcp_server import build_server


def test_role_toolsets_and_agent_run_binding_through_real_sdk():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"run_id": "assigned-run", "status": "running"})

    async def exercise():
        with LabClient("http://127.0.0.1:8765", "agent-secret", transport=httpx.MockTransport(handler)) as lab:
            agent = build_server(token="agent-secret", role="agent", run_id="assigned-run", client=lab)
            async with Client(agent) as connection:
                tools = (await connection.list_tools()).tools
                assert {tool.name for tool in tools} == {"observe", "request_decision", "read_decision", "act", "finish"}
                assert all("run_id" not in tool.input_schema.get("properties", {}) for tool in tools)
                result = await connection.call_tool("observe")
                assert not result.is_error
                assert result.structured_content["run_id"] == "assigned-run"
                forbidden = await connection.call_tool("get_report", {"run_id": "another-run"})
                assert forbidden.is_error
        assert len(seen) == 1
        assert seen[0].url.path == "/v1/runs/assigned-run/observe"
        assert seen[0].headers["authorization"] == "Bearer agent-secret"

    asyncio.run(exercise())


def test_admin_has_no_agent_action_tools_and_start_returns_application_run_id():
    def handler(request):
        assert request.url.path == "/v1/runs"
        assert json.loads(request.content)["agent"] == "external"
        return httpx.Response(200, json={"run_id": "new-run", "agent_token": "scoped", "status": "queued"})

    async def exercise():
        with LabClient("http://localhost:8765", "admin-secret", transport=httpx.MockTransport(handler)) as lab:
            admin = build_server(token="admin-secret", role="admin", client=lab)
            names = {tool.name for tool in await admin.list_tools()}
            assert names == {"list_scenarios", "list_bindings", "list_runs", "start_run", "get_run", "get_report", "cancel_run"}
            async with Client(admin) as connection:
                result = await connection.call_tool("start_run", {"scenario_id": "escrow-approved"})
                assert not result.is_error
                assert result.structured_content["run_id"] == "new-run"
                assert result.structured_content["status"] == "queued"

    asyncio.run(exercise())


def test_mcp_configuration_requires_valid_role_and_run_credential():
    with pytest.raises(ValueError, match="LAB_ROLE"):
        build_server(token="secret", role="other")
    with pytest.raises(ValueError, match="LAB_RUN_ID"):
        build_server(token="secret", role="agent")
    with pytest.raises(ValueError, match="token"):
        build_server(token="", role="admin")


def test_admin_binding_discovery_and_start_mapping_through_real_sdk():
    requests = []

    def handler(request):
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json=[{"id": "delivery-policy", "title": "Delivery"}])
        return httpx.Response(201, json={"run_id": "custom-run", "status": "queued"})

    async def exercise():
        with LabClient("http://localhost:8765", "admin-secret", transport=httpx.MockTransport(handler)) as lab:
            server = build_server(token="admin-secret", role="admin", client=lab)
            async with Client(server) as connection:
                listed = await connection.call_tool("list_bindings")
                assert not listed.is_error
                started = await connection.call_tool("start_run", {
                    "scenario_id": "escrow-normal", "backend": "container-glsim",
                    "binding_id": "delivery-policy",
                })
                assert not started.is_error
                assert started.structured_content["run_id"] == "custom-run"
                mismatch = await connection.call_tool("start_run", {
                    "scenario_id": "escrow-normal", "backend": "fixture",
                    "binding_id": "delivery-policy",
                })
                assert mismatch.is_error
            agent = build_server(token="admin-secret", role="agent", run_id="custom-run", client=lab)
            async with Client(agent) as connection:
                forbidden = await connection.call_tool("list_bindings")
                assert forbidden.is_error

    asyncio.run(exercise())
    assert requests[0].url.path == "/v1/bindings"
    assert len(requests) == 2
    assert json.loads(requests[1].content) == {
        "scenario_id": "escrow-normal", "agent": "external", "backend": "container-glsim",
        "binding_id": "delivery-policy",
    }


def test_mcp_studio_backend_preserves_explicit_binding_selection():
    payloads = []

    def handler(request):
        payloads.append(json.loads(request.content))
        return httpx.Response(201, json={"run_id": "studio-run", "status": "queued"})

    async def exercise():
        with LabClient("http://localhost:8765", "admin-secret", transport=httpx.MockTransport(handler)) as lab:
            server = build_server(token="admin-secret", role="admin", client=lab)
            async with Client(server) as connection:
                for extra in ({}, {"binding_id": "delivery-assessment"}):
                    result = await connection.call_tool("start_run", {
                        "scenario_id": "escrow-normal", "backend": "studio", **extra,
                    })
                    assert not result.is_error

    asyncio.run(exercise())
    assert payloads == [
        {"scenario_id": "escrow-normal", "agent": "external", "backend": "studio"},
        {"scenario_id": "escrow-normal", "agent": "external", "backend": "studio",
         "binding_id": "delivery-assessment"},
    ]
