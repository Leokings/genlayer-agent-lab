# Agent-driven local Studio workflows

This development extension lets the tested agent participate while a GenLayer
transaction is still provisional. It is separate from the original 18 scenario
tests, whose consumer event schedules remain scripted.

The first supported profile is `service_release`: a contract evaluates evidence,
returns a structured authorization, and maintains a ledger of test units. The
agent can read state, submit evidence, appeal an eligible transaction, and request
a permitted release after finalization. The contract actually executes in local
Studio; a finalized state read checks its resulting ledger. No real tokens move.

## Start and connect

Use the development installation containing these commands. From the repository,
or from the directory produced by `gl-agent-lab kit --output my-kit`:

```sh
gl-agent-lab studio build
gl-agent-lab studio up
gl-agent-lab studio status
gl-agent-lab serve
```

Use the same `--data-dir` on each command if your installation uses a custom
directory. `serve` stays running. In another terminal:

```sh
gl-agent-lab workflow validate examples/workflows/partial-release.json
gl-agent-lab workflow create examples/workflows/partial-release.json \
  --url http://127.0.0.1:8765 --show-agent-token
```

The creation response supplies a run ID and a run-only token. Give these to the
tested agent, with the Lab URL. Keep the administrator token with the developer;
it can create runs and inspect evaluation expectations. A coding agent can help
install the software or connect existing tools, but the tested agent does not
have to be a coding agent or use any particular framework.

Open `/assets/workflows.html` on the Lab's local URL to create a run, copy its
connection settings, inspect decisions and operations, or download its report.
The original dashboard links to this page.

To run the scripted reference checks through the same HTTP boundary:

```sh
gl-agent-lab workflow verify --case partial --url http://127.0.0.1:8765 --output partial-proof.json
gl-agent-lab workflow verify --case all --url http://127.0.0.1:8765 --output workflow-proof.json
```

The full set checks partial and full approval, denial, upheld and overturned
appeals, and an intentionally faulty agent. Each case has a separate bounded
deadline. A successful verification of the faulty case means the Lab detected
the expected failure. It does not mean that agent behaved correctly.

On a VPS, all of this runs in the developer's account. An example browser tunnel:

```sh
ssh -L 8765:127.0.0.1:8765 developer@your-server
```

Then open `http://127.0.0.1:8765/assets/workflows.html` on your laptop. Keep the
Lab and Studio ports bound to loopback. No shared Lab cloud account is required.

## Agent integration

The HTTP API, Python client, TypeScript client and MCP expose the same workflow
operations. For MCP, launch `gl-agent-lab-mcp` with:

```text
LAB_URL=http://127.0.0.1:8765
LAB_MODE=workflow
LAB_ROLE=agent
LAB_RUN_ID=<run ID>
LAB_TOKEN=<run-only token>
```

The agent receives `observe`, `invoke_operation`, `appeal_decision` and `finish`.
`LAB_MODE=scenario` remains the default for the original scenario tools.

This connection uses stdio MCP: the agent launches a local MCP process, which
calls the Lab's HTTP service at `LAB_URL`. The URL identifies the HTTP service,
not a hosted MCP endpoint. For a remote Lab, the local MCP process can use the
loopback SSH tunnel described above.

To exercise the scripted reference policy through this connection, set
`LAB_URL`, `LAB_RUN_ID` and the workflow's run-only `LAB_TOKEN` in the agent's
environment, then run from the repository or exported kit directory:

```sh
python examples/mcp_workflow_agent.py safe --timeout 600 --cleanup-timeout 45
```

The example launches its own stdio subprocess with `LAB_MODE=workflow` and
`LAB_ROLE=agent`, and verifies the four-tool interface before acting. The
positional mode is `safe` by default. Use `appeal` to request one eligible appeal
and wait for the current finalized decision, or `unsafe` to attempt an invalid
release before continuing the safe policy. The agent recovers recorded operation
keys on reconnection and waits for terminal cleanup after `finish`. Its output
contains the agent outcome, run status and verified tool names; the developer
retrieves grading separately with the administrator credential.

| Operation | What the agent does |
|---|---|
| Observe | Read the task, visible policy, finalized state, current transaction and its provisional or final decision |
| Invoke `evaluate` | Submit the scenario evidence, or new evidence through the contract's supported method |
| Invoke `get_state` | Read the current finalized contract state |
| Appeal | Supply the observed decision ID and a unique request key; Studio processes the appeal |
| Invoke `release` | Supply `requested_amount` and the current finalized decision ID |
| Finish | End the agent's work; the Lab drains known transactions and evaluates the result |

Example Python calls using a run-only `LabClient`:

```python
observation = client.workflow_observe(run_id)
client.workflow_invoke(run_id, "evaluate", {}, "evaluate-1")
# Observe until a decision is available. Decide whether an appeal is appropriate.
decision = client.workflow_observe(run_id)["decision"]
client.workflow_appeal(run_id, "appeal-1", decision["decision_id"])
# Observe again; an appeal request is not a finalized replacement decision.
```

