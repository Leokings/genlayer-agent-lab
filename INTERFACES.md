# Internal implementation agreement

Standalone fresh code. No production accounts or real fund transfers. Python >=3.12.

Root owns models.py, scenarios.py, store.py, engine.py and tests/test_engine.py.

## Runtime boundary

`runtime.evaluate(evidence: str, fixture_verdict: str, *, timeout: float = 60) -> dict`

Runs a bundled, pinned intelligent contract in a fresh GLSim process. Output contains `verdict` (`approve`/`deny`) and `provenance` (backend, package/runner versions, contract hash, actual execution/consensus metadata, mocked IO flag). Raises RuntimeError on infrastructure errors. No fallback to pretend execution. Fixture verdict is distinct from expected grade. Native runtime only loads our bundled trusted contract. Expose `runtime.doctor() -> dict`.

## Engine boundary

`Engine(data_dir: Path, evaluator=None)` owns SQLite and worker queue; `close()` stops it. Default evaluator is runtime.evaluate. Optional evaluator injection is for unit tests only.

- `list_scenarios() -> list[dict]`: public scenario summaries, no grading keys.
- `create_run(scenario_id: str, agent: str='external', backend: str='glsim') -> dict`: returns run_id, agent_token, status. Agent names: external, safe, unsafe, refuse. Enqueues preparation and optionally drives a scripted reference agent.
- `list_runs() -> list[dict]`: admin summaries; never tokens.
- `get_run(run_id: str) -> dict`: admin status, current public observation, no token.
- `observe(run_id: str) -> dict`: agent task, tick, world, decision if requested; advances scripted timeline one tick, bounded.
- `request_decision(run_id: str, idempotency_key: str) -> dict`: returns current decision (independently calculated contract verdict plus explicitly scripted lifecycle).
- `read_decision(run_id: str) -> dict`: current decision, does not advance time.
- `act(run_id: str, action: dict) -> dict`: `operation`, `resource_id`, `policy_version`, `decision_id`, `revision`, `amount`, `idempotency_key`. Exact retry returns original effect/result; conflicting reuse rejected.
- `finish(run_id: str) -> dict`: grade based on actual trace/effects, not agent claims.
- `cancel_run(run_id: str) -> dict`: terminal inconclusive with evidence preserved.
- `report(run_id: str) -> dict`: report with schema_version, run_id, scenario, agent, status, verdict, grades {decision,behavior,outcome,completion}, findings list, events list, manifest, world. Each grade is {status: pass/fail/inconclusive, detail: str}.
- `authenticate_agent(run_id: str, token: str) -> bool`.
- `import_scenario(path: Path) -> dict`: local admin-only YAML import.

Run statuses queued/preparing/running/completed/cancelled/interrupted/inconclusive. Only one active run. External agent waits until running; 10 minute deadline; 100 tool calls. Restart marks abandoned runs interrupted. Agent credentials cannot call admin tools.

## Public HTTP paths

Loopback server, admin Bearer token loaded from `<data_dir>/admin.token`. No unauthenticated reports. GET /health is minimal. `/` serves dashboard assets with no secrets; UI prompts for token and stores it in sessionStorage.

- GET /v1/scenarios; GET/POST /v1/runs; GET /v1/runs/{id}; POST /v1/runs/{id}/cancel; GET /v1/runs/{id}/report (admin).
- POST /v1/runs/{id}/observe; POST .../decision {idempotency_key}; GET .../decision; POST .../actions; POST .../finish (agent token only).
- GET /v1/runs/{id}/report?format=json|html|junit supports local export; compare may be client side.

POST /v1/runs body {scenario_id, agent='external', backend='glsim'}. API never accepts a filesystem path or arbitrary executable.

## Client/MCP

Python `LabClient(base_url, token)` exposes API operations. TypeScript standalone client same. MCP thin httpx bridge configured LAB_URL, LAB_TOKEN, LAB_ROLE=admin|agent and LAB_RUN_ID for agent role. Agent role lacks admin tools. No OpenClaw dependency. Only loopback URL by default. HTTP responses and stored trace are authoritative, not printed model output.

## Work allocation

Runtime worker owns runtime/ (including bundled contracts) and tests/test_runtime.py.
API/CLI worker owns api.py, cli.py, reports.py and tests/test_api.py, tests/test_cli.py.
Client/UI worker owns client.py, mcp_server.py, assets/, examples/, skills/, tests/test_client.py, tests/test_mcp.py.
Root owns engine, scenarios, models, store, packaging metadata, documentation, CI and integration.
