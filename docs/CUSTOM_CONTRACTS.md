# Custom contract integration

A binding connects a scenario to a developer's GenLayer intelligent contract. The agent still uses the same decision and action API. The binding only describes the contract call and result interpretation; it does not intercept an agent's production tools or introduce new protocol behavior.

## Supported shape

The binding supports one UTF-8 Python source file, a constructor with JSON positional arguments, and one public **write** method returning JSON with a string verdict somewhere inside it. The first source line must use the exact supported `py-genlayer` runner pin shown in the sample. Multi-file project loading, custom dependencies, view-only calls, live web/model providers and deployed public-network addresses are outside this adapter.

Alpha 3 also accepts the same binding with `--backend studio` for GenVM execution
on your owned local stack. Read [the Studio guide](STUDIO.md) for setup and its
restricted fixture-pattern syntax. The default custom binding runtime remains
`container-glsim`; neither backend runs imported source on the host.

The reference file is `examples/contracts/delivery-binding.yaml`:

```yaml
schema_version: 1
id: delivery-assessment
title: Custom delivery contract
source: delivery.py
constructor_args: []
method: assess_delivery
arguments:
  - from_field: evidence
  - from_field: resource_id
  - from_field: policy_version
result_path: [assessment, outcome]
approve_values: [approve]
deny_values: [deny]
llm_pattern: '(?s)^DELIVERY_ASSESSMENT_V1.*'
llm_response:
  decision: $fixture_verdict
```

`source` resolves below the binding directory, including after symlink resolution. Imports reject traversal, absolute paths, incompatible runner headers, source files over 128 KiB, YAML over 64 KiB, aliases and excessive nesting. Importing reads and hashes the file; it does not import Python.

Each `arguments` entry has exactly one `from_field` or `literal`. A literal may be any bounded JSON value, including `null`. Available fields are `evidence`, `resource_id`, `policy_version`, `amount` and `fixture_verdict`. Constructor arguments are literal JSON values.

`result_path` follows object keys or nonnegative array indices. The selected value must exactly match a configured approve or deny string. The sets cannot overlap; an unknown value is a runtime error, not an inferred approval. A returned verdict does not independently establish scope or authorization: the scenario's consumer-event model and the engine's action checks still govern those tests.

`llm_pattern` selects controlled responses for matching prompts. Matching happens inside the resource-limited container. In `llm_response`, an entire string such as `$fixture_verdict` substitutes that scenario field. Substitution is recursive; text such as `prefix $evidence` stays literal. The scenario's expected verdict is separate from its fixture response, so a wrong decision still fails grading. Unmatched provider calls fail with strict fixtures and disabled container network access.

## Developer workflow

1. Keep contract source and binding YAML in a project folder. Use the sample as a starting point.
2. With Docker's Linux engine running, execute `uv run gl-agent-lab worker build`. This explicit step prepares the pinned image and runs its readiness probe. `worker doctor` checks daemon and image availability.
3. Stop the lab service for the chosen data directory, then run `uv run gl-agent-lab --data-dir .lab/custom import-binding PATH`. List imports with `bindings`.
4. Run `uv run gl-agent-lab --data-dir .lab/custom run escrow-normal --binding delivery-assessment`. The CLI selects `container-glsim` when a binding is supplied and no backend is specified. An explicitly incompatible backend is rejected.
5. Start `serve` with the same data directory for HTTP, clients, MCP and dashboard use. Create a run with `backend: "container-glsim"` and `binding_id: "delivery-assessment"`. Supply its run-scoped token to your tested agent.
6. Inspect the four grades and runtime evidence. Re-import after source changes. Existing queued and finished runs retain their original snapshots.

HTTP discovery is administrator-only at `GET /v1/bindings`. Source imports are local CLI operations; the HTTP API accepts neither local filesystem paths nor source uploads. Each installation uses its owner's Docker engine and storage; no shared cloud account is required.

## Execution boundary and limits

The optional worker builds from an explicit package-file allowlist, checksum-verified GenVM SDK files and hash-locked Python dependencies. It uses a pinned base-image digest. The repository, environment files and wallet credentials are not copied into that build context.

Each run dispatches by Docker image ID and creates a fresh nonroot container with no network or host bind mounts, a read-only root, a 64 MiB scratch filesystem, 512 MiB memory, one CPU and a 64-process limit. The parent bounds output to 1 MiB and method execution to 60 seconds. Setup/inspection/cleanup probes have separate finite timeouts. Cancellation signals the worker and removes only its owned container. Abrupt host/daemon failure may prevent immediate cleanup; no blanket Docker prune is used.

A Linux Docker engine on the same machine is required; Unix sockets and Windows named pipes are accepted, remote TCP/SSH Docker contexts are not. Users running the lab on a VPS use that VPS's own local engine. No native fallback runs custom source if Docker fails.

GLSim executes the custom constructor and method with simplified consensus. Three validators and controlled model responses do not measure live model quality. Pending/provisional/revised consumer events are scripted; they are not actual GenLayer appeals or public-network finality.

The host verifies which source snapshot, binding and image were supplied and inspects container isolation. Arbitrary Python inside a container can forge its own output, including execution diagnostics. Reports are for a developer testing their own code; they are not third-party security attestations. Current live-execution verification status is recorded in `docs/BUILD_STATUS.md`.

## Stored data and upgrades

Binding source and definitions are saved in the local SQLite database; agent endpoints do not expose them. Administrator reports include the definition and hashes without embedding source. Keep secrets out of source, fixtures and literals. Finished reports are frozen.

Alpha 2 migrates schema 1 to schema 2 with a uniquely named `lab.schema1.*.backup.sqlite3` in the same data directory. It preserves historical run documents and administrator tokens. Older alpha 1 code rejects schema 2, so do not point it directly at upgraded data. Preserve both the current database and the backup if a manual rollback is needed.
