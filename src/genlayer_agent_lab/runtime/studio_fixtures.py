"""Native model fixtures for the isolated, pinned Studio v0.121.6 stack.

This module builds configuration only; it does not provision or contact Studio.
The stack owner MUST deny external egress, set FIXTURE_KEY_ENV to FIXTURE_KEY_VALUE,
and give every registered/fallback validator the fixture-only configuration.
Unmatched prompts fall through to the provider in upstream Studio. The dead local
endpoint is an extra guard, not a substitute for network isolation.

Source: genlayerlabs/genlayer-studio@366f085a479bb9e6028ce326c2c13f798a9752c7:
backend/node/llm.lua, backend/validators/__init__.py, backend/domain/types.py,
backend/node/create_nodes/providers_schema.json. Studio matches Lua patterns,
whereas existing Lab bindings contain Python regular expressions.
"""

from __future__ import annotations

import copy

from ..bindings import bounded_json

FIXTURE_KEY_ENV = "LAB_STUDIO_FIXTURE_KEY"
FIXTURE_KEY_VALUE = "fixture-only"
FIXTURE_API_URL = "http://127.0.0.1:9"
BUNDLED_PROMPT_PREFIX = "AGENT_LAB_EVIDENCE_V1\n"
DELIVERY_PROMPT_PREFIX = "DELIVERY_ASSESSMENT_V1\n"
MAX_VALIDATORS = 64

_LUA_MAGIC = frozenset("^$()%.[]*+-?")
_REGEX_MAGIC = frozenset(".^$*+?{}[]()|")
_PATTERN_ERROR = (
    "Unsupported Studio fixture pattern: use an anchored literal prefix, optionally "
    "preceded by (?s) and followed by .*; regex groups, classes, alternatives, "
    "quantifiers, end anchors and character-class escapes are not supported"
)


def _prefix(value: str) -> str:
    if type(value) is not str or not value or len(value) > 4096 or "\0" in value:
        raise ValueError("Prompt prefix must contain 1 to 4096 characters and no NUL")
    return value


def lua_prefix_pattern(prefix: str) -> str:
    """Convert explicitly literal text to an anchored Lua pattern, escaping magic.

    This function does not accept a regex: every input character is literal.
    Newlines are preserved so callers can include a prompt-header delimiter.
    """
    return "^" + "".join("%" + char if char in _LUA_MAGIC else char for char in _prefix(prefix))


def _literal_from_python_pattern(pattern: str) -> str:
    """Recognize a deliberately small, exact prefix subset without executing regexes."""
    if type(pattern) is not str or len(pattern) > 4096:
        raise ValueError(_PATTERN_ERROR)
    text = pattern.removeprefix("(?s)")
    if not text.startswith("^"):
        raise ValueError(_PATTERN_ERROR)
    text = text[1:]
    # These are prefix searches, so a final .* imposes no additional constraint,
    # whether or not (?s) was supplied. Do not strip other regex operators.
    chars = []
    index = 0
    while index < len(text):
        if text[index:] == ".*":
            break
        char = text[index]
        if char == "\\":
            index += 1
            if index >= len(text):
                raise ValueError(_PATTERN_ERROR)
            escaped = text[index]
            if escaped in {"n", "r", "t"}:
                chars.append({"n": "\n", "r": "\r", "t": "\t"}[escaped])
            elif escaped.isalnum():
                raise ValueError(_PATTERN_ERROR)
            else:
                chars.append(escaped)
        elif char in _REGEX_MAGIC:
            raise ValueError(_PATTERN_ERROR)
        else:
            chars.append(char)
        index += 1
    try:
        return _prefix("".join(chars))
    except ValueError as exc:
        raise ValueError(_PATTERN_ERROR) from exc


def python_pattern_to_lua_prefix(pattern: str) -> str:
    """Convert only an anchored Python literal-prefix pattern, rejecting the rest.

    Accepts ``^PREFIX``, ``(?s)^PREFIX.*``, escaped punctuation, and literal
    newline/tab/carriage-return escapes. It never treats arbitrary Python regex
    syntax as Lua syntax. Use lua_prefix_pattern for explicitly literal text.
    """
    return lua_prefix_pattern(_literal_from_python_pattern(pattern))


def _count(value: int) -> int:
    if type(value) is not int or not 1 <= value <= MAX_VALIDATORS:
        raise ValueError(f"Validator count must be an integer from 1 to {MAX_VALIDATORS}")
    return value


def _verdict(value: str) -> str:
    if type(value) is not str or value not in {"approve", "deny"}:
        raise ValueError("Fixture verdict must be approve or deny")
    return value


def _response(value, verdict: str):
    bounded_json(value)

    def resolve(item):
        if type(item) is str and item == "$fixture_verdict":
            return verdict
        if type(item) is dict:
            return {key: resolve(child) for key, child in item.items()}
        if type(item) is list:
            return [resolve(child) for child in item]
        return item

    return resolve(value)


def validator_config(verdict: str = "approve", amount: int = 12, prompts: dict | None = None) -> dict:
    """Return one complete VALIDATORS_CONFIG_JSON entry, including ``amount``.

    ``prompts`` maps non-overlapping literal prompt prefixes to JSON responses.
    Omission enables the bundled evidence and delivery-example prompt headers.
    ``$fixture_verdict`` is substituted only when it is an entire response value.
    All other response values remain literal; resolve other binding context first.
    Equivalence maps stay empty, so unexpected checks cannot receive blanket votes.
    """
    verdict = _verdict(verdict)
    amount = _count(amount)
    if prompts is None:
        prompts = {
            BUNDLED_PROMPT_PREFIX: {"verdict": "$fixture_verdict"},
            DELIVERY_PROMPT_PREFIX: {"decision": "$fixture_verdict"},
        }
    bounded_json(prompts)
    if type(prompts) is not dict or not prompts or len(prompts) > 32:
        raise ValueError("prompts must map 1 to 32 literal prefixes to JSON responses")
    prefixes = [_prefix(prefix) for prefix in prompts]
    for index, prefix in enumerate(prefixes):
        for other in prefixes[index + 1:]:
            if prefix.startswith(other) or other.startswith(prefix):
                # Upstream iterates a Lua table with pairs(), not insertion order.
                raise ValueError("Prompt prefixes must not overlap")
    return {
        "stake": 100,
        # The startup initializer resolves an existing default provider before
        # overriding its config. This is an upstream-supported pair, not a live call.
        "provider": "openai",
        "model": "gpt-4o",
        "amount": amount,
        "config": {"temperature": 0},
        "plugin": "openai-compatible",
        "plugin_config": {
            "api_key_env_var": FIXTURE_KEY_ENV,
            "api_url": FIXTURE_API_URL,
            "mock_response": {
                "response": {
                    lua_prefix_pattern(prefix): _response(response, verdict)
                    for prefix, response in prompts.items()
                },
                "eq_principle_prompt_comparative": {},
                "eq_principle_prompt_non_comparative": {},
            },
        },
    }


def virtual_validators(verdict: str, pattern: str, response, count: int = 5) -> list[dict]:
    """Return ``sim_config['validators']`` for one binding's model fixture.

    ``pattern`` is the restricted Python prefix regex accepted above, and
    ``response`` is a JSON response or a template containing $fixture_verdict.
    Entries follow upstream SimValidatorConfig: no amount, address, or key fields.
    This config controls initial execution; provision global fixture validators
    separately for appeals. Each returned entry owns its nested configuration.
    """
    entry = validator_config(verdict, count, {_literal_from_python_pattern(pattern): response})
    entry.pop("amount")
    return [copy.deepcopy(entry) for _ in range(count)]
