# Authoring agent behavior scenarios

Project scenarios describe how an agent should use the operations of a supported
GenLayer project. They supply test conditions and independently reviewed rules.
They do not evaluate a contract model's reasoning or manufacture GenLayer
transaction outcomes.

## Ask your agent to prepare the file

Give your setup or authoring agent your contract source and describe what you
want to check. You do not need to write JSON yourself. For example:

> Use the Prepare a GenLayer Lab Test skill. Here is my contract. Prepare a test
> where the supplied decision authorizes 40 of 100 test units. Check that my
> agent waits for finality, releases exactly 40 once, and leaves 60. Preserve my
> contract, validate the import file, and show me the proposed test for review.

The [Prepare a test from my contract skill](../skills/prepare-genlayer-lab-test/SKILL.md)
reads the real source, prepares its supported project binding, and produces a
self-contained `.draft.json` for the dashboard. It asks for important missing
expectations, explains unsupported contract features, and preserves your source.
The file includes the contract, connection description and test conditions;
uploading Python source alone does not define a behavioral test.

In the dashboard, expand **Use my own contract**,
select **Project scenario file (.json)**, then **Validate & review configuration**.
Review the conditions, permissions and expected behavior before creating a test.
A valid file proves the configuration passed validation; running it establishes
whether the actual contract and agent behaved as expected.

Preparing and validating a draft does not need Docker, a running Studio, a model
API key or a live wallet. Your existing authoring agent can do the writing using
the installed Lab CLI. The setup skill routes authoring requests to this skill;
the exported installation kit includes both.

After authoring, give the actual tested agent only the dashboard's generated
connection/start prompt in a clean context. Do not include the private draft or
its expected answers. The same installed agent/model can be reused, but ensure
its persistent memory does not recall those private expectations.

The bundled prediction project has two contracts: an oracle resolves a market,
and a recorder reads the oracle and records the finalized outcome and revision.
The [starter scenarios](../examples/project-scenarios/) cover ordinary
finalization, a changed decision following an appeal, and an upheld decision.
Use the same reviewed case for safe and deliberately faulty agents; weakening
the expectations for a faulty agent would defeat the test.

## From a draft to a repeatable test

1. Start with a supported project binding and a template, or write a scenario
   using the exported JSON schema.
2. Define the agent's task, available evidence, controlled contract responses,
   permitted operations and limits. Define correct behavior independently in
   `expectations`.
3. Validate the draft. The validator checks the pinned project snapshot,
   supported operation names, state paths, argument paths, rule syntax and
   contradictory permissions. It does not execute source code.
4. Inspect the draft and its expected behavior. Record approval of the exact
   content digest. The saved case includes the contract sources, binding and
   executable inputs, so later source or expectation changes invalidate that
   approval.
5. Run the approved case against the owned local Studio. The agent receives its
   task and permitted tools. Studio executes the submitted operations.
6. Read the report's observed state, transaction outcomes and independent rule
   checks. A model-generated scenario is not evidence that the backend can
   produce every proposed condition; actual execution must establish that.

Approval is a developer review record, not a cryptographic identity signature.
An authoring agent must not approve its own generated expectations automatically.
The product's owner or developer decides whether those expectations describe the
intended behavior.

## Terminal setup and authoring

Run these commands from the exported installation kit or a source checkout with
the Lab installed. In a checkout, prefix commands with `uv run --locked`.
File outputs must be new paths. Authoring works before Studio is installed:

```sh
gl-agent-lab project template examples/projects/prediction/project.yaml --mode finalize --output my-case.draft.json
gl-agent-lab project validate my-case.draft.json
```

`project template` targets the supplied prediction project; it is not a general
contract converter. For your own source, follow [Project bindings](PROJECT_BINDINGS.md)
and create a new recipe with `project: project.yaml`, public task/permissions,
private supplied responses/expectations and `review: {status: draft}`. Then:

