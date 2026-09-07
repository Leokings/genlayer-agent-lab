# Build status

This is alpha 8. Status follows executable behavior, not the original release target.

Deferred expansion: [Job 1 — larger contract projects and protocol extensions](BACKLOG.md#job-1-support-larger-contract-projects-and-protocol-extensions).
Revisit with the user after the current developer version is finished; it is
outside the current build scope.

Research review: [agent investigation, appeals and decision workflows](AGENT_WORKFLOW_RESEARCH.md).
Its proposed coverage is not implemented and does not change Job 1's saved scope.

Scope clarification, September 7: developers supply possible contract outcomes
and workflow rules; the Lab tests the agent's behavior. Evaluating the contract
LLM's understanding of evidence is outside the product scope. The recommended
next extension is the structured-response/workflow portion of Job 1, with tests
built alongside it; larger contract-project support can follow later.

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
| Framework-independent connection | Authenticated HTTP, Python client, TypeScript client, MCP bridge | Broader framework adapters and real model-driven validation |
| Scenario packs | 18 cases across escrow, treasury and generic decisions; declarative YAML import | Domain-specific cases and new protocol semantics |
| Custom contracts | Verified GLSim Docker worker and Studio GenVM delivery-contract execution; YAML bindings, immutable snapshots, Python/TypeScript/MCP/HTTP/CLI/dashboard selection, cancellation and bounded resources | Deferred: multi-file contracts and custom dependency sets |
| GenLayer fidelity | GLSim plus an owned local Studio backend using GenVM, bounded SDK calls and separate observed conformance evidence | Separate future verification: modern bond accounting and public-network compatibility; contract-LLM judgment evaluation is outside scope |
| Usability | CLI, dashboard, report export, packaged setup kit, native Windows/Linux/macOS user-service lifecycle, real Ubuntu guest reboot recovery with administrator-configured lingering, orderly Windows 11 guest reboot and standard-user AutoLogon recovery, offline backup/restore, upgrade/rollback procedure | macOS reboot and separate desktop logout/login trials; recurring schedules are deferred |
| Recovery | Exclusive-lock SQLite snapshots, integrity/checksum verification, fresh restore destinations, credential rotation, preserved historical reports; explicit Studio restart verifier with retained volumes | Studio-volume disaster recovery and interrupted-consensus recovery remain separate, unimplemented capabilities |
| Publication | Dedicated [public repository](https://github.com/Leokings/genlayer-agent-lab), wheel/source alpha, checksums and passing installed-artifact CI matrix | Complete two external developer installation trials before claiming broader release validation |

The Docker execution gate has passed: the pinned worker image builds, executes its readiness contract and runs the custom delivery contract through real client connections. The install/startup/recovery work from the September 13–16 stages is implemented. Native package CI passed on all three operating systems. Alpha 5 fixed a Linux startup defect; alpha 6 fixes exact macOS argument verification and asynchronous job unloading. The corrected native Linux and macOS service lifecycles passed on free standard CI runners. A separate Ubuntu guest OS reboot passed with administrator-configured lingering and an outside observer; the Lab recovered before its user logged in. Alpha 8 passed an [orderly Windows 11 guest reboot and real standard-user AutoLogon trial](https://github.com/Leokings/genlayer-agent-lab/actions/runs/34048951171): the owned login task recovered the Lab without a manual start, preserved its data and completed a fresh four-grade GLSim run.

Remaining validation includes two human external installations, macOS reboot, separate desktop logout/login, Windows startup before login, automatic Studio startup and physical power-loss recovery. The native runtime remains restricted to bundled code. See the dated verification record for tested behavior and evidence limits.

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
