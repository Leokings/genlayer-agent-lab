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

## Optional Studio runtime

When GenVM execution or local appeal checks are requested, read `docs/STUDIO.md`.
Use this installation's `studio build`, `studio up`, `studio status` and
`studio verify` commands. They manage a separate owned Docker Compose project
without taking the Lab database lock. Do not start the upstream full Compose
stack, change the global GenLayer CLI network, inherit wallet/provider keys or
enable paid inference. Build downloads dependencies; the running stack uses
fixtures and an internal network. Its RPC must remain on loopback.

For agents, select `--backend studio` with an optional imported binding. Explicit
Studio failures must remain inconclusive; do not substitute GLSim or fixture
results. Describe actual Studio execution checkpoints separately from scripted
consumer lifecycle events. Use `studio verify --appeal` and inspect a completed
new appeal round before claiming observed local appeal processing. Stable Studio
does not verify modern appeal bond accounting or public-network finality.
Canceling a test may leave an already submitted Studio transaction running.
`studio down` preserves this installation's database and VM cache.