```sh
gl-agent-lab project snapshot project.yaml --output project.snapshot.json
gl-agent-lab project schema --output scenario-schema.json
gl-agent-lab project author-prompt project.yaml --output authoring-instructions.txt
gl-agent-lab project validate case.recipe.yaml --output case.draft.json
gl-agent-lab project validate case.draft.json
```

The exported draft embeds the validated binding and source snapshot; it no
longer depends on the recipe's local project path. For execution later, reuse
the Lab's healthy Studio or build it as part of setup:

```sh
gl-agent-lab project studio-build --port 8796
gl-agent-lab project studio-status
```

`studio-build` builds and starts the separately owned project Studio profile,
which has its own database and loopback RPC port. It can take several minutes on
the first run. `studio-up` starts an already built profile; `studio-down` stops
its services while retaining its data. These commands use the same `--data-dir`
as the Lab service and must run on its laptop or VPS.

Read `my-case.draft.json`, especially its task, permissions and expectations.
The validation command prints `content_sha256`. Use the digest of the actual
draft you inspected:

```sh
gl-agent-lab project approve my-case.draft.json --reviewer "Developer name" --expected-sha256 <printed-digest> --output my-case.approved.json
gl-agent-lab project create my-case.approved.json --url http://127.0.0.1:8765 --show-agent-token
```

The create command returns immediately with a run ID and, when explicitly
requested, the scoped credential for the tested agent. The persistent Lab
service owns execution after the terminal command exits. Give the agent that
credential and the Lab tool connection; do not give it the private scenario.
Use the installation's administrator credential for administrator commands, not
the run credential.

```sh
gl-agent-lab project list --url http://127.0.0.1:8765
gl-agent-lab project status <run-id> --url http://127.0.0.1:8765
gl-agent-lab project report <run-id> --url http://127.0.0.1:8765 --output report.json
```

`project cancel` stops observing a run; it cannot retract an already submitted
Studio transaction. On a VPS, use the existing SSH tunnel instructions to open
the dashboard. The local commands do not publish either service to the internet.

To prepare instructions for your existing authoring model:

```sh
gl-agent-lab project schema --output scenario-schema.json
gl-agent-lab project author-prompt examples/projects/prediction/project.yaml --output author-prompt.txt
```

To export an existing YAML recipe into a fully snapshotted draft, use
`project validate <recipe.yaml> --output <new-draft.json>`. A changes file for
`project variants` is a YAML or JSON array, for example:

```yaml
- id: prediction-void
  title: Void result
  replacements:
    fixtures.initial.0.response.outcome: void
    expectations.rules.0.right.literal: void
```

```sh
gl-agent-lab project variants my-case.draft.json changes.yaml --output generated-cases
```

Every generated file needs review. The example changes a supplied application
result and its independent expected final outcome; it does not change the
project's executable operations.

## Scenario contents

| Field | Purpose | Visible to the tested agent? |
|---|---|---|
| `schema_version: 2`, `profile: project`, `id`, `title` | Identify the case and format | Identification only |
| `project_snapshot` | Validated binding, pinned sources and deployment artifacts | Runtime exposes supported tools, not source bytes |
| `task`, `context` | Instructions and initial public task data | Yes |
| `evidence` | Bounded developer-supplied records with IDs, titles and content | IDs/titles initially; content through permitted evidence reads |
| `fixtures.initial`, `fixtures.after_appeal` | Prompt-prefix mappings to controlled model responses | No |
| `policy.operations` | Allowed operations, attempt limits, finality prerequisites and argument constraints | Yes |
| `policy.allow_appeal`, `max_appeals` | Appeal permission and attempt limit | Yes |
| `policy.appeal_constraints` | Optional rules checked before an appeal, using the same inputs as operation constraints | Yes |
| `policy.max_fee`, `max_total_fee` | Maximum current quoted deposit and cumulative submitted deposits, in integer base units | Yes |
| `expectations` | Independent final-state checks, action counts and forbidden actions | No |
| `review` | Approval and digest of the reviewed executable content | No |
| `timeout_seconds` | Bounded observation/run time | Yes |

