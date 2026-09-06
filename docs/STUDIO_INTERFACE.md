# Optional local Studio client

The low-level adapter targets Studio **v0.121.6**, commit `366f085a479bb9e6028ce326c2c13f798a9752c7`, with the existing `genlayer-py==0.16.3` and GenVM `v0.2.16`. It does not alter the GLSim dependency profile. The Studio checkout, Docker stack, fixture provider, validator provisioning and ownership are managed separately.

`runtime.studio.StudioClient(endpoint, timeout=180, cancel_event=None, account=None)` is a context manager; call `close()` otherwise. The endpoint must use HTTP with a literal loopback IP. A fresh ephemeral signing account is created when omitted; its private key is never returned. Each public operation has a bounded deadline, shared by its nested RPC calls. HTTP ignores proxy/environment settings and redirects. No public-network endpoint, SDK default-provider fallback, source import on the host, or paid inference is performed by this client.

- `doctor() -> dict`: read-only chain ID, mandatory ConsensusMain ABI and finality-window checks. Returns `ready`, `rpc_compatible`, expected release/SDK/VM pins, `chain_id`, `finality_window_seconds`, capability flags, and a safe error code when not ready. RPC compatibility cannot establish the actual image/checkout release; the stack owner must record that identity separately.
- `deploy(snapshot, sim_config=None) -> str`: validates an immutable binding snapshot and deploys its source with the SDK in NORMAL mode. Returns the actual transaction ID; submission is not execution success. No source is imported on the host.
- `write(address, snapshot, context, sim_config=None) -> str`: verifies the deployed code matches the snapshot hash, checks the public write schema, resolves binding arguments and submits NORMAL-mode execution. Returns its actual transaction ID.
- `transaction(tx_id) -> dict`: returns a sanitized snapshot of the real Studio receipt, including `tx_id`, `contract_address`, `status`, `execution_success`, `raw_result` (decoded JSON contract return), `result_code`, `votes`, `rounds`, `round_count`, `appealed`, appeal timing/failure fields, and capabilities. `_raw_transaction()` is private and must not be persisted or exposed: Studio receipts include validator private keys and provider configuration.
- `wait(tx_id, until='accepted'|'finalized') -> dict`: polls every 250 ms. Accepted waits also return FINALIZED or a decided failure; finalized waits return FINALIZED or CANCELED. Callers must check `execution_success is True` and map `raw_result` through `bindings.extract_verdict`; ACCEPTED/FINALIZED alone never establishes success. A timeout/cancellation raises `StudioError` and does not imply that Studio canceled the submitted transaction.
- `appeal(tx_id) -> dict`: sends a zero-value appeal of an eligible decision and verifies that Studio actually recorded the request, using changed appeal timestamp, appeal flag or new history. Returns `transaction`, `request_observed`, `appeal_completed`, and `completed_rounds`. An observed request is not a completed appeal. Continue waiting/polling and inspect the new real rounds to establish completion.

Every public snapshot reports `backend='studio'`, `public_chain=False`, `bond_accounting=False`, and `advanced_lifecycle=False`. Stable Studio's appeal endpoint ignores the payment value and its receipt hardcodes an appeal bond of zero. This adapter therefore does not claim modern decision-bound appeals, bond settlement, or public-chain finality.

Use a fixed stack-level `VITE_FINALITY_WINDOW` (initially 30 seconds) and NORMAL execution. The RPC setter can silently do nothing in deployments where the API process has no consensus instance. Initial execution accepts per-transaction virtual validators through `sim_config`; **appeal workers use the global registered validator pool**, so appeal tests also need enough globally provisioned fixture validators. Provider mocks are controlled by the separate owned Studio stack, not by this SDK client.

## Native fixtures and per-transaction configuration

`runtime.studio_fixtures` generates the native fixture configuration supported by the pinned Studio release. It does not start a provider, contact a model API, provision validators, or change the host environment. The stack owner supplies `LAB_STUDIO_FIXTURE_KEY=fixture-only` inside the relevant containers; this is a dummy value for upstream environment interpolation, not an API credential.

