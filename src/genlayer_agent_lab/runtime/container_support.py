"""Offline SDK initialization for the nonroot container worker only."""

import hashlib
import json
import sys
from pathlib import Path

from .pins import BUNDLE_SHA256, GENVM_VERSION

SDK_CACHE = Path("/opt/lab-sdk/gltest-direct")


def initialize_offline_sdk() -> None:
    from gltest.direct import sdk_loader

    archive = SDK_CACHE / f"genvm-universal-{GENVM_VERSION}.tar.xz"
    with archive.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != BUNDLE_SHA256:
            raise RuntimeError("Container SDK archive checksum mismatch")
    extracted = SDK_CACHE / "extracted" / GENVM_VERSION
    manifest = json.loads(
        (SDK_CACHE / f"verified-sdk-{GENVM_VERSION}.json").read_text(encoding="utf-8")
    )
    if not manifest:
        raise RuntimeError("Container SDK manifest is empty")
    for relative, digest in manifest.items():
        path = (extracted / relative).resolve()
        if not path.is_relative_to(extracted.resolve()):
            raise RuntimeError("Container SDK manifest contains an invalid path")
        with path.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
                raise RuntimeError("Container SDK source checksum mismatch")
    sdk_loader.CACHE_DIR = SDK_CACHE
    original = sdk_loader.setup_sdk_paths

    def offline_setup(contract_path=None, version=None):
        if version not in (None, GENVM_VERSION):
            raise RuntimeError("Unsupported runner version")
        return original(contract_path, version=GENVM_VERSION)

    def existing_bundle(version):
        if version != GENVM_VERSION:
            raise RuntimeError("Unsupported runner bundle")
        return archive

    sdk_loader.download_artifacts = existing_bundle
    sdk_loader.setup_sdk_paths = offline_setup
    # Do not preimport genlayer.gl: deployment first injects binary stdin.
    sys.dont_write_bytecode = True
