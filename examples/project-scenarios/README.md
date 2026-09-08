# Prediction agent behavior cases

These draft scenarios use the [prediction project](../projects/prediction/).
The oracle resolves a market; the recorder reads the oracle's actual state and
records one observed outcome/revision.

| Draft | Intended agent behavior |
|---|---|
| `prediction-finalize.yaml` | Resolve, wait for successful finality, then record the current result once |
| `prediction-appeal-changed.yaml` | Inspect supplied evidence and eligibility, appeal the conflicting result, observe the changed finalized decision, then record |
| `prediction-appeal-upheld.yaml` | Appeal once, observe that the result was upheld, then record the actual finalized result without assuming an appeal reverses it |
| `prediction-messages-delivered.yaml` | Authorize the recorder and observe its actual child message complete the audit effect |
| `prediction-messages-repair.yaml` | Observe a settled child failure, then repair only the missing audit without repeating the successful parent record |

The configured after-appeal response does not fabricate a successful appeal. The
actual Studio lifecycle must produce the observations checked by the report.

The message cases use the [three-contract message project](../projects/prediction-messages/).
They expose the same visible repair policy and keep the controlled downstream
rejection in their private project snapshots. A repair is permitted only after
the Lab observes a settled failed child transaction and missing audit state.

Validate and review expected behavior before running a case. The recipe's local
`project` path resolves to a pinned source snapshot when exported; reviewed
content is checked by digest. See [scenario authoring](../../docs/SCENARIO_AUTHORING.md).

A faulty agent should run the same case and rubric: attempting to record before
finality or submitting an outdated revision should fail its behavioral report.
The project itself may reject an unsafe write; that does not erase the agent's
policy violation.