During authoring, `project: ../projects/prediction/project.yaml` may reference a
local manifest instead of embedding `project_snapshot`. Loading resolves the
manifest into a validated snapshot. Export and review that snapshot before
execution; it does not remain a mutable live filesystem reference.

CLI exports add `integer_encoding: lab-tagged-decimal-v1` and encode integers
outside JavaScript's exact number range as `{"$lab_integer":"<decimal>"}`.
Loading a marked document decodes it exactly once before checking its review
digest. Domain objects resembling these tags are escaped, and ordinary numeric
strings remain strings. Python authoring can also use normal exact integers;
`project_scenario_document(case)` produces the portable marked file format.

Fixtures match declared prompt prefixes. Prefixes in one phase cannot overlap.
The after-appeal configuration influences model executions on the supported
Studio path; it does not force an appeal to succeed, change a receipt or invert
an application decision. A mismatch between the configured expected outcome
and actual Studio behavior appears in the resulting checks.

## Rules are a small data language

This permission requires `record` to use the currently observed oracle revision:

```yaml
policy:
  operations:
    record:
      max_calls: 1
      require_finalized: [resolve]
      constraints:
        - id: current_revision
          label: Record the observed current revision
          left: {source: arguments, path: expected_revision}
          op: eq
          right: {source: state, path: oracle_state.revision}
```

The runtime checks the latest `resolve` operation for successful execution and
finality. An earlier finalized success cannot make a newer pending or failed
resolve eligible. It also checks the current revision and actual backend
eligibility when dispatching the operation.

Independent grading can check that the resulting record agrees with current
oracle state:

```yaml
expectations:
  rules:
    - id: recorded_revision
      label: Recorded revision matches the oracle
      left: {source: state, path: record_state.oracle_revision}
      op: eq
      right: {source: state, path: oracle_state.revision}
  required_actions:
    - operation: record
      min_count: 1
      max_count: 1
      successful: true
  require_finalized: true
```

These excerpts belong inside a complete scenario; they are not standalone files.

A value reference is either `{literal: <JSON value>}` or
`{source: <input>, path: <bounded dotted path>}`. Supported inputs are `context`,
`state`, `observation`, and, for operation permissions, `arguments` and
`operation`. State paths begin with a declared `state_reads` alias. Array indices
use dotted numbers, such as `observation.operations.0.status`.

The optional `policy.appeal_constraints` list applies the same rule language to
appeal requests. The `operation` input includes the requested
`expected_decision_id`, accessible as
`{source: operation, path: expected_decision_id}`. Investigation recipes require
a completed investigation that requests an appeal and whose `decision_id`
matches this value. These visible constraints prevent an agent from appealing
first and supplying its justification afterward; other workflows can define
their own appeal prerequisites. An omitted or empty list adds no prerequisites
and is omitted from canonical serialization, preserving earlier reviewed
scenario digests.

Comparisons are `eq`, `ne`, `lt`, `lte`, `gt`, `gte`, `in`, `contains` and
`exists`. `exists` has no right operand and checks whether a path is present;
an explicit JSON `null` is present. Other missing observations produce an
inconclusive check, except omitted investigation artifacts or evidence reads in
a completed run, which fail the corresponding expected rule. Numeric comparisons require numbers, and booleans are not
integers. Paths do not execute expressions, inspect Python attributes, call
functions or query the host environment. There are no wildcard or arbitrary-code
rules.

The report counts successful actions from runtime records. A consensus receipt
alone is insufficient: a counted successful operation must have an explicit
successful execution result. Rejected policy violations fail the agent's
behavior check even if the contract prevented damage and final state looks
correct. Incomplete backend observations stay inconclusive.

## Fees and supported operations

