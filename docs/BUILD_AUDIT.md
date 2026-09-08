# Build audit before the VPS pilot

Candidate: **0.1.0a11**, September 8, 2026. This engineering audit covers the
agreed GenLayer agent-behavior product, the combined Job 1 extension and the
investigation addition. The test target is the agent's use of GenLayer and
reaction to its decisions. Developers supply possible contract model responses
and independently reviewed behavior expectations.

## Consolidated results

The audit is complete for this candidate's documented scope. All seven findings
below were corrected. The local regression passed **1,306 tests**, with ten
explicit environment skips. [Eight actual Studio investigation trials](evidence/investigation-workflows-2026-09-08.json)
produced the expected outcomes: six safe policies passed, and both deliberately
faulty policies failed for the intended findings. The appeal trial verified its
real submission, completed round, changed outcome and ordering.

The actual desktop/mobile dashboard and report export passed. [Package CI](https://github.com/Leokings/genlayer-agent-lab/actions/runs/34195400993)
passed on Linux, Windows and macOS, including fresh virtual-environment
installations, HTTP/MCP probes, scenario authoring and all eight packaged
investigation recipes. The Linux custom-contract worker also passed.
[Saved installation evidence](evidence/alpha11-ci-2026-09-08.json) identifies the
exact artifacts. The tested implementation commit is
`9b2585b8c8ed936a02232470bffa315c29761f85`; later documentation records these results.

Native Linux/macOS user-service checks and the existing Linux guest reboot
workflow also passed. These do not establish whole-machine reboot recovery for
the new modern Studio stack. The user VPS/selected-agent trial remains open.

## Requirements and implementation

| Area | Implemented behavior | Evidence and boundary |
|---|---|---|
| Self-hosted installation | Lab service, SQLite, dashboard, MCP bridge, setup skill and installation kit on the developer's machine | Fresh package checks; the user VPS pilot is still separate |
| Contract integration | Typed methods/results, multi-file snapshots, declared deployments and pinned dependency validation | Executed two/three-contract examples; supported manifests, not automatic deployed-protocol import |
| Agent integration | Scoped HTTP, Python, TypeScript and MCP operations with exact integer handling | Client/API/transport tests; developer adapters must redirect their agent's relevant tools |
| GenLayer behavior | Actual local transactions, finality, upheld/changed appeals, fee quotes, bonds and observed accounting | Pinned local Studio profile; no public-network parity claim |
| Dependent actions | Child transaction observation, partial failure and repair without replaying a successful parent | Actual three-contract workflow and saved contract states |
| Investigation | Missing, stale, contradictory, misleading, supporting and challenge-worthy supplied evidence | Six independently specified cases; two faulty-agent controls use the same expectations |
| Findings and justification | Bounded findings, cited evidence hashes, concise summary and the actual decision snapshot | Agent-authored report artifacts; no prose-quality or private-reasoning grading |
| Permissions and grading | Operation limits, required finality, appeal prerequisites, independent final-state/action checks | Missing agent work fails; incomplete backend evidence remains inconclusive |
| Recovery | Original signed operation identity and local signer/cohort recovery; cleanup-only retry after a transient failure | Lab-process interruption with Studio state intact; portable restores do not replay transactions |
| Authoring | Reusable templates/variations, schema validation, optional external-model drafting and exact-content review | No mandatory model subscription and no automatic approval of generated expectations |
| Reports and dashboard | Timeline, decision/contract/child state, fees, findings, independent checks and report export | Untrusted text rendered as text; desktop/mobile checks |

## Defects found and corrected

1. A contract operation alias could collide with a Lab built-in. Binding
   validation now rejects those names instead of silently invoking a different
   operation.
2. An unresolved cleanup could permanently block later runs. On restart, an
   eligible original local journal can retry cleanup, reconcile the existing
   transaction identities and restore fixtures. It preserves the failed run's
   outcome and never starts new work. Missing signers, portable restores and a
   changed backend cannot use this path.
3. An agent could appeal and then retrospectively add its investigation.
   Explicit public appeal constraints now require the appropriate investigation
   of the current decision before the challenge is submitted.
4. Missing finalized execution evidence could look like an agent failure.
   Reports now keep that case inconclusive. A known failed execution remains a
   failed effect; canceled child transactions are settled without a successful
   effect.
5. Repeated permitted evidence reads could exceed the single-document
   authoring limit and prevent report generation. Aggregate report validation
   now has a separate byte allowance while retaining structural limits.
6. The investigation reference tried to act on a provisional consensus receipt.
   It now waits for acceptance or finalization before citing the decision's
   identity. The runtime continues to reject stale identities.
7. Faulty-agent verifier controls could accept an unrelated failing grade.
   They now require the intended disposition/result/finding failures. Appeal
   verification also checks the separate submission, completed round, changed
   result and action ordering.

Focused regressions accompany these corrections. The consolidated verification
record and candidate artifact evidence identify the final test outcomes; this
requirements table alone is not proof that a runtime trial passed.

## Scope remaining after this audit

The user's short Linux VPS trial must check installation, dashboard access and
connection of the chosen agent. Independent developer onboarding remains a
separate validation step. Scripted reference-agent success does not certify an
arbitrary agent model, framework or contract.

Evidence source identities and dates are supplied test data. This version does
not authenticate real websites, investigate the open web or judge a contract
LLM's reasoning. A `request_review` disposition does not notify a person.
Submitting findings creates a Lab report artifact; it does not upload new
evidence through GenLayer's appeal API. Application-specific evidence submission
requires a method in the developer's contract and its binding.

Arbitrary deployed-state import, every possible protocol/dependency, public-chain
equivalence, Studio database-loss recovery and full-machine reboot coverage of
the new Studio stack are not included. Existing native installation/service and
earlier OS reboot records retain their separately documented scope.

Start the user pilot with [VPS_QUICKSTART.md](VPS_QUICKSTART.md). The full
maintainer suite should not be repeated on the VPS unless a specific failure
needs investigation.
