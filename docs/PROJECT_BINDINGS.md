# Project bindings (schema version 2)

A project binding describes a bounded collection of GenLayer contracts and the
operations an agent can request during a Lab test. Source packaging, contract
state and operation results belong to this format. Scenario policy and expected
behavior belong to a separate scenario. Neither format executes Python on the
Lab host.

The reference is `examples/projects/prediction/project.yaml`. It deploys a
prediction decision contract, then a record contract that reads the decision
contract. The decision contract comprises `oracle/__init__.py` and `oracle/rules.py`.
The shared module normalizes supplied model replies and uses Python's `hashlib`
to identify the supplied evidence. The record contract checks market, revision
and outcome, and refuses a duplicate record. The agent's responsibility to wait
for finality is independently checked by the scenario: a state read itself does
not prove finality.

This is a separate format and runtime from the original single-file service
workflow. It does not convert deployed public protocol addresses into replicas.

The additional `examples/projects/prediction-messages/project.yaml` project
adds an actual audit contract and asynchronous child call. Its recorder emits
`audit.append` at the configured GenVM `decided` or `finalized` stage. The agent
authorizes the recorder as the audit's emitter, resolves the oracle, records its
final decision and observes the separate audit effect. A supplied constructor
condition can deliberately make the child reject delivery. The record remains
observable; the agent must inspect the failed child and invoke the supported
audit repair operation when its scenario permits it. Repeating the source record
or pretending the failed child rolled back its parent is incorrect.

The audit's child-only `append` method is deliberately not an agent operation.
`authorize_audit` and `repair_audit` require the owner on-chain. The scenario must
also constrain authorization to this run's recorder address, require the
appropriate transaction finality, and define the expected audit state. A child
execution failure can be an expected condition; its mere existence is not an
automatic agent failure if the agent carries out the required remedy.

## What the developer supplies

- `contracts`: named deployment targets, each with `source_project`, typed
  `constructor_args`, and optional explicit `depends_on` deployment ordering.
- `source_project`: a relative `root`, declared `.py` `files`, the `entrypoint`,
  the exact `runner`, and a list of supported pinned `dependencies`.
- `operations`: an alias mapped to one contract, method, read/write flag, ordered
  named argument schemas, and a result schema.
- `state_reads`: a name mapped to a declared no-argument readonly operation.

An operation argument is `{name: evidence, type: {type: string, max_length: 16000}}`.
A result can preserve nested objects and arrays as well as strings, integers,
booleans, addresses and null. Object fields are required and extra fields are
rejected. Integers do not accept booleans or numeric strings. Constraints include
string enums/lengths, integer minimum/maximum, and bounded arrays.

A constructor argument carries a type and exactly one value source:

```yaml
constructor_args:
  - type: {type: address}
    value: {contract_ref: oracle}
  - type: {type: string, max_length: 128}
    value: {literal: market-001}
```

`contract_ref` resolves to a successfully deployed local contract in the same
run. It also creates a deployment dependency. `from_context` can instead name a
typed scenario context value. These are data substitutions, never expressions.
Unknown targets, circular dependencies and undeclared operation arguments fail
validation. Agent-supplied addresses cannot override an operation's target.
Contract operation aliases cannot be `appeal`, `inspect_fees`, `inspect_appeal`,
`read_evidence` or `submit_investigation`: these names identify Lab operations.
Use another alias to expose a contract method with one of those method names.

## Real GenVM packages and compatibility

The combined project profile targets the GenVM v0.3 executor family shipped with
GenVM Manager v0.6.0-rc3 and modern local Studio. The exact supported runner pins
are constants in `project_bindings.py`. They come from the manager's pinned
executor commit `4ddb57b68d2cb76eec01d94671e9594338f7e5a8`:

- `py-genlayer:5jycge4q8k23462jtb0b9fyey1s9qz928sz2nbrd9mg4sxqg2qng`
- `py-genlayer-multi:faykzar6hr5ehfm07jatm69erx6nv96wmz1ab1dszjthbcgre3m0`
- `py-lib-genlayer-std:kzr02ndm9et4qkmbqpq5djjt5sme2yt76n7sz1qbzax0knt6mam0`

The standard-library runner is already loaded by the pinned Python runner.
Listing it in the manifest is an explicit dependency lock; it does not load it
twice, fetch a host package or claim support for arbitrary PyPI packages. Adding
a different external runner requires a compatible allowlist entry and actual
deployment/execution verification. Local Python modules listed in the package
manifest need no package installation.

Modern entrypoint source begins with `# v0.3.0`, then its pinned `Depends` comment.
This version line is required for the modern executor selection and differs
from the legacy contract header. A single-file deployment uses the UTF-8 source
bytes directly. A multi-file deployment is a deterministic uncompressed ZIP:

```text
version                 (v0.3.0)
runner.json             (Depends: the pinned multi-file runner)
contract/__init__.py
contract/rules.py
```

