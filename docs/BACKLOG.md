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

**Reminder:** When the current developer version is explicitly recorded as
finished, remind the user about Job 1 and ask whether they want to revisit it.
An existing alpha release or a passing CI run alone is not that completion
milestone. Keep this job deferred until the user elects to resume it.
