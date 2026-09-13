# Post-demo audit — September 13, 2026

This audit reviews the local alpha 12 candidate after the recorded owner demo,
including setup, process ownership, agent access, workflow cancellation and
receipt handling. The supported Studio flow tests agent behavior using actual
local contract execution and controlled model responses. It does not establish
public-network behavior or the quality of a live contract model's reasoning.

## Recorded demo and separate control

The recorded owner test, `project-39d080a7b63e4236897158b4836a97b7`, passed all
**14 checks** and restored cleanup. Its evaluation denied the request, and the
agent did not release. This is an observed owner demo, not an independent
blinded assessment.

The scripted installation control passed **13 checks**. That control verifies
the installation separately; it is not evidence that an external agent completed
the owner's task.

## Corrections from the audit

- Windows commands now start suspended, enter their owned process job, then
  resume. Cleanup covers successful commands and failed startup, preventing
  descendants from escaping before job assignment.
- Setup checks complete Studio readiness, including isolation and a valid
  validator cohort. Credential-free health probes bound response headers and
  bodies, so a trickling listener cannot hold setup or service checks indefinitely.
- Service removal waits for observed unloading and loss of the owned health
  response before discarding its recovery metadata.
- Quick-mode agent `finish` returns only the run identifier and lifecycle status,
  including retries. Private grading stays in the administrator report; the
  TypeScript client and interface agreement now match this boundary.
- Cancellation, completion and expired deadlines prevent new transaction
  identities after slow preparation. Already journaled identities remain available
  for reconciliation without creating a duplicate transaction.
- Canceled receipts cannot count as successful finalized actions. Failed or
  canceled deployments persist their terminal evidence and end preparation
  promptly, including after restart, so cleanup can restore controlled inputs.

The documentation review also checked setup, project authoring/import and the
public Studio/quick-mode scope. Older audit entries now retain their dates and
original evidence limits rather than appearing to describe every later change.

## Verification state

- **Prior local regression snapshot:** 1,653 passed and 10 skipped. This predates
  the final follow-up corrections and must not be presented as a full regression
  of the final source.
- **Final focused regression:** 238 passed in 52.51 seconds. These checks cover
  the corrected boundaries and overlap the broader suite; counts are not additive.
- **Live Studio/MCP audit: passed.** Actual local Studio changed an appealed
  decision (14 passing report checks), repaired a multi-contract workflow (23),
  and correctly failed the deliberately unsafe control (7 failed report checks).
  The misleading-evidence and investigation-before-appeal cases also passed
  through MCP, including a separate appeal receipt and completed target round.
  All five runs restored their controlled inputs. These are scripted reference
  policies with supplied model responses, separate from the recorded agent demo.
- **Browser checks: passed.** Guided connection, inline JSON validation, review,
  quick-mode switching, browser pairing, mobile layouts and readable export were
  checked. The recorded Studio report also passed its actual dashboard recheck.
- **Packaging:** the source distribution and wheel built successfully. Full
  package, platform and native service CI results are recorded on this change's
  pull request; local focused counts do not substitute for those checks.

The [sanitized evidence](evidence/post-demo-audit-2026-09-13.json) preserves run
identities, outcomes and cleanup without copying credentials or private run state.

See [Build status](BUILD_STATUS.md) and the [verification record](VERIFICATION.md)
for the wider candidate history and remaining evidence gates.
