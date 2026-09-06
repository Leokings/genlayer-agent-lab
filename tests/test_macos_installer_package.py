"""Synthetic package trust tests; never contact Apple or execute host commands."""

import hashlib
import importlib.util
import io
import json
import os
import ssl
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts/macos-installer-package.py"
SPEC = importlib.util.spec_from_file_location("macos_installer_package", SCRIPT)
package = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(package)


@pytest.fixture(autouse=True)
def no_real_commands_or_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Package tests must not execute subprocesses or network requests")

    monkeypatch.setattr(package.subprocess, "run", forbidden)
    monkeypatch.setattr(package.urllib.request.OpenerDirector, "open", forbidden)


def trusted_signature(subjects=None, fingerprint=None):
    subjects = subjects or package.CERTIFICATE_SUBJECTS
    fingerprint = fingerprint or package.LEAF_SHA256
    fingerprint = " ".join(fingerprint[index:index + 2] for index in range(0, 64, 2))
    return ("Package: test.pkg\nStatus: signed by a certificate trusted by macOS\n"
            "Certificate Chain:\n"
            f"  1. {subjects[0]}\n     SHA256 Fingerprint:\n      {fingerprint}\n"
            f"  2. {subjects[1]}\n  3. {subjects[2]}\n").encode()


@pytest.fixture
def trial(tmp_path, monkeypatch):
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    installer = tmp_path / "Install macOS Monterey.app"
    target = private / "InstallAssistant.pkg"
    body = b"synthetic signed distribution payload"
    original_stat = Path.stat
    ownership = {"root_owned": False}

    def owned_stat(path, *args, **kwargs):
        result = original_stat(path, *args, **kwargs)
        if path in (private, target):
            fields = list(result)
            fields[4] = 0 if path == target and ownership["root_owned"] else 501
            if path == private:
                fields[0] = (fields[0] & ~0o777) | 0o700
            # Preserve the nanosecond timestamp; reconstructing os.stat_result
            # does not preserve its named nanosecond fields on all platforms.
            changed = os.stat_result(fields)
            return SimpleNamespace(st_mode=changed.st_mode, st_uid=changed.st_uid,
                                   st_nlink=changed.st_nlink, st_dev=changed.st_dev,
                                   st_ino=changed.st_ino, st_size=changed.st_size,
                                   st_mtime_ns=result.st_mtime_ns)
        return result

    def response():
        stream = io.BytesIO(body)
        stream.status = 200
        stream.headers = {"Content-Length": str(len(body))}
        stream.geturl = lambda: package.PACKAGE_URL
        return stream

    monkeypatch.setattr(package.capacity, "hosted_intel_temp", lambda: tmp_path)
    monkeypatch.setattr(package.os, "geteuid", lambda: 501, raising=False)
    monkeypatch.setattr(Path, "stat", owned_stat)
    monkeypatch.setattr(package, "INSTALLER", installer)
    monkeypatch.setattr(package, "PACKAGE_SIZE", len(body))
    monkeypatch.setattr(package, "PACKAGE_SHA1", hashlib.sha1(body).hexdigest())
    monkeypatch.setattr(package, "_open_download", response)
    return SimpleNamespace(private=private, installer=installer, target=target, body=body,
                           ownership=ownership)


@pytest.mark.parametrize(("system", "architecture", "uid", "hosted"), [
    ("Windows", "x86_64", 501, "github-hosted"),
    ("Linux", "x86_64", 501, "github-hosted"),
    ("Darwin", "arm64", 501, "github-hosted"),
    ("Darwin", "x86_64", 0, "github-hosted"),
    ("Darwin", "x86_64", 501, "self-hosted"),
])
def test_host_guard_refuses_before_creating_or_downloading(
        tmp_path, monkeypatch, system, architecture, uid, hosted):
    monkeypatch.setattr(package.capacity.platform, "system", lambda: system)
    monkeypatch.setattr(package.capacity.platform, "machine", lambda: architecture)
    monkeypatch.setattr(package.os, "geteuid", lambda: uid, raising=False)
    for key, value in {"GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": hosted,
                       "RUNNER_OS": "macOS", "RUNNER_TEMP": str(tmp_path)}.items():
        monkeypatch.setenv(key, value)
    state = {}
    with pytest.raises(package.PackageError, match="package_hosted_intel_guard_failed"):
        package.download_and_install(tmp_path / "unused", package.INSTALLER, state)
    assert list(tmp_path.iterdir()) == []
    assert state["apple_package"]["package_signature_and_policy_verified"] is False