```python
import json

from genlayer_agent_lab.runtime.studio_fixtures import (
    validator_config,
    virtual_validators,
)

# Startup configuration for the owned Studio stack: one entry expands to 12
# registered validators. Both bundled evidence and delivery prompts are covered.
validators_config_json = json.dumps([validator_config("approve", amount=12)])

# Pass this object to StudioClient.deploy/write as sim_config. The entries follow
# Studio's SimValidatorConfig schema; each entry represents one virtual validator.
sim_config = {
    "validators": virtual_validators(
        verdict="deny",
        pattern=r"(?s)^DELIVERY_ASSESSMENT_V1.*",
        response={"decision": "$fixture_verdict"},
        count=5,
    )
}
```

The generated `sim_config` contains a `validators` array with five independent objects of this form. `amount` is only for the startup configuration, never a field on a virtual validator:

```json
{
  "stake": 100,
  "provider": "openai",
  "model": "gpt-4o",
  "config": {"temperature": 0},
  "plugin": "openai-compatible",
  "plugin_config": {
    "api_key_env_var": "LAB_STUDIO_FIXTURE_KEY",
    "api_url": "http://127.0.0.1:9",
    "mock_response": {
      "response": {"^DELIVERY_ASSESSMENT_V1": {"decision": "deny"}},
      "eq_principle_prompt_comparative": {},
      "eq_principle_prompt_non_comparative": {}
    }
  }
}
```

These are model responses supplied to the contract's execution, not expected grades or fabricated validator votes. Both example contracts still run their own validation functions. Empty equivalence maps deliberately avoid blanket `true` answers. The two default literal prefixes include their newline delimiter: `AGENT_LAB_EVIDENCE_V1\n` returns `{"verdict": ...}` and `DELIVERY_ASSESSMENT_V1\n` returns `{"decision": ...}`.

For another known prompt header, call `validator_config(prompts={"MY_LITERAL_HEADER\n": {"answer": "$fixture_verdict"}})`. This replaces the default prompt mappings. Keys are **literal prefixes**, converted by `lua_prefix_pattern`; they are never interpreted as Python regular expressions. Overlapping prefixes are rejected because upstream Lua table iteration does not define which matching response wins.

`virtual_validators` accepts the restricted Python pattern forms `^LITERAL` and `(?s)^LITERAL.*`. It also accepts escaped literal punctuation and `\n`, `\r`, `\t`. It rejects unanchored patterns, groups, alternatives, character classes, end anchors, other quantifiers, and character-class escapes such as `\d`. An unsupported existing binding requires an explicit literal-prefix fixture choice; it must not be silently translated into a different Lua pattern. An exact response value of `$fixture_verdict` is replaced with the requested verdict; resolve any other binding-context substitutions before calling this builder.

**Network isolation is required for fail-closed operation.** Upstream Studio falls through to a provider request when no mock matches and may choose a fallback validator. The stack must deny external egress from every GenVM execution service, expose only its intended loopback RPC endpoint, provide no live provider credentials, and provision every registered/fallback validator with the same dead local endpoint policy. The generated endpoint is a second guard; the builder itself cannot establish network isolation. Unexpected web access also requires that boundary; these fixtures provide no web-response mocks.

Changes to `plugin_config` are absent from Studio's startup validator comparison hash. Use a fresh owned database for a changed profile, or explicitly update every validator and verify the stored configuration. Keep the response cohorts fixed during each test. Configure `VITE_FINALITY_WINDOW` on actual consensus workers before startup: in this release the RPC service constructs a separate consensus instance, so setting and reading a value through its RPC methods does not prove that worker finality changed. Confirm real receipt transitions and appeal rounds separately.

Pinned source references: [fixture lookup](https://github.com/genlayerlabs/genlayer-studio/blob/366f085a479bb9e6028ce326c2c13f798a9752c7/backend/node/llm.lua), [validator host-data forwarding](https://github.com/genlayerlabs/genlayer-studio/blob/366f085a479bb9e6028ce326c2c13f798a9752c7/backend/validators/__init__.py), [SimConfig schema](https://github.com/genlayerlabs/genlayer-studio/blob/366f085a479bb9e6028ce326c2c13f798a9752c7/backend/domain/types.py), and [startup comparison](https://github.com/genlayerlabs/genlayer-studio/blob/366f085a479bb9e6028ce326c2c13f798a9752c7/backend/protocol_rpc/validators_init.py).
