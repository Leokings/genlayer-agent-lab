"""Exercise the MCP workflow example without launching a child or making requests."""

import asyncio
import copy
import importlib.util
import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from mcp.client import Client as MCPClient

from genlayer_agent_lab.client import LabClient, LabError
from genlayer_agent_lab.mcp_server import build_server

SCRIPT = Path(__file__).parents[1] / "examples/mcp_workflow_agent.py"
SPEC = importlib.util.spec_from_file_location("mcp_workflow_example", SCRIPT)
example = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(example)

URL = "http://127.0.0.1:8765"
TOKEN = "scoped-test-secret"
RUN_ID = "workflow-mcp-example"
TOOL_NAMES = {"observe", "invoke_operation", "appeal_decision", "finish"}


def tool_list():
    properties = {
        "observe": {}, "finish": {},
        "invoke_operation": {name: {} for name in
                             ("operation", "arguments", "idempotency_key", "expected_decision_id")},
        "appeal_decision": {name: {} for name in ("idempotency_key", "expected_decision_id")},
    }
    return [SimpleNamespace(name=name, input_schema={"type": "object", "properties": fields})
            for name, fields in properties.items()]


def result(value=None, *, error=False, text=None):
    return SimpleNamespace(
        is_error=error, structured_content=value,
        content=[] if text is None else [SimpleNamespace(type="text", text=text)],
    )


def install_client(monkeypatch, *, tools=None, callback=None):
    captured = {"calls": [], "entered": False, "exited": False, "threads": []}

    class FakeClient:
        def __init__(self, parameters, **kwargs):
            captured["parameters"], captured["options"] = parameters, kwargs

        async def __aenter__(self):
            captured["entered"] = True
            return self

        async def __aexit__(self, *_):
            captured["exited"] = True

        async def list_tools(self):
            return SimpleNamespace(tools=tool_list() if tools is None else tools)

        async def call_tool(self, name, arguments):
            await asyncio.sleep(0)
            captured["calls"].append((name, copy.deepcopy(arguments)))
            captured["threads"].append(threading.get_ident())
            return callback(name, arguments) if callback else result({"run_id": RUN_ID, "tool": name})

    monkeypatch.setattr(example, "Client", FakeClient)
    return captured


def test_stdio_child_has_only_run_credentials_and_bridge_keeps_event_loop_live(monkeypatch):
    captured = install_client(monkeypatch)
    monkeypatch.setenv("LAB_ADMIN_TOKEN", "must-not-be-forwarded")
    driver_calls = {}

    def driver(client, run_id, **kwargs):
        driver_calls.update(thread=threading.get_ident(), run_id=run_id, options=kwargs)
        assert client.workflow_observe(run_id) == {"run_id": RUN_ID, "tool": "observe"}
        assert client.workflow_invoke(run_id, "evaluate", {}, "evaluate-1") == {
            "run_id": RUN_ID, "tool": "invoke_operation",
        }
        assert client.workflow_invoke(run_id, "release", {"requested_amount": 40}, "release-1",
                                      expected_decision_id="current-decision") == {
            "run_id": RUN_ID, "tool": "invoke_operation",
        }
        assert client.workflow_appeal(run_id, "appeal-1", "current-decision") == {
            "run_id": RUN_ID, "tool": "appeal_decision",
        }
        assert client.workflow_finish(run_id) == {"run_id": RUN_ID, "tool": "finish"}
        return {"run_id": run_id, "status": "completed", "outcome": "released"}

    monkeypatch.setattr(example, "run_workflow_agent", driver)

    async def exercise():
        loop_thread = threading.get_ident()
        value = await asyncio.wait_for(example.run_mcp_workflow_agent(
            URL, TOKEN, RUN_ID, "appeal", 17, cleanup_timeout=3, poll_interval=0.01,
        ), timeout=3)
        assert set(captured["threads"]) == {loop_thread}
        assert driver_calls["thread"] != loop_thread
        return value

    value = asyncio.run(exercise())
    assert value == {"run_id": RUN_ID, "status": "completed", "outcome": "released",
                     "tools": sorted(TOOL_NAMES)}
    parameters = captured["parameters"]
    assert parameters.command == sys.executable
    assert parameters.args == ["-m", "genlayer_agent_lab.mcp_server"]
    assert parameters.env == {"LAB_URL": URL, "LAB_TOKEN": TOKEN, "LAB_RUN_ID": RUN_ID,
                              "LAB_ROLE": "agent", "LAB_MODE": "workflow"}
    assert captured["options"] == {"read_timeout_seconds": 35}
    assert captured["entered"] and captured["exited"]
    assert driver_calls["run_id"] == RUN_ID
    assert driver_calls["options"] == {"mode": "appeal", "timeout_seconds": 17,
                                       "cleanup_timeout": 3, "poll_interval": 0.01}
    assert captured["calls"] == [
        ("observe", {}),
        ("invoke_operation", {"operation": "evaluate", "arguments": {},
                              "idempotency_key": "evaluate-1"}),
        ("invoke_operation", {"operation": "release", "arguments": {"requested_amount": 40},
                              "idempotency_key": "release-1", "expected_decision_id": "current-decision"}),
        ("appeal_decision", {"idempotency_key": "appeal-1", "expected_decision_id": "current-decision"}),
        ("finish", {}),
    ]
    assert TOKEN not in json.dumps(value)


