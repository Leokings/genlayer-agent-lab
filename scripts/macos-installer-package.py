"""Fetch and verify Apple's pinned full-installer distribution on a disposable Mac.

The signed package only populates the /Applications installer. This helper never
runs that application, creates installation media, installs an OS, or reboots.
The catalog SHA-1 covers the compressed XAR table of contents, not the entire
download. Apple's package signature and normal package installation policy are
both mandatory execution gates; the recorded whole-file SHA-256 is informational.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import re
import ssl
import stat
import struct
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "macos_package_capacity", Path(__file__).with_name("macos-reboot-preflight.py"))
capacity = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(capacity)

INSTALLER = Path("/Applications/Install macOS Monterey.app")
PACKAGE_URL = (
    "https://swcdn.apple.com/content/downloads/24/16/062-40406-A_LQ4WW26M04/"
    "j7bl9ygay5prezturwh72ai10fvseh2uhw/InstallAssistant.pkg"
)
PACKAGE_SIZE = 12409187001
CATALOG_TOC_SHA1 = "a654cd91b86528bbf0e1b006e9a7e62967f73de8"
CERTIFICATE_SUBJECTS = (
    "Software Update", "Apple Software Update Certification Authority", "Apple Root CA",
)
LEAF_SHA256 = "e074d204ac2498e9dc904a7bc7ced8464119b79d05668028920583b1e896ebb4"
DOWNLOAD_SECONDS = 1500
CHUNK_BYTES = 1024 * 1024
# Absolute archived-data ranges from the TOC authenticated by CATALOG_TOC_SHA1.
# These cover five archived items, not padding, certificate data or the trailer.
ARCHIVED_RANGES = (
    ("Bom", 4637, 55870, "caa9bad21b707fa4c968292a3e8173b1109b4bd9"),
    ("Payload", 60507, 16723233, "d48b0fb06cdbddcdf35883d19b5c62a2c89a5728"),
    ("Scripts", 16783740, 645, "ac97bfb06db5fefe7024dbdab263246ff941fc11"),
    ("PackageInfo", 16784385, 430, "d41e5ce04c2a5941d0df9267c61a613de2298231"),
    ("SharedSupport.dmg", 16784847, 12392401130, "1713cdb44386a860b8d6b1a080abe4a79cacf649"),
)


class PackageError(RuntimeError):
    """Fixed public codes; arbitrary command output is never an exception message."""


def require(condition, code):
    if not condition:
        raise PackageError(code)


def _metadata(release):
    require(release == "monterey", "package_unknown_release")
    require(isinstance(PACKAGE_URL, str) and isinstance(PACKAGE_SIZE, int)
            and not isinstance(PACKAGE_SIZE, bool) and 0 < PACKAGE_SIZE < 20 * 1024 ** 3
            and isinstance(CATALOG_TOC_SHA1, str) and re.fullmatch(r"[0-9a-f]{40}", CATALOG_TOC_SHA1)
            and isinstance(LEAF_SHA256, str) and re.fullmatch(r"[0-9a-f]{64}", LEAF_SHA256)
            and CERTIFICATE_SUBJECTS == (
                "Software Update", "Apple Software Update Certification Authority", "Apple Root CA"),
            "package_metadata_unavailable")
    _download_url(PACKAGE_URL)


def _download_url(url):
    parsed = urllib.parse.urlsplit(url)
    require(parsed.scheme == "https" and parsed.hostname == "swcdn.apple.com"
            and parsed.port in (None, 443) and not parsed.username and not parsed.password
            and not parsed.fragment and not parsed.query,
            "package_download_url_rejected")


class _AppleRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, newurl):
        _download_url(newurl)
        return super().redirect_request(request, response, code, message, headers, newurl)


def _open_download():
    # No custom CA replacement, credential headers, unverified TLS or HTTP downgrade.
    opener = urllib.request.build_opener(
        _AppleRedirect(), urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    request = urllib.request.Request(PACKAGE_URL, headers={"Accept-Encoding": "identity"})
    return opener.open(request, timeout=30)


def _file_identity(path, *, require_single_link=True, allow_root_owner=False):
    require(not path.is_symlink() and path.resolve() == path, "package_file_not_owned")
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode)
            and (info.st_uid == os.geteuid() or (allow_root_owner and info.st_uid == 0))
            and (not require_single_link or info.st_nlink == 1),
            "package_file_not_owned")
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _download(package, record):
    deadline = time.monotonic() + DOWNLOAD_SECONDS
    digest = hashlib.sha256()
    total = 0
    try:
        with package.open("xb") as stream, _open_download() as response:
            _download_url(response.geturl())
            require(response.status == 200, "package_download_status_rejected")
            length = response.headers.get("Content-Length")
            require(length is None or (length.isdigit() and int(length) == PACKAGE_SIZE),
                    "package_download_size_mismatch")
            while True:
                require(time.monotonic() < deadline, "package_download_deadline_exceeded")
                # One buffered read avoids waiting to fill a whole chunk while a
                # slow peer keeps resetting the socket's individual read timeout.
                chunk = response.read1(CHUNK_BYTES)
                require(time.monotonic() < deadline, "package_download_deadline_exceeded")
                if not chunk:
                    break
                total += len(chunk)
                require(total <= PACKAGE_SIZE, "package_download_size_mismatch")
                digest.update(chunk)
                stream.write(chunk)
        require(total == PACKAGE_SIZE, "package_download_size_mismatch")
    except (OSError, urllib.error.URLError, ValueError):
        raise PackageError("package_download_failed") from None
    record.update(downloaded_bytes=total, download_sha256=digest.hexdigest(),
                  download_sha256_scope="whole_file_informational")
    _verify_catalog_toc(package, record)


def _read_catalog_toc(stream, available_bytes):
    """Hash a bounded compressed TOC; this does not verify the rest of a package."""
    require(isinstance(available_bytes, int) and not isinstance(available_bytes, bool)
            and available_bytes >= 28, "package_xar_header_truncated")
    header = stream.read(28)
    require(len(header) == 28, "package_xar_header_truncated")
    magic, header_size, version, compressed, uncompressed, algorithm = struct.unpack(
        ">4sHHQQI", header)
    require(magic == b"xar!" and header_size == 28 and version == 1 and algorithm == 1,
            "package_xar_header_rejected")
    require(0 < compressed <= 1024 * 1024 and 0 < uncompressed <= 8 * 1024 * 1024,
            "package_xar_toc_bounds_rejected")
    require(header_size + compressed <= available_bytes, "package_xar_toc_truncated")
    toc = stream.read(compressed)
    require(len(toc) == compressed, "package_xar_toc_truncated")
    return hashlib.sha1(toc).hexdigest()


def _verify_catalog_toc(package, record):
    """Check only the pinned compressed TOC, with bounded reads and no XML parsing."""
    record.update(catalog_digest_algorithm="sha1", catalog_digest_scope="compressed_xar_toc",
                  catalog_digest_expected=CATALOG_TOC_SHA1, catalog_digest_observed=None,
                  catalog_digest_verified=False)
    identity = _file_identity(package)
    require(identity[2] == PACKAGE_SIZE, "package_download_size_mismatch")
    with package.open("rb") as stream:
        record["catalog_digest_observed"] = _read_catalog_toc(stream, identity[2])
        record["xar_heap_offset"] = stream.tell()
    require(_file_identity(package) == identity, "package_changed_during_catalog_verification")
    require(record["catalog_digest_observed"] == CATALOG_TOC_SHA1, "package_catalog_digest_mismatch")
    record["catalog_digest_verified"] = True


def _verify_archived_ranges(package, record):
    """Stream the five pinned archived items without extracting or executing them."""
    record.update(archived_contents_verified=False, archived_items_verified=0,
                  archived_bytes_verified=0, archived_digest_algorithm="sha1",
                  archived_digest_scope="five_archived_items_only")
    require(record.get("catalog_digest_verified") is True
            and record.get("catalog_digest_observed") == CATALOG_TOC_SHA1,
            "package_archived_toc_not_verified")
    identity = _file_identity(package)
    require(identity[2] == PACKAGE_SIZE, "package_download_size_mismatch")
    require(len(ARCHIVED_RANGES) == 5
            and [item[0] for item in ARCHIVED_RANGES]
            == ["Bom", "Payload", "Scripts", "PackageInfo", "SharedSupport.dmg"],
            "package_archived_manifest_rejected")
    end = record["xar_heap_offset"]
    for name, offset, length, expected in ARCHIVED_RANGES:
        require(isinstance(offset, int) and isinstance(length, int)
                and not isinstance(offset, bool) and not isinstance(length, bool)
                and offset >= end and length > 0 and offset + length <= PACKAGE_SIZE
                and isinstance(expected, str) and re.fullmatch(r"[0-9a-f]{40}", expected),
                "package_archived_manifest_rejected")
        end = offset + length
    deadline = time.monotonic() + 600
    with package.open("rb") as stream:
        for name, offset, length, expected in ARCHIVED_RANGES:
            stream.seek(offset)
            remaining = length
            digest = hashlib.sha1()
            while remaining:
                require(time.monotonic() < deadline, "package_archived_verification_timeout")
                chunk = stream.read1(min(CHUNK_BYTES, remaining))
                require(bool(chunk), "package_archived_data_truncated")
                digest.update(chunk)
                remaining -= len(chunk)
            observed = digest.hexdigest()
            if observed != expected:
                record.update(archived_failure_item=name, archived_digest_expected=expected,
                              archived_digest_observed=observed)
                raise PackageError("package_archived_digest_mismatch")
            record["archived_items_verified"] += 1
            record["archived_bytes_verified"] += length
    require(_file_identity(package) == identity, "package_changed_during_archived_verification")
    record["archived_contents_verified"] = True


def _diagnostic(raw, package):
    text = raw[:4096].decode("utf-8", errors="replace")
    for value, label in ((str(package), "$PACKAGE"), (str(package.parent), "$PRIVATE"),
                         (str(INSTALLER), "$INSTALLER"), (str(Path.home()), "$HOME")):
        text = text.replace(value, label)
    text = re.sub(r"(?i)(authorization\s*[:=]\s*)[^\r\n]+", r"\1[REDACTED]", text)
    text = re.sub(r"(?i)\bbearer\s+\S+", "Bearer [REDACTED]", text)
    return "".join(char for char in text if char in "\n\t" or char.isprintable())[:4096]


def _run(args, code, package, record, *, timeout, diagnostic=False):
    env = dict(os.environ, LC_ALL="C", LANG="C")
    try:
        result = subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True,
                                check=False, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        raise PackageError(code + "_timeout") from None
    except OSError:
        raise PackageError(code + "_unavailable") from None
    if diagnostic:
        record.setdefault("verification_checks", {})[code] = {
            "exit_code": result.returncode,
            "stdout": _diagnostic(result.stdout, package),
            "stderr": _diagnostic(result.stderr, package),
        }
    require(result.returncode == 0, code)
    return result.stdout


def _trusted_apple_chain(raw):
    require(len(raw) <= 65536, "package_signature_output_invalid")
    text = raw.decode("utf-8", errors="replace")
    require(re.search(r"(?m)^\s*Status: (?:signed Apple Software|"
                      r"signed by a certificate trusted by (?:macOS|Mac OS X))\s*$", text),
            "package_signature_not_trusted")
    sections = re.split(r"(?m)^\s*Certificate Chain:\s*$", text)
    require(len(sections) == 2, "package_certificate_chain_rejected")
    entries = list(re.finditer(r"(?m)^\s*([0-9]+)\. ([^\r\n]+)\s*$", sections[1]))
    require([(match.group(1), match.group(2).strip()) for match in entries]
            == [(str(index), subject) for index, subject in enumerate(CERTIFICATE_SUBJECTS, 1)],
            "package_certificate_chain_rejected")
    leaf = sections[1][entries[0].end():entries[1].start()]
    fingerprint = re.search(r"SHA256 Fingerprint:\s*((?:[0-9a-fA-F]{2}[\s:]*){32})", leaf)
    require(fingerprint is not None and re.sub(r"[\s:]", "", fingerprint.group(1)).lower() == LEAF_SHA256,
            "package_leaf_fingerprint_rejected")


def download_and_install(private: Path, installer: Path, state: dict, *, release="monterey") -> None:
    record = {"status": "running", "release": release, "package_signature_and_policy_verified": False,
              "package_installed": False, "installer_application_executed": False,
              "host_os_install_requested": False, "host_reboot_requested": False,
              "owned_package_removed": False}
    state["apple_package"] = record

    def stage(value):
        record["stage"] = value
        print("GLAB_MACOS_INSTALLER_STAGE:" + value, flush=True)

    try:
        try:
            runner_temp = capacity.hosted_intel_temp()
        except capacity.PreflightError:
            raise PackageError("package_hosted_intel_guard_failed") from None
        _metadata(release)
        private, installer = Path(private), Path(installer)
        require(private.is_absolute() and private.is_dir() and not private.is_symlink()
                and private.resolve() == private and private != runner_temp
                and private.is_relative_to(runner_temp), "package_private_directory_invalid")
        info = private.stat()
        require(info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o700,
                "package_private_directory_not_owned")
        require(installer == INSTALLER and installer.is_absolute()
                and not installer.exists() and not installer.is_symlink(), "package_installer_target_rejected")
        package = private / "InstallAssistant.pkg"
        require(not package.exists() and not package.is_symlink(), "package_existing_download")
        stage("download_official_monterey_installassistant_package")
        _download(package, record)
        identity = _file_identity(package)
        stage("verify_apple_installassistant_archived_contents")
        _verify_archived_ranges(package, record)
        stage("verify_apple_installassistant_package")
        signature = _run(["/usr/sbin/pkgutil", "--check-signature", str(package)],
                         "package_signature_rejected", package, record, timeout=120, diagnostic=True)
        _trusted_apple_chain(signature)
        _run(["/usr/sbin/spctl", "--assess", "--type", "install", "--verbose=4", str(package)],
             "package_policy_rejected", package, record, timeout=180, diagnostic=True)
        require(_file_identity(package) == identity, "package_changed_after_verification")
        record["package_signature_and_policy_verified"] = True
        require(not installer.exists() and not installer.is_symlink(), "package_installer_target_rejected")
        stage("populate_verified_monterey_installer_application")
        _run(["/usr/bin/sudo", "-n", "/usr/sbin/installer", "-pkg", str(package), "-target", "/"],
             "package_installation_failed", package, record, timeout=600)
        require(installer.is_dir() and not installer.is_symlink() and installer.resolve() == installer,
                "package_installer_not_created")
        record["package_installed"] = True
        # This helper never attaches this file; remove only its unchanged regular download.
        # Apple's verified postinstall script hardlinks its input into the app
        # on the same volume, then changes that shared inode to root:wheel.
        # Only our original private name is unlinked; any app hardlink is retained.
        require(_file_identity(package, require_single_link=False, allow_root_owner=True) == identity,
                "package_changed_before_cleanup")
        package.unlink()
        record.update(status="pass", owned_package_removed=True)
    except PackageError as exc:
        record.update(status="fail", failure=str(exc))
        raise
    except (OSError, ValueError, TypeError):
        record.update(status="fail", failure="package_preparation_error")
        raise PackageError("package_preparation_error") from None
