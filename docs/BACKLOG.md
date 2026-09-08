# Jobs and scope decisions

## Job 1: Support larger contract projects and protocol extensions

Current implementation: alpha 11 contains the bounded project extension below,
plus [supplied-evidence investigation cases and findings](INVESTIGATION.md).
The [build audit](BUILD_AUDIT.md) records coverage, corrected defects and remaining
external validation. Historical first-slice status statements below do not
describe the current implementation.
See [project workflows](PROJECT_WORKFLOWS.md) and the
[actual verification record](VERIFICATION.md#combined-project-workflows--september-8).
Deployed-state import and unbounded protocol compatibility remain separate work.

Recorded September 7, 2026. **Scope and sequencing updated September 8, 2026:**
the user confirmed combining the remaining Job 1 extension and the additional
local Studio capabilities below before consolidated testing and the VPS pilot.
This supersedes the earlier decision to defer the expansion until after pilots.
The scope is agreed; the extensions are not thereby implemented or verified.

Extend the Lab beyond the current single-file GenLayer contract binding and
three built-in protocol models:

1. Support contract projects with multiple related source files and explicitly
   supported dependencies.
2. Support workflows with several contract operations, such as agreement
   creation, evidence submission, a decision and payment.
3. Define a reusable protocol-extension interface covering actions, test state
   and expected outcomes. Supply reference examples so developers can contribute
   integrations for their own protocols.
4. Investigate importing relevant deployed state where network capabilities
   permit it. Treat this as separate feasibility work, not a promise to reproduce
   any protocol automatically from an address.

Begin with one real developer's supported GenLayer contract project and a
complete workflow before generalizing the interface. Adding scenario files
alone does not implement new protocol mechanics.

### Scope and sequencing clarification — September 7, 2026

The user confirmed developer-controlled contract outcomes and simulated workflows
as the product scope. Contract-LLM evidence interpretation or judgment quality is
not a required capability.

Selected runtime direction: local Studio processes actual supported transactions
and appeals, with developer-supplied model responses. Scripted appeal outcomes do
not replace observing Studio's real appeal lifecycle. See the consolidated
[development roadmap](DEVELOPMENT_ROADMAP.md), which supersedes the earlier
controlled-appeal-first recommendation.

The original first slice was incremental Studio execution, structured results and
action/state bindings from items 2–3 above, built and tested through one workflow.
That first-slice sequencing is historical; the September 8 scope below governs
the remaining work and the next consolidated pilot.

The user subsequently authorized implementation of this selected first slice.
The `service_release` workflow is now built and has live HTTP/Studio evidence;
see [its supported scope](STUDIO_WORKFLOWS.md). This does not mark all of
Job 1 complete.

### Combined extension agreed September 8, 2026

Build Job 1 as a bounded extension of testing how agents use GenLayer:

1. General workflow/action definitions, richer developer-defined results and
   behavior rules, multi-file contract projects with explicitly supported pinned
   dependencies, reusable bindings and developer validation/examples. Preserve
   the existing workflow and demonstrate a materially different second workflow.
2. Defined multi-contract workflows in local Studio: deployment dependencies,
   separate contract/transaction identities, child effects, partial failures and
   agent behavior when decisions change. This is not arbitrary protocol import.
3. Fees and appeal-bond behavior on a verified fee-enabled local Studio/SDK
   profile. Prove actual local accounting and its exposed operations before
   claiming coverage; application budgets or gasless runs are insufficient.
4. Durable Lab workflow recovery: save operation identities and required run
   context, reconcile with surviving Studio state after a Lab interruption and
   avoid duplicate submissions/effects. Studio database loss and full OS reboot
   remain separate disaster-recovery work.
5. Scenario authoring from supported templates and variations, plus a documented
   optional LLM-assisted drafting route using the developer's own agent/model.
   Validate drafts against schemas, backend capabilities and developer-reviewed
   expected behavior. Save reusable scenarios; no claim of exhaustive generation
   or requirement for a centrally paid model service.
6. Carry the supported operations through HTTP, Python, TypeScript and MCP;
   update dashboard evidence, reports, setup instructions and packaging.

Run focused checks during implementation to resolve concrete risks. Once the
combined candidate is built, perform consolidated regression, actual Studio,
agent behavior, recovery and clean-install verification. Then the user can run
the simpler laptop/VPS pilot against the same candidate. Do not rent a VPS or
start repeated paid onboarding trials before that gate.

Contract-LLM judgment evaluation remains outside scope. Deployed-state import,
unbounded protocol support, public-network parity and independent external
developer validation remain separate work. See the updated
[implementation sequence](DEVELOPMENT_ROADMAP.md#combined-job-1-extension--september-8-2026).
