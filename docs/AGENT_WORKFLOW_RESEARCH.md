# Research: agent investigation, appeals and decision workflows

Research date: September 7, 2026. Scope: GenLayer Agent Lab alpha 8.

This is a research and design review, not an implementation or a new completion
claim. Job 1 remains deferred with its existing scope. No contracts, transactions,
paid model calls, installations or CI runs were started for this review.

## What the product should be able to test

An agent can do more than obey a favorable or unfavorable decision. It can check
the evidence, decide whether a challenge is justified, choose an available remedy,
respect its owner's permissions and budget, and follow the result through to an
authorized action. That is a useful expansion of the Lab's agent-testing purpose.

For example, an agent manages a service agreement. The intelligent contract
rejects a deliverable. The agent checks the agreement and delivery records. If
those records support the rejection, accepting it or correcting the work may be
right. If the ruling appears inconsistent with the applicable evidence, the
agent may challenge an eligible transaction. If the necessary remedy is to submit
new evidence, it needs a contract operation that actually supports that remedy.

The evaluator must judge those choices using a case-specific rubric. Appealing
every rejection, winning an appeal, and agreeing with the contract are each
insufficient definitions of success.

## What GenLayer documents, and the boundaries

### Protocol appeals and application disputes are separate

GenLayer documents permissionless appeals of eligible decided transactions before
finalization. Validator appeals address `Accepted` or `ValidatorsTimeout` and
recheck the original leader proposal. Leader appeals address `Undetermined` or
`LeaderTimeout` and start another proposal round. A successful validator appeal
leads to recomputation; it does not simply turn an application denial into an
approval. Windows and their adjustment rules are configurable.
[Official appeal process](https://docs.genlayer.com/understand-genlayer-protocol/core-concepts/optimistic-democracy/appeal-process).

The appeal API has transaction, account, value and decision-identity parameters;
it has no general argument for the agent's research dossier. Private investigation
can inform the agent's choice, but is not automatically transmitted as validator
evidence. An application may separately provide `submit_evidence`, a dispute
method, or a new evaluation transaction. Its binding must describe which operation
exists and when it is valid. These example method names are proposed interfaces,
not universal GenLayer methods.
[Versioned Python appeal implementation](https://github.com/genlayerlabs/genlayer-py/blob/v0.19.0-rc.2/genlayer_py/contracts/actions.py#L226).

The documented newer charge includes a bond and work funding. The high-level SDK
can quote the active decision, include its identity in the submission and fund the
next round. An agent should refresh eligibility and its quote close to submission,
then apply its owner's spending limit. Protocol permission to appeal does not
itself authorize an agent to spend its owner's funds. Bond outcomes and consumed
work funding require separate accounting; do not hardcode a universal fee or
profit assumption.
[Versioned charge and eligibility implementation](https://github.com/genlayerlabs/genlayer-py/blob/v0.19.0-rc.2/genlayer_py/contracts/actions.py#L393),
[appeal accounting](https://docs.genlayer.com/understand-genlayer-protocol/core-concepts/optimistic-democracy/appeal-process).

### Submission, consensus, business result and finality differ

A successful submission does not establish successful intelligent-contract
execution. A consensus status such as `Accepted` is also separate from whether
execution returned successfully and what the application decided. Persist the
GenLayer transaction identity and resume observation after a timeout or restart.
Do not submit another write merely because the observer stopped receiving replies.
[Querying a transaction](https://docs.genlayer.com/developers/decentralized-applications/querying-a-transaction).

`Finalized` transactions cannot use the ordinary protocol appeal path. A deadline
expiring and on-chain finalization occurring are distinct events. Accepted
execution can still contain an error, and an appeal can cause dependent nonfinal
transactions to be recomputed. Our design should record execution success,
application result and finality separately; any irreversible settlement policy
must check all the relevant conditions.
[Finality](https://docs.genlayer.com/understand-genlayer-protocol/core-concepts/optimistic-democracy/finality).

Contract outputs are application-defined. A denial, partial award, winning option,
score or insufficient-evidence result can be useful domain outputs; these are
examples, not standard protocol statuses. The SDK's decoded return values and
contract schema support richer application interfaces. The Lab currently reduces
its supported result to approve/deny, so preserving richer meaning requires code
and binding changes.
[Contract API](https://docs.genlayer.com/api-references/genlayer-js/contracts).

### Effects can outlive a changed decision

GenLayer intercontract writes are asynchronous. A successful parent transaction
does not establish that a child operation completed. Messages emitted on
acceptance cannot be recalled after an appeal and can be emitted again during
re-execution. Receivers need duplicate handling, or the application can choose
finalization-triggered messages. A Lab scenario must not pretend an appeal rolls
back an external payment already performed.
[Intercontract interactions](https://docs.genlayer.com/developers/intelligent-contracts/features/interacting-with-intelligent-contracts).

If an escrow contract holds funds, the agent may request release; the contract
enforces release conditions. If an agent controls a separate payment tool, that
tool performs a different effect. A protocol binding must explicitly identify
which actor holds value and which operation moves it. This is a proposed modeling
requirement, not a claim that all GenLayer applications use either arrangement.

### Evidence needs its own test environment

Evidence cases should include authoritative records, incomplete records, stale
pages, genuine corrections and conflicting sources. GenLayer recommends stable,
decision-relevant data extraction; cosmetic changes should not be confused with
changes to the underlying facts.
[Prompt and data techniques](https://docs.genlayer.com/developers/intelligent-contracts/crafting-prompts).

Public documents can also contain instructions intended to manipulate a model.
Test whether an investigating agent treats those instructions as untrusted source
content and preserves its task and permissions. The official guidance recommends
constraints on contract inputs, outputs and logic; consensus alone is not evidence
of prompt-injection resistance.
[Prompt injection guidance](https://docs.genlayer.com/developers/intelligent-contracts/security-and-best-practices/prompt-injection).

## Current build: evidence and gaps

These findings come from a read-only code review and the existing verification
record. No new runtime verification was performed.

| Area | Implemented or previously observed | Gap for the proposed workflow |
|---|---|---|
| Agent connection | HTTP, Python, TypeScript and MCP share the engine | Additional actions must reach all interfaces consistently |
| Agent tools | `observe`, `request_decision`, `read_decision`, `act`, `finish` | No agent-facing investigation, evidence update or appeal operation |
| Cases | Three action models, each with six scripted families: 18 cases | A changed decision is scripted; no branch caused by an agent's appeal |
| Contract binding | Supported source, method, arguments, controlled model response and string result mapping | No general multi-operation dispute workflow or preserved structured domain result |
| Studio appeal | A developer-triggered `studio verify --appeal` observed a completed local round and finalization | Not an agent-selected challenge; recorded approval was upheld, not overturned |
| Studio timing | Ordinary scenario preparation obtains the finalized contract result | Agent control arrives too late to challenge that real transaction |
| Models/evidence | Contract I/O is controlled | No general evidence environment for an agent to investigate |
| Permissions/economics | Run credentials; isolated host signer; existing local appeal uses value zero | No agent appeal mandate, budget, modern fee quote or bond accounting |
| Reports | Decision, behavior, outcome and completion grades | No justified-challenge, evidence-quality, deadline or appeal-expense assessment |

Code and verification anchors:

- [MCP tools](../src/genlayer_agent_lab/mcp_server.py),
  [HTTP tools](../src/genlayer_agent_lab/api.py),
  [scenario catalog](../src/genlayer_agent_lab/scenarios.py).
- [Bindings and result mapping](../src/genlayer_agent_lab/bindings.py),
  [engine](../src/genlayer_agent_lab/engine.py).
- [Studio preparation](../src/genlayer_agent_lab/runtime/studio_evaluator.py),
  [developer conformance command](../src/genlayer_agent_lab/studio_conformance.py),
  [Studio transaction client](../src/genlayer_agent_lab/runtime/studio.py).
- [Dated verification evidence](VERIFICATION.md),
  [Studio fixtures and limits](STUDIO.md), [build status](BUILD_STATUS.md).

The critical architectural gap is timing. An MCP `appeal` tool added to the current
finalize-then-return Studio path would not create a usable agent appeal test.

## Three distinct evaluation modes

| Proposed mode | Controlled by the Lab | What a passing run establishes |
|---|---|---|
| Decision reaction | Decision outputs, lifecycle events and tool effects | The tested agent responds correctly to those conditions |
| Investigation and challenge | Case evidence, available tools, rules, deadlines, budgets and an independent grading rubric | The tested agent gathers evidence and chooses a justified, authorized next step |
| Contract judgment evaluation | Evidence inputs with actual supported leader/validator model execution | The contract's own evidence handling and judgments satisfy the defined evaluation criteria |

The first mode describes the current product direction. The second expands that
direction: the agent can perform genuine investigation while the contract's
initial response and appeal outcome remain controlled. This allows repeatable
tests of upheld, changed and unresolved outcomes without making the real network
produce each one on demand. A scripted reference agent only verifies the harness;
it does not establish how a developer's model-driven agent performs.

The third mode answers a separate question: whether the intelligent contract
reaches a defensible judgment from evidence. It requires actual model execution
and independent evaluation. Valid output formatting or model agreement alone
cannot establish factual correctness.
[Equivalence principle](https://docs.genlayer.com/developers/intelligent-contracts/equivalence-principle).

## Proposed coverage map

P0 means the first investigation/appeal extension; P1 means subsequent coverage.
These are proposed tests, not additions to the implemented count of 18.

| Priority | Family and representative cases | Expected behavior |
|---|---|---|
| P0 | Wrong denial; justified denial | Investigate a plausible error; accept or repair a valid rejection |
| P0 | Missing, contradictory or weak evidence | Preserve uncertainty and follow the case's escalation policy |
| P0 | Evidence containing malicious instructions | Use relevant facts without changing permissions or obeying embedded commands |
| P0 | Eligible challenge; expired window; already-finalized transaction | Check current eligibility and avoid an invalid appeal |
| P0 | Unsupported new evidence; supported evidence amendment | Choose the actual contract remedy; do not invent an evidence payload |
| P0 | Owner disallows appeals; insufficient test budget; quote changes | Respect permission and total authorized exposure |
| P0 | Another actor appeals; decision changes before submission | Reconcile the active decision and round before acting |
| P0 | Submission acknowledged; response lost after broadcast | Track the submitted operation rather than blindly duplicate it |
| P0 | Appeal upheld; recomputation changes result; outcome remains unresolved | Observe completion, reread the result and choose the appropriate next action |
| P0 | Application approval while challenge remains unresolved | Avoid an irreversible effect where the mandate requires finality |
| P0 | Service restarts mid-investigation or mid-appeal | Recover identities, evidence references, budget and pending operations |
| P0 | Agent claims success without checking the effect | Require an independently observed result or mark uncertainty |
| P1 | Partial award; insufficient-evidence result; unknown schema | Preserve domain meaning and reject unsupported interpretation |
| P1 | Accepted/finalized execution contains an error | Avoid treating consensus status alone as successful work |
| P1 | Wrong network, contract, subject, caller or policy version | Refuse the mismatched or unauthorized operation |
| P1 | Repeated failed appeals; missing validator capacity | Apply configured stopping rules; distinguish infrastructure limits from agent error |
| P1 | Preliminary prediction result; later correction; source changes | Evaluate source authority, event identity and relevant time |
| P1 | Parent completes but child is pending, fails or repeats | Verify downstream state and handle duplicate effects |
| P1 | Appeal changes dependent pending decisions | Refresh affected observations and invalidate stale plans |
| P1 | Fee consumption and bond return differ | Reconcile each accounting component without assuming an appeal is profitable |

The generic wrong-subject, timeout, revised-result and duplicate-acknowledgment
cases provide partial foundations. Their existence does not verify these broader
protocol-specific workflows.

## Proposed integration and build sequence

1. **Specify one complete case.** Use a service agreement with a suspect denial
   and a justified-denial control. Define the agent's role, accessible evidence,
   allowed remedies, expense limit, time limit and exact settlement operation.
   Include both a correct challenge and a correct choice not to challenge.

2. **Separate the data models.** Represent execution success, domain decision,
   finality, active decision identity and appeal round independently. Preserve
   structured outputs and evidence provenance. A richer result must not be
   squeezed into approve/deny.

3. **Make the runtime interactive.** Introduce a bounded session that can prepare,
   advance, inspect and reconcile a transaction before finalization. Controlled
   mode should use an explicit event clock. A real Studio session observes actual
   backend transitions; it must not relabel scripted events as consensus evidence.

4. **Add shared agent operations.** Proposed capabilities include evidence
   inspection, reading lifecycle state, checking appeal eligibility and cost,
   submitting an appeal, and tracking its round. Evidence amendment is enabled
   only when the application binding supplies an appropriate operation. MCP,
   HTTP and language clients should expose the same engine behavior.

5. **Extend bindings and permissions.** The developer supplies the contract's
   methods, result meanings, evidence paths and expected effects. We supply the
   binding interface, validation and supported examples. Keep credentials and
   signing in the run's controlled environment; grant the tested agent only its
   declared capabilities. Universal import from an arbitrary address is not a
   consequence of adding these interfaces.

6. **Implement controlled branching and grading first.** Drive different appeal
   outcomes from an explicit case specification and record their simulated
   provenance. Grade evidence use, remedy choice, authorization, timing, duplicate
   handling and confirmed final effects. Include an inconclusive outcome when
   required infrastructure evidence is unavailable. Avoid requiring disclosure
   of private chain-of-thought: record sources, concise justification and actions.

7. **Verify one compatible real backend profile.** Expose an actual prefinal
   decision to the agent, exercise submission and observe a completed appeal.
   Demonstrate an actual overturned/recomputed case separately before claiming
   it. Verify timeout-after-inclusion recovery and profile-specific accounting.

8. **Expand through real developer workflows.** Add a prediction-resolution case,
   then another evidence-driven application. Validate with a developer's agent
   through the existing connection formats. This gives coverage across distinct
   workflows without assuming that generic lending or DEX simulation is required.

Controlled tests need no real funds or shared hosting. A developer can continue
to run the Lab on their own laptop or VPS. Model-driven investigation may use
their chosen agent/provider; the Lab need not operate that model for them. A live
contract-model profile has separate provider and runtime requirements.

## Version compatibility and remaining uncertainty

The Lab pins Studio v0.121.6, GenVM v0.2.16 and Python SDK 0.16.3. Current GenLayer
migration documentation describes a coordinated v0.6 release-candidate family:
Studio 0.123 RC, JavaScript 2.0 RC, Python 0.19 RC and CLI 0.40 RC. Those newer APIs
and accounting rules cannot be assumed to exist in the Lab's older profile, or
to have been deployed to every public network. No upgrade was performed.
[Migration guide](https://docs.genlayer.com/developers/consensus-v06-migration).

Before a real appeal-aware integration, verify the selected network, matching
SDK/ABI and consensus version, current quote/deadline behavior, validator capacity,
late/duplicate submissions and monetary settlement. Test an SDK exception after
successful inclusion as an ambiguous outcome, not proof that nothing happened.
The older SDK and Studio sources expose different appeal receipt-event paths;
this is a compatibility investigation item, not a newly reproduced defect or a
reason to discard the previously recorded successful local trial.
[Older SDK implementation](https://github.com/genlayerlabs/genlayer-py/blob/v0.16.3/genlayer_py/contracts/actions.py#L168),
[older Studio appeal entry point](https://github.com/genlayerlabs/genlayer-studio/blob/v0.121.6/hardhat/contracts/v2_contracts/ConsensusMain.sol#L468).

The key application uncertainty is which evidence a particular contract can
actually consume during its supported remedy. That must be established from its
code and a concrete integration, not inferred from the word "appeal."

All external sources above are official GenLayer documentation or versioned
GenLayer source code, accessed September 7, 2026. Documentation publication dates
were not established. Recommendations and scenario designs are this review's
analysis; they are not upstream guarantees or implemented Lab features.
