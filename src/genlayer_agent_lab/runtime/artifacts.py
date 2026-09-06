"""Verified, pinned GenVM artifact preparation in this toolkit's own cache."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import time
import urllib.request
from contextlib import contextmanager
from pathlib import Path

from .pins import BUNDLE_SHA256, BUNDLE_SIZE, BUNDLE_URL, GENVM_VERSION


def _sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


@contextmanager
def _cache_lock(path: Path):
    # Advisory OS lock releases even if a downloading worker is killed.
    with path.open("a+b") as stream:
        if os.name == "nt":
            import msvcrt

            stream.seek(0)
            stream.write(b"0")
            stream.flush()
            while True:
                try:
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(0.1)
        else:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def prepare_sdk(contract_path: Path) -> None:
    from gltest.direct import sdk_loader

    cache = Path.home() / ".cache" / "genlayer-agent-lab" / "gltest-direct"
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / f"genvm-universal-{GENVM_VERSION}.tar.xz"
    manifest_path = cache / f"verified-sdk-{GENVM_VERSION}.json"
    sdk_loader.CACHE_DIR = cache
    original_setup = sdk_loader.setup_sdk_paths

    with _cache_lock(cache / ".prepare.lock"):
        if not archive.exists():
            temporary = archive.with_suffix(".download")
            existing = Path.home() / ".cache" / "gltest-direct" / archive.name
            try:
                if existing.exists() and _sha256(existing) == BUNDLE_SHA256:
                    shutil.copyfile(existing, temporary)
                else:
                    request = urllib.request.Request(
                        BUNDLE_URL, headers={"User-Agent": "genlayer-agent-lab/0.1"}
                    )
                    with urllib.request.urlopen(request, timeout=60) as response:
                        with temporary.open("wb") as output:
                            shutil.copyfileobj(response, output, length=1024 * 1024)
                if temporary.stat().st_size != BUNDLE_SIZE or _sha256(temporary) != BUNDLE_SHA256:
                    raise RuntimeError("Pinned GenVM bundle checksum mismatch")
                temporary.replace(archive)
            finally:
                temporary.unlink(missing_ok=True)
        if archive.stat().st_size != BUNDLE_SIZE or _sha256(archive) != BUNDLE_SHA256:
            raise RuntimeError("Cached GenVM bundle checksum mismatch; remove toolkit runtime cache")

        extracted = cache / "extracted" / GENVM_VERSION
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            for relative, digest in manifest.items():
                path = extracted / relative
                if not path.is_file() or _sha256(path) != digest:
                    raise RuntimeError("Extracted GenVM SDK integrity check failed")
        elif extracted.exists():
            # A killed extraction must never be accepted as a complete SDK.
            # Only remove the exact installation-owned version under this cache.
            resolved = extracted.resolve()
            if resolved.parent != (cache / "extracted").resolve():
                raise RuntimeError("Unsafe runtime cache path")
            shutil.rmtree(resolved)

        added_paths = original_setup(contract_path, version=GENVM_VERSION)
        # Prepare files only. GLSim must inject the binary message into fd 0
        # before the SDK's genlayer.gl module can be imported during deployment.
        for path in added_paths:
            if str(path) in sys.path:
                sys.path.remove(str(path))
        if not manifest_path.exists():
            manifest = {
                str(path.relative_to(extracted)): _sha256(path)
                for path in sorted(extracted.rglob("*"))
                if path.is_file() and "__pycache__" not in path.parts
            }
            manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")

    def pinned_setup(contract_path=None, version=None):
        if version not in (None, GENVM_VERSION):
            raise RuntimeError("Unsupported GenVM version")
        return original_setup(contract_path, version=GENVM_VERSION)

    # Installed 0.29.2 does not honor GENVM_VERSION; pass the explicit argument.
    sdk_loader.setup_sdk_paths = pinned_setup
