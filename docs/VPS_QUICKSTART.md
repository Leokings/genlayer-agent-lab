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

Setup checks prerequisites, prepares or starts the owned project Studio stack, and starts the Lab. Keep this terminal open while its foreground Lab is running. The first build can take tens of minutes. For a read-only diagnostic, use `uv run gl-agent-lab setup --check`.

## Open the dashboard from your computer

In a terminal on your computer, replace the SSH destination:

```sh
ssh -N -L 8875:127.0.0.1:8765 your-user@your-server
```

Keep the tunnel open and browse to [the workflow dashboard](http://127.0.0.1:8875/assets/workflows.html). In a second private VPS terminal, show the installation's dashboard credential:

```sh
uv run gl-agent-lab init --show-token
```

Enter that administrator token in the dashboard. If you selected a custom data directory, include the same `--data-dir PATH` when retrieving it. Use a private terminal for this command and keep the token out of shared logs. Public ports 8765 and 8796 do not need to be opened.

Follow [your first agent test](GETTING_STARTED.md): choose a template, review its expected behavior, create a test, and paste **Copy agent setup prompt** into your agent. Have the agent ready before creating the timed run. Give the tested agent that run's credential, not the administrator token.

An agent on your computer uses the tunnel address, `http://127.0.0.1:8875`. An agent running directly on the VPS uses `http://127.0.0.1:8765`. A separate container needs a reachable route to the Lab host; its own loopback address is not the host. The dashboard's connection check records actual run-scoped requests so you can see whether the configured agent reached the run.

## Optional installation check

Before using your own agent, you can run one scripted control in a second VPS terminal:

```sh
uv run gl-agent-lab project verify --case prediction --url http://127.0.0.1:8765 --output vps-check.json
```

Expect `verification: pass`. This deploys two contracts, requests a decision, waits for finality, and records the result once. Inspect a failure's report instead of replacing Studio with a fixture backend or repeatedly rebuilding. The full maintainer suite is unnecessary for a normal first installation.

This check establishes the scripted local workflow. Complete a separate run with your own agent to establish its connection and evaluate its behavior. The [onboarding checklist](EXTERNAL_ONBOARDING.md) helps record both outcomes.

## Return later or stop

Return to the checkout and run `setup --no-open` again to start the same installation. Optional [user startup services](SERVICES.md) have separate host requirements; they do not arrange Docker or Studio startup automatically. `project studio-down` stops the owned Studio stack while preserving its volumes. Follow [backup and recovery instructions](RECOVERY.md) before upgrades.

Closing the foreground Lab terminal stops that Lab process. It does not shut down the VPS or stop provider billing.
