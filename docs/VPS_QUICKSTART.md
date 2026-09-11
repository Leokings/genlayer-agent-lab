# Start the Lab on your VPS

This path runs the current project Studio environment on a Linux x86-64 server you control. You use the dashboard through an SSH tunnel and can connect your agent from the server or your own computer.

Open the [public setup page](https://genlayer-agent-lab-setup.vercel.app) and copy its prompt into an agent with terminal access to your existing VPS's normal user account. The page needs no Lab installation, account or login; your Lab and test data stay on your VPS. The [installation prompt](INSTALL.md#setup-with-an-agent) is also available directly in the docs.

Your agent installs missing Git, uv, Docker Engine and Compose before starting the Lab. Ubuntu 22.04/24.04 x86-64 has a bundled prerequisite helper. For this short trial, plan for 8 GiB RAM and an 80 GB disk with room for downloads and Docker cache. This is planning guidance; a minimum server size has not been measured.

The Lab does not provision a VPS account or purchase server resources. Its scripted installation check needs no paid AI model or public GenLayer tokens. Your own agent uses its chosen model and provider.

You handle a password or new SSH login only when required, then tell the same agent to resume. Long builds can run in tmux while you leave. After you review a test, the dashboard's separate **Copy agent setup prompt** connects the tested agent to that run. Advanced terminal instructions follow.

## Ubuntu prerequisite helper

The agent can obtain this helper before Git is installed. Download it to a local file with an available HTTPS tool, inspect it, then run its read-only check from a normal user account:

```sh
curl --fail --location --output bootstrap-ubuntu.sh https://raw.githubusercontent.com/Leokings/genlayer-agent-lab/main/scripts/bootstrap-ubuntu.sh
bash bootstrap-ubuntu.sh --check
```

If `curl` is missing, use `wget` or the agent's download/file tools. If none are available, the agent can install `ca-certificates` and `curl` using the host package manager within the same authorization; it should give you only the exact password/capability handoff if blocked.

After inspecting the proposed changes, the agent uses:

```sh
bash bootstrap-ubuntu.sh --install
```

The helper supplies Git, curl, CA certificates, tmux, uv, Docker Engine and Compose as needed. It uses `sudo -n` by default. If it reports that a password is required, run its printed `--install --interactive` command in your own terminal; keep the password out of chat. Use the helper's saved absolute path when returning from a different directory.

If it adds Docker group membership, reconnect over SSH, start a fresh tmux session and rerun `--check`. A running agent, Gateway or user-service manager can retain old groups even after a service restart. Your agent must check Docker access from its actual terminal and, if still blocked, give the specific login/service-context action needed before resuming from its saved note. It should not terminate your session or reboot for you. If uv is absent in the parent shell, follow the printed user-local PATH instruction. The helper preserves working local Docker installations and reports conflicting packages or remote contexts for resolution instead of replacing them. It does not install the Lab itself.

Exit codes: `0` ready, `2` missing dependencies in check mode, `3` unsupported host or conflicting setup, `4` privilege handoff, `5` installation/service failure, `6` new login required, `64` invalid usage. A nonzero result is a resume or diagnosis point, not installation success.

## Start the installation

Once dependencies are ready, in your VPS terminal:

```sh
git clone https://github.com/Leokings/genlayer-agent-lab.git
cd genlayer-agent-lab
uv sync --locked --python 3.12
uv run gl-agent-lab setup --no-open
```

If you already have the checkout, enter that directory and run `setup --no-open`. Use the source or supplied artifact version intended for your trial; these commands do not install a candidate from a package registry.

Setup checks prerequisites, prepares or starts the owned project Studio stack, and starts the Lab. Keep this terminal open while its foreground Lab is running. The first preparation can take tens of minutes; setup prints its stage and elapsed time every 30 seconds. For a read-only diagnostic, use `uv run gl-agent-lab setup --check`.

## Leave setup running and return later

For an unattended SSH installation, start a tmux session before running setup. If tmux is missing on Ubuntu, install it with `sudo apt install tmux`.

```sh
tmux new -s genlayer-lab
```

Inside that session, enter the checkout and run `uv run gl-agent-lab setup --no-open`. To disconnect while it works, press **Ctrl+B**, release the keys, then press **D**. After reconnecting to the VPS:

```sh
tmux attach -t genlayer-lab
```

Inspect the existing session before starting another setup process. Closing a plain SSH terminal can interrupt foreground setup; detaching tmux keeps it running. Once the Lab is serving, that same tmux session keeps its foreground process alive.

If setup ended with an error, open the specific stage log it printed. Logs live in `~/.genlayer-agent-lab/studio-modern/operation-logs/` by default and include elapsed time, timeout, exit status, and redacted output. A Compose failure has its own log, separate from an earlier successful image build.

Ordinary `uv run gl-agent-lab setup --no-open` resumes the owned installation and reuses completed image/precompile work. Keep the same `--data-dir` if you selected one. A failed wait may leave containers still preparing; it does not establish why startup failed. Cold startup waits up to 1800 seconds by default. If the stage diagnostic warrants more time, request a bounded longer wait:

```sh
LAB_STUDIO_STARTUP_TIMEOUT_SECONDS=3600 uv run gl-agent-lab setup --no-open
```

The accepted range is 60–3600 seconds, with 30 additional seconds for the Docker command to exit. Repeated image builds or deleting the data directory are unnecessary for an ordinary startup retry.

## Open the dashboard from your computer

After installation, your setup agent should guide you through connecting from your laptop using the app you already use. With **Termius**, it gives the exact fields and values one screen at a time. With **PowerShell**, it gives one complete command with your server details already filled in, tells you where to paste it and explains which window stays open. It should ask which app only when that is not already known.

Use [Open my dashboard](https://genlayer-agent-lab-setup.vercel.app/?location=vps#open-dashboard) as a visual companion. Choose **A VPS**, then **Termius** or **Windows PowerShell**. If your Lab connection already works, open the dashboard directly. You only need to save the Termius forwarding entry once and start it when returning; a PowerShell connection remains open while its window stays open.

In the dashboard:

1. Choose **Connect this browser**.
2. Choose **Copy sign-in request** and paste it into the agent that installed your Lab.
3. Return to the dashboard. It signs in automatically after your agent approves.

You do not need to copy a workspace key. The approval request authorizes your browser to open the owner workspace; it does not start a test or connect a tested agent. Keep that page open while approving. If it expires or you reload it, request a new code.

### Advanced: terminal connection and approval

The following is optional for people who prefer a terminal. Replace the SSH destination with your actual saved host or login:

```sh
ssh -N -L 8875:127.0.0.1:8765 your-user@your-server
```

Keep the tunnel open and browse to [your dashboard](http://127.0.0.1:8875/). Choose **Connect this browser**. In a second VPS terminal, approve the displayed code:

```sh
cd ~/genlayer-agent-lab
uv run gl-agent-lab dashboard approve CODE_FROM_YOUR_BROWSER
```

Use the installation's actual data directory and port (`--data-dir` and `--port`) if customized. Approval verifies that this installation is running before using its owner credential, and never displays it. No server, agent or Studio restart is needed for sign-in. Public ports 8765 and 8796 do not need to be opened.

Browser requests last ten minutes and can be claimed only once by the requesting browser. Browser sign-in lasts up to 24 hours or until the Lab restarts; it is separate from the saved installation key. The normal `setup` server uses one worker, which is required for this in-memory sign-in flow. Workspace-key login remains under Advanced for existing integrations.

In Termius, create a **local** forwarding rule using your VPS SSH connection:

| Setting | Value |
|---|---|
| Local address and port | `127.0.0.1:8875` on your computer |
| Destination address and port | `127.0.0.1:8765` on the VPS |

After saving, start/connect that forwarding entry and leave it active. The browser address is `http://127.0.0.1:8875/`.

If the page does not open, check the VPS side first, in its terminal:

```sh
curl --fail --max-time 5 http://127.0.0.1:8765/health
```

If that cannot connect, inspect the setup/tmux session: the Lab is not yet reachable on the VPS. If it succeeds, check the forwarded side on your own computer. In Windows PowerShell:

```powershell
curl.exe --fail --max-time 5 http://127.0.0.1:8875/health
```

Use `curl` on Linux/macOS. If only the forwarded side fails, start the tunnel and check its local port, destination, and SSH connection. A delivered dashboard still needs its workspace login and Studio readiness before a test can run.

## Connect and test your agent

Follow [your first agent test](GETTING_STARTED.md): choose a template, review its expected behavior, create a test, and paste **Copy agent setup prompt** into your agent. Dashboard project tests have a separate 60-minute setup allowance. Once Studio is ready and the agent has made its first authenticated `observe`, it receives the full selected test duration. Configuration, tool discovery, and **Check connection** do not consume that behavioral duration. CLI/API runs start their timer immediately by default; HTTP creation can request `wait_for_agent: true` for the separate setup allowance.

An agent on your computer uses the tunnel address, `http://127.0.0.1:8875`. An agent running directly on the VPS uses `http://127.0.0.1:8765`. A separate container needs a reachable route to the Lab host; its own loopback address is not the host. The dashboard's connection check records actual run-scoped requests so you can see whether the configured agent reached the run.

For OpenClaw, use the [short connection guide](OPENCLAW_QUICKSTART.md), including the owning Gateway restart and a fresh chat with the same agent. The agent receives the generated test credential; the administrator token stays with you.

## Optional installation check

Before using your own agent, you can run one scripted control in a second VPS terminal:

```sh
uv run gl-agent-lab project verify --case prediction --url http://127.0.0.1:8765 --output vps-check.json
```

Expect `verification: pass`. This deploys two contracts, requests a decision, waits for finality, and records the result once. Inspect a failure's report instead of replacing Studio with a fixture backend or repeatedly rebuilding. The full maintainer suite is unnecessary for a normal first installation.

This check establishes the scripted local workflow. Complete a separate run with your own agent to establish its connection and evaluate its behavior. The [onboarding checklist](EXTERNAL_ONBOARDING.md) helps record both outcomes.

## Return later or stop

Return to the checkout and run `setup --no-open` again to start the same installation. Optional [user startup services](SERVICES.md) have separate host requirements; they do not arrange Docker or Studio startup automatically. `project studio-down` stops the owned Studio stack while preserving its volumes. Follow [backup and recovery instructions](RECOVERY.md) before upgrades.

Creating a fresh reviewed test gives it fresh contracts, a run ID, and a test key; it reuses the Lab installation. An expired test stays expired. Restarting the Lab or rebooting the VPS does not renew a run's deadline, and neither requires erasing the installation. After a reboot, reconnect and use the same setup command; automatic startup has the separate requirements in [Services](SERVICES.md).

Closing the foreground Lab terminal stops that Lab process. On Vultr, powering off the VPS still incurs instance charges; destroying it stops those instance charges and permanently deletes its data. Back up what you need before choosing deletion in the provider console. [Vultr billing for stopped instances](https://docs.vultr.com/support/platform/billing/are-stopped-instances-still-billed-on-vultr).
