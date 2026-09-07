"""Small, explicit overlays on an archived pinned Studio build context."""

import hashlib
from pathlib import Path

LEGACY_FIXTURE_CONFIG_PATCH = "validator-config-forwarding-v1"
FIXTURE_CONFIG_PATCH = "validator-config-and-appeal-snapshot-v2"
PATCH_LABEL = "io.genlayer.agent-lab.studio.fixture-config-patch"
_PINNED_WRAPPER_SHA256 = "a6657c619c49c15d9a9d3a3605f3f7c90137475f0dfc0f315487ed6561f943cc"
_PINNED_APPEAL_CLAIM_SHA256 = "59f70ed1d3c23d0f1a33185b5c7806380b22e3cca381df808cd80be6e5c0d6f5"


def apply_fixture_config_patch(context: Path) -> None:
    """Forward the config argument already supported by the implementation.

    Studio 0.121.6's RPC wrapper omits it, causing fixture updates to replace
    valid provider config with None. Only this wrapper changes, never consensus.
    Fail on an unexpected source shape rather than guessing at a new release.
    """
    path = context / "backend/protocol_rpc/rpc_methods.py"
    source = path.read_text(encoding="utf-8")
    try:
        start = source.index('@rpc.method("sim_updateValidator")')
        end = source.index('@rpc.method("sim_deleteValidator")', start)
    except ValueError:
        raise RuntimeError("Pinned Studio fixture config patch does not match source") from None
    block = source[start:end]
    declaration = "    plugin_config: dict | None = None,\n"
    forwarding = "        plugin_config=plugin_config,\n"
    if (hashlib.sha256(block.encode()).hexdigest() != _PINNED_WRAPPER_SHA256
            or block.count(declaration) != 1 or block.count(forwarding) != 1
            or "    config:" in block or "        config=config," in block):
        raise RuntimeError("Pinned Studio fixture config patch does not match source")
    patched = block.replace(declaration, declaration + "    config: dict | None = None,\n")
    patched = patched.replace(forwarding, forwarding + "        config=config,\n")
    path.write_text(source[:start] + patched + source[end:], encoding="utf-8", newline="\n")


def apply_appeal_snapshot_patch(context: Path, *, studio_version: str, source_commit: str) -> None:
    """Carry the saved pre-execution snapshot into the existing appeal handler.

    The pinned worker drops this field from its claim query and result payload.
    A successful appeal then restores empty accepted storage, losing the code
    slot. Only claim transport changes; execution, voting and rollback stay intact.
    """
    if (studio_version != "0.121.6"
            or source_commit != "366f085a479bb9e6028ce326c2c13f798a9752c7"):
        raise RuntimeError("Pinned Studio appeal snapshot patch does not match release")
    path = context / "backend/consensus/worker.py"
    source = path.read_text(encoding="utf-8")
    try:
        start = source.index("    async def claim_next_appeal(")
        end = source.index("    async def claim_next_transaction(", start)
    except ValueError:
        raise RuntimeError("Pinned Studio appeal snapshot patch does not match source") from None
    block = source[start:end]
    column = "                      transactions.status, transactions.consensus_data,\n"
    field = '                "consensus_data": result.consensus_data,\n'
    if (hashlib.sha256(block.encode()).hexdigest() != _PINNED_APPEAL_CLAIM_SHA256
            or block.count(column) != 1 or block.count(field) != 1):
        raise RuntimeError("Pinned Studio appeal snapshot patch does not match source")
    patched = block.replace(column, column + "                      transactions.contract_snapshot,\n")
    patched = patched.replace(field, field + '                "contract_snapshot": result.contract_snapshot,\n')
    path.write_text(source[:start] + patched + source[end:], encoding="utf-8", newline="\n")
