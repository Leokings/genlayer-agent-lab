# Deferred jobs

## Job 1: Support larger contract projects and protocol extensions

Recorded September 7, 2026. **Deferred until the current developer version is
finished.** The user may then choose whether to pursue this expansion; recording
it does not authorize starting the implementation now.

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

Recommended first slice: incremental Studio execution, structured results and
action/state bindings from items 2–3 above, built and tested through one workflow.
Multi-file projects, extra dependencies and deployed-state import can follow
later. Existing-alpha installation trials can continue; each new capability needs
its own verification and an agent integration trial. Completing all of Job 1 is
not a prerequisite to starting those tests.

The user subsequently authorized implementation of this selected first slice.
The `service_release` workflow is now built and has live HTTP/Studio evidence;
see [its supported scope](STUDIO_WORKFLOWS.md). The broader multi-file,
dependency and deployed-state work remains deferred. This does not mark all of
Job 1 complete.

**Reminder:** When the current developer version is explicitly recorded as
finished, remind the user about Job 1 and ask whether they want to revisit it.
An existing alpha release or a passing CI run alone is not that completion
milestone. Keep this job deferred until the user elects to resume it.
