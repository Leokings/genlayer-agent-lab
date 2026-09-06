# Architecture and evidence boundaries

The HTTP service owns a single active run and a bounded queue. SQLite commits the complete run document (state, event trace and idempotency records) atomically. A process-level advisory lock prevents two engines from owning the same database. Reopening state marks abandoned runs interrupted; it never resumes an in-memory contract or pretends an interrupted job completed.

Developer credentials create/inspect/cancel runs. Separately generated, hashed run credentials can observe, request/read a decision, attempt an action and finish only their run. Agent responses do not supply grades. Raw expected answers and fixture definitions are not available through observation tools. Local administrators can inspect all state; these reports are diagnostics, not independent certifications.

The runtime starts a fresh, isolated Python subprocess with an environment allowlist. It loads only the bundled EvidenceDecision contract. A pinned GenVM SDK bundle is downloaded and checked against a release SHA256 in a dedicated cache. Three GLSim validator callbacks assess the contract result. Strict model fixtures are installed before deployment. The complete provenance includes the actual contract hash, runner hash, input hash, worker identity, execution result and simplified consensus votes.

The contract assesses supplied evidence through a nondeterministic model call with a custom validator comparison. For this alpha the model responses are controlled fixtures, independent from the scenario's expected verdict. This executes the contract but does not measure a live model's evidence reasoning. The contract stores its verdict and evidence; the off-chain simulated protocol owns balances and actions. No actual token settlement occurs.

The scenario clock advances on `observe`. Pending/provisional/final statuses, changes in revision, and execution errors are explicitly scripted consumer events layered over the actual contract result. They are not real GenLayer network finality or appeals. Reports retain this distinction. `read_decision` does not advance time. An action is checked against current status/scope/revision immediately before its effect.

Balances use integer test units. At most one effect is allowed for the reference resource. Exact idempotent retries recover the original action response, including after simulated acknowledgement loss. Reusing a key with different arguments is a recorded rejection. Unsafe attempts fail the behavior grade even when blocked. Doing nothing fails useful completion, including a hold case where the agent never obtains a conclusive decision.

The SDK, TypeScript example and MCP bridge call the public HTTP API. They have no OpenClaw dependency. MCP does not own run state and is not required for Python or TypeScript integration.

Custom bindings are separate versioned definitions imported by the local developer. Import validates bounded YAML and reads a confined single Python source file without executing it. Each queued run snapshots the definition and source; reports expose the definition and hashes, while agent observation tools expose neither. SQLite schema 2 adds bindings; opening a schema 1 installation creates a uniquely named SQLite backup before the transactional migration. Future schema versions are rejected.

Custom execution uses the optional `container-glsim` backend. The parent passes only the snapshot and five declared scenario fields over stdin to a fresh Docker container selected by immutable image ID. It inspects isolation settings before and after execution, maps the result on the host, and removes only the UUID-labelled container it owns. Cancellation signals the executing process. Build context is a package allowlist plus a verified SDK cache, rather than the developer's repository. Runtime egress, mounts and provider keys are absent; downloads occur during the explicit image build.

Arbitrary Python can control its worker's stdout and reported votes. The host independently records the supplied source/binding hashes, image ID and inspected container settings. Execution results and consensus details remain contract-controlled diagnostics. The container limits host access; it does not turn self-hosted test reports into attestations. See the [binding guide](CUSTOM_CONTRACTS.md).

## Optional Studio path

`--backend studio` uses the same run queue, bindings, clients and independent
grades. The installation owns a pinned Studio Compose project. The runtime
checks its actual images, networks, ports and volumes before connecting. It
deploys a fresh contract in GenVM, submits a public write, validates the real
execution result and waits for local finalization. Per-transaction virtual
validators receive the controlled model response; the fixture is not the grade.

GenVM, RPC, database and worker services stay on an internal Docker network.
A separate bounded relay connects that network to a host-access network and
publishes only a loopback port. It has no keys or mounts, accepts two fixed
routes and always forwards to the same internal RPC host. No generic outbound
proxy is exposed.

The resulting `studio_evidence` contains sanitized actual transaction
checkpoints. Agent observations still follow the scripted scenario timeline.
`studio verify --appeal` independently requires a completed real local appeal
round. Stable Studio has no supported modern bond accounting; cancellation of
Lab waiting does not undo submitted Studio transactions. See `STUDIO.md`.

## Boundaries of this alpha

- Native worker code is trusted bundled code, not a security sandbox for arbitrary Python.
- Fixture mode skips contract execution and is labeled accordingly; `glsim` is the default.
- No live wallet, production key, arbitrary RPC execution, remote SaaS or public validator is needed.
- Custom contracts require the Docker backend and the documented single-file/write-method binding format. Linux worker execution has been verified through Docker Desktop on Windows; separate Linux/macOS host installation checks remain.
- Studio is optional and uses the pinned stable release family; modern bond accounting is unsupported.
- YAML scenario extension uses the existing three protocol models; new protocol semantics need explicit implementation.
- Scripts demonstrate reference behavior, not an AI model's measured capability.
- No public network certification, public-network appeals, model-accuracy benchmark or cross-platform support claim is implied by local test success.
