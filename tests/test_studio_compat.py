import asyncio
import base64
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from genlayer_agent_lab.runtime.studio_compat import (
    apply_appeal_snapshot_patch,
    apply_fixture_config_patch,
)

PINNED_WRAPPER = '''@rpc.method("sim_updateValidator")
async def update_validator(
    validator_address: str,
    stake: int | None = None,
    provider: str | None = None,
    model: str | None = None,
    plugin: str | None = None,
    plugin_config: dict | None = None,
    session: Session = Depends(get_db_session),
    validators_manager=Depends(get_validators_manager),
) -> dict:
    return await impl.update_validator(
        session=session,
        validators_manager=validators_manager,
        validator_address=validator_address,
        stake=stake,
        provider=provider,
        model=model,
        plugin=plugin,
        plugin_config=plugin_config,
    )


'''
AFTER = '''@rpc.method("sim_deleteValidator")
async def delete_validator():
    return "unchanged"
'''


def source_file(tmp_path, source):
    path = tmp_path / "backend/protocol_rpc/rpc_methods.py"
    path.parent.mkdir(parents=True)
    path.write_text(source, encoding="utf-8", newline="\n")
    return path


def test_patch_changes_only_two_lines_and_forwards_config_to_existing_implementation(tmp_path):
    before = "# unrelated source remains unchanged\n"
    path = source_file(tmp_path, before + PINNED_WRAPPER + AFTER)
    apply_fixture_config_patch(tmp_path)
    expected = PINNED_WRAPPER.replace(
        "    plugin_config: dict | None = None,\n",
        "    plugin_config: dict | None = None,\n    config: dict | None = None,\n",
    ).replace("        plugin_config=plugin_config,\n",
              "        plugin_config=plugin_config,\n        config=config,\n")
    patched = path.read_text(encoding="utf-8")
    assert patched == before + expected + AFTER
    calls = []

    async def update(**kwargs):
        calls.append(kwargs)
        return {"updated": True}

    namespace = {"rpc": SimpleNamespace(method=lambda name: lambda fn: fn),
                 "Depends": lambda dependency: None, "get_db_session": None,
                 "get_validators_manager": None, "Session": object,
                 "impl": SimpleNamespace(update_validator=update)}
    exec(compile(patched, str(path), "exec"), namespace)
    config = {"temperature": 0}
    result = asyncio.run(namespace["update_validator"]("0x01", config=config))
    assert result == {"updated": True}
    assert calls[0]["config"] is config and calls[0]["validator_address"] == "0x01"
    assert asyncio.run(namespace["delete_validator"]()) == "unchanged"


@pytest.mark.parametrize("change", [
    lambda source: source.replace('sim_updateValidator', 'otherMethod'),
    lambda source: source.replace('sim_deleteValidator', 'otherMethod'),
    lambda source: source.replace('stake: int | None', 'stake: str | None'),
    lambda source: source.replace('impl.update_validator(', 'impl.other_operation('),
    lambda source: source.replace('        provider=provider,\n', ''),
])
def test_unknown_source_shape_is_rejected_without_writing(tmp_path, change):
    path = source_file(tmp_path, change(PINNED_WRAPPER + AFTER))
    original = path.read_bytes()
    with pytest.raises(RuntimeError, match="does not match source"):
        apply_fixture_config_patch(tmp_path)
    assert path.read_bytes() == original


def test_patch_refuses_already_modified_source(tmp_path):
    path = source_file(tmp_path, PINNED_WRAPPER + AFTER)
    apply_fixture_config_patch(tmp_path)
    original = path.read_bytes()
    with pytest.raises(RuntimeError, match="does not match source"):
        apply_fixture_config_patch(tmp_path)
    assert path.read_bytes() == original


PINNED_APPEAL_SOURCE = (Path(__file__).parent / "fixtures/studio_0_121_6_appeal_claim.py.txt").read_text(
    encoding="utf-8")
PINNED_RELEASE = {"studio_version": "0.121.6",
                  "source_commit": "366f085a479bb9e6028ce326c2c13f798a9752c7"}


def appeal_source_file(tmp_path, source=PINNED_APPEAL_SOURCE):
    path = tmp_path / "backend/consensus/worker.py"
    path.parent.mkdir(parents=True)
    path.write_text(source, encoding="utf-8", newline="\n")
    return path


