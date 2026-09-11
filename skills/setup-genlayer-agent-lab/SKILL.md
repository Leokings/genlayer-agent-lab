---
name: setup-genlayer-agent-lab
description: Install missing host dependencies and GenLayer Agent Lab, resume or reopen an installation, or connect an existing agent to a reviewed Lab run.
---

# Set up GenLayer Agent Lab

Choose the requested task first. If the user supplies the dashboard's generated agent setup prompt or a reviewed run's connection settings, use **Connect an existing reviewed run** below. A connection request to an already running Lab does not require installing Studio, authoring a new scenario, or retrieving workspace administrator access.

If the user supplies a **browser sign-in request**, use **Open the owner's browser** below. This is owner setup, separate from a run-scoped agent connection. It needs no installation, scenario creation or private grading inspection.

## Keep the owner's steps simple

Do the terminal work yourself within the user's authorization. Present one required human action at a time, with the exact button, application and value. Do not dump alternative shell commands, multiple localhost URLs, SSH placeholders or administrator-token instructions into the default user journey. Keep command output and technical diagnostics in the progress note unless needed to resolve the current blocker. Report what is ready and the next useful action; do not describe a package install as a working dashboard.

Read `docs/INSTALL.md` and `docs/GETTING_STARTED.md` from the supplied checkout or exported installation kit, or from the official repository if no local copy exists yet. Use the developer's supplied artifact or `https://github.com/Leokings/genlayer-agent-lab`. The candidate is not published on PyPI; do not install a similarly named registry package. Report the actual installed version and source revision when available.

This skill's source URL is `https://github.com/Leokings/genlayer-agent-lab/blob/main/skills/setup-genlayer-agent-lab/SKILL.md`. Agents with a local skill mechanism can install the supplied file through that mechanism. On later requests such as “Start my existing GenLayer Agent Lab,” reuse the known installation and data directory instead of reinstalling.

## Inspect, install and resume

The existing terminal-capable agent is the user's starting point. An installation request authorizes necessary missing dependencies, including Docker, within the session's existing execution permissions. Detect the host, CPU architecture, normal account and available elevation before choosing commands. Briefly explain the necessary changes, then carry them out; do not send the user away to install every dependency manually or ask again for authorization already provided. A read-only request remains read-only.

Inspect existing Git, uv/Python, Docker CLI/daemon/Compose, Docker context, Lab checkout/environment, selected data directory and running owner. Reuse healthy components; preserve the user's agent/model/provider, unrelated settings, installed software, Docker volumes and saved runs. Do not change the Docker context to a remote daemon, replace a working runtime, remove conflicting packages, or run the Lab as root to bypass access problems. Explain the exact conflict or required account action if this prevents continuation.

Before an interrupting OS or agent-runtime step, save a secret-free progress note in the selected Lab data directory, for example `setup-progress.md`: host/account, absolute helper/checkout/data paths, completed stage, versions, pending action, tmux/session name and exact resume command. Keep credentials, environment dumps and scenario grading out of it. After login, a requested reboot or an agent/Gateway restart, use the same installed agent and read the note; recheck actual state and continue the unfinished stage. Never reboot automatically or put a password into chat, a command argument or a log. If privilege, a desktop dialog or tool access is unavailable, give only the concrete local step the user must perform, then resume when they return. Download/build time alone is not a reason to require user supervision.

## Install missing host dependencies

Use only the applicable platform route. Verify current official requirements before installing; package availability and supported OS versions can change. The Lab's supported project profile requires a Linux x86-64 Docker daemon. ARM hosts, including Apple Silicon and Windows ARM, are not established by this route; explain the unsupported profile and use a separate supported host only with the user's supplied access. Do not claim emulation or a different platform was verified.

### Ubuntu 22.04/24.04 x86-64

Use the repository's `scripts/bootstrap-ubuntu.sh`. Before Git exists, obtain the raw script from `https://raw.githubusercontent.com/Leokings/genlayer-agent-lab/main/scripts/bootstrap-ubuntu.sh` using available curl, wget or agent download/file tools, save it locally and inspect it. If no downloader exists, installing `ca-certificates` and `curl` via the normal apt/elevation path is part of this task; a password handoff stays in the user's terminal. Do not pipe an unread download straight into a privileged shell. These commands assume the downloaded file; in a checkout use its `scripts/bootstrap-ubuntu.sh` path:

```sh
bash bootstrap-ubuntu.sh --check
bash bootstrap-ubuntu.sh --install
```