@pytest.mark.parametrize("field", ["PACKAGE_URL", "PACKAGE_SIZE", "PACKAGE_SHA1", "LEAF_SHA256"])
def test_unset_metadata_fails_closed(trial, monkeypatch, field):
    monkeypatch.setattr(package, field, None)
    with pytest.raises(package.PackageError, match="package_metadata_unavailable"):
        package.download_and_install(trial.private, trial.installer, {})
    assert list(trial.private.iterdir()) == []


@pytest.mark.parametrize("url", [
    "http://swcdn.apple.com/InstallAssistant.pkg",
    "https://example.com/InstallAssistant.pkg",
    "https://swcdn.apple.com.example.com/InstallAssistant.pkg",
    "https://user:password@swcdn.apple.com/InstallAssistant.pkg",
    "https://swcdn.apple.com:8443/InstallAssistant.pkg",
])
def test_initial_url_and_redirects_reject_downgrade_foreign_host_or_credentials(url):
    with pytest.raises(package.PackageError, match="package_download_url_rejected"):
        package._download_url(url)
    with pytest.raises(package.PackageError, match="package_download_url_rejected"):
        package._AppleRedirect().redirect_request(None, None, 302, "redirect", {}, url)


@pytest.mark.parametrize(("change", "code"), [
    ("digest", "package_catalog_digest_mismatch"),
    ("size", "package_download_size_mismatch"),
    ("certificate", "package_download_failed"),
])
def test_bad_download_cannot_reach_any_verifier_or_installer(trial, monkeypatch, change, code):
    if change == "digest":
        monkeypatch.setattr(package, "PACKAGE_SHA1", "0" * 40)
    elif change == "size":
        monkeypatch.setattr(package, "PACKAGE_SIZE", len(trial.body) - 1)
    else:
        def untrusted_tls():
            raise ssl.SSLCertVerificationError("private certificate diagnostic")

        monkeypatch.setattr(package, "_open_download", untrusted_tls)
    state = {}
    with pytest.raises(package.PackageError, match=code):
        package.download_and_install(trial.private, trial.installer, state)
    assert state["apple_package"]["package_signature_and_policy_verified"] is False
    assert not trial.installer.exists()
    assert "private certificate diagnostic" not in json.dumps(state)


@pytest.mark.parametrize(("failure", "code"), [
    ("signature_exit", "package_signature_rejected"),
    ("developer_id", "package_certificate_chain_rejected"),
    ("wrong_leaf", "package_leaf_fingerprint_rejected"),
    ("untrusted_status", "package_signature_not_trusted"),
    ("policy", "package_policy_rejected"),
])
def test_trust_failures_cannot_invoke_sudo_installer(trial, monkeypatch, failure, code):
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        assert args[0] in {"/usr/sbin/pkgutil", "/usr/sbin/spctl"}
        assert kwargs["env"]["LC_ALL"] == "C"
        signature = trusted_signature()
        if failure == "developer_id":
            signature = trusted_signature(("Developer ID Installer: Other", *package.CERTIFICATE_SUBJECTS[1:]))
        elif failure == "wrong_leaf":
            signature = trusted_signature(fingerprint="0" * 64)
        elif failure == "untrusted_status":
            signature = signature.replace(b"trusted by macOS", b"not trusted")
        failed = ((failure == "signature_exit" and args[0] == "/usr/sbin/pkgutil")
                  or (failure == "policy" and args[0] == "/usr/sbin/spctl"))
        return SimpleNamespace(returncode=int(failed), stdout=signature, stderr=b"rejected")

    monkeypatch.setattr(package.subprocess, "run", fake_run)
    state = {}
    with pytest.raises(package.PackageError, match=code):
        package.download_and_install(trial.private, trial.installer, state)
    assert calls and trial.target.read_bytes() == trial.body
    assert not trial.installer.exists()
    assert state["apple_package"]["package_signature_and_policy_verified"] is False