def execute_appeal_claim(source, saved_snapshot):
    """Run the exact pinned claim method with a DB double honoring RETURNING."""
    namespace = {"text": lambda query: query,
                 "time": SimpleNamespace(perf_counter=lambda: 1.0)}
    exec(compile("from __future__ import annotations\n" + source, "claim.py", "exec"), namespace)
    worker = namespace["Worker"]()
    worker.worker_id = "fixture-worker"
    worker.transaction_timeout_minutes = 5
    worker._log_query_result = lambda *args: None
    code_slot = "code-slot"
    unrelated = {"states": {"accepted": {code_slot: "another-contract"}}}
    rows = {"appealed-tx": saved_snapshot, "another-tx": unrelated}
    calls = []

    def execute(query, arguments):
        calls.append((query, arguments))
        returning = query.split("RETURNING ", 1)[1].split(";", 1)[0]
        names = re.findall(r"transactions\.(\w+)", returning)
        values = {name: "preserved-" + name for name in names}
        values["hash"] = "appealed-tx"
        if "contract_snapshot" in names:
            values["contract_snapshot"] = rows[values["hash"]]
        return SimpleNamespace(first=lambda: SimpleNamespace(**values))

    session = SimpleNamespace(execute=execute, commit=lambda: None)
    result = asyncio.run(worker.claim_next_appeal(session))
    assert len(calls) == 1 and calls[0][1]["worker_id"] == "fixture-worker"
    return result


def test_appeal_claim_preserves_pre_execution_code_and_snapshot_scope(tmp_path):
    code = b'# { "Depends": "py-genlayer:test" }\nclass Contract: pass\n'
    stored_code = base64.b64encode(len(code).to_bytes(4, "little") + code).decode()
    snapshot = {"contract_address": "appealed-contract", "balance": 0,
                "states": {"accepted": {"code-slot": stored_code, "decision-slot": "before"},
                           "finalized": {"code-slot": stored_code}}}
    original = execute_appeal_claim(PINNED_APPEAL_SOURCE, snapshot)
    # The existing successful-appeal restore sees no saved snapshot in this payload.
    assert original.get("contract_snapshot") is None
    path = appeal_source_file(tmp_path)
    apply_appeal_snapshot_patch(tmp_path, **PINNED_RELEASE)
    patched = path.read_text(encoding="utf-8")
    expected = PINNED_APPEAL_SOURCE.replace(
        "                      transactions.status, transactions.consensus_data,\n",
        "                      transactions.status, transactions.consensus_data,\n"
        "                      transactions.contract_snapshot,\n",
    ).replace('                "consensus_data": result.consensus_data,\n',
              '                "consensus_data": result.consensus_data,\n'
              '                "contract_snapshot": result.contract_snapshot,\n')
    assert patched == expected
    result = execute_appeal_claim(patched, snapshot)
    saved = result.pop("contract_snapshot")
    assert result == original
    assert saved is snapshot and saved["contract_address"] == "appealed-contract"
    restored = saved["states"]["accepted"]
    assert restored["decision-slot"] == "before"
    assert base64.b64decode(restored["code-slot"])[4:] == code
    assert saved["states"]["finalized"]["code-slot"] == stored_code


@pytest.mark.parametrize("change", [
    lambda source: source.replace("claim_next_appeal", "claim_other_appeal"),
    lambda source: source.replace("claim_next_transaction", "claim_other_transaction"),
    lambda source: source.replace("transactions.consensus_data,", "transactions.hash,"),
    lambda source: source.replace('"consensus_data": result.consensus_data,',
                                  '"consensus_data": None,'),
    lambda source: source.replace("session.commit()", "session.rollback()"),
])
def test_appeal_patch_rejects_unknown_source_without_writing(tmp_path, change):
    path = appeal_source_file(tmp_path, change(PINNED_APPEAL_SOURCE))
    original = path.read_bytes()
    with pytest.raises(RuntimeError, match="does not match source"):
        apply_appeal_snapshot_patch(tmp_path, **PINNED_RELEASE)
    assert path.read_bytes() == original


@pytest.mark.parametrize("change", [{"studio_version": "0.121.7"}, {"source_commit": "main"}])
def test_appeal_patch_rejects_other_release_without_writing(tmp_path, change):
    path = appeal_source_file(tmp_path)
    original = path.read_bytes()
    with pytest.raises(RuntimeError, match="does not match release"):
        apply_appeal_snapshot_patch(tmp_path, **{**PINNED_RELEASE, **change})
    assert path.read_bytes() == original


def test_appeal_patch_rejects_already_patched_source(tmp_path):
    path = appeal_source_file(tmp_path)
    apply_appeal_snapshot_patch(tmp_path, **PINNED_RELEASE)
    original = path.read_bytes()
    with pytest.raises(RuntimeError, match="does not match source"):
        apply_appeal_snapshot_patch(tmp_path, **PINNED_RELEASE)
    assert path.read_bytes() == original
