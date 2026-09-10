# Start the Lab on your VPS

This path runs the current project Studio environment on a Linux x86-64 server you control. You use the dashboard through an SSH tunnel and can connect your agent from the server or your own computer.

You need an existing VPS account, SSH access, Git, [uv](https://docs.astral.sh/uv/getting-started/installation/), Docker Engine, and Docker Compose available to your installation user. For this short trial, plan for 8 GiB RAM and an 80 GB disk with room for runtime downloads and Docker build cache. This is planning guidance; a minimum server size has not been measured. See [installation](INSTALL.md) if a prerequisite is missing.

The Lab does not provision a VPS account or purchase server resources. Its scripted installation check needs no paid AI model or public GenLayer tokens. Your own agent uses its chosen model and provider.

If your agent already has terminal access to this VPS, you can give it the [setup prompt](INSTALL.md#setup-with-an-agent) to perform the following installation and access steps. After you review a test, **Copy agent setup prompt** in the dashboard lets your agent configure its own supported connection and begin. Terminal and manual connection instructions remain available below.

## Start the installation

In your VPS terminal:

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

In a terminal on your computer, replace the SSH destination:

```sh
ssh -N -L 8875:127.0.0.1:8765 your-user@your-server
```

Keep the tunnel open and browse to [the workflow dashboard](http://127.0.0.1:8875/assets/workflows.html). In a second private VPS terminal, show the installation's dashboard credential:

```sh
cd ~/genlayer-agent-lab
uv run gl-agent-lab init --show-token
```

Enter that administrator token in the dashboard. `init --show-token` does not start the HTTP server; wait for setup to announce that the Lab is serving. If you selected a custom checkout or data directory, use those same paths. Use a private terminal for this command and keep the token out of shared logs. Public ports 8765 and 8796 do not need to be opened.

In Termius, create a **local** forwarding rule using your VPS SSH connection:

| Setting | Value |
|---|---|
| Local address and port | `127.0.0.1:8875` on your computer |
| Destination address and port | `127.0.0.1:8765` on the VPS |

After saving, start/connect that forwarding entry and leave it active. The browser address is `http://127.0.0.1:8875/assets/workflows.html`.

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
