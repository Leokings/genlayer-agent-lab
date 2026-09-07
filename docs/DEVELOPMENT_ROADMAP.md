# Development roadmap: agents using GenLayer

Recorded September 7, 2026, following the user's scope and runtime decisions.
This is the current improvement plan. It supersedes the research document's
earlier proposal to implement scripted appeal outcomes first and add Studio
integration later. The first `service_release` implementation is now available
in the development candidate; [Studio workflow documentation](STUDIO_WORKFLOWS.md)
describes its actual scope. The remaining items below are gates, not automatic
claims of completion.

The immediate validation target is an expanded Studio workflow installed on the
user's Windows laptop, followed by a Linux VPS. These are two pilot environments;
if the user operates both, they are one participating developer. See the
[pilot checklist](PILOT_TESTING.md). Independent external onboarding remains a
separate later validation activity.

## Product purpose and selected approach

GenLayer Agent Lab is a self-hosted developer tool for testing how an agent reads
intelligent-contract decisions, reacts to changes, uses supported GenLayer
operations and completes an authorized workflow.

The developer supplies possible model responses, the contract interface, scenario
conditions and expected agent behavior. Local GenLayer Studio executes the
supported contract and processes its transactions and appeals. The Lab connects
the agent, controls test inputs, records observations and evaluates behavior.

The test target is the agent. Evaluating whether a contract's LLM understood
evidence correctly is outside this product scope. An investigating agent can
still be tested against supplied evidence and a developer-defined action policy.

**Local Studio is the primary backend for the expanded GenLayer-function and
appeal workflow.** GLSim remains useful for existing fast checks. Scripted
responses can accurately exercise the situations encoded in a unit test, but
cannot establish that an agent correctly integrates with Studio's actual appeal
processing. No scripted result should stand in for missing Studio evidence.

Studio is itself a development environment. A passing local trial establishes
behavior on its pinned profile, not every public-network deployment, economic
rule or failure mode. Those limits must appear in reports.

## How a developer would use the expanded product

1. Install the Lab on a laptop or VPS using the setup instructions, optionally
   assisted by a coding agent and setup skill.
2. Run the compatibility and resource checks, start the owned Studio stack and
   verify the required backend capabilities.
3. Select an example or supply a supported contract, its binding and scenarios.
4. Connect the agent being tested through HTTP, a language client or MCP. Redirect
   its relevant contract/action tools to the test environment.
5. Start a run. The Lab initializes isolated test state and installs controlled
   responses. The agent sees only its permitted observations, rules and tools.
6. Studio processes the operations the agent actually submits. The Lab monitors
   the real local lifecycle while the agent continues reading and acting.
7. Inspect the dashboard/report, fix the agent or integration and rerun.

A coding agent is an installation convenience; the agent being tested can be any
compatible tool-using agent. OpenClaw is an optional client example. Installing
MCP alone does not redirect hardcoded wallet or network calls.

The Lab service, database, dashboard, MCP bridge, Studio containers and tested
agent can share the developer's VPS. The agent can also connect from elsewhere
through a deliberately configured secure connection. The existing MCP bridge
uses stdio and forwards to the Lab HTTP API; it is not a shared public endpoint.
The dashboard is served by the VPS and viewed from the developer's browser,
using an SSH tunnel with the current loopback-only setup. Studio requires Docker
with Linux containers and adequate resources. An agent may use its own external
model provider; no centrally operated Lab server or public Studionet connection
is required for this development profile.

## Baseline before this extension

The alpha 8 baseline has HTTP, Python, TypeScript and MCP connections; a dashboard
and reports; 18 scenarios; controlled contract model replies; single-file contract
bindings; GLSim execution; and optional owned local Studio execution.

In that baseline, the developer command `studio verify --appeal` observed a completed local
appeal that upheld approval. It does not test an agent choosing to appeal, and it
does not demonstrate an overturned decision. Ordinary Studio scenario preparation
obtains a finalized result before giving the agent control. Results
are reduced to approve/deny. The alpha 9 `service_release` workflow adds a
separate incremental path that removes these restrictions for its supported
profile; the original scenario engine remains available.

Evidence: [build status](BUILD_STATUS.md), [Studio setup and boundaries](STUDIO.md),
[verification record](VERIFICATION.md),
[Studio preparation](../src/genlayer_agent_lab/runtime/studio_evaluator.py),
[result mapping](../src/genlayer_agent_lab/bindings.py).

