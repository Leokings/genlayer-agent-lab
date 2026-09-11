# Install GenLayer Agent Lab

The primary setup runs the Lab and its owned project Studio stack on your machine. After installation, follow [your first agent run](GETTING_STARTED.md). For a remote Linux machine, use [the VPS quickstart](VPS_QUICKSTART.md).

Open the [public setup page](https://genlayer-agent-lab-setup.vercel.app) before installing anything and copy its prompt into your existing terminal-capable agent. The page requires no Lab installation, account or login. It includes installing missing dependencies, including Docker; you do not need a second agent installation or a prepared developer toolchain. The Lab and its data remain on your own machine. The [prompt below](#setup-with-an-agent) is also available as a fallback.

The agent checks your host, explains the necessary changes and proceeds within its access. You handle a password, OS dialog, new login or reboot if required, then resume with the same agent. A prompt cannot supply unavailable administrator rights, virtualization or terminal tools.

## What your agent prepares

| Requirement | What to check |
|---|---|
| Git and [uv](https://docs.astral.sh/uv/getting-started/installation/) | Install missing tools; retain existing working installations |
| Python 3.12 | `uv sync --locked --python 3.12` creates the project environment |
| Docker Engine and Docker Compose | Install if missing, start the daemon and verify access from the normal installation account |
| Linux x86-64 Docker environment | Required by the supported project Studio profile; on Windows use Docker Desktop's Linux containers |
| Resources and internet access | For a short VPS trial, plan for 8 GiB RAM and an 80 GB disk, with room for runtime downloads and Docker build cache. These are planning estimates, not a measured minimum |

On a headless Linux server, use Docker Engine with its Compose plugin. On a machine whose Docker architecture is unsupported, use an appropriate Linux x86-64 host and connect through an SSH tunnel. Setup reports the environment it detects; a Python-only installation does not establish Studio readiness.

A VPS account and access to that machine must already exist; the setup agent can install its software dependencies within your authorization. The Lab has no shared hosting account to sign into. Its scripted installation check needs no paid model key, production wallet, or public-network tokens. Your own tested agent keeps its chosen model and provider. Node.js 24 is needed only when running the TypeScript example.

The [setup skill](../skills/setup-genlayer-agent-lab/SKILL.md#install-missing-host-dependencies) gives the concrete platform steps: the bundled helper for Ubuntu 22.04/24.04 x86-64, Docker Desktop with WSL 2 on supported Windows x86-64, and Docker Desktop on supported Intel macOS. ARM hosts and other distributions need a separately supported route; do not treat emulation as verified Lab support. These instructions are an installation path, not a claim that every fresh-host trial has passed.

## Advanced: install from source

Use the [official repository](https://github.com/Leokings/genlayer-agent-lab), a supplied source checkout, or an extracted source archive. This candidate is not published on PyPI. The commands below use the source you actually checked out; record its revision if you need a reproducible trial.

```sh
git clone https://github.com/Leokings/genlayer-agent-lab.git
cd genlayer-agent-lab
uv sync --locked --python 3.12
uv run gl-agent-lab setup
```

These terminal commands assume the dependencies above are ready. If you already have the source, enter that directory and reuse its environment. The agent setup path installs missing host dependencies before running these commands. `gl-agent-lab setup` itself checks dependencies and prepares the owned runtime; it is not the system-package installer. For a diagnostic without starting services, run `uv run gl-agent-lab setup --check`.

`setup` initializes the selected data directory, builds or starts its project Studio stack, and starts the Lab in the foreground, or recognizes the same installation if it is already running. On a desktop it opens the workflow dashboard with a local authentication handoff. On an SSH session it prints headless connection instructions. Use `--no-open` to request that explicitly.

The first Studio preparation can take tens of minutes. Setup reports the current stage and elapsed time every 30 seconds. Download, image build, and Compose startup each save a bounded, redacted diagnostic in `studio-modern/operation-logs/` under the data directory. A failure identifies its own stage, exit status, and log path.

Cold runtime preparation and service startup have a default 1800-second wait. Set `LAB_STUDIO_STARTUP_TIMEOUT_SECONDS` to a whole number from 60 to 3600 to change that bounded wait. The Docker command has a further 30 seconds to exit. If preparation takes longer, inspect the failed stage and run ordinary `setup` again: it reuses the owned image, database, and completed precompile cache. A failed Compose wait alone does not identify its underlying cause.

On an SSH server, use [tmux and the resume instructions](VPS_QUICKSTART.md#leave-setup-running-and-return-later) if you want to disconnect during preparation. Wait for setup to announce that the Lab is serving before opening the dashboard. `init --show-token` retrieves the workspace key; it does not start the Lab or establish readiness.

## Choose where it runs

State defaults to `~/.genlayer-agent-lab`. To keep a separate installation and use a different Lab port:

```sh
uv run gl-agent-lab setup --data-dir /absolute/path/to/my-lab --port 8875
```

Use the same data directory on subsequent commands. The default Lab address is `http://127.0.0.1:8765`; the default project Studio RPC port is 8796. Setup does not expose them publicly. For server access, follow [the tunnel instructions](VPS_QUICKSTART.md).

Keep the foreground setup terminal open. To use the Lab later, run `setup` again from the same environment. Optional [user startup services](SERVICES.md) have their own platform requirements; they do not automatically arrange Docker or Studio startup. Follow [recovery and backups](RECOVERY.md) before upgrading an installation with saved runs.

## From a supplied wheel

A trusted supplied wheel is an alternative to a checkout. Check it against its supplied `SHA256SUMS`, create an environment at a stable path, and install the exact file you received. Replace the placeholder below with its actual filename; this is not a registry install.

```sh
uv venv --python 3.12 .lab-env
```

On Linux/macOS:

```sh
uv pip install --python .lab-env/bin/python /path/to/supplied-wheel.whl
.lab-env/bin/gl-agent-lab kit --output lab-kit
.lab-env/bin/gl-agent-lab setup
```

On Windows PowerShell:

```powershell
uv pip install --python .lab-env/Scripts/python.exe C:/path/to/supplied-wheel.whl
.lab-env/Scripts/gl-agent-lab.exe kit --output lab-kit
.lab-env/Scripts/gl-agent-lab.exe setup
```

The artifact must include the commands described in this guide; check its actual version and `setup --help`. The kit contains documentation, the setup skill, contracts, and runnable examples. Open `lab-kit/docs/GETTING_STARTED.md`. For example commands, replace `uv run gl-agent-lab` and `uv run python` with the installed environment's absolute executable paths, and use paths inside the exported kit. The source archive includes `uv.lock`; wheel installation resolves transitive dependencies through the package index.

## Setup with an agent

Use **Copy setup prompt** on the [public setup page](https://genlayer-agent-lab-setup.vercel.app), or paste the text below into your existing terminal-capable agent. It links to the [setup skill](../skills/setup-genlayer-agent-lab/SKILL.md) and authorizes necessary dependency installation:

```text
Set up GenLayer Agent Lab on this machine using https://github.com/Leokings/genlayer-agent-lab/blob/main/skills/setup-genlayer-agent-lab/SKILL.md. I authorize installing missing prerequisites, including Git, uv/Python and Docker with Compose, then installing and starting the Lab and its owned Studio environment. First detect this host, architecture, available access and existing installation. Briefly state the changes, then proceed within your available permissions. Preserve my agent, model, unrelated settings, software and saved data. Reuse completed setup stages. Pause only when a password, OS dialog, login, reboot or unavailable capability requires me; never request passwords in chat or reboot automatically. Before an interruption, save a secret-free progress note and give the exact next action so we can resume with this same agent. On a VPS, leave long setup stages in tmux and tell me how to return. Verify the running Docker service, Studio readiness and Lab health, then help me open the dashboard and review a first test. Keep workspace keys and private grading out of the tested agent's context; use the dashboard's generated agent setup prompt only after I review and create that test.
```

For local use, a downloaded checkout or exported kit includes [START.html](START.html). GitHub shows that HTML file as source. An installed Lab also serves the setup page at `/setup`; neither download nor installation is needed for the public page.

An agent that supports local skills can install the supplied file using its normal mechanism. If setup pauses, complete the exact local action it gives you, then say “Resume GenLayer Agent Lab setup from the saved progress note.” It should inspect the current stage and continue with the same installation. Later, “Start my existing GenLayer Agent Lab” reopens it. OpenClaw is optional.

The installation prompt above prepares software. After you review and create a test, **Copy agent setup prompt** supplies a different message containing that run's private connection key. Send it only to the agent you want to test, in a context that has not seen private grading. That message connects to the existing run; it must not reinstall the Lab or create another test. Manual connection settings remain available for hosts without configuration tools.

Dashboard-created project tests allow up to 60 minutes for Studio preparation and agent connection. The selected full test duration starts once Studio is ready and the agent has made an authenticated `observe` request. Saving configuration, discovering tools, and pressing **Check connection** do not start that timer. CLI/API-created runs keep their immediate timer by default; HTTP clients can opt into the separate setup allowance with `wait_for_agent: true` in the create request. If you use OpenClaw, follow [its short connection guide](OPENCLAW_QUICKSTART.md).

## Check the installation, then test your agent

Follow [GETTING_STARTED.md](GETTING_STARTED.md). If you need a scripted Studio installation check before connecting your agent, run this in another terminal while setup remains running:

```sh
uv run gl-agent-lab project verify --case prediction --url http://127.0.0.1:8765 --output installation-check.json
```

Use the actual Lab port and data directory if you changed them. Expect `verification: pass`, with actual Studio execution and restored cleanup in the saved report. This is a scripted control with supplied model responses, not a test of your own AI agent. An inconclusive infrastructure result needs diagnosis; a failed agent evaluation can be a valid test outcome.

The lightweight `doctor` and original GLSim tests remain available for their separate compatibility profile. They are not prerequisites for the primary Studio path and do not replace a failed Studio readiness check.
