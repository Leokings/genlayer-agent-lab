# Project workflows: agents using GenLayer

The expanded project workflow runs developer-supplied GenLayer contracts in an
owned local Studio environment. It tests how an agent reads their decisions,
waits for finality, uses an appeal, follows permissions and completes supported
downstream actions. The developer controls possible contract model responses;
the Lab records actual local execution and grades agent behavior.

This is the development candidate's integration guide. The current verification
record in [build status](BUILD_STATUS.md) identifies which runtime and client
trials have actually passed. Example availability or a passing synthetic
transport test does not establish actual Studio consensus, fee or bond behavior.

## What runs where

The developer's laptop or VPS hosts the Lab HTTP service, database, dashboard,
private signing journal and Docker-based project Studio stack. The MCP bridge
runs as a local stdio process and forwards scoped operations to the Lab service.
There is no shared public Lab cloud account. Local test balances do not require
buying public-network tokens.

The developer's tested agent can run on the same machine, or connect through an
SSH tunnel. The agent's own model and provider remain the developer's choice.
The included reference agents are scripted examples, so those examples do not
require a model API key. An authoring model is also optional: it helps draft
scenario files, then normal validation and developer review apply.

The existing `service_release` workflow and GLSim scenarios remain separate
compatibility paths. Project workflows use the separately owned fee-enabled
Studio profile, not the legacy Studio database.

## Preparing a laptop or Linux VPS

For the user's short onboarding check, start with [the VPS quickstart](VPS_QUICKSTART.md).

Use the expanded candidate identified in the verification record. Start from its
source checkout or install its released artifact and export the installation kit.
The source path requires Python 3.12 or 3.13, `uv`, Git and Docker with Linux
containers. Node.js 24 is only needed for the TypeScript example. Follow the
[setup skill](../skills/setup-genlayer-agent-lab/SKILL.md) for the platform's
dependency installation and resource checks.

From the candidate's source checkout:

```sh
uv sync --frozen
uv run gl-agent-lab init
uv run gl-agent-lab project studio-build --port 8796
uv run gl-agent-lab project studio-status
```

The first Studio build downloads pinned dependencies and can take several
minutes. A successful command must report readiness; review its diagnostics if
it does not. It does not reuse or erase a legacy Studio database. On subsequent
starts, use `project studio-up` instead of rebuilding.

Keep the Lab service running in another terminal, or install the existing user
startup service according to the setup instructions:

```sh
uv run gl-agent-lab serve --port 8765
```

All commands must refer to the same installation data directory. If you chose a
custom directory, pass the same `--data-dir` to each Lab and Studio command.

On your own computer, open a tunnel to the VPS:

```sh
ssh -N -L 8765:127.0.0.1:8765 your-user@your-vps
```

Then open `http://127.0.0.1:8765/` or the workflow page at
`http://127.0.0.1:8765/assets/workflows.html`. If port 8765 is already used on your
computer, choose another local tunnel port, such as
`-L 8875:127.0.0.1:8765`, and browse local port 8875. No public dashboard or Studio
RPC port needs opening. Keep the SSH session alive while using the tunnel.

An agent inside a separate container needs an explicit route to the host Lab;
its own `127.0.0.1` points inside that container. MCP installation cannot change
hardcoded wallet/RPC calls or automatically connect unrelated container networks.

## Creating a reviewed run

The [scenario authoring guide](SCENARIO_AUTHORING.md) explains the complete schema,
rules, optional model prompt and review process. A terminal example:

```sh
uv run gl-agent-lab project template examples/projects/prediction/project.yaml --mode finalize --output prediction.draft.json
uv run gl-agent-lab project validate prediction.draft.json
```

Inspect the draft's task, permissions and expected behavior. The validation
output includes the digest for that exact content. Record the developer's
review, then create a persistent run:

```sh
uv run gl-agent-lab project approve prediction.draft.json --reviewer "Developer name" --expected-sha256 <reviewed-digest> --output prediction.approved.json
uv run gl-agent-lab project create prediction.approved.json --url http://127.0.0.1:8765 --show-agent-token
```

The creation result supplies a `run_id` and scoped `agent_token`. The tested
agent needs those values and the Lab URL. It must not receive the administrator
credential, private fixtures or saved grading expectations. Creation and report
commands use the installation's administrator credential; the example agent
processes use the run credential.

## Connecting an agent

The four run-scoped capabilities are observe, invoke a declared operation,
appeal an identified decision and finish. Invocation accepts the operation
alias, its typed argument object, a stable idempotency key and, where applicable,
the current decision identity. Reads and writes resolve through the project's
binding; the agent never supplies an arbitrary deployed address to bypass it.

Use a fresh run for each agent trial. In the agent's terminal on a Linux VPS:

```sh
export LAB_URL=http://127.0.0.1:8765
export LAB_RUN_ID=<run-id>
export LAB_TOKEN=<that-run-agent-token>
```

In PowerShell, use `$env:LAB_URL = "http://127.0.0.1:8765"`,
`$env:LAB_RUN_ID = "<run-id>"` and `$env:LAB_TOKEN = "<that-run-agent-token>"`.
These variables belong in the agent process's environment; avoid putting the
credential into a shared prompt, report or public repository.

