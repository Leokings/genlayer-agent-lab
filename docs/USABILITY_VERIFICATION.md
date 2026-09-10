# Alpha 12 usability verification — September 8, 2026

This record covers the **0.1.0a12 working-tree candidate**, before publication.
It adds guided setup, scenario review, agent connection instructions and readable
results to the existing project workflow runtime. It does not supersede the
historical alpha 11 artifact and Studio evidence in [VERIFICATION.md](VERIFICATION.md).

## Implemented changes

- `setup` checks host prerequisites, prepares or reuses the installation's owned
  project Studio stack, starts the foreground Lab, and opens the dashboard or
  prints private SSH-tunnel instructions. Existing-listener recognition requires
  a fresh nonce and a token-keyed HMAC proof before automatic sign-in; a copied
  public installation fingerprint alone is insufficient.
- The primary dashboard offers eleven bundled cases: three prediction decisions
  and appeals, two downstream message cases, and six evidence investigations.
  Forms bound the task, title and time limit. Supported prediction outcome and
  confidence changes keep the supplied responses, evidence and expected result
  consistent; the developer still reviews the resulting case explicitly.
- Custom project JSON can be previewed and approved in the dashboard. Approval
  records the digest of the exact executable scenario, and changed content
  requires another review. Tagged integers and literal tag-shaped objects retain
  their meaning through import, review and execution requests.
- Connection instructions cover MCP, Python, TypeScript and HTTP. They use a
  fresh run credential and distinguish the server's loopback address from an
  agent-side SSH tunnel. A connection check records actual authenticated agent
  tool requests since service startup; copying settings or administrator polling
  does not establish a connection or prove that an agent process is still alive.
- Results show the observed actions and evaluation, with readable HTML and full
  JSON export. Saved run titles come from the persisted scenario. Private
  fixtures, grading rules and administrator credentials remain outside the
  tested agent's tool responses and generated connection settings.

## Recorded local evidence

The [sanitized evidence](evidence/usability-2026-09-08.json) retains run identities,
check outcomes, selected actual transactions, browser rechecks and the original
mobile failure. It omits credentials, private scenario fixtures and raw receipts.

| Check | Recorded result | Scope |
|---|---|---|
| Local Python regression | **1,398 passed, 10 skipped, 1 warning in 174.52 seconds** | Current local source; local log `.lab/usability-regression.log`, not a published artifact |
| Browser authoring | Passed sign-in, template selection, review invalidation after edits, exact-integer handling, escaped untrusted text, responsive review and no page errors | Browser interaction with the local candidate; no agent or Studio outcome inferred from authoring alone |
| Final setup/onboarding/wire checks | **118 passed, 1 warning in 9.18 seconds** | Focused checks overlap the full regression; counts are not additive |
| Installer boundary checks | **12 passed, 1 warning in 6.24 seconds** | Separate focused result; do not add it to the regression total |
| Browser-created safe Studio run | **Completed; verification pass; 13 checks; cleanup restored** | Actual run `project-03f2c6e6538c4bc9afd56b12f3f3231e`; scripted safe agent, generated connection settings and authenticated-contact boundary checked |
| Browser-created deliberately faulty Studio run | **Completed; expected verification fail; 7 failed checks; cleanup restored** | Actual run `project-6f6867c18a63435ab898460e60cd307b`; rejected premature record attempt detected, including policy-compliance and behavior failures |
| Browser report and export | Both saved reports passed final desktop/mobile display, readable HTML export and no-page-error checks; exports contained no tokens | Read-only rechecks of the two actual completed runs after the layout correction; no additional workflow mutations |
| Mobile connection layouts | **All 8 combinations passed**: MCP/Python/TypeScript/HTTP × same computer/SSH tunnel | Mocked creation response pointing to an existing completed run; each 390-pixel layout had scroll width 390, no page errors and no actual workflow mutations |
| Local built-wheel onboarding probe | **Passed** | Fresh virtual environment outside the checkout, cached dependencies, isolated imports, installed template/review checks, setup help and exact dashboard asset hashes; [receipt](evidence/usability-wheel-local-2026-09-08.json). This targeted probe did not start Studio |
| Alpha 12 remote package CI | **macOS passed; Linux and Windows failed synthetic workflow cleanup tests** | [Run 34247358475](https://github.com/Leokings/genlayer-agent-lab/actions/runs/34247358475); the test-budget correction below awaits remote confirmation |
| Alpha 12 artifact publication | **Not published** | The local wheel receipt is not a published-artifact or clean-OS installation claim |

The regression skips cover an unavailable optional Linux worker image, explicit
live Studio opt-ins and a platform-specific permissions check. The warning is
the upstream Starlette/AnyIO deprecation. Separate actual Studio runs must carry
their own evidence rather than being inferred from this regression count.

On September 8, 2026, remote run `34247358475` at `5d19a52` passed the macOS
package and fresh installed-wheel checks. Linux failed one legacy synthetic
workflow test and Windows failed four: their shared 150-millisecond cleanup
budget could expire during durable persistence, producing an unresolved-cleanup
report despite finalized transactions. The normal test cleanup budget is now
three seconds, with explicit expired-budget and unfinished-transaction refusal
checks retained. Production cleanup behavior is unchanged. The focused workflow
suite passed locally (**39 passed in 7.87 seconds**) and lint passed; the corrected
Linux/Windows remote gates remain pending. Their wheel probes were skipped after
the test failures, so this record does not claim they passed.

## Review findings addressed

The integration review identified and corrected the default page's relative
asset paths, replayable static listener recognition, setup command hints that
omitted a custom data directory, and a dashboard readiness result that could
accept an empty validator cohort. Readiness now checks the owned isolated
runtime and a nonempty cohort without building or starting Docker from a GET
request. A slow status inspection uses a bounded HTTP wait and one cached probe.
Both initial live browser attempts created their intended actual Studio runs,
reached the expected result and exported reports, then failed the mobile
horizontal-overflow assertion because long transaction hashes exceeded the
timeline width. The original attempts are not recorded as fully passing browser
runs. After the wrapping correction, read-only saved-report rechecks passed
desktop and mobile display, export and no-page-error assertions for both runs.
Separate connection-layout checks used a mocked creation response and made no
additional actual workflow mutations; they are layout evidence, not eight more
agent integrations or Studio trials.

Scenario and connection checks cover administrator-only authoring, run-scoped
access, changed-digest rejection, coherent prediction overrides, installed-kit
template loading and exact imported integer values. These checks establish
implementation behavior; they do not establish that a new human developer can
complete onboarding without help.

## Remaining validation

On September 10, 2026, the agent connection flow gained a generated **Copy agent
setup prompt** action, with matching setup-skill and installation guidance.
Focused onboarding tests passed (**48 passed**, one existing upstream
deprecation warning). The isolated Chromium connection check passed its prompt,
selected-client, clipboard fallback, run-change, expiry and workspace-lock
assertions, with **zero live Lab requests**. These checks use synthetic browser
responses and establish UI behavior; they do not establish that a real agent
host accepted the configuration or completed a test. The updated setup skill
also passed its structural validator. The actual OpenClaw/VPS connection trial
remains pending.

No paid model or live LLM-agent trial has been performed for this usability
candidate. A scripted safe or faulty control is a workflow check, not a measure
of an arbitrary model's ability. The user's VPS/selected-agent pilot and the two
independent human developer onboarding trials remain open. Existing-machine
reuse does not establish a fresh OS installation, a new provider's account
activation, or whole-machine reboot recovery of the modern Studio stack.

Contract-model judgment quality, public-network settlement and automatic
integration of arbitrary production tools remain outside the documented scope.
