---
name: setup-genlayer-agent-lab
description: Install or reopen GenLayer Agent Lab, or configure an agent's tools from the Lab's generated setup prompt and connect it to a reviewed GenLayer test.
---

# Set up GenLayer Agent Lab

Choose the requested task first. If the user supplies the dashboard's generated agent setup prompt or a reviewed run's connection settings, use **Connect an existing reviewed run** below. A connection request to an already running Lab does not require installing Studio, authoring a new scenario, or retrieving workspace administrator access.

Read `docs/INSTALL.md` and `docs/GETTING_STARTED.md` from the supplied checkout or exported installation kit. Use the developer's supplied artifact or the official repository at `https://github.com/Leokings/genlayer-agent-lab`. The candidate is not published on PyPI; do not install a similarly named registry package. Report the actual installed version and source revision when available.

This skill's source URL is `https://github.com/Leokings/genlayer-agent-lab/blob/main/skills/setup-genlayer-agent-lab/SKILL.md`. Agents with a local skill mechanism can install the supplied file through that mechanism. On later requests such as “Start my existing GenLayer Agent Lab,” reuse the known installation and data directory instead of reinstalling.

## Connect an existing reviewed run

Use the supplied connection settings and the installed agent host's supported configuration tools. This path requires terminal/configuration access; the prompt itself does not grant missing permissions or add tool support to a chat-only client. If a needed capability is unavailable, identify the exact remaining host-setting action for the user.

For MCP, check the installed host's help/schema, then merge the supplied server definition into its native configuration. For example, the Lab's generic `mcpServers["genlayer-lab"]` entry maps to current OpenClaw's `mcp.servers["genlayer-lab"]`; other hosts use their own format. Retain the supplied absolute command, arguments, and `LAB_MODE`, `LAB_ROLE`, `LAB_URL`, `LAB_RUN_ID`, and run-scoped `LAB_TOKEN`. The bridge is stdio; its `LAB_URL` is the Lab HTTP API, not a remote MCP transport endpoint. Preserve unrelated settings and the user's chosen model.

Apply configuration to the actual agent runtime/session, following that installed host's reload or restart mechanism. A CLI command that reloads only its own process does not establish that the running agent has new tools. If a new chat turn or host-managed approval is required, explain that specific step. Verify an actual `observe` request through the selected connection rather than treating saved configuration or MCP tool discovery as success.

For HTTP or supplied Python/TypeScript clients, use the generated adapter with the agent's existing tool/policy loop. Keep the role and credential scoped to this run. The agent performs the test itself using public observations and declared operations; do not substitute a scripted reference policy or read workspace administrator credentials, private scenario files, or grading rules. Do not print credentials in replies or logs.

After connecting, follow the public task and permissions, preserve retry keys, and finish the run when the requested work is complete. Report observed actions and the run identifier without claiming access to the developer's private grade. If the run has expired, tell the user to provide a fresh reviewed run's settings rather than silently creating a replacement with administrator access.

## Use one primary setup path

The default journey is the supported project Studio environment, followed by the guided workflow dashboard and a real run by the developer's agent. The original GLSim scenarios, `service_release`, and legacy Studio profile are advanced compatibility paths; use them only when requested or relevant to a specific diagnosis.

From a new source checkout:

```console
git clone https://github.com/Leokings/genlayer-agent-lab.git
cd genlayer-agent-lab
uv sync --locked --python 3.12
uv run gl-agent-lab setup
```

For an existing checkout, enter its directory and use the environment already selected. Do not replace global Python or another project's environment. For a supplied wheel, follow `docs/INSTALL.md`, install the exact artifact in a dedicated environment, and export the kit with the installed `gl-agent-lab kit --output NEW_DIRECTORY`. Use that environment's executable paths instead of `uv run`.

`setup` checks prerequisites, initializes the chosen data directory, prepares or starts its owned project Studio stack, and starts the Lab in the foreground or recognizes the same installation already running. It opens the workflow dashboard on a desktop. Use `setup --no-open` on a VPS or when browser opening is unwanted. Use `setup --check` for read-only diagnosis, not as an obligatory extra step on every launch. Supported options include `--data-dir PATH` and `--port 8765`.

## Resolve prerequisites and access

The primary profile requires a running Docker daemon with Compose and Linux x86-64 containers, plus the source installation tools. On Windows, use Docker Desktop's Linux containers; on a headless Linux VPS, use Docker Engine and its Compose plugin. Setup does not install system packages, create a VPS account, or buy resources. If a prerequisite is missing, identify the exact dependency and follow its official host-platform installation instructions within the developer's existing authorization. Do not silently change the intended runtime or claim readiness from a Python package install alone.

Allow the first pinned Studio build to finish; it may take tens of minutes. Report the specific diagnostic if it fails rather than rebuilding repeatedly or deleting state. Reuse the owned stack on subsequent starts. Keep the Lab and Studio loopback bindings. An unsupported local Docker architecture can use an appropriate remote Linux x86-64 host with the developer's access.