@pytest.mark.parametrize("mutation", ["none", "during_policy", "during_install", "hardlink", "root_hardlink"])
def test_only_verified_unchanged_package_is_installed_and_then_removed(trial, monkeypatch, mutation):
    calls = []
    private = (str(trial.target) + "\n" + str(Path.home()) + "/cache\n"
               "Authorization: Bearer secret-value\n\x00\x1b" + "x" * 5000).encode()

    def fake_run(args, **kwargs):
        calls.append(args)
        assert kwargs["stdin"] == package.subprocess.DEVNULL
        if args[0] == "/usr/sbin/pkgutil":
            assert args == ["/usr/sbin/pkgutil", "--check-signature", str(trial.target)]
            return SimpleNamespace(returncode=0, stdout=trusted_signature() + private, stderr=b"")
        if args[0] == "/usr/sbin/spctl":
            assert args == ["/usr/sbin/spctl", "--assess", "--type", "install", "--verbose=4", str(trial.target)]
            if mutation == "during_policy":
                trial.target.write_bytes(b"replacement")
            return SimpleNamespace(returncode=0, stdout=b"", stderr=private)
        assert args == ["/usr/bin/sudo", "-n", "/usr/sbin/installer", "-pkg", str(trial.target), "-target", "/"]
        assert len(calls) == 3
        trial.installer.mkdir()
        if mutation == "during_install":
            trial.target.write_bytes(b"replacement")
        elif mutation in {"hardlink", "root_hardlink"}:
            os.link(trial.target, trial.installer / "SharedSupport.dmg")
            if mutation == "root_hardlink":
                # Simulate Apple's root:wheel chown, never change real privileges.
                trial.ownership["root_owned"] = True
        return SimpleNamespace(returncode=0, stdout=b"private installation output", stderr=b"")

    monkeypatch.setattr(package.subprocess, "run", fake_run)
    state = {}
    if mutation in {"none", "hardlink", "root_hardlink"}:
        package.download_and_install(trial.private, trial.installer, state)
        assert state["apple_package"]["status"] == "pass"
        assert state["apple_package"]["owned_package_removed"] is True
        assert trial.installer.is_dir() and not trial.target.exists()
        if mutation in {"hardlink", "root_hardlink"}:
            assert (trial.installer / "SharedSupport.dmg").read_bytes() == trial.body
    else:
        code = ("package_changed_after_verification" if mutation == "during_policy"
                else "package_changed_before_cleanup")
        with pytest.raises(package.PackageError, match=code):
            package.download_and_install(trial.private, trial.installer, state)
        assert trial.target.read_bytes() == b"replacement"
        assert state["apple_package"]["owned_package_removed"] is False
        assert len(calls) == (2 if mutation == "during_policy" else 3)
    record = state["apple_package"]
    assert record["installer_application_executed"] is False
    assert record["host_os_install_requested"] is False and record["host_reboot_requested"] is False
    for check in record["verification_checks"].values():
        for field in ("stdout", "stderr"):
            assert len(check[field]) <= 4096
            assert str(trial.target) not in check[field] and str(Path.home()) not in check[field]
            assert "secret-value" not in check[field] and "\x00" not in check[field] and "\x1b" not in check[field]
    assert "private installation output" not in json.dumps(record)


@pytest.mark.parametrize("existing", ["package", "installer", "foreign_installer"])
def test_existing_or_foreign_targets_refused_before_download(trial, existing):
    target = trial.installer
    if existing == "package":
        trial.target.write_bytes(b"foreign download")
    elif existing == "installer":
        target.mkdir()
    else:
        target = trial.installer.with_name("Other.app")
    with pytest.raises(package.PackageError, match="package_(existing_download|installer_target_rejected)"):
        package.download_and_install(trial.private, target, {})
    if existing == "package":
        assert trial.target.read_bytes() == b"foreign download"


def test_unowned_or_nonprivate_directory_refused_before_download(trial, monkeypatch):
    monkeypatch.setattr(package.os, "geteuid", lambda: 502)
    with pytest.raises(package.PackageError, match="package_private_directory_not_owned"):
        package.download_and_install(trial.private, trial.installer, {})
    assert list(trial.private.iterdir()) == []


def test_root_owned_download_is_rejected_before_verification(trial):
    trial.ownership["root_owned"] = True
    with pytest.raises(package.PackageError, match="package_file_not_owned"):
        package.download_and_install(trial.private, trial.installer, {})
    assert not trial.installer.exists()


def test_download_deadline_stops_before_verification(trial, monkeypatch):
    ticks = iter((0, 0, package.DOWNLOAD_SECONDS + 1))
    monkeypatch.setattr(package.time, "monotonic", lambda: next(ticks))
    with pytest.raises(package.PackageError, match="package_download_deadline_exceeded"):
        package.download_and_install(trial.private, trial.installer, {})
    assert not trial.installer.exists()