@pytest.mark.parametrize("method,args", [
    ("workflow_observe", ()),
    ("workflow_invoke", ("evaluate", {}, "evaluate-1")),
    ("workflow_appeal", ("appeal-1", "decision-1")),
    ("workflow_finish", ()),
])
def test_adapter_rejects_another_run_before_scheduling_any_tool(method, args):
    class Connection:
        async def call_tool(self, *_):
            pytest.fail("A mismatched run must never reach MCP")

    async def exercise():
        adapter = example._RunBoundMCPClient(Connection(), asyncio.get_running_loop(), RUN_ID)
        with pytest.raises((ValueError, LabError)):
            getattr(adapter, method)("another-workflow", *args)

    asyncio.run(exercise())


@pytest.mark.parametrize("reply", [
    result({"run_id": RUN_ID, "status": "running"}),
    result(text=json.dumps({"run_id": RUN_ID, "status": "running"})),
])
def test_adapter_accepts_structured_or_json_text_public_results(reply):
    class Connection:
        async def call_tool(self, name, arguments):
            assert (name, arguments) == ("observe", {})
            return reply

    async def exercise():
        adapter = example._RunBoundMCPClient(Connection(), asyncio.get_running_loop(), RUN_ID)
        return await asyncio.to_thread(adapter.workflow_observe, RUN_ID)

    assert asyncio.run(exercise()) == {"run_id": RUN_ID, "status": "running"}


@pytest.mark.parametrize("reply,status", [
    (result({"private": TOKEN}, error=True, text=TOKEN), 400),
    (result(error=True, text="Lab HTTP 403: " + TOKEN), 403),
    (result(error=True, text="Lab HTTP 503: " + TOKEN), 503),
    (result(error=True, text="Lab connection failed (ReadError) " + TOKEN), None),
    (result(error=True, text="Lab returned an invalid JSON response " + TOKEN), None),
    (result({"run_id": "different-workflow", "private": TOKEN}), 400),
    (result(text="Malformed JSON " + TOKEN), None),
    (result(text=json.dumps([TOKEN])), None),
    (result(), None),
    (RuntimeError("Transport failure containing " + TOKEN), None),
])
def test_adapter_sanitizes_errors_and_preserves_only_retry_classification(reply, status):
    class Connection:
        async def call_tool(self, *_):
            if isinstance(reply, Exception):
                raise reply
            return reply

    async def exercise():
        adapter = example._RunBoundMCPClient(Connection(), asyncio.get_running_loop(), RUN_ID)
        with pytest.raises(LabError) as raised:
            await asyncio.to_thread(adapter.workflow_observe, RUN_ID)
        assert TOKEN not in str(raised.value)
        assert raised.value.status_code == status

    asyncio.run(exercise())


@pytest.mark.parametrize("mutation", ["extra", "missing", "duplicate", "run_id", "source",
                                     "spec", "fixtures", "expectations"])
def test_unexpected_tool_names_or_private_schema_parameters_stop_before_driver(monkeypatch, mutation):
    tools = tool_list()
    if mutation == "extra":
        tools.append(SimpleNamespace(name="get_report_" + TOKEN, input_schema={"properties": {}}))
    elif mutation == "missing":
        tools = [tool for tool in tools if tool.name != "finish"]
    elif mutation == "duplicate":
        tools.append(copy.deepcopy(tools[0]))
    else:
        tools[0].input_schema["properties"][mutation] = {"type": "string"}
    captured = install_client(monkeypatch, tools=tools)
    monkeypatch.setattr(example, "run_workflow_agent",
                        lambda *_args, **_kwargs: pytest.fail("Unsafe toolset must not run the policy"))
    value = asyncio.run(example.run_mcp_workflow_agent(URL, TOKEN, RUN_ID))
    assert value == {"run_id": RUN_ID, "status": "unknown", "outcome": "mcp_toolset_mismatch",
                     "tools": []}
    assert TOKEN not in json.dumps(value)
    assert captured["calls"] == [] and captured["exited"]