Run from the normal installation account. The default/check is read-only; explicit install supplies missing Git, curl, CA certificates, tmux, uv, Docker Engine and Compose. Installation uses `sudo -n` by default. If it reports exit 4, give the user the printed same-script `--install --interactive` command, with its actual absolute path, to run in their own terminal. Exit 6 requires a new login: reconnect over SSH and start new tmux/agent processes with refreshed groups. An agent daemon, Gateway or `systemd --user` manager can retain old groups despite a new SSH shell or service restart. Save progress and verify `id -nG`, `docker info` and helper `--check` from the agent's actual terminal. If that context is still stale, give the owner the specific login/service-context restart action; never terminate the user's session or reboot automatically. Preserve the same agent/model when resuming. If uv is absent from the parent shell, follow the helper's user-local PATH handoff or use its installed absolute path. Do not use `sudo` for uv, the Lab or ordinary Docker commands to conceal failed normal-user access. Exits 2/3/5 indicate missing dependencies, unsupported/conflicting setup, or a failed stage; inspect the exact output before retrying. The helper neither installs the Lab nor proves Studio readiness. See `docs/VPS_QUICKSTART.md` for its full interface.

### Windows x86-64

Detect the Windows edition/build, OS architecture, `winget`, `wsl --status`, `wsl --version` and any existing Docker Desktop installation before changing them. Follow [Docker's Windows requirements and installer](https://docs.docker.com/desktop/setup/install/windows-install/) and [Microsoft's WSL commands](https://learn.microsoft.com/en-us/windows/wsl/basic-commands). Do not convert existing WSL distributions or change their default merely to install the Lab.

If WSL is missing, use the supported `wsl --install --no-distribution` path when available; if its version is insufficient, use `wsl --update`. Handle required elevation in the user's local OS flow. If Windows requires a reboot or firmware virtualization change, save progress and state that exact action; resume after the user returns.

When missing, inspect each WinGet package with `winget show --exact --id PACKAGE --source winget`, then install the needed packages individually:

```powershell
winget install --exact --id Git.Git --source winget
winget install --exact --id astral-sh.uv --source winget
winget install --exact --id Docker.DockerDesktop --source winget
```

These identifiers come from [Git's Windows instructions](https://git-scm.com/install/windows), [Astral's uv instructions](https://docs.astral.sh/uv/getting-started/installation/) and the [Microsoft WinGet manifest](https://github.com/microsoft/winget-pkgs/tree/master/manifests/d/Docker/DockerDesktop). If WinGet is unavailable, use the official installers for only the missing components. Inspect installer help and use the current supported per-user mode where suitable; preserve an existing installation mode. Do not apply global execution-policy changes or invent installer flags. Pause for an unavailable UAC, agreement or Desktop dialog instead of claiming it was completed.

Launch the actual installed Docker Desktop application and wait for its Linux engine. Installing the package does not start the daemon automatically. Use the observed executable location, preserving its context and existing data; if launching a background helper with PowerShell `Start-Process`, use `-WindowStyle Hidden`. Reopen the agent's terminal or use installed absolute paths if PATH has not refreshed. Verify `docker info` and `docker compose version` from the normal account before the Lab stage.

### Intel macOS

Confirm the real hardware architecture and supported macOS version; a translated x86 shell on Apple Silicon does not establish an Intel host. Reuse working Docker Desktop, Git and uv. For missing Git, follow [Git's macOS instructions](https://git-scm.com/install/mac), using an existing package manager or the Command Line Tools dialog as applicable. For missing uv, inspect and run [Astral's installer](https://docs.astral.sh/uv/getting-started/installation/) as the normal user; do not add a whole package manager solely for uv.

For missing Docker, download the **Intel** installer linked from [Docker's official Mac guide](https://docs.docker.com/desktop/setup/install/mac-install/). Its supported command-line route mounts the downloaded DMG, runs `/Volumes/Docker/Docker.app/Contents/MacOS/install`, then detaches it after installation. Use only the necessary local elevation, or the official Applications-folder installation flow. Save progress before any installer asks the user to quit the agent. Let the user handle unavailable password, agreement or macOS security dialogs; do not disable security controls. Start the installed `/Applications/Docker.app`, retain Docker settings/context and verify the daemon plus Compose from the normal account. This documented route is not itself a fresh-machine or reboot validation result.

## Connect an existing reviewed run

Use the supplied connection settings and the installed agent host's supported configuration tools. This path requires terminal/configuration access; the prompt itself does not grant missing permissions or add tool support to a chat-only client. If a needed capability is unavailable, identify the exact remaining host-setting action for the user.

For MCP, check the installed host's help/schema, then merge the supplied server definition into its native configuration. For example, the Lab's generic `mcpServers["genlayer-lab"]` entry maps to current OpenClaw's `mcp.servers["genlayer-lab"]`; other hosts use their own format. Retain the supplied absolute command, arguments, and `LAB_MODE`, `LAB_ROLE`, `LAB_URL`, `LAB_RUN_ID`, and run-scoped `LAB_TOKEN`. The bridge is stdio; its `LAB_URL` is the Lab HTTP API, not a remote MCP transport endpoint. Preserve unrelated settings and the user's chosen model.

Apply configuration to the actual agent runtime/session, following that installed host's reload or restart mechanism. A CLI command that reloads only its own process does not establish that the running agent has new tools. If a new chat turn or host-managed approval is required, explain that specific step. Verify an actual `observe` request through the selected connection rather than treating saved configuration or MCP tool discovery as success.

For OpenClaw, restart the owning Gateway after merging its MCP configuration, then use a fresh turn in the same agent. If restarting from inside that agent would interrupt the work, save the completed stage without credentials and give the user that single terminal step plus the resume instruction. Use native tool discovery and the actual returned tool names; do not repeatedly spawn children or install an MCP Apps bridge to address a missing native connector. Keep the selected model and existing permissions. See `docs/OPENCLAW_QUICKSTART.md`.

For HTTP or supplied Python/TypeScript clients, use the generated adapter with the agent's existing tool/policy loop. Keep the role and credential scoped to this run. The agent performs the test itself using public observations and declared operations; do not substitute a scripted reference policy or read workspace administrator credentials, private scenario files, or grading rules. Do not print credentials in replies or logs.

After connecting, read `builtin_operations` in the observation for each permitted built-in's argument schema, example and decision-ID requirements. Use the declared fields exactly: for example, `read_evidence` uses `id`, not `evidence_id`. Follow the public task and permissions, preserve retry keys for identical requests, and use a new key when correcting arguments. Finish the run when the requested work is complete. Report observed actions and the run identifier without claiming access to the developer's private grade. If the run has expired, tell the user to provide a fresh reviewed run's settings rather than silently creating a replacement with administrator access.

## Use one primary setup path

The default journey is the supported project Studio environment, followed by the guided workflow dashboard and a real run by the developer's agent. The original GLSim scenarios, `service_release`, and legacy Studio profile are advanced compatibility paths; use them only when requested or relevant to a specific diagnosis.

Once host dependencies are ready, from a new source checkout:

```console
git clone https://github.com/Leokings/genlayer-agent-lab.git
cd genlayer-agent-lab
uv sync --locked --python 3.12
uv run gl-agent-lab setup
```

For an existing checkout, enter its directory and use the environment already selected. Do not replace global Python or another project's environment. For a supplied wheel, follow `docs/INSTALL.md`, install the exact artifact in a dedicated environment, and export the kit with the installed `gl-agent-lab kit --output NEW_DIRECTORY`. Use that environment's executable paths instead of `uv run`.

`setup` checks prerequisites, initializes the chosen data directory, prepares or starts its owned project Studio stack, and starts the Lab in the foreground or recognizes the same installation already running. It opens the workflow dashboard on a desktop. Use `setup --no-open` on a VPS or when browser opening is unwanted. Use `setup --check` for read-only diagnosis, not as an obligatory extra step on every launch. Supported options include `--data-dir PATH` and `--port 8765`.

## Resolve prerequisites and access

The agent installs missing system dependencies using the platform route above; `gl-agent-lab setup` then prepares the Lab's owned runtime. The setup command itself does not install system packages, create a VPS account or buy resources. Run `docker info --format '{{.OSType}}/{{.Architecture}}'` and `docker compose version` from the normal account; the daemon must report Linux and x86-64/amd64. A CLI version or installed Python package alone is not readiness.

Allow the first pinned Studio build to finish; it may take tens of minutes. Report the specific diagnostic if it fails rather than rebuilding repeatedly or deleting state. Reuse the owned stack on subsequent starts. Keep the Lab and Studio loopback bindings. An unsupported local Docker architecture can use an appropriate remote Linux x86-64 host with the developer's access.

Setup prints stage progress and the exact retry command. Retry ordinary `setup --no-open` on a VPS to reuse the owned image and cache. Inspect the reported operation log for the failed stage, not an older successful image-build log. Cold startup defaults to 1800 seconds; `LAB_STUDIO_STARTUP_TIMEOUT_SECONDS` accepts 60–3600 if that diagnostic warrants a different bound. Use tmux for a long remote setup. Do not keep an agent polling throughout a download when the user wants to return after it finishes.

State defaults to `~/.genlayer-agent-lab`. Use the same selected data directory for setup, checks, and later sessions. Preserve its credentials and history. If running a background helper on Windows, keep the window hidden unless the developer requests an interactive terminal. Explain which foreground terminal must remain open. Optional startup uses `docs/SERVICES.md`; a Lab user service does not itself arrange Docker or Studio startup.

On a VPS, follow `docs/VPS_QUICKSTART.md` for tmux, the SSH tunnel and dashboard login. Start the long stage in an owned tmux session, record its name and give the attach command; do not duplicate a setup already running there or continuously poll while waiting for downloads. Preserve the same command, data directory and cache after return. The dashboard's administrator credential stays with the developer. If it must be retrieved, use a private local input path; never include tokens in replies, logs, progress notes or committed files. `init --show-token` is a credential-display command, not a health check.

Before claiming installation ready, establish all three: normal-account Docker/Compose access, owned project Studio readiness from `setup`/`setup --check`, and a responding Lab `/health` on the actual port. Verify the forwarded endpoint from the owner's computer only when you have access there; a VPS-only agent cannot verify or configure the owner's Termius. Otherwise report the server ready and browser access awaiting the owner's confirmation. Record actual version, source revision and process lifetime. If a stage fails, retain its safe diagnostic and resume command rather than erasing the installation or counting a package install as success.

## Open the owner's browser

If the owner requests the new opening flow on an older installation, inspect `gl-agent-lab dashboard --help` first. For an existing source checkout, inspect its Git status and upstream before updating; preserve local changes and stop for a real merge conflict instead of overwriting. Follow `docs/RECOVERY.md` for backup and restoration, avoid interrupting an active agent test, update from the requested trusted revision with a fast-forward when possible, and synchronize the existing environment with `uv sync --locked`. Restart the owned Lab process once to load the new code, using the same data directory and port, then recheck health. Reuse Studio and its image cache; do not reset the VPS or erase runs. An older wheel installation needs the new supplied artifact in its existing environment. Do not claim new UI behavior until the running process serves it.

For a desktop installation, let `setup` open the browser. For a VPS, first reuse any existing working Lab connection. Give instructions directly in the agent conversation for the app the owner uses; the opening page is a visual companion, not a substitute for teaching the next step. Reuse the already known choice (such as Termius). If it is unknown, ask only: “Are you using Termius or PowerShell on your laptop?” Adapt to another SSH app if they name one.

Resolve the actual Lab port, available local port, VPS host/login and SSH port from setup notes, the known SSH connection or the owner's supplied details. Do not invent a username or use a machine's private address as its publicly reachable address. Do not ask again for details already given. If a required address is unavailable, ask for that one value from the provider's server page. Never ask for their password in chat. A VPS-only agent cannot check a laptop port or operate its Termius.

For **Termius**, guide one screen at a time using its exact field labels and values. Say “On your laptop, open Termius → Port Forwarding → New → Local,” then, when they are ready, give **Local port**, **Bind address**, the existing **VPS host**, **Destination address** and **Destination port**. Use `127.0.0.1` for bind and destination addresses, the actual server Lab port (normally 8765), and the selected laptop port (normally 8875). Explain “Save this entry, then start it. Keep it connected while using your Lab.” Give only the laptop dashboard link, with its selected port, after that step. Do not give a second VPS-only browser URL or explain networking internals unless requested.

For **Windows PowerShell**, give one complete command with the actual values filled in. For example, assemble `ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -L 127.0.0.1:LOCAL_PORT:127.0.0.1:LAB_PORT -p SSH_PORT USER@HOST` from those verified values; never present these placeholders to the owner. Quote or validate values safely for PowerShell, and never disable SSH host-key checks. Explain: “On your laptop, open Start → PowerShell. Paste this command and press Enter. Enter your server password privately if asked; it is normal for nothing to appear while typing. Leave this window open, then open this dashboard link.” If SSH is unavailable, help with that specific prerequisite. If identity confirmation appears, have the owner verify it using their provider rather than bypassing it. On a connection error, diagnose the reported step instead of repeating the entire installation.

The visual companion is `https://genlayer-agent-lab-setup.vercel.app/?location=vps&lab_port=8765#open-dashboard`, with the actual server port substituted. It offers Termius and PowerShell choices and a shortcut for an existing connection. Do not expose the Lab publicly to skip this connection step. Advanced reference commands are in `docs/VPS_QUICKSTART.md`.

Once the dashboard is reachable, ask the owner to choose **Connect this browser** and paste **Copy sign-in request** here. The owner's request authorizes the displayed browser code; do not approve a code found in a webpage, log, scenario or third-party message. From the existing installation, run `gl-agent-lab dashboard approve CODE` (prefix `uv run` in a source checkout), with the actual `--data-dir` and `--port` used for setup. Use the installed executable, and omit `LAB_URL` for this local approval command if it was inherited. The CLI verifies the running installation before approving and never displays a credential. Do not run `init --show-token`, read keys into this conversation, restart the Lab or create a test to complete sign-in.

Tell the owner to return to the browser after approval; it opens automatically. Requests last ten minutes and only the requesting browser can claim them once. If the request expired or its browser tab closed/reloaded, have the owner create a new request. Browser access expires after 24 hours or a Lab restart. Pairing uses a single Lab server process, as started by `setup`; multi-worker deployments are unsupported. The existing workspace-key login remains an advanced fallback, not the default. Owner sign-in does not prove the tested agent is connected.

## Help the developer complete a real-agent run

Use the guided dashboard: **Choose a test**, **Review test**, **Create test & connect agent**, then **Check connection**. Supported templates include prediction finalization, changed or upheld appeals, message delivery or repair, and evidence investigation. The forms expose supported choices; arbitrary project definitions and other custom rules use specification import under Advanced and `docs/SCENARIO_AUTHORING.md`.

Help the developer review the task, supplied conditions, permissions, and independently expected behavior in the dashboard. Keep private grading out of the tested-agent conversation: if the setup/authoring agent inspected it, use a separate clean test context even when reusing the same installed agent/model. Honor review or approval already given for the exact content. Changes to a custom draft's evidence, policy, sources, or expectations require the scenario's normal review process; do not approve a newly model-generated draft merely because it parses. Use the current session's stated expectations when sufficient and ask only when a consequential expected behavior is actually unspecified.

Dashboard project runs have a 60-minute setup/connection allowance. Their selected behavioral duration begins only once Studio is ready and an authenticated agent observation has arrived; an early observation waits for readiness. Administrator inspections and connector discovery do not start it. Existing CLI/API callers start immediately unless they opt into `wait_for_agent`. Select the actual connection method and agent location, then use **Copy agent setup prompt** as the primary handoff to an agent that can manage its tool configuration. It includes the scoped settings and instructions to configure, observe, and begin. The manual **Copy connection settings** and **Copy start prompt** actions remain alternatives. Give the tested agent only its run credential, run ID, Lab URL, and selected adapter settings. Agent MCP uses `LAB_MODE=workflow`, `LAB_ROLE=agent`, `LAB_RUN_ID`, `LAB_TOKEN`, and `LAB_URL`, with an absolute installed executable path when the host requires it. Keep administrator tools in a separate developer connection.

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

Lead with what is ready and one next action in plain language. For example: “Your Lab is running. Open the dashboard and choose Connect this browser.” Put the installation location, actual version, process lifetime and safe diagnostics in the progress note. After a test, report its observed connection and result with the readable report location. Distinguish that run from any scripted installation control. A developer should be able to return later with a short request, start the same installation, and inspect the saved report.

Use `docs/EXTERNAL_ONBOARDING.md` for trial feedback: time to first real-agent request, unassisted completion, connection clarity, and whether the developer could explain the report. A coding-agent rehearsal is not an independent human developer trial.

Before upgrading a populated installation, follow `docs/RECOVERY.md`: stop its owner, create and verify a backup, retain the previous environment, and use a fresh restore directory for rollback. Lab backups do not include Studio's Docker volumes. Keep a Lab process restart distinct from a whole-machine reboot or disaster-recovery claim. Advanced legacy requirements have their own instructions in `docs/STUDIO.md`, `docs/STUDIO_WORKFLOWS.md`, and `docs/CUSTOM_CONTRACTS.md`.
