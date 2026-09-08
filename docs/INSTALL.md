# Install on your own machine or server

GenLayer Agent Lab is a Python package. Use Python 3.12 for the verified setup.
Docker is optional for bundled GLSim tests and required for custom contracts or
local Studio. Node.js 24 is needed only for the TypeScript example. No model key,
hosted account or paid provider is required for the bundled fixture-backed tests.

Use the [official repository](https://github.com/Leokings/genlayer-agent-lab) or
its [release artifacts](https://github.com/Leokings/genlayer-agent-lab/releases).
This alpha is not published on PyPI. Do not install a similarly named package
from a registry. A supplied source archive or wheel also works.

The expanded project workflows require the alpha 10 candidate. Use the supplied
candidate wheel or source's actual version; earlier artifacts do not include
these project interfaces. For the combined build's terminal setup, Studio
startup and VPS dashboard tunnel, follow [project workflows](PROJECT_WORKFLOWS.md).
Its Docker-based Studio profile is required for project runs. The lightweight
GLSim commands below remain a separate installation check.

## From the source checkout or extracted source archive

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and run:

If you do not already have the source, clone
`https://github.com/Leokings/genlayer-agent-lab.git` and enter that directory.
Use a published release tag when you need a fixed version.

```sh
uv sync --locked --python 3.12
uv run --locked gl-agent-lab --version
uv run --locked gl-agent-lab doctor
uv run --locked gl-agent-lab run escrow-normal --agent safe
uv run --locked gl-agent-lab serve
```

Keep the environment at a stable path if you install automatic startup. The
server listens at `http://127.0.0.1:8765`. State defaults to
`~/.genlayer-agent-lab`; choose `--data-dir PATH` for a separate installation.

## From a supplied wheel

Check the wheel against its supplied `SHA256SUMS` before installation. A matching
checksum detects a changed file; obtain the checksum and artifact from a source
you trust. Create a dedicated environment at a permanent path:

```sh
uv venv --python 3.12 .lab-env
```

On Linux/macOS:

```sh
uv pip install --python .lab-env/bin/python /path/to/genlayer_agent_lab-0.1.0a10-py3-none-any.whl
.lab-env/bin/gl-agent-lab kit --output lab-kit
.lab-env/bin/gl-agent-lab doctor
.lab-env/bin/gl-agent-lab run escrow-normal --agent safe
.lab-env/bin/gl-agent-lab serve
```

On Windows PowerShell:

```powershell
uv pip install --python .lab-env/Scripts/python.exe C:/path/to/genlayer_agent_lab-0.1.0a10-py3-none-any.whl
.lab-env/Scripts/gl-agent-lab.exe kit --output lab-kit
.lab-env/Scripts/gl-agent-lab.exe doctor
.lab-env/Scripts/gl-agent-lab.exe run escrow-normal --agent safe
.lab-env/Scripts/gl-agent-lab.exe serve
```

`kit` exports the setup skill, documentation and runnable Python/TypeScript/MCP
examples to a new directory. A checkout is unnecessary. In those examples,
replace `uv run --locked python` with the absolute Python path in this installed
environment, and `uv run --locked gl-agent-lab` with its installed CLI path.
Run example file paths relative to the exported `lab-kit` directory.
If you received this guide as a standalone file, open `lab-kit/docs/INSTALL.md`
after export so its relative documentation links resolve inside the kit.

The source archive carries `uv.lock` for the locked development environment.
Wheel installation resolves transitive dependencies through the package index;
the release verification records what was actually installed. Direct runtime
dependencies and the downloaded GenVM archive are pinned.

## Prompt-driven setup

Give a coding agent the supplied setup skill and this request, substituting your
actual artifact and installation paths:

> Install GenLayer Agent Lab from the supplied artifact in a dedicated Python
> environment on this machine. Read its setup skill. Use a separate test data
> directory. Verify actual bundled contract execution, show a passing safe test
> and a failing provisional-decision test, start the loopback service, and
> connect my agent through HTTP or MCP with a run-scoped credential. Report
> the version, endpoint, run IDs and report locations. If I requested automatic
> startup, install it for my current user and verify it.

The agent runs normal installation commands; MCP is an integration interface
after setup. The skill can work with any coding agent that can read its text and
execute the required commands. It does not depend on OpenClaw or on Codex.

Use [SERVICES.md](SERVICES.md) for optional automatic startup and
[RECOVERY.md](RECOVERY.md) before upgrading a populated installation. A VPS can
run the same package under its own user. Keep the API on loopback and use an SSH
tunnel or run the tested agent alongside it. A Linux user service running after
logout requires the host's user-session/linger policy; installing the Lab does
not silently change that policy.

## First integration check

Read [the example instructions](../examples/README.md). Route the agent's decision
and action calls through the lab in a separate test profile. The lab provides
the test environment and grades its observed behavior; it does not intercept
unmodified wallet or production tool calls.

The default `doctor` verifies the downloaded runtime and runs a bundled
contract. Its first preparation can take several minutes to download and verify
about 217 MB. The setup deadline defaults to 900 seconds; use
`doctor --timeout 1800` for a slower connection. Ordinary evaluation deadlines
remain unchanged. `--backend fixture` deliberately skips contract execution. Never
substitute fixture success for a failed GLSim, Docker or Studio requirement.

For a release installation trial, record the OS, Python/toolkit versions,
artifact checksum, doctor result, safe/unsafe run IDs, report export, connection
method and startup behavior. Share sanitized reports and errors, never token
files or the whole data directory.
