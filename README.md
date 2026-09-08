# GenLayer Agent Lab

A self-hosted testing toolkit for agents that consume GenLayer decisions. Connect through HTTP, Python, TypeScript or MCP. No OpenClaw dependency, central account, production wallet or hosted database is required.

[Source repository](https://github.com/Leokings/genlayer-agent-lab) · [Release artifacts](https://github.com/Leokings/genlayer-agent-lab/releases) · [Installation](docs/INSTALL.md)

**Version 0.1.0a11 — development candidate.** Test how agents use GenLayer with developer-supplied possible model responses. [Project workflows](docs/PROJECT_WORKFLOWS.md) support typed operations/results, multi-file contracts, defined multi-contract effects, actual local appeals and fee accounting, and recovery after a Lab interruption. Developers supply reviewed behavior rules and can add scenarios using templates or their own authoring agent. See [build status](docs/BUILD_STATUS.md) and the dated [verification record](docs/VERIFICATION.md) for passed and open gates. Contract-LLM judgment evaluation and public-network settlement are outside scope.

[Investigation scenarios](docs/INVESTIGATION.md) test missing, stale, conflicting
and misleading evidence, with decision-linked findings and concise agent
summaries. The [build audit](docs/BUILD_AUDIT.md) maps requirements to implemented
behavior and records the remaining VPS/developer validation.

Install from a supplied wheel or source archive with the [installation guide](docs/INSTALL.md). The wheel includes a setup kit and all three agent examples.

The [external developer trial checklist](docs/EXTERNAL_ONBOARDING.md) includes
the expected passing and failing results and a report template. For an owned
Studio stack, `studio verify-recovery` checks finalized results and a new write
across a controlled container restart; see [Studio recovery checks](docs/STUDIO.md#verify-recovery-from-a-controlled-restart).

## Start the project profile

Use Python 3.12, [uv](https://docs.astral.sh/uv/getting-started/installation/), Git
and Docker with Linux containers on your laptop or VPS. From this checkout:

```sh
uv sync --locked --python 3.12
uv run gl-agent-lab init
uv run gl-agent-lab project studio-build
uv run gl-agent-lab serve
```

The first Studio build downloads its pinned runtime. In another terminal:

```sh
uv run gl-agent-lab project verify --case prediction --url http://127.0.0.1:8765
```

This runs a scripted installation check with no paid model. Connect your own
agent using the [project integration guide](docs/PROJECT_WORKFLOWS.md), which
also explains dashboard access through an SSH tunnel from a VPS. The project
Studio RPC uses loopback port 8796. Subsequent starts use `project studio-up`.
For a short server installation check, use [the VPS quickstart](docs/VPS_QUICKSTART.md).

The original 18 scenarios and `service_release` workflows remain available as
separate compatibility profiles. Their commands are below.

## Original GLSim quickstart

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) if necessary, then run these commands from this project directory:

```sh
uv sync --locked --python 3.12
uv run gl-agent-lab init
uv run gl-agent-lab doctor
uv run gl-agent-lab run escrow-normal --agent safe
```

The first `doctor` downloads a pinned, SHA256-verified GenVM SDK archive (about 217 MB). Subsequent evaluations reuse the checked cache. Each evaluation still starts a new process and clean contract state. No API key or model charge is involved in these fixture-backed tests.

To test the engine without executing the contract, explicitly select `--backend fixture`. Reports identify that mode as having no contract execution. The default backend is `glsim`.

```sh
uv run gl-agent-lab run escrow-provisional --agent unsafe
uv run gl-agent-lab run escrow-normal --agent refuse
uv run gl-agent-lab suite
```

The safe reference should pass; the unsafe and refusing references should fail their intended checks. CLI exit codes are **0 success/pass, 1 evaluation failure, 2 infrastructure or input error**. These reference programs are scripted controls, not AI performance claims.

## Run the local workspace

```sh
uv run gl-agent-lab serve
```

Open http://127.0.0.1:8765. In another terminal, reveal the local administrator token for the dashboard:

```sh
uv run gl-agent-lab init --show-token
```

State defaults to `~/.genlayer-agent-lab`. Use `--data-dir PATH` or `LAB_DATA_DIR` to select another installation. The dashboard token stays in its tab's session storage. Keep it separate from tested-agent credentials.

While the service is running, use `--url` to access its state:

```sh
uv run gl-agent-lab --url http://127.0.0.1:8765 run escrow-normal --wait
uv run gl-agent-lab --url http://127.0.0.1:8765 status
uv run gl-agent-lab --url http://127.0.0.1:8765 report RUN_ID --format html --output report.html
```

Only one process may own a data directory. Local commands that would open a second engine fail with a connection hint. `serve` currently runs in the foreground; a user-owned process supervisor can run it on a server. Access a remote installation through an SSH tunnel to its loopback port. Use `service install --start` for user startup; see [startup services](docs/SERVICES.md) for platform requirements.

## Connect your agent

The service creates a separate token for each external test. The agent uses that token to observe its task, request/read a decision, attempt an action and finish. It cannot list other runs, change scenarios or use administrator endpoints.

The runnable examples are [Python](examples/python_agent.py), [TypeScript](examples/typescript/agent.ts) and [MCP stdio](examples/mcp_agent.py); see [integration instructions](examples/README.md). All three use the same HTTP engine. Their tool adapters can be incorporated into an existing agent. Configure a test profile to route the relevant actions to this environment; installing MCP does not automatically replace production wallet calls.

For MCP, use the installed `gl-agent-lab-mcp` command with:

```text
LAB_URL=http://127.0.0.1:8765
LAB_ROLE=agent
LAB_RUN_ID=<created-run-id>
LAB_TOKEN=<that-run-token>
```

Use `LAB_ROLE=admin` only in a developer-controlled client with the administrator token. Runs are owned by the HTTP service, so closing an MCP client does not delete test history.

## Scenarios and reports

The 18 bundled cases span **escrow, treasury and general decision consumers**. Each pack covers normal approval, provisional approval, revised decisions, execution timeouts, wrong scope and lost acknowledgements/duplicate requests. All balances are integer test units.

Reports grade **decision, behavior, outcome and completion** separately. A blocked unsafe attempt still fails behavior. A refusing agent fails completion. Provider/runtime failures, cancellation and interruption are inconclusive. Exact retries recover an action result without repeating its effect. An agent must reconcile an injected lost acknowledgement before finishing successfully.

The dashboard supports history, failure inspection, timeline, comparison and JSON/HTML/JUnit downloads. Full reports include immutable scenario configuration, originating toolkit version, runtime hashes and execution provenance. These locally owned reports are diagnostics, not independent certifications.

Custom scenarios use validated YAML, bounded to 64 KiB, with no executable code. Obtain the schema with:

```sh
uv run python -c "import json; from genlayer_agent_lab.models import Scenario; print(json.dumps(Scenario.model_json_schema(), indent=2))"
uv run gl-agent-lab import-scenario my-scenario.yaml
```

Custom scenarios must use a new ID and one of the existing protocol models. Existing reports retain the scenario version they ran. New protocol execution semantics still require adapter code.

## Test your own contract

Describe the source file, constructor arguments, public write method, scenario inputs, result mapping and controlled model response in a binding file. The [delivery example](examples/contracts/delivery-binding.yaml) calls a custom method and maps its nested JSON result. It does not require changes to the agent interface.

With a running **Linux Docker engine** on your own machine or server:

```sh
uv run gl-agent-lab worker doctor
uv run gl-agent-lab worker build
uv run gl-agent-lab --data-dir .lab/custom import-binding examples/contracts/delivery-binding.yaml
uv run gl-agent-lab --data-dir .lab/custom run escrow-normal --binding delivery-assessment --agent safe
uv run gl-agent-lab --data-dir .lab/custom suite --binding delivery-assessment
```

`worker doctor` reports a missing image until the explicit build succeeds. The first build downloads a pinned Python image and locked dependencies; fixture execution uses no paid model service. Imports run locally while the selected data directory's service is stopped. Then start `serve` with that data directory and select the binding in the dashboard, or send `backend: "container-glsim"` and `binding_id` through HTTP, a client or MCP.

Each run stores an immutable copy of its binding and source. The Docker worker has no network or host mounts, a read-only root, a nonroot user and resource limits. Custom source is never executed by the native worker. A missing or failing container produces an inconclusive report. See [custom contract integration](docs/CUSTOM_CONTRACTS.md) for the schema, limits and evidence boundaries.

## Setup through an agent

Give your coding agent the setup skill in `skills/setup-genlayer-agent-lab/SKILL.md` from this checkout. Ask it to install the package, run `doctor`, start the service, and demonstrate a safe and unsafe test. The skill invokes actual CLI commands and reports missing prerequisites. It does not invent access to your production wallet or silently reconfigure unrelated agents.

## Development and packaging

```sh
uv run pytest -q
uv run ruff check src tests
uv build
```

`genlayer-test`'s automatic pytest plugins are disabled because our contract checks run through owned worker processes; the upstream plugin otherwise loads ambient configuration and clears an artifacts directory.

Browser verification uses the development-only Playwright dependency:

```sh
npm ci
npx playwright install chromium
npm run verify:dashboard
```

That script expects a running server, a token at `.lab/demo/admin.token` (override with `LAB_DATA_DIR`), and tests launching, grading, comparison, download and mobile layout. It reads the token directly without printing it.

Alpha 9 passed [native Linux, macOS and Windows package CI](https://github.com/Leokings/genlayer-agent-lab/actions/runs/34131594830), including fresh installed-wheel checks: GLSim readiness, all 18 fixture scenarios, Python HTTP, MCP stdio and setup-kit export. Its separate Linux Docker-worker job passed five live tests and all 18 custom-binding scenarios. These CI checks do not exercise Studio workflows, whole-machine reboot or human onboarding; alpha 9 has separate local Windows installation and Studio workflow evidence.

Windows user startup was exercised locally; alpha 5 passed the native Linux systemd user-service lifecycle, and alpha 6 passed the native macOS LaunchAgent lifecycle. Alpha 6 also recovered automatically after a real Ubuntu guest OS reboot with administrator-configured lingering. Alpha 8 passed recovery after an orderly Windows 11 guest reboot and a real standard-user AutoLogon, with no manual service start.

macOS reboot, separate desktop logout/login, Windows startup before login, automatic Studio startup and physical power-loss recovery remain unverified. See the [verification record](docs/VERIFICATION.md) for exact evidence. No paid model calls are part of the default suite.

To verify all three external clients against an already running service and imported delivery binding:

```sh
uv run python scripts/verify-custom-clients.py
```

The script defaults to the demo installation at `.lab/demo`; use its `--help` options for another data directory or loopback URL. It passes only run-scoped credentials to the agent processes and saves a concise local verification record.

See [architecture](docs/ARCHITECTURE.md), [build status](docs/BUILD_STATUS.md), [verification](docs/VERIFICATION.md), [troubleshooting](docs/TROUBLESHOOTING.md) and [third-party notices](THIRD_PARTY.md).
