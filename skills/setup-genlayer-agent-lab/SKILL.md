---
name: setup-genlayer-agent-lab
description: Install and configure GenLayer Agent Lab from supplied source or a wheel, verify its runtime, and connect a separate agent test profile through HTTP or MCP. Use when a developer asks to set up or diagnose this toolkit on their own machine or server.
---

# Set up GenLayer Agent Lab

Use the developer's supplied checkout or release artifact, or the official source at `https://github.com/Leokings/genlayer-agent-lab`. The alpha is not published on PyPI: do not fetch a similarly named registry package. For a release bundle, read its standalone `INSTALL.md` to install the wheel and export the kit. Then read `docs/INSTALL.md` and `examples/README.md` from the source or exported kit. Report the actual version and source revision when available.

## Install and check

From the toolkit directory, use `uv sync --locked --python 3.12` to create an isolated environment from the checked-in lockfile. If `uv` is unavailable, use its official installation instructions for the host OS. Do not replace the developer's global Python or another project's environment.

For a wheel, follow `docs/INSTALL.md`: create a dedicated environment, install the exact supplied wheel, and run its installed `gl-agent-lab kit --output NEW_DIRECTORY` to export these instructions and the examples. Use the installed environment's executable paths in place of `uv run --locked` below. Do not require a source checkout for a wheel installation. Keep the environment in a stable location if automatic startup is requested.

Use these implemented commands:

```console
uv run --locked gl-agent-lab --version
uv run --locked gl-agent-lab init
uv run --locked gl-agent-lab doctor
uv run --locked gl-agent-lab scenarios
```

The default state directory is `~/.genlayer-agent-lab`; `--data-dir PATH` selects another installation. Initialization preserves an existing token. Do not expose `admin.token` in logs or commit it. `init --show-token` is an explicit secret-display action, not a necessary health check.

If the runtime check fails, inspect its error before proceeding. Distinguish native Windows from WSL2 results. A fixture test validates orchestration only; it is not evidence that a GenLayer contract executed.

## Verify execution and start the service

For the expanded multi-contract project profile, use the project setup below.
The original GLSim smoke test in this section is useful when that backend is
requested; it does not verify project Studio readiness.

Choose an identifier returned by `scenarios` and run:

```console
uv run --locked gl-agent-lab run SCENARIO_ID --agent safe --wait
```

Inspect the actual result and runtime provenance. The default backend is GLSim. Do not silently substitute `--backend fixture` when contract execution fails.

Also run `escrow-provisional --agent unsafe` and verify an evaluation failure (exit 1) with an actual report; a missing runtime (exit 2) is not the intended failure. Start the foreground service with `uv run --locked gl-agent-lab serve` and check `http://127.0.0.1:8765/health`. For a background terminal/helper on Windows, keep its window hidden unless the user requests an interactive window.

If automatic startup is part of the developer's request, read `docs/SERVICES.md` and use `service install --start`, then `service status`. Otherwise explain the foreground process lifetime. User startup is distinct from a machine-wide boot service; Linux after-logout availability depends on the host's user-session policy. Report an unavailable OS manager precisely instead of installing a different global service.

When the service is running, use `--url http://127.0.0.1:8765` on CLI operations to reach it. Do not start an offline engine against the same state directory. Open the local dashboard and authenticate with the installation's administrator token through a private input path.

## Connect an agent

Follow `examples/README.md` for Python, TypeScript and MCP connections. Create a **separate test profile** with a run-scoped token, and route its relevant decision/action tools through the lab. Preserve existing production configuration. Installing MCP alone does not intercept production wallet calls.

If the agent has hardcoded side effects, identify the functions that need an adapter and implement the requested test integration without changing the production default. Explain any unsupported tool or contract shape rather than claiming universal compatibility.

For MCP, resolve the absolute installed executable path and set `LAB_ROLE=agent`, `LAB_RUN_ID`, `LAB_TOKEN` and `LAB_URL`. Give the tested agent only its run token. Keep developer/admin MCP tools in a separate connection. No OpenClaw installation is required.

## Finish with evidence

Report the installation location, actual version, service URL and process lifetime; the test's run ID, verdict and report location; and whether a GenLayer contract actually executed. Distinguish scripted reference behavior from a real model-driven agent. List a remaining failed prerequisite precisely instead of marking setup complete. Before upgrading an existing installation, follow `docs/RECOVERY.md`: stop its owner, create and verify a backup, keep the old environment, and use a fresh restore directory for rollback. Lab backups do not include Studio's Docker database volumes.

The lightweight native runtime runs bundled contracts with mocked evidence. Alpha 2 custom bindings use the optional Docker worker: read `docs/CUSTOM_CONTRACTS.md`, run `worker doctor`, explicitly build with `worker build`, and locally `import-binding` while the selected data directory's service is stopped. A binding selects `container-glsim`; never run custom source in the native worker or substitute fixture execution after a Docker failure. Preserve existing run history during the schema upgrade.

Report whether an actual container executed, separately from passing interface/unit tests.

## Project Studio: expanded agent workflows

For agents reacting to structured decisions, appeals, fees and declared
multi-contract effects, read `docs/PROJECT_WORKFLOWS.md`. This profile requires
Docker with Linux containers and uses a separately owned modern Studio stack.
When Docker is missing, install Docker Engine and its Compose plugin using the
official host-platform instructions within the requested setup authorization.
Use Engine on a headless Linux VPS; confirm daemon access for the installation
user before building. A new VPS may need these dependencies even when the Lab
Python package installs successfully.
From source, initialize the chosen data directory and run:

