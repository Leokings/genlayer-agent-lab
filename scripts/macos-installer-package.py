"""Fetch and verify Apple's pinned full-installer distribution on a disposable Mac.

The signed package only populates the /Applications installer. This helper never
runs that application, creates installation media, installs an OS, or reboots.
The catalog SHA-1 is an additional transport check; Apple's package signature
and normal package installation policy are both mandatory execution gates.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import re
import ssl
import stat
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
PACKAGE_SHA1 = "a654cd91b86528bbf0e1b006e9a7e62967f73de8"
CERTIFICATE_SUBJECTS = (
    "Software Update", "Apple Software Update Certification Authority", "Apple Root CA",
)
LEAF_SHA256 = "e074d204ac2498e9dc904a7bc7ced8464119b79d05668028920583b1e896ebb4"
DOWNLOAD_SECONDS = 1500
CHUNK_BYTES = 1024 * 1024


class PackageError(RuntimeError):
    """Fixed public codes; arbitrary command output is never an exception message."""


def require(condition, code):
    if not condition:
        raise PackageError(code)


def _metadata(release):
    require(release == "monterey", "package_unknown_release")
    require(isinstance(PACKAGE_URL, str) and isinstance(PACKAGE_SIZE, int)
            and not isinstance(PACKAGE_SIZE, bool) and 0 < PACKAGE_SIZE < 20 * 1024 ** 3
            and isinstance(PACKAGE_SHA1, str) and re.fullmatch(r"[0-9a-f]{40}", PACKAGE_SHA1)
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
    digest = hashlib.sha1()
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
        require(digest.hexdigest() == PACKAGE_SHA1, "package_catalog_digest_mismatch")
    except (OSError, urllib.error.URLError, ValueError):
        raise PackageError("package_download_failed") from None
    record.update(downloaded_bytes=total, catalog_digest_algorithm="sha1", catalog_digest_verified=True)


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
    require(re.search(r"(?m)^\s*Status: signed by a certificate trusted by (?:macOS|Mac OS X)\s*$", text),
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
