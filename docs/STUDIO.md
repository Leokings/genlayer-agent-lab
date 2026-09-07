# Local Studio and GenVM

The optional `studio` backend deploys and executes the contract in GenVM through
an owned local GenLayer Studio stack. GLSim remains the faster default. Studio
uses controlled model responses; this does not measure a live model's reasoning.

## Start your stack

From your installed Lab environment:

```sh
gl-agent-lab --data-dir .lab/my-lab studio build
gl-agent-lab --data-dir .lab/my-lab studio up
gl-agent-lab --data-dir .lab/my-lab studio status
```

The first build needs Docker with Linux containers, Git, internet access and
several minutes to download and build dependencies. Running the prepared stack
does not require model keys, a paid provider, a public wallet or a shared Lab
cloud account. The initial configuration allows up to roughly 5 GiB across its
running services, plus Docker overhead. Smaller-memory machines can use GLSim.
This resource budget is a configuration, not a measured minimum requirement.

The development source now records a failed image build's stage, recognized
category, exit code and fixed recovery hint in `studio/build-logs` under the
selected data directory. The CLI prints that diagnostic path. Raw Docker output,
environment values and credentials are excluded. Unknown failures remain labeled
`build_failure`; the log does not invent a cause. This diagnostic follow-up is
newer than the separately recorded alpha 9 candidate wheel.

Studio binds to `127.0.0.1:8766`; the Lab dashboard remains on port 8765. The
`studio build --port` option chooses another Studio port at initialization.
The contract services' network has external access disabled. Its database,
Redis, GenVM worker and preparation services are not published on host ports.
A small relay exposes only the fixed Studio RPC destination and `/health` on
the loopback port. The relay has a second Docker network for host access, with
no keys, host mounts or contract execution. This is necessary on Docker engines
that do not publish ports on internal networks. The RPC is unauthenticated and
must remain local; use an SSH tunnel for a VPS installation.

The exact upstream source commit, built image ID and supporting image digests
are recorded. The stable combination is Studio v0.121.6, GenVM v0.2.16 and
genlayer-py 0.16.3. The source build still uses upstream Ubuntu/apt and release
downloads, so it is not fully reproducible from these pins alone.

## Two different kinds of test

Test the contract's real local Studio execution and optional appeal:

```sh
gl-agent-lab --data-dir .lab/my-lab studio verify
gl-agent-lab --data-dir .lab/my-lab studio verify --appeal
gl-agent-lab --data-dir .lab/my-lab studio verify --binding examples/contracts/delivery-binding.yaml
```

The conformance output records actual submitted transaction IDs, observed
acceptance, execution success, finalization, validator votes and appeal rounds.
An appeal request acknowledgment does not count as a completed appeal. An
ACCEPTED or FINALIZED status does not count as successful execution by itself.
Raw Studio receipts contain validator credentials and are never included in Lab
reports. Contract returns are mapped to `approve` or `deny`, then omitted from
the exported conformance evidence.

Test how an agent handles scenarios using a GenVM-produced decision:

```sh
gl-agent-lab --data-dir .lab/my-lab run escrow-normal --backend studio
gl-agent-lab --data-dir .lab/my-lab import-binding examples/contracts/delivery-binding.yaml
gl-agent-lab --data-dir .lab/my-lab run escrow-normal --backend studio --binding delivery-assessment
```

For a running Lab daemon, add `--url http://127.0.0.1:8765 --wait` to `run`.
The dashboard offers Studio under **Contract runtime**. Python, TypeScript and
MCP clients use the same `backend="studio"` option. A custom binding is optional.
The agent still uses its run-scoped token and the existing observation, decision
and action tools. It never receives Studio signing keys or validator settings.

These scenario tests first deploy a fresh contract and obtain its finalized
GenVM result. They then apply the scenario's **scripted consumer events** to
test the agent. A revised scenario decision is a test injection; it is not proof
of a real Studio appeal. Actual Studio checkpoints are stored separately in
`manifest.runtime.studio_evidence`. Use `studio verify --appeal` for observed
local appeal processing.

## Fixture boundaries

Each scenario execution supplies its own virtual validator configuration, so
approve and deny cases do not rewrite the stack's global validator pool. The
stack's fixed global pool supplies fixture-only validators for appeal checks.
It recognizes the bundled evidence prompt and the delivery example prompt with
approval responses. A custom appeal test needs an explicitly matching global
fixture cohort; arbitrary global fixture reconfiguration is not exposed yet.

Studio uses Lua patterns; GLSim bindings use Python regexes. The adapter accepts
anchored literal-prefix patterns, optionally with `(?s)` and trailing `.*`.
It rejects unsupported regex syntax rather than silently changing its meaning.
Unmatched prompts fall through to a dead local provider endpoint, with Docker
external access blocked. Web fetching, live providers and multi-file contract
dependencies are outside this initial Studio integration.

Stable Studio does not implement current appeal bond accounting. These tests
make no claim about public-network finality, modern fee/bond settlement or agent
production safety. Canceling a Lab run stops its waiting and marks it canceled;
an already submitted Studio transaction may still finish on the owned stack.

## Stop without deleting tests

```sh
gl-agent-lab --data-dir .lab/my-lab studio down
```

Only this installation's Compose project is stopped. The Lab reports, Studio
database and GenVM cache remain available for the next startup. There is no
automatic global Docker cleanup or reset.

## Verify recovery from a controlled restart

Stop the Lab service or foreground server and pause other clients that write
directly to this Studio endpoint. Leave Studio running. Then run:

```sh
gl-agent-lab --data-dir .lab/my-lab studio verify-recovery --output studio-recovery.json
```

The command requires a healthy, owned stack and refuses unfinished Studio
transactions. It holds this installation's Lab and Studio lifecycle locks while
it creates a finalized approval, stops and recreates the Studio containers,
checks the original volumes and finalized result, and obtains a new denial
from the same deployed contract. The evidence file must be new. Restart the Lab
service after the check; the verifier does not change its startup registration.

The default overall budget is 600 seconds, including a reserved recovery attempt
if the check fails after stopping Studio. Progress appears on stderr; the JSON
result contains sanitized checks and failure codes. Exit codes are 0 for a pass,
1 for an observed verification failure, and 2 for an inconclusive or infrastructure
failure. If cleanup cannot return Studio to readiness, inspect the error and
`studio status`. Missing or replaced volumes stop recovery before Compose can
create empty storage; restore the original volumes before resuming. For other
startup failures, correct the reported problem and run `studio up`. Direct Studio writers must stay paused:
the Lab's locks cannot prevent an unrelated process from calling Studio's RPC.

This checks an orderly restart with retained Docker volumes. It does not back up
or restore a lost PostgreSQL volume, resume interrupted consensus, restart Docker
or the host OS, or prove automatic startup after login. Studio still requires
explicit `studio up` after a Docker/host restart. Redis is temporary; only the
PostgreSQL database and GenVM cache are retained. Use [RECOVERY.md](RECOVERY.md)
for the separate Lab SQLite backup boundary.
