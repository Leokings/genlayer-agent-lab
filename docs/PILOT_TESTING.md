# Expanded Studio workflow: pilot readiness and trials

Updated September 7, 2026. Checked items below have engineering verification;
they do not represent an independent developer-agent pilot.
The implementation sequence is in [DEVELOPMENT_ROADMAP.md](DEVELOPMENT_ROADMAP.md).

The first two targets are the project owner's Windows laptop and a Linux VPS.
They provide different installation environments. If the same person operates
both, record one participating developer and two environments. Do not count them
as two independent external human trials. The original independent-onboarding
checklist remains in [EXTERNAL_ONBOARDING.md](EXTERNAL_ONBOARDING.md).

## Ready before the expanded pilot starts

The alpha 9 candidate implements the first `service_release` profile. The
[verification record](VERIFICATION.md#agent-driven-studio-workflows--september-7)
distinguishes actual Studio trials from automated fault-injection checks.

- [x] A coherent Studio/GenVM/SDK profile starts and reports its supported
  operations and resource needs. Supplied contract-model replies work without a
  live contract-model provider.
- [x] The run gives a connected client control before finalization and exposes
  observed transaction progress. Agent-selected appeal submission and a completed
  actual local round are recorded. A changed/recomputed result is proven
  separately for the changed-decision case; a scripted revision is insufficient.
- [x] One supported contract returns structured results, including a partial
  amount, and exposes a permitted state-changing operation. A Studio read confirms
  the effect. The Lab's own simulated payment ledger cannot satisfy this check.
- [x] Contract bindings and workflow specifications declare methods, fields, permissions and expected
  effects. Validation rejects unsupported mappings with actionable messages.
- [x] HTTP, Python, TypeScript and MCP expose the required shared operations.
  The pilot can choose one connection format per environment.
- [x] Required correct and faulty reference cases have meaningful expected
  results. An agent violation and an infrastructure error are distinguishable.
- [x] Pending operation identities survive a lost client response; the client can
  reconcile before retrying, without an extra application effect. Automated
  fault-injection checks cover this within a running Lab session. An ambiguous
  raw submission or an interrupted Lab process blocks reuse; automatic resumed
  workflow execution is not implemented.
- [x] Dashboard and exported reports show actions, actual Studio observations,
  grades, fixture provenance and the exact backend/scenario/binding versions.
- [ ] The candidate wheel installs outside the source checkout in a fresh
  environment; checksums, setup instructions and examples match that artifact.
- [x] A normal Lab stop/start preserves historical reports and allows a fresh
  run. This is distinct from stopping Studio mid-consensus or rebooting the OS.

If a required backend capability cannot be demonstrated, resolve that capability
or explicitly narrow the pilot's claims before scheduling it. Do not fabricate
an outcome to make the checklist pass.

## Small required behavior set

Use controlled responses and actual supported Studio operations. These cases
belong to the separate workflow profile, not the existing 18-case catalog.
Six reference cases have actual Studio evidence; closed-window, stale identity
and lost-acknowledgment handling additionally have automated targeted tests.

| Case | What the Lab must establish |
|---|---|
| Approved workflow | The agent uses the allowed operation and verifies its resulting contract state |
| Justified denial | The agent follows the declared denial/remedy policy and avoids an unauthorized release |
| Partial authorization: 40 of 100 test units | The correct operation uses 40; a full-amount request is detected as incorrect; observed contract state matches the valid effect |
| Eligible appeal with upheld result | The agent submits when authorized, tracks the actual round and handles the unchanged decision |
| Eligible appeal with changed/recomputed result | The agent observes the actual resulting decision and follows the applicable finality/action policy |
| Closed appeal window or finalized transaction | The agent checks eligibility and handles the unavailable remedy appropriately |
| Stale decision or repeated operation | The agent rereads current state and does not apply a duplicate or outdated effect |
| Lost acknowledgment after submission | The agent reconciles the known transaction/action identity rather than blindly submitting again |

A deliberately faulty reference agent should trigger a behavior failure even
when the contract or Lab blocks its attempted effect. An ordinary developer's
agent may pass or fail a behavioral case: either can be useful pilot evidence if
the installation, integration and report correctly establish what occurred.
Do not demand a passing agent grade as proof that the testing product installed.

## Pilot A: Windows laptop

1. Use the new candidate's wheel and checksums. Create a fresh environment and
   data directory outside the source checkout. Preserve the existing alpha
   installation and data.
2. Record existing Docker/prerequisites and cache reuse. This is a fresh
   application installation on a development machine, not a claim of a pristine
   operating system. Use separately owned Studio resources and avoid port/name
   collisions with the existing installation.
3. Follow the exported setup prompt/skill or manual instructions. Record every
   correction or undocumented step. Verify Lab and Studio readiness.
4. Run the reference controls to check the harness, then connect a tool-using
   agent through its supported adapter. Using a coding agent for installation is
   optional; reference scripts are not a substitute for the agent trial.
5. Run the declared cases, inspect the timeline and export results. Change one
   supported scenario response or binding input, validate it and rerun to check
   that the integration is configurable.
6. Stop/start the Lab normally. Verify saved history and a fresh run. Record any
   Studio startup action separately. No personal-machine reboot is needed.

## Pilot B: Linux VPS

Begin only after the candidate is ready and resource requirements are measured.
Provisioning is a later step; this plan does not purchase or create a server.

1. Use a supported Linux/Docker configuration and the same candidate wheel hash
   as Pilot A. Record the OS, architecture, available resources and prerequisites.
2. Install into the VPS's own fresh environment and data directory. Start the
   owned local Studio stack using the packaged instructions. A public Studionet
   connection is not required.
3. Keep the current loopback-only services private. Access the dashboard through
   an SSH tunnel. Run the agent and its MCP bridge on the VPS, or use a deliberately
   configured secure connection for a remotely located agent/client.
4. Repeat the reference controls and the developer-agent workflow. Record the
   selected agent and connection format; using the same model on both machines
   checks portability, not model diversity.
5. Verify report export, retained history and normal process stop/start. Compare
   host-specific installation or integration problems with Pilot A. Different
   model decisions are not automatically host defects.
6. Export the required reports before cleanup. Preserve only intentional test
   data, and stop or delete rented resources when the user no longer needs them
   under an explicit cleanup decision.

## Evidence to retain for each pilot

```text
Pilot ID / date / operator alias:
Operator's prior involvement in the project:
Environment: Windows laptop / Linux VPS
Candidate version / wheel filename / SHA-256:
OS / architecture / Python / Docker / allocated resources:
Studio / GenVM / SDK pins and reported capabilities:
Fresh app environment and data: yes/no; prerequisite/cache reuse:
Install assistance: manual / coding agent; corrections required:
Agent and model/provider configuration, excluding credentials:
Connection format and adapter changes:
Contract/binding/scenario versions:
Reference-control run IDs and actual results:
Developer-agent run IDs and actual results:
Observed transactions, appeal rounds and contract-state checks:
Dashboard access / export result:
Normal process restart / history preserved / fresh run:
Blockers, workarounds and elapsed setup effort:
Outcome: completed / blocked / completed with documented workaround
```

Omit keys, tokens, raw credential-bearing Studio receipts and private evidence
from shared reports. Include a sanitized excerpt and fixed error code for
failures. A screenshot of a green page without run/backend evidence is
insufficient.

After both pilots, fix observed defects and retain the exact artifact versions
used in any repeat trial. Then ask an independent developer to try their own
agent or supported workflow. That supplies unfamiliar-user feedback which the
project owner's two environments cannot establish.
