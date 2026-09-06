"""Narrow worker-local portability repair for genlayer-test 0.29.2.

Reproduced on Windows: upstream loader._inject_message_to_fd0 unlinks its
temporary file while fd 0 still references it, raising WinError 32. Keep that
owned file until VM deactivation restores fd 0; never edit installed libraries.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def install_windows_stdin_fix() -> tuple[list[Path], object | None]:
    owned_paths: list[Path] = []
    if os.name != "nt":
        return owned_paths, None
    from gltest.direct import loader

    original = loader._inject_message_to_fd0

    def inject(vm):
        from genlayer.py import calldata
        from genlayer.py.types import Address

        def address(value):
            return Address(value) if isinstance(value, bytes) else value

        message = {
            "contract_address": address(vm._contract_address),
            "sender_address": address(vm.sender),
            "origin_address": address(vm.origin),
            "stack": [],
            "value": vm._value,
            "datetime": vm._datetime,
            "is_init": False,
            "chain_id": vm._chain_id,
            "entry_kind": 0,
            "entry_data": b"",
            "entry_stage_data": None,
        }
        fd, name = tempfile.mkstemp(prefix="lab-message-")
        path = Path(name)
        owned_paths.append(path)
        try:
            with os.fdopen(fd, "wb", closefd=True) as stream:
                stream.write(calldata.encode(message))
            with path.open("rb") as stream:
                if getattr(vm, "_original_stdin_fd", None) is None:
                    vm._original_stdin_fd = os.dup(0)
                os.dup2(stream.fileno(), 0)
        except BaseException:
            path.unlink(missing_ok=True)
            raise

    loader._inject_message_to_fd0 = inject
    return owned_paths, original


def cleanup_windows_stdin_fix(paths: list[Path], original: object | None) -> None:
    # Must run AFTER engine.deactivate(), which closes the duplicate and restores fd 0.
    if original is not None:
        from gltest.direct import loader

        loader._inject_message_to_fd0 = original
    for path in paths:
        path.unlink(missing_ok=True)