@pytest.mark.parametrize("overrides", [
    {"url": "https://example.com"}, {"url": "http://localhost/elsewhere"},
    {"url": "http://user:pass@localhost"}, {"token": ""}, {"token": "bad token"},
    {"token": "non-ascii-\u00e9"}, {"token": "a" * 257},
    {"run_id": ""}, {"run_id": "."}, {"run_id": ".."}, {"run_id": "a/b"},
    {"mode": "other"}, {"timeout_seconds": 0}, {"timeout_seconds": float("inf")},
    {"timeout_seconds": float("nan")}, {"timeout_seconds": True},
    {"cleanup_timeout": -1}, {"poll_interval": 0},
])
def test_invalid_connection_or_policy_inputs_are_rejected_before_child_creation(monkeypatch, overrides):
    monkeypatch.setattr(example, "Client",
                        lambda *_args, **_kwargs: pytest.fail("Invalid input must not start a child"))
    options = {"url": URL, "token": TOKEN, "run_id": RUN_ID, **overrides}
    with pytest.raises(ValueError):
        asyncio.run(example.run_mcp_workflow_agent(**options))


def test_real_policy_and_mcp_sdk_wait_for_release_and_terminal_cleanup_without_network(monkeypatch):
    context = {"resource_id": "service-1", "policy_version": "v1", "amount": 100,
               "evidence": "Forty units delivered"}
    state = {**context, "unit": "test_units", "decision": "partial", "authorized_amount": 40,
             "released_amount": 0, "remaining_amount": 100, "revision": 1}
    operations = []
    lifecycle = {"evaluation_polls": 0, "release_polls": 0, "cleanup_polls": 0,
                 "closing": False, "release": None}

    def public(name, arguments):
        if name == "invoke_operation":
            operation = arguments["operation"]
            intent = {"run_id": RUN_ID, "operation": operation,
                      "idempotency_key": arguments["idempotency_key"],
                      "status": "queued"}
            if operation == "release":
                assert lifecycle["evaluation_polls"] >= 2
                assert arguments["arguments"] == {"requested_amount": 40}
                assert arguments["expected_decision_id"] == "current-final-decision"
                lifecycle["release"] = intent
            else:
                assert operation == "evaluate" and arguments["arguments"] == {}
            operations.append(intent)
            return result(copy.deepcopy(intent))
        if name == "finish":
            assert lifecycle["release"]["status"] == "completed"
            lifecycle["closing"] = True
            return result({"run_id": RUN_ID, "status": "running"})
        assert name == "observe"
        decision = None
        status = "running"
        if operations:
            lifecycle["evaluation_polls"] += 1
            finalized = lifecycle["evaluation_polls"] >= 2
            operations[0]["status"] = "completed" if finalized else "submitted"
            decision = {"decision_id": "current-final-decision",
                        "status": "FINALIZED" if finalized else "ACCEPTED", "execution_success": True,
                        "result": copy.deepcopy(state), "appeal_eligible": not finalized, "rounds": []}
        if lifecycle["release"] is not None:
            lifecycle["release_polls"] += 1
            if lifecycle["release_polls"] >= 2:
                lifecycle["release"]["status"] = "completed"
                state.update(released_amount=40, remaining_amount=60)
            else:
                lifecycle["release"]["status"] = "submitted"
        if lifecycle["closing"]:
            lifecycle["cleanup_polls"] += 1
            status = "completed" if lifecycle["cleanup_polls"] >= 2 else "closing"
        return result({"run_id": RUN_ID, "profile": "service_release", "status": status,
                       "context": context, "state": copy.deepcopy(state), "decision": decision,
                       "operations": copy.deepcopy(operations)})

    calls = []

    def handler(request):
        assert request.headers["authorization"] == "Bearer " + TOKEN
        assert request.method == "POST"
        prefix = f"/v1/workflows/{RUN_ID}/"
        assert request.url.path.startswith(prefix)
        names = {"observe": "observe", "operations": "invoke_operation",
                 "appeals": "appeal_decision", "finish": "finish"}
        name = names[request.url.path.removeprefix(prefix)]
        arguments = json.loads(request.content) if request.content else {}
        calls.append((name, arguments))
        return httpx.Response(200, json=public(name, arguments).structured_content)

    with LabClient(URL, TOKEN, transport=httpx.MockTransport(handler)) as lab:
        server = build_server(token=TOKEN, run_id=RUN_ID, client=lab, mode="workflow")

        def in_memory_client(parameters, **options):
            assert parameters.command == sys.executable
            assert parameters.env["LAB_RUN_ID"] == RUN_ID
            return MCPClient(server, **options)

        monkeypatch.setattr(example, "Client", in_memory_client)
        value = asyncio.run(example.run_mcp_workflow_agent(
            URL, TOKEN, RUN_ID, timeout_seconds=2, cleanup_timeout=1, poll_interval=0.001,
        ))
    assert value == {"run_id": RUN_ID, "status": "completed", "outcome": "released",
                     "tools": sorted(TOOL_NAMES)}
    assert [arguments["operation"] for name, arguments in calls
            if name == "invoke_operation"] == ["evaluate", "release"]
    assert lifecycle["cleanup_polls"] == 2
    assert calls[-1] == ("observe", {})