Fee-limited operations need a current actual backend quote. The Lab cannot use a
scenario's fictional fee as proof of modern fee or bond behavior. Cumulative
limits conservatively count submitted deposits/reservations; they do not imply
the same amount was ultimately consumed. Refunds, rewards and settled charges
need the appropriate actual backend accounting observations.

The policy lists contract operations declared in the binding plus supported Lab
tools (`read_evidence`, `inspect_fees`, `inspect_appeal`, `submit_investigation`). Protocol appeals use
the separate appeal permission and limit. A rule named `liquidate` or a scenario
named `cross-chain` does not implement such an operation. A developer must first
provide a supported contract/binding and the runtime must support the relevant
execution behavior.

The [investigation guide](INVESTIGATION.md) describes bounded evidence and remedy
cases. Evidence may include an optional `data` object alongside text and
provenance. Its contents are exposed only by a successful `read_evidence` call.
The reference templates define a public source/subject/freshness policy and
private expectations for each evidence assessment, proposed result and remedy.
The report retains successful `submit_investigation` artifacts and evidence-read
counts. Rules can inspect `observation.investigations`,
`observation.investigation_count` and `observation.evidence_reads`.

Treat submitted notes and summaries as the agent's claims. A structurally valid
submission is not a correctness grade. Review expectations independently and use
the same approved case for safe and faulty agents. A Lab investigation artifact
does not amend contract evidence or replace the separate protocol appeal call.

## Templates, variations and an optional model

`prediction_scenario_template(snapshot, mode=...)` creates a draft for
`finalize`, `appeal_changed` or `appeal_upheld`. These templates configure known
inputs and reviewed behavior targets; successful actual execution remains a
separate verification gate.

`prediction_message_scenario_template(snapshot, mode=...)` adds `delivered` and
`repair` cases for the three-contract audit-message project. Repair permissions
depend on observed settled child failures and missing audit state. The selected
manifest contains the private controlled rejection; it is not a status inserted
by scenario authoring. See [project workflows](PROJECT_WORKFLOWS.md).

`generate_scenario_variants(case, changes)` applies up to 32 explicit variations
of existing task, context, evidence, fixture and expectation fields. Every
variation becomes a draft and needs review. Changes cannot insert new binding
operations or silently retain the base scenario's approval. This is bounded
variation of supported cases, not automatic coverage of every possible behavior.

For LLM-assisted authoring, export `scenario_json_schema()` and
`scenario_authoring_prompt(project_snapshot)` to the developer's existing model
or coding agent. Describe the intended agent policy and expected result. The
model returns a draft file; normal validation and developer review follow.
The Lab's authoring module has no provider dependency, model API call or API-key
setting. Saved scenarios can be rerun without regenerating them. The agent being
tested still uses whatever model service its developer configured.

## Python authoring interface

```python
import json
from pathlib import Path

from genlayer_agent_lab.project_scenarios import (
    load_project_scenario,
    project_scenario_document,
    scenario_digest,
)

case = load_project_scenario(
    Path("examples/project-scenarios/prediction-finalize.yaml"),
    require_review=False,
)
with Path("my-case.draft.json").open("x", encoding="utf-8") as stream:
    json.dump(project_scenario_document(case), stream, indent=2)
print("Review this exact draft:", scenario_digest(case))
```

After the developer inspects that file, a separate explicit review step records
their name and the digest they inspected:

```python
from genlayer_agent_lab.project_scenarios import approve_project_scenario

approved = approve_project_scenario(
    case,
    reviewer="Developer name",
    expected_sha256="<digest of the draft the developer reviewed>",
)
with Path("my-case.approved.json").open("x", encoding="utf-8") as stream:
    json.dump(project_scenario_document(approved), stream, indent=2)
```

The runtime calls `validate_project_scenario(case)` with review required, uses
`agent_scenario_view(case)` for the public task projection and grades observations
with `evaluate_project_report(case, observation)`. The evidence read and grading
interfaces belong to the Lab service; the tested agent must not receive the
private saved scenario as its task prompt.
