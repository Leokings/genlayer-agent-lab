# Build status

This is alpha 4. Status follows executable behavior, not the original release target.

| Planned area | Implemented now | Remaining release work |
|---|---|---|
| Standalone package and runtime | Pinned Python dependencies, verified GenVM artifact, worker-only Windows compatibility wrapper, clean installed-wheel verification script | Execute the remote OS CI matrix; consult verification for local Windows/Linux-container results |
| Complete escrow test | Actual bundled contract execution, independent grades, safe/unsafe/refusing reference agents | Real developer-agent onboarding |
| Framework-independent connection | Authenticated HTTP, Python client, TypeScript client, MCP bridge | Broader framework adapters and real model-driven validation |
| Scenario packs | 18 cases across escrow, treasury and generic decisions; declarative YAML import | Domain-specific cases and new protocol semantics |
| Custom contracts | Verified GLSim Docker worker and Studio GenVM delivery-contract execution; YAML bindings, immutable snapshots, Python/TypeScript/MCP/HTTP/CLI/dashboard selection, cancellation and bounded resources | Multi-file contracts, custom dependency sets and live providers |
| GenLayer fidelity | GLSim plus an owned local Studio backend using GenVM, bounded SDK calls and separate observed conformance evidence | Live-model evaluation, modern bond accounting and public-network compatibility |
| Usability | CLI, dashboard, report export, packaged setup kit, user startup management, offline backup/restore, upgrade/rollback procedure | Native Linux/macOS startup trials; recurring schedules are deferred |
| Recovery | Exclusive-lock SQLite snapshots, integrity/checksum verification, fresh restore destinations, credential rotation, preserved historical reports | Separate Studio-volume disaster recovery; recovery is scoped to Lab SQLite |
| Publication | Dedicated [public repository](https://github.com/Leokings/genlayer-agent-lab), local wheel/source package, checksum bundle preparation and installed-artifact CI matrix | Record remote CI results, publish a marked prerelease and complete two external installation trials |

The Docker execution gate has passed: the pinned worker image builds, executes its readiness contract and runs the custom delivery contract through real client connections. Alpha 4 adds install/startup/recovery work from the September 13–16 stages of the original plan. The remaining release gates include external developer installations and the remote operating-system CI jobs. The native runtime remains restricted to bundled code. See the dated verification record for tested behavior and evidence limits.

## Position against the original schedule

| Original stage | Current position |
|---|---|
| September 5–6: runtime/platform feasibility | Runtime execution proven on Windows and Linux containers; independent Linux/macOS runner results still required |
| September 7–9: service, workers, trace storage, first escrow case | Implemented and verified end to end |
| September 10–12: all scenarios, bindings, dashboard and integration | 18 scenarios implemented; Python/TypeScript/MCP verified. OpenClaw is optional, with no dedicated host trial claimed |
| September 13–14: setup skill, packaging, startup, optional Studio | Implemented; artifact and startup evidence recorded separately by platform |
| September 15–16: recovery, upgrades, documentation, external onboarding | Local recovery/upgrade verification passed; release preparation and remote CI in progress; two external developer trials remain open |

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