## Improvements to build

### 1. Let agents participate before finalization

Replace finalize-then-return preparation with an incremental run session. It
submits an operation, exposes the observed decision when available, lets the
agent act during the appropriate window, then continues monitoring.

Track contract, transaction, decision and appeal-round identities as supported by
the pinned backend. Keep submission acknowledgment, execution success, application
result and finality separate. A timeout must preserve the pending operation for
reconciliation. Canceling observation must not be represented as canceling an
already submitted Studio transaction.

This is necessary for appeals and for any agent that must respond before a
decision becomes final. Consensus acceptance alone does not establish successful
execution. [Transaction observation](https://docs.genlayer.com/developers/decentralized-applications/querying-a-transaction).

### 2. Preserve richer contract results

Replace the binary-only result mapping with a versioned, validated result schema.
Preserve the fields the agent actually reads from contract returns or state.
Possible application results include partial approval, an amount, a winning
option, outstanding requirements and an insufficient-evidence outcome. These are
application-defined examples, not universal GenLayer statuses.

Keep the domain result separate from transaction execution and finality. An
unknown field value or unsupported schema should produce an explicit unsupported
or inconclusive result, never an invented approval or denial.

Example: a finalized result authorizes 40 test units. The agent must use the
appropriate operation for 40, rather than treating partial approval as permission
for the original full amount. The contract/output shape and action policy come
from the developer. [Contract interfaces](https://docs.genlayer.com/api-references/genlayer-js/contracts).

### 3. Expose supported GenLayer operations to the agent

Provide run-scoped tools for contract reads, allowed writes, transaction/lifecycle
inspection, appeal eligibility, appeal submission and tracking. Include fee or
bond information only where the selected backend actually supports it.

Resolve targets and permitted operations through the run's binding. Validate
arguments, caller role and limits. Keep signing keys in the backend boundary;
the agent receives scoped capabilities. Preserve structured errors so it can
distinguish a condition requiring another read from a forbidden action.

The same operations must work through HTTP, Python, TypeScript and MCP. Existing
agents need a configurable action boundary or an adapter; framework independence
does not imply automatic interception of every network call.

### 4. Expand bindings to complete workflows

There are three related integration descriptions:

| Description | What it supplies | Responsibility |
|---|---|---|
| Contract/protocol binding | Supported methods, arguments, result fields, state reads and observable effects | We build the format and reference implementations; developers map their contracts |
| Agent adapter | Routes the tested agent's tool calls to the Lab and returns observations in the expected shape | We provide clients/examples; developers adapt their action boundary where needed |
| Scenario | Initial conditions, controlled inputs, available evidence, permissions, timing and evaluation expectations | We provide starter cases; developers add cases for supported workflows |

Begin with one supported contract and several operations: for example, create an
agreement, obtain a decision, read state, use an available remedy and complete the
permitted effect. The binding must identify who holds funds and which operation
releases them. An agent requesting escrow release and an agent sending a separate
payment are different workflows.

New scenarios can reuse existing operations. New operations require an execution
handler or adapter and meaningful checks of their effects; naming a scenario
does not implement those operations. Version and validate all three descriptions.
Provide examples and a validation command so developers can contribute bindings
without editing the Lab core for every contract.

### 5. Use controlled replies with actual Studio appeal processing

Developers supply model-response fixtures appropriate to their contract. The
runtime needs isolated, bounded configuration for relevant initial and appeal
validator executions, including later recomputation when the backend permits it.
The current fixed appeal fixture pool is insufficient for general workflows.

These fixtures influence contract execution; they do not authorize the Lab to
invent transaction statuses or announce a successful appeal. Observe the actual
completed round and any recomputed result. An expected changed decision that did
not occur is a setup/backend discrepancy to investigate, not an outcome to inject
silently into the agent's observations.

The first technical proof must demonstrate the supported appeal path with
controlled responses and actual local observations. Verify an upheld case and a
changed/recomputed case separately. If the chosen version cannot support a
required case, record that limitation and select a compatible implementation
before claiming coverage.

### 6. Test investigation and remedy selection

Allow a case to expose relevant agreements, records and other evidence through
bounded tools. An agent can inspect those facts and follow its developer's policy
for accepting a ruling, correcting work, requesting help or challenging it.

Include a justified denial as well as a case where a challenge is appropriate.
Also include missing, stale, contradictory or misleading evidence. This tests the
agent's information use and subsequent action; it does not require assessing the
contract model's reasoning.

A protocol appeal and new evidence submission are different operations. The
documented appeal API does not accept a general research dossier. Additional
evidence needs a method that the application contract implements. The binding
must expose only the remedy that exists. [Appeal API implementation](https://github.com/genlayerlabs/genlayer-py/blob/v0.19.0-rc.2/genlayer_py/contracts/actions.py#L226).

### 7. Handle timing, permissions, changes and recovery

Appeals require a currently eligible decision. The agent must handle a closed
window, an active appeal, a changed decision or an unavailable operation. A
successful validator appeal can lead to recomputation; it is not an automatic
inversion of the business result. Check the actual current backend state.
[Appeal process](https://docs.genlayer.com/understand-genlayer-protocol/core-concepts/optimistic-democracy/appeal-process).

Apply the developer's limits on allowed actions, spending, repeat attempts and
completion. Use current backend cost information when supported. A profile with
no modern bond accounting cannot certify that accounting; an application-level
test budget must be labeled separately.

Preserve transaction identities across lost replies, agent restarts and Lab
restarts. Reconcile before retrying writes. Define which actions require finality
and verify the associated contract effect. An accepted/finalized execution error
must not be counted as completed work. [Finality](https://docs.genlayer.com/understand-genlayer-protocol/core-concepts/optimistic-democracy/finality).

As workflows expand, track dependent operations and child transactions. A parent
result does not prove every child effect succeeded. Messages emitted on acceptance
may survive an appeal and repeat on re-execution, so reports must not imply an
automatic rollback of all effects. [Intercontract interactions](https://docs.genlayer.com/developers/intelligent-contracts/features/interacting-with-intelligent-contracts).

### 8. Make reports explain agent behavior and test coverage

Show a timeline of contract observations, agent tool calls, appeal rounds and
confirmed effects. Preserve richer response fields and explain each grade using
the developer's rules. Record whether the agent completed the task, exceeded its
authority, acted on stale data, duplicated an operation or chose an unsupported
remedy. Refusing every action should not automatically pass a task requiring work.

Keep the independent expected results outside the agent's observations, while
giving it the task policy and information it is entitled to use. For investigation,
record observable evidence access, artifacts and a concise justification; private
chain-of-thought is unnecessary. Do not use appeal wins or appeal frequency as the
definition of agent quality.

Distinguish actual Studio observations, controlled model replies, injected
transport failures and any simulated external effect. Include backend versions,
binding/scenario identifiers and capability limits for replay and comparison.
Infrastructure failure or an unsupported feature needs a separate outcome from
agent failure. A pass applies to the recorded cases and profile.

## Implementation order and acceptance gates

Testing accompanies each step; it is not postponed until all expansion is done.
Complete phases 1–6 below before beginning the full expanded-workflow pilot.
Existing alpha installation checks may continue while that work is in progress.

| Phase | Deliverable | Completion evidence |
|---|---|---|
| 1. Backend proof | Compatible Studio/GenVM/SDK profile, controlled appeal fixtures and incremental transaction driver | A client observes a prefinal decision, submits an eligible appeal and records the actual completed round; upheld and changed/recomputed cases are demonstrated separately |
| 2. Complete reference workflow | One supported single-file contract with structured decisions and an actual state-changing operation, plus its binding | The contract can authorize 40 test units, the client invokes its permitted release operation, and a Studio state read verifies the expected balances or allocation |
| 3. Agent connection | Run-scoped operations and persistent identity through HTTP, Python, TypeScript and MCP | An independently connected agent client performs the workflow before deadlines; it cannot access unrelated targets or keys; a lost response can be reconciled without a duplicate effect |
| 4. Behavioral coverage | Small required set covering approval, valid denial, partial amounts, appeals, stale state, deadlines and duplicate attempts | Correct and deliberately faulty reference agents produce the intended distinct results using observed Studio evidence; unsupported/setup failures are classified separately |
| 5. Developer experience | Input validation, examples, setup guidance, dashboard timeline and reports | The complete workflow can be configured, connected, diagnosed and repeated; reports distinguish controlled replies, observed Studio events and effects |
| 6. Installable candidate | Versioned wheel, checksums, pinned backend, exported setup kit and a clean-install verification | The candidate runs outside the source checkout in a fresh environment; normal process restart preserves history and permits another run |
| 7. Laptop pilot | Install that candidate on the user's Windows laptop and connect a tool-using agent | Record install/integration effort and observed agent outcomes using the pilot checklist; preserve the existing alpha installation |
| 8. VPS pilot | Install the same candidate on a fresh Linux VPS with Docker and sufficient resources | Verify remote setup, dashboard access, agent connection, reports and ordinary process restart; identify host-specific differences |
| 9. Independent feedback | Another human developer integrates their own agent or supported contract workflow | Record fresh-user feedback and actual integration changes separately from the two same-owner pilots |

Phase 2 is a real change from the current alpha. Its `act` handler changes the
Lab's own simulated balances, including when Studio produced the decision. The
new reference workflow must invoke the contract method and verify state through
Studio. A contract ledger of test units is acceptable for this pilot; it must be
labeled as test accounting rather than a native-token or production payment.

## What to build, reuse and defer

**Build before the expanded pilot:** the incremental Studio lifecycle, richer
results, multi-operation bindings, scoped agent functions, suitable controlled
validator replies, the reference contract workflow, a small meaningful behavior
suite, actionable reports and an installed candidate package.

**Extend the existing foundation:** the HTTP service, Python/TypeScript clients,
MCP bridge, dashboard, database, setup kit, packaging and existing verification
tools. Their present existence does not mean they already expose the new
workflow. GLSim stays available for fast existing checks; both backends need not
be run for every pilot case.

**Do not build for this scope:** contract-LLM judgment evaluation, a second
scripted appeal engine presented as GenLayer appeal processing, a shared hosted
Lab service with accounts/billing, a public MCP hosting service, or an
OpenClaw-only integration. Public Studionet access and real funds are not needed
for the local Studio pilot.

**Defer until a concrete integration needs them:** multi-file contract loading,
additional dependencies, deployed-state import, broad multi-contract orchestration,
exhaustive scenario generation and public-network compatibility. Modern bond and
fee accounting is a separate backend capability: if unsupported in the chosen
profile, report that limit and omit the corresponding coverage claim. A locally
enforced agent budget is not proof of protocol bond settlement.

Broad evidence-investigation libraries and every possible decision shape are
also unnecessary before the first pilot. Include a small supplied-evidence case
if needed to exercise the agent's remedy choice. Expand after observing real
integration needs. Full OS reboot and disaster-recovery testing are separate
checks, not prerequisites for this focused functional pilot.

First backend work should be headless and small, so compatibility problems are
resolved before UI expansion. Existing GLSim cases remain regression checks.
Existing installation trials can proceed concurrently, but do not validate new
features that have not been implemented.

Do not rent the VPS before the candidate is ready. Measure the chosen Studio
profile's resource use and confirm the required Docker environment first. The
first-run dependency downloads and any model used by the tested agent are
separate from the supplied contract-model responses.

The current profile pins Studio v0.121.6, GenVM v0.2.16 and Python SDK 0.16.3. Newer
protocol documentation describes a coordinated release-candidate family. Choose
and verify a coherent profile; do not mix current API assumptions into the old
stack or silently upgrade dependencies. Basic appeal functionality and modern
monetary accounting are separate capability gates.
[Compatibility guide](https://docs.genlayer.com/developers/consensus-v06-migration).

## How this relates to Job 1

The structured-result, multi-operation and protocol-extension improvements are
the relevant first slice of Job 1. They should be developed with the Studio agent
workflow above. Multi-file packaging and additional dependencies follow when a
concrete developer integration needs them; they are not prerequisites for the
first complete single-file example.

The larger deferred job remains recorded in [BACKLOG.md](BACKLOG.md). This roadmap
does not mark it complete or claim that implementation has begun. Work requiring
a developer's actual interface can use a reference example initially; external
validation must still involve a real developer integration before that claim is
made.

The existing count of 18 cases is a starter catalog. Expansion should add the
supported operations and lifecycle behavior needed by a workflow, then cases
that exercise them. Increasing the case count alone is not the release gate.