| Connection | Example command | Reference behavior |
|---|---|---|
| Python over HTTP | `uv run python examples/python/project_agent.py` | Prediction decision, appeals, finality and supported audit-message recovery |
| TypeScript over HTTP | `node examples/typescript/project-agent.ts` | Two-contract prediction decision, appeals, finality and recording |
| MCP stdio | `uv run python examples/mcp_project_agent.py` | Python reference behavior through the same four MCP tools |

The TypeScript reference deliberately covers the two-contract example. Its
HTTP client can invoke all supported declared operations; developers provide
their own behavior for other projects. Python and MCP share the reference policy
while exercising different transports.

For a deliberately faulty agent, use Python/MCP `--unsafe` or TypeScript `unsafe`.
It attempts an early record; the Lab should report the policy violation even if
the contract prevented an effect. A reference agent returning `completed` means
the run reached its terminal status. Retrieve the independent administrator
report to determine pass, fail or inconclusive.

An existing agent's adapter redirects its relevant contract and action tools to
these Lab calls. It keeps its own reasoning and model. The provided clients do
not require OpenClaw or another specific framework. Agents with hardcoded live
RPC/wallet calls need their developer to make those calls configurable.

For a general MCP-compatible agent, configure `gl-agent-lab-mcp` with:

```json
{
  "command": "gl-agent-lab-mcp",
  "env": {
    "LAB_MODE": "workflow",
    "LAB_ROLE": "agent",
    "LAB_URL": "http://127.0.0.1:8765",
    "LAB_RUN_ID": "<run-id>",
    "LAB_TOKEN": "<that-run-agent-token>"
  }
}
```

The bridge exposes exactly `observe`, `invoke_operation`, `appeal_decision` and
`finish`; tool arguments cannot select another run or retrieve administrator
reports. `read_evidence`, `inspect_fees` and `inspect_appeal` are permitted
operation aliases inside `invoke_operation`, as listed by the scenario policy.

## Included project paths

The two-contract prediction project provides `finalize`, `appeal_changed` and
`appeal_upheld` templates. The upheld template supplies exactly the same initial
and appeal model response; the observed Studio appeal round must establish that
the decision was upheld. Changing an unrelated response field would exercise a
different recomputation path and is not the same proof.

The three-contract message example adds an audit contract. The recorder emits
an actual child message to it. The developer can use the delivered profile or
the private controlled-rejection profile:

```sh
uv run gl-agent-lab project template examples/projects/prediction-messages/project.yaml --mode delivered --output delivered.draft.json
uv run gl-agent-lab project template examples/projects/prediction-messages/project-repair.yaml --mode repair --output repair.draft.json
```

Both expose the same visible recovery policy. The agent authorizes the recorder,
records once, observes the child's actual execution and inspects audit state.
It may repair only after a settled child failure and a missing audit. It must
not replay the successful parent record to recover the child effect. The report
checks the distinct contract states, child outcomes and resulting counts.

This is a defined contract graph with implemented methods. It does not reproduce
every protocol's state, dependencies or downstream behavior from an address.

The [investigation examples](INVESTIGATION.md) add supplied records with explicit
source, subject, age and availability data. Agents read those records, submit
concise findings with `submit_investigation`, and follow the reviewed policy for
acceptance, appeal or requesting review. Missing, stale, conflicting and
misleading evidence have distinct expected behavior. The existing operation
invocation carries this capability through HTTP, language clients and MCP; it
does not add another MCP tool. The submitted findings appear in the dashboard
and report as agent-authored claims with independent evaluation. This Lab
artifact does not upload evidence to a GenLayer appeal.

## Reports, recovery and the VPS trial

Use an administrator terminal, without the agent's `LAB_TOKEN` override:

```sh
uv run gl-agent-lab project status <run-id> --url http://127.0.0.1:8765
uv run gl-agent-lab project report <run-id> --url http://127.0.0.1:8765 --output project-report.json
```

Reports retain observed operation results, finality, contract identities, state,
rule checks and runtime provenance. Fee deposits/reservations are distinguished
from actual settled costs, refunds and appeal outcomes. Large integer amounts
use an explicit lossless wire format; the supplied language clients decode it.
Ordinary numeric-looking domain strings remain strings.

The Lab journals submission intent before sending signed transactions. Recovery
after a Lab interruption keeps the original transaction identity and reconciles
Studio's observed state. A lost response may resend identical signed bytes; it
must not create a new logical action. Recovery covers a Lab interruption with
Studio state intact. It does not restore a deleted Studio database or prove
full-machine reboot behavior.

If cleanup times out while a submitted transaction is unsettled, the run stays
inconclusive and blocks new project runs. Restarting the Lab retries cleanup
when the original encrypted signing journal and fixture state are present. It
verifies the same backend and deployed code, reconciles existing transaction
identities and restores fixtures after settlement. It does not resume the failed
agent task, deploy another contract or accept new actions. Successful cleanup
removes the block while preserving the original inconclusive result and error.
Portable restored histories without the original signing material cannot use
this recovery path.

The main consolidated verifier is an administrator operation:

```sh
uv run gl-agent-lab project verify --case all --timeout 900 --url http://127.0.0.1:8765 --output project-verification.json
```

It runs selected cases on actual local Studio and saves full reports when an
output path is supplied. These commands describe how to run verification; the
build-status evidence records which trials have passed. The user's later VPS
pilot should use that same completed candidate for a short install, dashboard,
agent-connection and report check, after consolidated verification is complete.
