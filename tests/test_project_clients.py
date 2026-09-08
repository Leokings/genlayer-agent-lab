"""Real HTTP/stdio client transport against a synthetic workflow fixture.

These tests verify agent/client integration and decision gates; they are not
Studio consensus, fee, bond or contract-execution evidence.
"""

import asyncio
import copy
import importlib.util
import json
import shutil
import socket
import subprocess
import sys
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
NODE = shutil.which("node")
RUN_ID = "project-client-fixture"
TOKEN = "scoped-client-fixture-secret"


def load_mcp_example():
    # Load the adjacent examples using their normal script imports.
    sys.path.insert(0, str(ROOT / "examples"))
    try:
        spec = importlib.util.spec_from_file_location("project_mcp_example", ROOT / "examples/mcp_project_agent.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.pop(0)


class WorkflowFixture:
    def __init__(self, *, appeal=False, lose_record_reply=False, malformed_record_reply=False):
        self.appeal, self.lose_record_reply = appeal, lose_record_reply
        self.malformed_record_reply = malformed_record_reply
        self.calls, self.operations = [], []
        self.tick, self.resolved_tick, self.record_count = 0, None, 0
        self.finished, self.appealed, self.lost = False, False, False

    def state(self):
        revision = 2 if self.appealed else 1
        outcome = "no" if self.appeal and not self.appealed else "yes"
        return {"outcome": outcome, "revision": revision, "confidence_bps": 9100}

    def observe(self):
        self.tick += 1
        transactions = {}
        if self.resolved_tick is not None:
            finalized = self.appealed or (not self.appeal and self.tick - self.resolved_tick >= 2)
            preaccepted = self.appeal and self.tick - self.resolved_tick < 2
            transactions["tx-resolve"] = {
                "operation": "resolve", "tx_id": "tx-resolve", "execution_success": True,
                "decision_id": "decision-appealed" if self.appealed else "decision-original",
                "status": "FINALIZED" if finalized else "COMMITTING" if preaccepted else "ACCEPTED", "result": self.state(),
                "appeal_eligible": not finalized and not preaccepted,
            }
        return {"run_id": RUN_ID, "profile": "project", "status": "completed" if self.finished else "running",
                "context": {"evidence": "The supplied record reports yes."},
                "policy": {"allow_appeal": self.appeal, "operations": {}},
                "state": {"oracle_state": self.state()} if transactions else {},
                "operations": copy.deepcopy(self.operations), "transactions": transactions}

    def operation(self, payload):
        existing = next((item for item in self.operations
                         if item["idempotency_key"] == payload["idempotency_key"]), None)
        if existing:
            assert existing["arguments"] == payload.get("arguments", {})
            return {"run_id": RUN_ID, **existing}
        name = payload["operation"]
        entry = {"operation": name, "arguments": copy.deepcopy(payload.get("arguments", {})),
                 "idempotency_key": payload["idempotency_key"], "status": "completed", "execution_success": True}
        if name == "read_evidence":
            entry["result"] = {"content": "The supplied record reports yes."}
        elif name == "resolve":
            self.resolved_tick = self.tick
            entry["result"] = self.state()
        elif name == "inspect_appeal":
            entry["result"] = {"appeal_eligible": True, "fee_value": 5}
        elif name == "record":
            decision_id = "decision-appealed" if self.appealed else "decision-original"
            expected = {"expected_revision": self.state()["revision"], "outcome": self.state()["outcome"]}
            if (payload.get("expected_decision_id") != decision_id or payload["arguments"] != expected
                    or (not self.appealed and self.tick - self.resolved_tick < 2)):
                entry.update(status="rejected", execution_success=False, error_code="decision_not_current_final")
            else:
                self.record_count += 1
                entry["result"] = {"record_count": self.record_count}
        else:
            raise AssertionError("Unexpected operation: " + name)
        self.operations.append(entry)
        return {"run_id": RUN_ID, **entry}


@contextmanager
def synthetic_service(**options):
    fixture = WorkflowFixture(**options)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            assert self.headers.get("Authorization") == "Bearer " + TOKEN
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length)) if length else {}
            fixture.calls.append((self.path, copy.deepcopy(payload)))
            prefix = f"/v1/workflows/{RUN_ID}/"
            assert self.path.startswith(prefix), "Agent must not request administrator endpoints"
            action = self.path[len(prefix):]
            if action == "observe":
                result = fixture.observe()
            elif action == "operations":
                result = fixture.operation(payload)
                if (payload["operation"] == "record" and not fixture.lost
                        and (fixture.lose_record_reply or fixture.malformed_record_reply)):
                    fixture.lost = True
                    if fixture.malformed_record_reply:
                        raw = b"{deliberately-invalid-success-json"
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", str(len(raw)))
                        self.end_headers()
                        self.wfile.write(raw)
                        return
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.connection.close()
                    return
            elif action == "appeals":
                assert payload["expected_decision_id"] == "decision-original"
                if not fixture.appealed:
                    fixture.appealed = True
                    fixture.operations.append({"operation": "appeal", "status": "completed", "execution_success": True,
                                               "idempotency_key": payload["idempotency_key"]})
                result = {"run_id": RUN_ID, "status": "completed"}
            elif action == "finish":
                fixture.finished = True
                result = {"run_id": RUN_ID, "status": "completed"}
            else:
                raise AssertionError("Unexpected client endpoint: " + action)
            raw = json.dumps(result).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", fixture
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def run_typescript(url, *, unsafe=False):
    script = """
import { LabClient } from './examples/typescript/client.ts';
import { runProjectAgent } from './examples/typescript/project-agent.ts';
const [url, token, runId, unsafe] = process.argv.slice(1);
console.log(JSON.stringify(await runProjectAgent(new LabClient(url,token),runId,{
  unsafe:unsafe === 'true', timeoutSeconds:10, pollIntervalSeconds:.005,
})));
"""
    process = subprocess.run([NODE, "--input-type=module", "-e", script, url, TOKEN, RUN_ID,
                              str(unsafe).lower()], cwd=ROOT, capture_output=True, text=True, timeout=20)
    assert process.returncode == 0, process.stderr
    assert TOKEN not in process.stdout + process.stderr
    return json.loads(process.stdout)


