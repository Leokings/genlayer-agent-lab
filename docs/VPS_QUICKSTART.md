# Short VPS trial

Use the alpha 11 candidate identified in the verification record. This trial
checks installation and access on your server after the maintainer's main
verification. It does not require a paid AI model or public GenLayer tokens.

Use a Linux x86-64 VPS with Docker Engine, Docker Compose, Git and `uv` available
to your installation user. Allow roughly 8 GiB RAM for Studio, Docker and some
headroom, plus disk space for several gigabytes of runtime downloads and Docker
cache. This is planning guidance; the minimum VPS size has not been measured.
If dependencies are missing, the [setup skill](../skills/setup-genlayer-agent-lab/SKILL.md)
can guide a coding agent through installing them. A supplied wheel can be used
instead of source; see [installation](INSTALL.md).

For a first installation, download the source in your VPS terminal:

```sh
git clone https://github.com/Leokings/genlayer-agent-lab.git
cd genlayer-agent-lab
```

If you already cloned it, enter that directory. The following commands use the
alpha 11 candidate from the current main branch:

```sh
uv sync --locked --python 3.12
uv run gl-agent-lab init
uv run gl-agent-lab project studio-build
uv run gl-agent-lab serve
```

Leave the last command running. The first Studio build downloads and compiles
dependencies; it can take tens of minutes. Later starts use
`uv run gl-agent-lab project studio-up`. The Lab and Studio run on this VPS;
there is no Lab-owned hosting account to connect.

In a second SSH terminal, from the same directory:

```sh
uv run gl-agent-lab project verify --case prediction --url http://127.0.0.1:8765 --output vps-check.json
```

Expect `verification: pass`. This scripted example deploys two contracts, asks
for a decision, waits for finality, and records the result once. It usually takes
a few minutes. A failure should retain its report; do not repeatedly rebuild
Studio or erase data to turn it green. The full verifier is unnecessary for this
short onboarding check unless its result reveals a specific remaining problem.

On your laptop, open a separate terminal and replace the SSH destination:

```sh
ssh -N -L 8875:127.0.0.1:8765 your-user@your-server
```

Keep that tunnel open. Browse
[the workflow dashboard](http://127.0.0.1:8875/assets/workflows.html). In your
private VPS terminal, `uv run gl-agent-lab init --show-token` shows the local
administrator token to enter in the dashboard. Select the completed project run
to inspect its evaluation, contracts and recorded state. Public ports 8765 and
8796 do not need to be opened.

After this passes, connect the agent you actually want to test using
[the project integration guide](PROJECT_WORKFLOWS.md#connecting-an-agent).
That agent receives a new run's scoped credential and keeps its own model.
Python, TypeScript, HTTP and MCP are supported; installing OpenClaw is unnecessary.
The scripted installation check is separate from testing that agent's behavior.

The foreground Lab stops when its terminal closes. For persistent startup, use
[the user service instructions](SERVICES.md); Studio readiness is checked
separately. `project studio-down` stops Studio while preserving its volumes.
Stopping application processes does not stop your VPS provider's billing.
