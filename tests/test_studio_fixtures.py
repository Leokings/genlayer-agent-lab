import json
from pathlib import Path

import pytest
import yaml

from genlayer_agent_lab.runtime.studio_fixtures import (
    BUNDLED_PROMPT_PREFIX,
    DELIVERY_PROMPT_PREFIX,
    FIXTURE_API_URL,
    FIXTURE_KEY_ENV,
    lua_prefix_pattern,
    python_pattern_to_lua_prefix,
    validator_config,
    virtual_validators,
)


def test_default_fixture_covers_actual_contract_prompts_without_live_provider():
    entry = validator_config("deny")
    assert entry["amount"] == 12
    assert entry["plugin"] == "openai-compatible"
    config = entry["plugin_config"]
    assert config["api_url"] == FIXTURE_API_URL == "http://127.0.0.1:9"
    assert config["api_key_env_var"] == FIXTURE_KEY_ENV
    mocks = config["mock_response"]
    assert mocks["response"] == {
        "^" + BUNDLED_PROMPT_PREFIX: {"verdict": "deny"},
        "^" + DELIVERY_PROMPT_PREFIX: {"decision": "deny"},
    }
    assert mocks["eq_principle_prompt_comparative"] == {}
    assert mocks["eq_principle_prompt_non_comparative"] == {}
    assert "" not in mocks["response"]
    assert json.loads(json.dumps(entry)) == entry


@pytest.mark.parametrize(("pattern", "expected"), [
    (r"^AGENT_LAB_EVIDENCE_V1\n", "^AGENT_LAB_EVIDENCE_V1\n"),
    (r"(?s)^DELIVERY_ASSESSMENT_V1.*", "^DELIVERY_ASSESSMENT_V1"),
    (r"^v1\.2\[x\]\(y\)\+\-\?\*\^\$%.*", "^v1%.2%[x%]%(y%)%+%-%?%*%^%$%%"),
    (r"^path\\name\t", "^path\\name\t"),
    (r"^literal\.\*", "^literal%.%*"),
])
def test_restricted_python_patterns_preserve_literal_meaning(pattern, expected):
    assert python_pattern_to_lua_prefix(pattern) == expected


def test_literal_prefix_api_escapes_lua_magic_without_interpreting_regex():
    assert lua_prefix_pattern("^a.(b)[c]*+-?%$") == "^%^a%.%(b%)%[c%]%*%+%-%?%%%$"


@pytest.mark.parametrize("pattern", [
    "PREFIX", "", "^", "^.*", "(?s)^.*", "^PREFIX$", "^PREFIX.+", "^PREFIX.*$",
    "^PREFIX.*?", "^A|B", "^(A)", "^[AB]", "^A{2}", "^A?", "(?i)^A", "^A(?s:.)",
    r"^A\d", r"^A\s", r"^A\b", r"^A\1", r"^A\x41", r"^A\u0041", "^A\\", "^A\0",
])
def test_unsupported_regex_is_rejected_instead_of_silently_broadening(pattern):
    with pytest.raises(ValueError, match="Unsupported Studio fixture pattern"):
        virtual_validators("approve", pattern, {"verdict": "$fixture_verdict"})


def test_actual_delivery_binding_builds_virtual_schema_and_substitutes_fixture():
    path = Path(__file__).parents[1] / "examples/contracts/delivery-binding.yaml"
    binding = yaml.safe_load(path.read_text(encoding="utf-8"))
    entries = virtual_validators("deny", binding["llm_pattern"], binding["llm_response"])
    assert len(entries) == 5
    assert set(entries[0]) == {"stake", "provider", "model", "config", "plugin", "plugin_config"}
    assert entries[0]["plugin_config"]["mock_response"]["response"] == {
        "^DELIVERY_ASSESSMENT_V1": {"decision": "deny"},
    }
    entries[0]["plugin_config"]["mock_response"]["response"].clear()
    assert entries[1]["plugin_config"]["mock_response"]["response"]
    assert json.loads(json.dumps({"validators": entries}))["validators"] == entries


def test_input_responses_are_copied_and_overlapping_prefixes_are_rejected():
    response = {"items": ["$fixture_verdict", "literal $fixture_verdict"]}
    prompts = {"HEADER\n": response}
    entry = validator_config("approve", 3, prompts)
    response["items"].clear()
    result = entry["plugin_config"]["mock_response"]["response"]["^HEADER\n"]
    assert result == {"items": ["approve", "literal $fixture_verdict"]}
    with pytest.raises(ValueError, match="overlap"):
        validator_config(prompts={"HEADER": "approve", "HEADER_A": "deny"})


@pytest.mark.parametrize("count", [0, -1, True, 1.5, "5", 65])
def test_validator_counts_are_bounded(count):
    with pytest.raises(ValueError, match="Validator count"):
        validator_config(amount=count)
    with pytest.raises(ValueError, match="Validator count"):
        virtual_validators("approve", "^HEADER", {}, count=count)


@pytest.mark.parametrize("prompts", [{}, {"": {}}, {"A\0": {}}, {"A": float("nan")},
                                    {"A": {1: "value"}}, {"A": object()}])
def test_invalid_fixture_data_cannot_produce_configuration(prompts):
    with pytest.raises(ValueError):
        validator_config(prompts=prompts)


def test_invalid_verdict_is_rejected():
    with pytest.raises(ValueError, match="verdict"):
        validator_config("maybe")