State defaults to `~/.genlayer-agent-lab`. Use the same selected data directory for setup, checks, and later sessions. Preserve its credentials and history. If running a background helper on Windows, keep the window hidden unless the developer requests an interactive terminal. Explain which foreground terminal must remain open. Optional startup uses `docs/SERVICES.md`; a Lab user service does not itself arrange Docker or Studio startup.

On a VPS, follow `docs/VPS_QUICKSTART.md` for the SSH tunnel and dashboard login. The dashboard's administrator credential stays with the developer. If it must be retrieved, use a private local input path; never include tokens in the final response, logs, or committed files. `init --show-token` is a credential-display command, not a health check.

## Help the developer complete a real-agent run

Use the guided dashboard: **Choose a test**, **Review test**, **Create test & connect agent**, then **Check connection**. Supported templates include prediction finalization, changed or upheld appeals, message delivery or repair, and evidence investigation. The forms expose supported choices; arbitrary project definitions and other custom rules use specification import under Advanced and `docs/SCENARIO_AUTHORING.md`.

Help the developer review the task, supplied conditions, permissions, and independently expected behavior. Honor review or approval already given for the exact content. Changes to a custom draft's evidence, policy, sources, or expectations require the scenario's normal review process; do not approve a newly model-generated draft merely because it parses. Use the current session's stated expectations when sufficient and ask only when a consequential expected behavior is actually unspecified.

Have the tested agent ready before creating a fresh run: the deadline includes connection time. Select the actual connection method and agent location, then use **Copy agent setup prompt** as the primary handoff to an agent that can manage its tool configuration. It includes the scoped settings and instructions to configure, observe, and begin. The manual **Copy connection settings** and **Copy start prompt** actions remain alternatives. Give the tested agent only its run credential, run ID, Lab URL, and selected adapter settings. Agent MCP uses `LAB_MODE=workflow`, `LAB_ROLE=agent`, `LAB_RUN_ID`, `LAB_TOKEN`, and `LAB_URL`, with an absolute installed executable path when the host requires it. Keep administrator tools in a separate developer connection.

Connect the developer's existing agent through its normal tool configuration or supported client adapter. OpenClaw is not required. A separate test profile should route the relevant operations into the Lab; installing MCP does not reroute arbitrary hardcoded RPC, wallet, or production calls. Report unsupported tool or contract shapes precisely and implement only the integration within the requested scope.

Confirm an actual request from the tested agent. A copied configuration or connection-check click alone is not connection evidence. If requests are absent, inspect the endpoint, scoped credential, executable path, loaded MCP configuration, SSH tunnel, and any container-to-host routing. Avoid exposing secrets while diagnosing them.

## Verify and explain the result

The developer's own agent should observe its task, use the declared operations and permissions, and finish. Inspect the readable report for the actions taken, actual Studio execution and finality, and passed or failed expected checks. Use Advanced JSON only when the diagnostic needs it. Completed lifecycle status is not a passing grade. A failed agent evaluation can be a valid integration result; an inconclusive runtime failure needs diagnosis before judging the agent.

For an installation-only diagnostic, run one scripted control against the active Lab:

```console
uv run gl-agent-lab project verify --case prediction --url http://127.0.0.1:8765 --output installation-check.json
```

Use the actual port and data directory. This check uses supplied contract model responses and a scripted reference agent, so it needs no paid model. Inspect the returned verification and cleanup results. The full maintainer suite is unnecessary unless the user requests it or a specific failure warrants it. Never count scripted success as a completed test of the developer's own agent.

For supplied-evidence cases, read `docs/INVESTIGATION.md`. Findings are decision-linked agent claims, checked against independently reviewed expectations. A request for review is a local recorded disposition; it does not notify someone. Submitting an investigation does not itself submit a protocol appeal or upload new evidence to GenLayer.

## Finish with a usable handoff

Report the installation location, actual version, dashboard URL, and process lifetime; the developer-agent run ID and observed connection; its result and report location; and the next useful action if a check failed. Distinguish that run from any scripted installation control. A developer should be able to return later with a short request, start the same installation, and inspect the saved report.

Use `docs/EXTERNAL_ONBOARDING.md` for trial feedback: time to first real-agent request, unassisted completion, connection clarity, and whether the developer could explain the report. A coding-agent rehearsal is not an independent human developer trial.

Before upgrading a populated installation, follow `docs/RECOVERY.md`: stop its owner, create and verify a backup, retain the previous environment, and use a fresh restore directory for rollback. Lab backups do not include Studio's Docker volumes. Keep a Lab process restart distinct from a whole-machine reboot or disaster-recovery claim. Advanced legacy requirements have their own instructions in `docs/STUDIO.md`, `docs/STUDIO_WORKFLOWS.md`, and `docs/CUSTOM_CONTRACTS.md`.
