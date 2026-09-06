# GenLayer Agent Lab

A self-hosted testing toolkit for agents that consume GenLayer decisions. Connect through HTTP, Python, TypeScript or MCP. No OpenClaw dependency, central account, production wallet or hosted database is required.

[Source repository](https://github.com/Leokings/genlayer-agent-lab) · [Release artifacts](https://github.com/Leokings/genlayer-agent-lab/releases) · [Installation](docs/INSTALL.md)

**Version 0.1.0a5 — developer alpha.** Test bundled or custom GenLayer contracts with controlled model responses. GLSim provides fast local execution; the optional [Studio backend](docs/STUDIO.md) uses GenVM and records observed local consensus checkpoints. Agent scenario events remain scripted, with separate conformance checks for Studio appeals. Live-model evaluation and public-network settlement are outside this release. See [build status](docs/BUILD_STATUS.md) and the dated [verification record](docs/VERIFICATION.md).

Install from a supplied wheel or source archive with the [installation guide](docs/INSTALL.md). The wheel includes a setup kit and all three agent examples.

## Start from this checkout

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

Native Linux, macOS and Windows CI passed, including fresh installed-wheel checks and TypeScript integration. The separate Docker job passed its live checks and all 18 custom-binding scenarios. Windows user startup was exercised locally, and alpha 5 passed the native Linux systemd user-service lifecycle. Native macOS startup and full-machine reboot/logout trials remain open. See the [verification record](docs/VERIFICATION.md) for exact evidence. No paid model calls are part of the default suite.

To verify all three external clients against an already running service and imported delivery binding:

```sh
uv run python scripts/verify-custom-clients.py
```

The script defaults to the demo installation at `.lab/demo`; use its `--help` options for another data directory or loopback URL. It passes only run-scoped credentials to the agent processes and saves a concise local verification record.

See [architecture](docs/ARCHITECTURE.md), [build status](docs/BUILD_STATUS.md), [verification](docs/VERIFICATION.md), [troubleshooting](docs/TROUBLESHOOTING.md) and [third-party notices](THIRD_PARTY.md).