```console
uv run --locked gl-agent-lab project studio-build --port 8796
uv run --locked gl-agent-lab project studio-status
uv run --locked gl-agent-lab serve --port 8765
```

Pass the same `--data-dir PATH` to each command if using a custom directory. For
an installed wheel, use its installed command and exported kit. The first build
downloads pinned dependencies; let the command finish without repeated rebuilds.
Subsequent starts use `project studio-up`. The Lab user service starts the Lab;
it does not build or start Studio implicitly. Check Studio readiness separately.

Use `project verify --case prediction --timeout 900 --url http://127.0.0.1:8765`
for a bounded installation smoke test. Use the complete verifier only when a
full verification run is requested. It requires no model key: contract model
responses are controlled fixtures and reference agents are scripted. The tested
developer agent keeps its own model. Do not claim it has been tested merely
because a reference agent passed.

Project bindings define source files, pinned dependencies, contract addresses
within the created run, typed operations and state reads. Read
`docs/PROJECT_BINDINGS.md` and `docs/SCENARIO_AUTHORING.md` when adapting a
developer project. Generated scenarios are drafts; the developer reviews the
expected behavior before approving their exact content digest. This is not an
assessment of whether the contract's LLM reached a correct real-world judgment.

For evidence investigation, read `docs/INVESTIGATION.md`. Use
`project investigate-template PROJECT --mode missing --output case.draft.json`
to draft a case; other modes cover stale, contradictory, misleading, supporting
and appeal-worthy supplied evidence. Findings are submitted through the existing
`submit_investigation` operation and appear in the report with their decision
and evidence references. They are agent-authored claims, not a Lab certification
of their truth. `request_review` records a disposition; it does not contact a
person, and submitting findings does not itself submit a protocol appeal.

`project verify-investigation --case all --transport mixed --url URL` is the
maintainer's broader investigation check. For an ordinary VPS installation,
the short prediction check above is sufficient unless diagnosing a problem or
the user explicitly requests the wider suite.

The existing HTTP, Python, TypeScript and run-scoped MCP workflow interfaces
also accept project runs. Set `LAB_MODE=workflow` for agent MCP. Project numeric
values use the documented lossless integer encoding; use the supplied clients
instead of rounding fees or interpreting every decimal-looking string as a
number. The workflow dashboard imports approved scenario JSON files and shows
the resulting contract states, fees, child effects and reports.

On a VPS, bind the Lab and Studio to loopback and open the dashboard through an
SSH tunnel, as described in `docs/PROJECT_WORKFLOWS.md`. Installing on the
developer's VPS does not require an account on a shared Lab hosting service.

Project workflows journal signed submission identity and reconcile after a Lab
process interruption with Studio state intact. Preserve the installation's
`admin.token`, which protects that journal, and the Studio volumes. Follow
`docs/RECOVERY.md` for backups and restored histories; a restored archive is
different from restarting the same installation. Require observed finality,
execution success and completed appeal rounds before reporting verification.

## Legacy Studio runtime

When GenVM execution or local appeal checks are requested, read `docs/STUDIO.md`.
Use this installation's `studio build`, `studio up`, `studio status` and
`studio verify` commands. They manage a separate owned Docker Compose project
without taking the Lab database lock. Do not start the upstream full Compose
stack, change the global GenLayer CLI network, inherit wallet/provider keys or
enable paid inference. Build downloads dependencies; the running stack uses
fixtures and an internal network. Its RPC must remain on loopback.

For the agent-driven `service_release` profile, read `docs/STUDIO_WORKFLOWS.md`.
Use `workflow validate` on a supplied case and optional workflow binding, then
`workflow create --url <local-Lab-URL> --show-agent-token` against the persistent
service. Give the tested agent only the returned run credential. For MCP set
`LAB_MODE=workflow`, `LAB_ROLE=agent`, `LAB_RUN_ID` and the run-only `LAB_TOKEN`.
The workflow dashboard is `/assets/workflows.html`. A reference case tests the
installation; it does not establish compatibility with an arbitrary developer
contract. Custom bindings currently map the get_state/evaluate/release roles to
the supported typed service-release state. Observe actual successful execution,
completed appeal rounds and any later recomputed result before claiming success.
An ambiguous submission or unresolved cleanup is inconclusive and must not be
replayed. This candidate does not automatically resume a workflow after an abrupt
Lab stop. Keep this limitation distinct from ordinary agent reconnection.

For the original 18 agent scenarios, select `--backend studio` with an optional
imported binding. Explicit
Studio failures must remain inconclusive; do not substitute GLSim or fixture
results. Describe actual Studio execution checkpoints separately from scripted
consumer lifecycle events. Use `studio verify --appeal` and inspect a completed
new appeal round before claiming observed local appeal processing. Stable Studio
does not verify modern appeal bond accounting or public-network finality.
Canceling a test may leave an already submitted Studio transaction running.
`studio down` preserves this installation's database and VM cache.

When the developer requests a Studio restart recovery check, use
`studio verify-recovery --output <new-evidence-file>` after stopping the Lab
service and pausing other Studio writers. Studio must already be healthy, with
no unfinished transactions. Inspect the returned checks and cleanup status;
do not claim host reboot, automatic Studio startup, interrupted-consensus
recovery or PostgreSQL disaster recovery from this result. Restart the Lab
service after the check. Use `docs/EXTERNAL_ONBOARDING.md` to collect human
developer trial feedback; an agent-assisted rehearsal alone does not count as
an independent human installation.
