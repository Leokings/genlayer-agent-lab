# Independent agent connections

These are **scripted reference agents**, not demonstrations of a particular AI model's ability. They connect through public HTTP or MCP only; none imports engine internals. Replace their policy loop with your model/framework to evaluate your own agent.

Start the service from the project checkout:

```console
uv run --locked gl-agent-lab init
uv run --locked gl-agent-lab serve
```

In a second terminal, list valid scenario identifiers:

```console
uv run --locked gl-agent-lab --url http://127.0.0.1:8765 scenarios
```

Set `LAB_URL` to the loopback origin and `LAB_TOKEN` to your local administrator token. By default the token is in `~/.genlayer-agent-lab/admin.token`; never commit it. The examples' `--scenario` mode uses that credential to create a run, then switches to the run-scoped token for every agent action.

For a private PowerShell setup, the developer can create an external run without
printing a token or placing the administrator credential in the agent's
environment:

```powershell
$labData = Join-Path $env:USERPROFILE '.genlayer-agent-lab'
$labUrl = 'http://127.0.0.1:8765'
$labHeaders = @{ Authorization = 'Bearer ' + [IO.File]::ReadAllText((Join-Path $labData 'admin.token')).Trim() }
$labRequest = @{ scenario_id = 'escrow-normal'; agent = 'external'; backend = 'glsim' } | ConvertTo-Json
$labRun = Invoke-RestMethod -Method Post -Uri "$labUrl/v1/runs" -Headers $labHeaders -ContentType 'application/json' -Body $labRequest
```

Keep `$labHeaders` in the developer's shell. Configure a separate agent process
with only `$labRun.run_id` and `$labRun.agent_token` using the join-run mode below;
do not print `$labRun` or the token file. Wait for the run to reach `running`
before launching the agent, as described in the connection steps. Use the same
developer headers for `GET /v1/runs/<run_id>` to check preparation.

Run one example with an identifier returned by `scenarios`:

```console
uv run --locked python examples/python_agent.py --scenario escrow-normal
node examples/typescript/agent.ts --scenario escrow-normal
uv run --locked python examples/mcp_agent.py --scenario escrow-normal
```

The TypeScript example needs Node.js 24 and no npm dependencies. If the listed scenario has a different ID, use that ID. The Python and TypeScript examples can also join a previously created run: omit `--scenario` and supply `LAB_RUN_ID` with its run-scoped `LAB_TOKEN`.

## Connecting your own agent

1. Create an external run through `POST /v1/runs` with your administrator credential.
2. Pass only `run_id` and `agent_token` to a separate test profile of your agent.
3. Wait for preparation; observe the task and request a decision.
4. Use the test tools to read the latest decision and attempt actions. Verify resource, policy, revision, finality and execution success. Preserve an action's idempotency key across retries.
5. Call `finish`; retrieve the full report with your administrator client.

Keep the test agent's real wallet and production tools out of that profile. Adding our MCP connection does not automatically replace hardcoded production calls. A custom tool adapter must route the relevant calls into the lab.

`LAB_URL` accepts loopback origins only. For a server, run the agent beside the service or forward its port through SSH. The examples do not send tokens through a public endpoint or follow redirects.

## MCP configuration

Use the installed environment's **absolute** executable path so GUI clients do not depend on your terminal's PATH. The program is `gl-agent-lab-mcp`, or the environment's Python with arguments `-m genlayer_agent_lab.mcp_server`.

Agent connection environment:

```json
{
  "LAB_URL": "http://127.0.0.1:8765",
  "LAB_ROLE": "agent",
  "LAB_RUN_ID": "<run_id returned by the service>",
  "LAB_TOKEN": "<agent_token returned for that run>"
}
```

The agent role exposes only `observe`, `request_decision`, `read_decision`, `act` and `finish`. Run identifiers are bound at startup, not selected by tool arguments.

A separate developer connection may use `LAB_ROLE=admin` with the installation token. It exposes `list_scenarios`, `list_bindings`, `list_runs`, `start_run`, `get_run`, `get_report` and `cancel_run`. Do not give this connection to the tested agent. For custom contracts, `start_run` accepts `backend="container-glsim"` and `binding_id="delivery-assessment"` after that binding has been imported locally.

## Select a custom contract

Follow `docs/CUSTOM_CONTRACTS.md` to build the worker and import a binding. Developer clients can create an external run using:

```python
created = client.create_run("escrow-normal", backend="container-glsim", binding_id="delivery-assessment")
```

```typescript
const created = await client.createRun("escrow-normal", "external", "container-glsim", "delivery-assessment");
```

Both snippets assume an administrator client has already been constructed. Give `created.run_id` and its run-scoped `agent_token` to the existing agent example via `LAB_RUN_ID` and `LAB_TOKEN`. The observation, decision and action tools are unchanged. Explicitly choosing a custom binding with a native or fixture backend is rejected.

The daemon owns test state. Closing an MCP subprocess does not cancel a test; its configured run deadline still applies. Client configuration syntax differs between MCP hosts, so adapt the command/arguments/environment to your host's documented format.

## Use GenVM through local Studio

Prepare your installation's stack using `studio build` and `studio up`, as described
in `docs/STUDIO.md`. Use `backend="studio"` in Python/MCP, or `"studio"` as the
TypeScript backend argument. A binding is optional. The agent still uses the
same run-scoped tools and credentials; preparation can take a few minutes.
Studio produces the contract result, then the agent handles the scenario's
scripted consumer events. Real local appeal processing is checked separately
with `gl-agent-lab studio verify --appeal`.