Every path, byte and ZIP timestamp is stable. The archive is deployed as bytes,
not flattened into one Python file. No developer archive is extracted on the
host. The official modern Studio multi-file fixture uses the same runner and
`contract/` layout. The executor reads the ZIP's `version` entry for selection.

Primary implementation references:

- [Studio multi-file runner fixture](https://github.com/genlayerlabs/genlayer-studio/blob/v0.123.0-rc.6/tests/integration/icontracts/contracts/multi_file_contract/runner.json)
- [Pinned runner dependency hashes](https://github.com/genlayerlabs/genvm-executor/blob/4ddb57b68d2cb76eec01d94671e9594338f7e5a8/runners/support/versions/current.nix)
- [Executor version parsing](https://github.com/genlayerlabs/genvm-executor/blob/4ddb57b68d2cb76eec01d94671e9594338f7e5a8/executor/src/exe/parse_version.rs)

## Snapshots and validation boundaries

`load_project_binding(path)` returns a JSON-serializable snapshot containing the
canonical definition, artifacts, dependency-ordered deployments and a project
SHA-256. Each artifact includes source contents and hashes, deployment bytes as
base64, and a deployment-code SHA-256. Changing any source module changes the
project identity. Moving the project directory or changing filesystem timestamps
does not change it.

`validate_project_snapshot(snapshot)` rebuilds and checks a detached snapshot.
The runtime must call this at its import and execution boundaries. Hashes provide
integrity and identity, not authorship signatures or proof that arbitrary code
is safe. Developer code executes only inside the selected GenVM environment.

Limits: 64 KiB manifest, eight contracts, 32 files per contract, 128 KiB per file,
512 KiB total source, 64 operations, and a 2 MiB snapshot. Source paths must stay
inside the project directory after resolving links. Absolute paths, traversal,
Windows alternate data streams, duplicate/case-colliding paths, YAML aliases,
duplicate YAML keys, and unpinned/unsupported runners are rejected.

Public helpers consumed by the runtime:

```python
snapshot = load_project_binding(project_yaml)
order = deployment_order(snapshot)
code = project_code(snapshot, "oracle")
args = resolve_constructor(snapshot, "recorder", deployed_addresses, context)
call = resolve_operation(snapshot, "record", agent_arguments, deployed_addresses)
result = validate_project_result(snapshot, "record", observed_result)
```

`call` contains only the declared operation, contract alias, resolved address,
method, readonly flag and ordered `args`. `project_binding_summary` exposes the
argument/result schemas and state aliases to the agent without source artifacts.

Bindings describe available operations. They do not independently enforce a
scenario's permissions, prove an appeal outcome, evaluate a contract model's
judgment, or roll back downstream effects. Those require actual runtime
observations and the scenario's explicit behavioral rules.

## Exact integers across HTTP, MCP and JavaScript

Project responses identify `integer_encoding: lab-tagged-decimal-v1`. Integers
outside JavaScript's exact range (`-(2^53-1)` through `2^53-1`) use a tagged decimal
value instead of a JSON number:

```json
{"fee_value": {"$lab_integer": "1000000000000000000001"}}
```

This applies to quoted charges, budgets, balances, contract arguments/results,
scenario literals and integer schema limits. It is a transport representation;
the Lab still calculates and grades using exact Python integers. An ordinary
string such as `"1000000000000000000001"` remains a string. Booleans remain
booleans. The maximum transport representation is 256 decimal digits.

Use `encode_project_wire` and `decode_project_wire` at the transport boundary.
Decode only once. A literal domain object whose only key is `$lab_integer` or
`$lab_object` is escaped by the encoder with `$lab_object`, preserving arbitrary
fixture data without ambiguity. Codecs reject malformed tags, cycles, excessive
nesting and non-finite values. They are not substitutes for the binding's type
and range validation.

The Python `LabClient` handles project encoding automatically and returns exact
Python integers. The TypeScript client returns `bigint` for large project values
and ordinary `number` for safe integers. Its exported `parseProjectJson` reads
raw numeric scenario files without rounding on Node.js 24, and
`stringifyProjectJson` exports large values with tags. Use those helpers instead
of plain `JSON.parse`/`JSON.stringify` for project imports, reports and model
messages. The dashboard uses the same codec for import, requests and downloads.
Already rounded JavaScript `number` values are rejected; use `BigInt` or an exact
decimal representation before any arithmetic.

At a declared **integer argument** position, the Lab also accepts a canonical
decimal string, such as `"1000000000000000000001"`. It normalizes that slot before
policy checks and idempotency hashing. Strings in string fields are never
coerced. Exponents, hexadecimal, whitespace, leading zeros, `-0`, floats and
booleans are rejected as integer arguments. Backend contract results remain
strictly typed and are not automatically coerced.

Scenario fee policies use exact integer values or their explicit wire tags;
ordinary policy strings do not silently become numbers. Marked exported scenario
documents decode before review verification, so the content digest identifies
the same exact values before and after a browser round trip. Legacy v1 clients
and bounded legacy test-unit amounts retain their existing representation.