@pytest.mark.skipif(NODE is None, reason="Node.js is required for the TypeScript example")
@pytest.mark.parametrize("appeal", [False, True])
@pytest.mark.parametrize("malformed", [False, True])
def test_typescript_uses_observed_final_decision_and_retries_identical_lost_write(appeal, malformed):
    with synthetic_service(appeal=appeal, lose_record_reply=not malformed,
                           malformed_record_reply=malformed) as (url, fixture):
        result = run_typescript(url)
    assert result["status"] == "completed"
    assert fixture.record_count == 1 and fixture.lost
    records = [payload for _, payload in fixture.calls if payload.get("operation") == "record"]
    assert len(records) == 2 and records[0] == records[1]
    assert records[0]["arguments"] == {"expected_revision": 2 if appeal else 1, "outcome": "yes"}
    assert fixture.appealed is appeal


@pytest.mark.skipif(NODE is None, reason="Node.js is required for the TypeScript example")
def test_typescript_faulty_driver_attempt_is_observable_and_does_not_record():
    with synthetic_service() as (url, fixture):
        result = run_typescript(url, unsafe=True)
    assert result["status"] == "completed"
    assert fixture.record_count == 0
    assert any(item["status"] == "rejected" for item in fixture.operations)


@pytest.mark.skipif(NODE is None, reason="Node.js is required for the TypeScript example")
def test_typescript_step_waits_for_matching_final_state_and_does_not_repeat_record():
    fixture = WorkflowFixture()
    fixture.operations = [
        {"operation": "read_evidence", "status": "completed", "idempotency_key": "project-reference:evidence",
         "result": {"content": "The supplied record reports yes."}},
        {"operation": "resolve", "status": "completed", "idempotency_key": "project-reference:resolve"},
    ]
    fixture.resolved_tick, fixture.tick = 0, 4
    final = fixture.observe()
    stale = copy.deepcopy(final)
    stale["state"]["oracle_state"]["revision"] = 0
    accepted = copy.deepcopy(final)
    accepted["transactions"]["tx-resolve"]["status"] = "ACCEPTED"
    failed = copy.deepcopy(final)
    failed["transactions"]["tx-resolve"]["execution_success"] = False
    already = copy.deepcopy(final)
    already["operations"].append({"operation": "record", "status": "completed",
                                  "idempotency_key": "project-reference:record"})
    script = """
import fs from 'node:fs';
import { projectAgentStep } from './examples/typescript/project-agent.ts';
console.log(JSON.stringify(JSON.parse(fs.readFileSync(0,'utf8')).map(x => projectAgentStep(x))));
"""
    process = subprocess.run([NODE, "--input-type=module", "-e", script], cwd=ROOT,
                             input=json.dumps([stale, accepted, failed, final, already]),
                             capture_output=True, text=True, timeout=10)
    assert process.returncode == 0, process.stderr
    actions = json.loads(process.stdout)
    assert actions[:3] == [None, None, None]
    assert actions[3]["operation"] == "record" and actions[3]["expected_decision_id"] == "decision-original"
    assert actions[4] == {"kind": "finish"}


@pytest.mark.parametrize("malformed", [False, True])
def test_mcp_subprocess_uses_only_four_agent_tools_and_preserves_lost_action_identity(malformed):
    example = load_mcp_example()
    with synthetic_service(appeal=True, lose_record_reply=not malformed,
                           malformed_record_reply=malformed) as (url, fixture):
        result = asyncio.run(example.run_mcp_project_agent(url, TOKEN, RUN_ID,
                                                          timeout_seconds=10, poll_interval=.005))
    assert result["status"] == "completed"
    assert result["tools"] == ["appeal_decision", "finish", "invoke_operation", "observe"]
    assert result["driver"] == "scripted_prediction_mcp"
    assert fixture.record_count == 1 and fixture.appealed and fixture.lost
    records = [payload for _, payload in fixture.calls if payload.get("operation") == "record"]
    assert len(records) == 2 and records[0] == records[1]
    assert TOKEN not in json.dumps(result)


@pytest.mark.parametrize("status", [None, 200, 403, 503])
def test_mcp_expected_tool_error_preserves_classification_and_redacts_detail(status):
    from mcp.server.mcpserver.exceptions import ToolError

    from genlayer_agent_lab.client import LabError
    from genlayer_agent_lab.mcp_server import _scoped_workflow_call

    def failed():
        raise LabError("Provider body containing " + TOKEN, status)

    with pytest.raises(ToolError) as caught:
        _scoped_workflow_call(failed)
    assert TOKEN not in str(caught.value)
    assert (f"Lab HTTP {status}:" if status is not None else "Lab connection failed (") in str(caught.value)


def test_mcp_project_results_preserve_large_fees_and_escape_domain_tags():
    from genlayer_agent_lab.mcp_server import _workflow_tool_result
    from genlayer_agent_lab.project_wire import decode_project_wire

    original = {"run_id": RUN_ID, "profile": "project", "fee": 2**128 + 1,
                "result": {"$lab_integer": "a-domain-string"}}
    encoded = _workflow_tool_result(original)
    assert encoded["fee"] == {"$lab_integer": str(2**128 + 1)}
    assert decode_project_wire(encoded) == original
    assert decode_project_wire(_workflow_tool_result([original])[0]) == original
