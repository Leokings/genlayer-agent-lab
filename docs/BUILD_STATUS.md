# Build status

This checkout is the **alpha 12 usability development candidate**. It adds a
guided setup command, eleven template forms, exact-content scenario review,
run-scoped agent connection instructions and readable saved reports to the
existing project workflow runtime. The [usability verification record](USABILITY_VERIFICATION.md)
separates current source evidence from the historical release gates below.

The September 10 assisted owner VPS trial reached a completed real OpenClaw
agent run: **11 checks passed, 2 failed**, with finalized local Studio
transactions and restored cleanup. The failed checks shared one malformed
evidence request, exposing a missing public built-in argument schema. The
resulting interface, setup, timing and report fixes are recorded in
[the trial verification](USABILITY_VERIFICATION.md#assisted-owner-vps-trial--september-10-2026).
The updated journey still needs its next real-agent trial. This does not close
independent onboarding or published-artifact gates.

Post-trial local regression passed **1,445 tests** with ten environment skips;
twelve Chromium connection/report fixture checks, Ruff and the setup-skill
validator passed. These establish the fixes locally, not another real-agent
VPS run. The earlier candidate evidence below retains its original counts.

The local Python regression passed **1,398 tests**, with ten explicit environment
skips. Browser authoring checks passed sign-in, templates, changed-review
invalidation, exact integers, escaped untrusted text and responsive review.
Focused setup/onboarding/wire checks passed **118 tests**, with a separate
12-test installer boundary pass; these counts overlap the regression. The
browser-created safe Studio run passed all 13 checks; the faulty control produced
the intended failure with seven failed checks. Both restored cleanup. Initial
mobile report overflow was corrected, and both saved-report rechecks passed;
eight connection layouts passed with mocked creation and no extra mutations.
[The evidence](evidence/usability-2026-09-08.json) preserves that distinction.
The [local wheel onboarding probe](evidence/usability-wheel-local-2026-09-08.json)
passed outside the checkout with cached dependencies. Remote package CI and the
published-artifact gate remain unrecorded. These September 8 checks preceded the
September 10 owner trial above; they did not include a paid model or independent
human onboarding.

## Historical alpha 11 evidence

Alpha 11 was the development candidate for the combined
[Job 1 implementation](DEVELOPMENT_ROADMAP.md#combined-job-1-extension--september-8-2026).
Its project interfaces, authoring, clients and durable journal are implemented.
The [full build audit](BUILD_AUDIT.md) records the investigation addition and
the integration, cleanup, ordering and reporting defects found and corrected.
The expanded Python regression passed 1,306 tests (10 explicit environment
skips). [Eight actual investigation trials](evidence/investigation-workflows-2026-09-08.json)
passed their expected outcomes through HTTP/MCP, including both detected faulty
agents. Desktop/mobile report checks passed, and
[fresh package installations](evidence/alpha11-ci-2026-09-08.json) passed on Linux,
Windows and macOS. The full audit had no unresolved finding within that candidate's
documented scope; the user VPS/selected-agent pilot is the next validation step.
The earlier alpha 10 source/runtime checks passed: 1,194 Python tests, seven actual
Studio reference cases with the expected results, MCP and TypeScript appeals,
desktop/mobile report inspection, and forced Lab interruption during a pending
three-contract workflow. [The evidence](evidence/project-workflows-2026-09-08.json)
retains run identities, actual fees, child outcomes and recovery checks.

Installed-wheel verification and the OS package matrix are passed artifact
gates recorded with alpha 11. The user's short
[VPS onboarding trial](VPS_QUICKSTART.md) now has the assisted September 10
result above. Independent external developer validation remains unverified;
source checks do not substitute for those trials.

Read [project workflows](PROJECT_WORKFLOWS.md), [project bindings](PROJECT_BINDINGS.md)
and [scenario authoring](SCENARIO_AUTHORING.md) for the expanded interfaces.
Developers supply possible model responses and reviewed behavior rules. Actual
local Studio runs the contracts; reports assess how the agent reacts and acts.
Contract-LLM judgment quality evaluation is outside scope.

The project profile has its own fee-enabled Studio 0.123.0-rc.6 stack, separate
from the legacy installation. Its GenVM JSON fixture conversion is adapted to
the pinned decoder's text format. This changes serialization, not decisions or
consensus outcomes. See [verification](VERIFICATION.md) for actual evidence.

## Historical alpha 9 evidence

The following records describe the earlier `service_release` profile. They do
not independently verify alpha 10 project workflows or close its release gates.
See [legacy Studio workflows](STUDIO_WORKFLOWS.md) for those supported commands.

Scope clarification, September 7: developers supply possible contract model
responses and workflow rules; local Studio processes the actual supported
operations, and the Lab tests the agent's behavior. Evaluating the contract LLM's
understanding of evidence is outside scope. The new workflow extension combines
incremental Studio execution, structured responses and workflow bindings, with
tests built alongside it. Larger contract-project support can follow later.

The [fresh Windows laptop pilot](PILOT_TESTING.md#pilot-a-windows-laptop) completed
with a documented Studio build retry. The first profile's backend, integration,
reporting and Windows installed-candidate engineering gates have passed.
The Linux VPS pilot and independent human onboarding remain unverified. Both
planned environments may be operated by the project owner; that would be two
environments and one developer.
The original scenario payment actions update the Lab's test ledger. The new
Studio workflow calls the contract's release method and verifies its test-unit
ledger through a finalized state read. Six live HTTP reference cases have passed
their expected outcomes; [the evidence](VERIFICATION.md#agent-driven-studio-workflows--september-7)
includes both appeal outcomes and a detected faulty-agent action.
The alpha 9 candidate is installed as the laptop's user-login service. Its
exported TypeScript and MCP examples have also passed actual Studio workflows.
The final non-runtime/non-container regression run passed 1,006 tests; explicit
runtime checks and live Studio evidence are recorded separately.
The cache-empty Windows wheel installation also passed, including a real GLSim
readiness execution and exported integration kit checks. A model-driven agent
smoke test also passed a 73-of-200-unit appeal workflow through the public API.
The subsequent fresh laptop pilot installed the exact supplied alpha 9 wheel
in a new environment and data directory outside the checkout, with a separate
owned Studio stack. Existing tools and caches were reused on the same Windows
laptop. This was a developer-assisted application installation. The first
Studio build failed; a diagnostic retry succeeded using the installed build
implementation with only command-output flags changed. The original failure is
retained and its cause remains unknown.

The scripted control released 40 of 100 test units. An idle Lab process restart
preserved that report, and the fresh model-agent run afterward passed all four
grades: one evaluation, one appeal and one release, with finalized authorization
and contract state showing 61 of 150 test units released and 89 remaining.
Desktop/mobile dashboard checks and control-report JSON export passed. The
[pilot evidence](evidence/fresh-laptop-pilot-2026-09-07.json) records the exact
wheel hash, setup corrections and results. This adds no whole-machine reboot,
startup-service or interrupted-consensus recovery evidence.

[Alpha 9 package CI 34131594830](https://github.com/Leokings/genlayer-agent-lab/actions/runs/34131594830)
also passed all four jobs: native Windows, Linux and macOS package checks and
the separate Linux Docker worker. The CI-built wheel hashes are recorded
separately from the laptop pilot's candidate wheel; these package/worker gates
do not exercise Studio workflows.

Alpha 8 fixes the GenVM download's missing certificate issuer on fresh Windows
installations by adding bundled public certificate roots to its downloader's
default SSL context. System roots, certificate verification and pinned artifact
checks remain required.
It retains alpha 7's `studio verify-recovery` command and external developer
trial checklist. The published alpha 8 wheel passed an orderly Windows 11 guest
reboot and real standard-user AutoLogon recovery trial on GitHub Actions.

| Planned area | Implemented now | Remaining release work |
|---|---|---|
| Standalone package and runtime | Pinned dependencies, verified GenVM artifact, clean wheel installations and native Windows/Linux/macOS CI passed | Broader Python/OS versions and external machines |
| Complete escrow test | Actual bundled contract execution, independent grades, safe/unsafe/refusing reference agents | Real developer-agent onboarding |
| Framework-independent connection | Authenticated HTTP, Python client, TypeScript client, MCP bridge; model-driven HTTP workflow trials passed on the existing and fresh laptop installations | Broader framework adapters and independent agent trials |
| Scenario packs | 18 cases across escrow, treasury and generic decisions; declarative YAML import | Domain-specific cases and new protocol semantics |
| Custom contracts | Verified GLSim Docker worker and Studio GenVM delivery-contract execution; YAML bindings, immutable snapshots, Python/TypeScript/MCP/HTTP/CLI/dashboard selection, cancellation and bounded resources | Deferred: multi-file contracts and custom dependency sets |
| GenLayer fidelity | GLSim plus an owned local Studio backend using GenVM, bounded SDK calls and separate observed conformance evidence | Separate future verification: modern bond accounting and public-network compatibility; contract-LLM judgment evaluation is outside scope |
| Usability | CLI, dashboard, report export, packaged setup kit, native Windows/Linux/macOS user-service lifecycle, real Ubuntu guest reboot recovery with administrator-configured lingering, orderly Windows 11 guest reboot and standard-user AutoLogon recovery, offline backup/restore, upgrade/rollback procedure | macOS reboot and separate desktop logout/login trials; recurring schedules are deferred |
| Recovery | Exclusive-lock SQLite snapshots, integrity/checksum verification, fresh restore destinations, credential rotation, preserved historical reports; explicit Studio restart verifier with retained volumes | Studio-volume disaster recovery and interrupted-consensus recovery remain separate, unimplemented capabilities |
| Publication | Dedicated [public repository](https://github.com/Leokings/genlayer-agent-lab), wheel/source alpha, checksums, passing installed-artifact CI matrix and completed assisted laptop model-agent pilot | Linux VPS pilot and two independent human developer installation trials before broader release validation |

The Docker execution gate has passed: the pinned worker image builds, executes its readiness contract and runs the custom delivery contract through real client connections. The install/startup/recovery work from the September 13–16 stages is implemented. Native package CI passed on all three operating systems. Alpha 5 fixed a Linux startup defect; alpha 6 fixes exact macOS argument verification and asynchronous job unloading. The corrected native Linux and macOS service lifecycles passed on free standard CI runners. A separate Ubuntu guest OS reboot passed with administrator-configured lingering and an outside observer; the Lab recovered before its user logged in. Alpha 8 passed an [orderly Windows 11 guest reboot and real standard-user AutoLogon trial](https://github.com/Leokings/genlayer-agent-lab/actions/runs/34048951171): the owned login task recovered the Lab without a manual start, preserved its data and completed a fresh four-grade GLSim run.

Remaining validation includes the Linux VPS pilot, two independent human external installations, macOS reboot, separate desktop logout/login, Windows startup before login, automatic Studio startup and physical power-loss recovery. The native runtime remains restricted to bundled code. See the dated verification record for tested behavior and evidence limits.

**macOS full reboot validation is deferred.** The latest
[hosted Mac installer preflight](https://github.com/Leokings/genlayer-agent-lab/actions/runs/34083470338)
passed the archive-content, Apple package-signature and package-policy checks,
populated the installer application and verified its media tool and payload.
It then stopped at `media_disk_image_create_failed`; the underlying `hdiutil`
error was not retained. This failure precedes guest boot and does not exercise
Lab installation or recovery. No further trial is scheduled. The existing
native macOS installation and service-lifecycle evidence remains valid; full
macOS reboot recovery remains unverified. Developer-agent integration and the
two external installation trials can proceed with the published alpha 8.

## Position against the original schedule

| Original stage | Current position |
|---|---|
| September 5–6: runtime/platform feasibility | Passed: native Windows, Ubuntu and macOS runtime/installation CI plus Linux Docker execution |
| September 7–9: service, workers, trace storage, first escrow case | Implemented and verified end to end |
| September 10–12: all scenarios, bindings, dashboard and integration | 18 scenarios implemented; Python/TypeScript/MCP verified. OpenClaw is optional, with no dedicated host trial claimed |
| September 13–14: setup skill, packaging, startup, optional Studio | Implemented; artifact and startup evidence recorded separately by platform |
| September 15–16: recovery, upgrades, documentation, external onboarding | Recovery, upgrade/rollback and native CI passed; agent-assisted onboarding passed; two human external developer trials remain open |

The build follows the sequence, but a calendar date does not imply that a gate
passed. Python/TypeScript/MCP replaced OpenClaw as the first-release integration
requirement in response to the framework-independence decision. No browser,
OpenClaw installation, shared cloud account or paid LLM provider is required.

The optional Studio path pins stable Studio v0.121.6, GenVM v0.2.16 and SDK 0.16.3.
Its startup verifies actual Docker isolation and endpoint compatibility. A
GenVM deployment, approved and denied writes, and a completed local appeal have been observed successfully; consult
the dated verification record for the completed live checks. Scenario timelines
remain scripted even when Studio supplies the contract verdict. The separate
`studio verify --appeal` workflow checks actual local appeal rounds. Public-chain
finality and modern appeal bonds remain outside this release.
