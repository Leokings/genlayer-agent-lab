# Contract binding interface (alpha 2)

This describes the implemented Python interfaces beneath the CLI, HTTP API and clients. For developer setup and examples, see [custom contract integration](CUSTOM_CONTRACTS.md).

## Import and snapshot

`bindings.load_binding(path: Path) -> dict` reads a max 64 KiB YAML definition and its single Python source file (max128 KiB, relative path must stay under definition directory). It never imports or executes Python on the host. Validates a concrete runner header matching the supported runtime. Returns a JSON-serializable snapshot with keys `definition` (ContractBinding.model_dump()), `source` (string), `source_sha256`, `binding_sha256`. Bound source changes require a re-import. Runs snapshot the binding, preserving queued/historical inputs.

`ContractBinding` fields: schema_version=1, id slug, title, source (relative .py path), constructor_args (list[JSON values], default[]), method (public identifier), arguments (list of Argument {from_field: evidence|resource_id|policy_version|amount|fixture_verdict|None, literal: JSON|None}; exactly one set), result_path (list[str|int], default[]), approve_values (list[str], default['approve']), deny_values (list[str], default['deny']), llm_pattern (string regex, required), llm_response (JSON template; strings exactly '$fixture_verdict', '$evidence', '$resource_id', '$policy_version', '$amount' substitute scenario values recursively; other strings literal). No Python eval, arbitrary imports, shell snippets or provider credentials in definitions. LLM fixture regex/response is processed only in the container. A return mapping must match exactly one approved or denied string; unknown output is an error.

`bindings.resolve_arguments(definition: dict, context: dict) -> list`
`bindings.resolve_template(template, context: dict) -> JSON`
`bindings.extract_verdict(result, definition: dict) -> str` canonical approve/deny; reject bools/unknown/missing/ambiguous results.

Context keys evidence/resource_id/policy_version/amount/fixture_verdict from the run's immutable scenario.

## Runtime boundary

`runtime.container.evaluate_binding(snapshot: dict, context: dict, *, timeout: float=60, cancel_event: threading.Event|None=None) -> {verdict,provenance}`.
`runtime.container.doctor() -> dict` reports Docker Linux availability and local worker image readiness without auto-pulling or running user contracts.
`runtime.container.build_worker() -> dict` explicitly builds from the bundled Dockerfile with a digest-pinned base, package source and cached pinned SDK. It uses an explicit file allowlist, not the parent workspace. It returns the image ID after a successful contract readiness probe. The image tag is `genlayer-agent-lab-worker:0.1.0a3`; invocation resolves the immutable image ID.

Custom source and fixture payload enter through stdin only; no host bind mounts, network=none, read-only root, tmpfs scratch, cap-drop ALL, no-new-privileges, nonroot uid10001, pids/memory/CPU and wall-time/output limits. Container cannot access Docker socket/host paths or environment secrets. On timeout/cancel/error remove ONLY owned named container (UUID+ownership label), not broad prune. Windows helpers hidden.

Each call gets a fresh contract using the pinned SDK/GLSim and strict fixtures installed before deployment. The constructor and public write method execute through simplified consensus; nondeterministic calls invoke custom validator callbacks, while deterministic calls receive automatic votes. Method eligibility is checked on the bound instance because GLSim 0.29.2's cached proxy class exposes an empty method schema. There is no native fallback if Docker is missing. Provenance records `container-glsim`, image ID, binding/source hashes, method, raw JSON result, execution status, fixtures, votes and callback counts. Execution diagnostics remain contract-controlled; host-derived input/image identity and inspected isolation settings are identified separately. This is not a security certification or full Studio consensus.

## Engine/API boundary

`Engine.import_binding(path)->summary`, `Engine.list_bindings()->list[summary]`; local admin import only. Summary {id,title,method,source_sha256,binding_sha256}, no source or fixtures.
`Engine.create_run(scenario_id, agent='external', backend='glsim', binding_id: str|None=None)`.
backend choices fixture|glsim|container-glsim. A binding requires container-glsim; container-glsim requires a binding. No auto native fallback. Run/report manifest includes immutable binding hashes/definition (admin only), never source exposed to agent. Run-scoped agent API unchanged.

HTTP GET /v1/bindings (admin); POST /v1/runs adds optional binding_id and container-glsim backend. No HTTP path/file source upload.
CLI import-binding PATH; bindings; worker doctor|build; run and suite --binding ID (select container-glsim if explicitly bound and backend omitted; reject explicit conflicting backend).
Python/TS create_run optional binding_id, matching backend; MCP admin start_run optional binding_id, backend; dashboard binding select default bundled, uses container-glsim for selected custom binding.

No automatic changes to production agent tools, public deployments or paid inference. Optional Studio research remains separate; container-glsim must never be called Studio.