A request key identifies one operation. Reuse that exact key and payload when
reconciling a lost response. Changing the payload under an existing key is
rejected. The decision ID identifies the observed proposal and its history; it
is distinct from the transaction hash. The agent must reread it after a change.
Use the durable operations and round history to enforce a limit such as one
appeal. `decision.appealed` reflects the current backend flag and can clear after
an appeal round; it is not a lifetime appeal counter. A completed write may
precede the next observed ledger refresh. Read the resulting state before
finishing, and observe until the run actually becomes terminal after `finish`.

Installing MCP does not intercept hardcoded wallet calls. The developer must
route the relevant tools through this interface. The agent's own reasoning model
and unrelated tools remain under the developer's control.

### Give a model-driven agent a visible task

`policy.allowed_actions` specifies permissions. It does not explain when the
developer wants the agent to challenge a decision. Give that task/remedy policy
to the agent in its normal task instructions as well as configuring the private
scenario. Do not give it the fixtures, administrator credential or grading
expectations. For the supplied service-release interface, an example is:

> Inspect the acceptance record in your workflow observations and request its
> evaluation. If the decision materially contradicts that record, challenge it
> at most once while appeal is available. Track the actual result afterward.
> Release only the currently authorized accepted units after successful finalized
> execution, using the current decision identity. Verify the resulting contract
> state, then finish. Reconcile an uncertain response using the same operation
> key; do not duplicate an effect. Stop and report an infrastructure failure if
> the run becomes inconclusive.

Adapt that policy to the developer's task. Always appealing or always refusing
is not a general policy for every scenario. The model must use the connected
tools; a prompt alone does not add a working Lab connection.

## Supply scenarios and contract bindings

The JSON examples define the resource, policy version, amount and evidence;
initial controlled model reply; optional reply used after the agent appeals;
expected final state and required actions; and a run deadline. A visible
`policy.allowed_actions` can restrict operations. Grading expectations and model
fixtures are not returned to the tested agent.

For partial approval, the supplied response contains both `decision: partial`
and `authorized_amount: 40`. The Lab preserves those fields. It does not reduce
them to a binary approval that could authorize all 100 units.

The model fixture influences actual contract execution. It does not fabricate
Studio statuses or force the reported outcome. An appeal can be rejected, uphold
the result, or lead to a new execution. Reports retain the observed rounds and
the resulting decision separately.

`examples/contracts/service-workflow.yaml` demonstrates the contract binding.
To use a custom binding:

```sh
gl-agent-lab workflow validate my-case.json --binding my-contract.yaml
gl-agent-lab workflow create my-case.json --binding my-contract.yaml \
  --url http://127.0.0.1:8765 --show-agent-token
```

This profile requires three operation roles (`get_state`, `evaluate`, `release`)
and its declared typed state fields. A binding can map those roles to different
public contract method names. The source is snapshotted and hashed; runtime
checks compare the deployed code and method mutability before execution.
Custom source remains a single confined Python file.

More cases can reuse these operations. Supporting a different protocol workflow
requires another profile and meaningful effect checks. The more general binding
format alone does not make every contract compatible with this first profile.

## Backend and recovery boundaries

The owned Studio image pins version `0.121.6`, source commit
`366f085a479bb9e6028ce326c2c13f798a9752c7`, GenVM `v0.2.16`, and SDK `0.16.3`.
Its `validator-config-and-appeal-snapshot-v2` overlay fixes two version-checked
transport omissions: forwarding validator configuration during fixture updates,
and preserving a transaction's contract snapshot when claiming its appeal job.
Consensus decision rules are unchanged. The image label is verified before a
workflow may control fixtures; an older owned image must be rebuilt.

An exclusive lease keeps the same registered validator identities throughout a
workflow. Only their controlled replies change. Normal completion restores the
previous fixture settings after known transactions become terminal. Local model
provider traffic is isolated; no paid contract-model service is required.

The current workflow signer is memory-only. Agent reconnection can reuse the run
credential and stored operation identities while the Lab service is running.
Completed reports survive a Lab restart. An abrupt Lab stop or an ambiguous
submission is recorded honestly as interrupted or inconclusive; the Lab does not
replay an uncertain write. Unresolved cleanup blocks new workflow sessions.
Automatic recovery/resumption of such sessions is not implemented yet. Backups
preserve reports, revoke restored run credentials and exclude Studio volumes.

If preparation reports `owned_fixture_stack_required`, inspect
`gl-agent-lab studio status` for that installation. It must show `ready: true`
with the required owned image. Start an existing stopped stack with `studio up`;
build it first if missing or outdated. An administrator may create a new run
after readiness is restored **only if** the earlier run is terminal, has no
submitted operations/decision and reports `cleanup: restored`. Retain the
earlier infrastructure failure. This is not a recovery procedure for an
ambiguous submission or unresolved cleanup; do not replay those writes.

This profile does not verify public-network economics, modern appeal bonds,
contract-model reasoning quality, arbitrary deployed protocol imports, or
dependent cross-contract transactions. See [pilot readiness](PILOT_TESTING.md)
for the remaining release and developer-trial gates.
