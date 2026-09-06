"""Erase-target tests use synthetic inventories; no disk commands may execute."""

import importlib.util
import json
import os
import plistlib
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts/macos-installer-media.py"
SPEC = importlib.util.spec_from_file_location("macos_installer_media", SCRIPT)
media = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(media)


@pytest.fixture(autouse=True)
def no_subprocesses(monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("Media guard tests must never execute a subprocess")

    monkeypatch.setattr(media.subprocess, "run", unexpected)


@pytest.mark.parametrize(("system", "architecture", "uid", "changed_env"), [
    ("Windows", "x86_64", 501, {}),
    ("Linux", "x86_64", 501, {}),
    ("Darwin", "arm64", 501, {}),
    ("Darwin", "x86_64", 0, {}),
    ("Darwin", "x86_64", 501, {"GITHUB_ACTIONS": "false"}),
    ("Darwin", "x86_64", 501, {"RUNNER_ENVIRONMENT": "self-hosted"}),
    ("Darwin", "x86_64", 501, {"RUNNER_OS": "Linux"}),
])
@pytest.mark.parametrize("audit_only", [False, True])
def test_prepare_refuses_wrong_host_before_media_operations(
        tmp_path, monkeypatch, system, architecture, uid, changed_env, audit_only):
    monkeypatch.setattr(media.capacity.platform, "system", lambda: system)
    monkeypatch.setattr(media.capacity.platform, "machine", lambda: architecture)
    monkeypatch.setattr(media.os, "geteuid", lambda: uid, raising=False)
    for key, value in {"GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "github-hosted",
                       "RUNNER_OS": "macOS", "RUNNER_TEMP": str(tmp_path)}.items():
        monkeypatch.setenv(key, value)
    for key, value in changed_env.items():
        monkeypatch.setenv(key, value)
    report = {}
    with pytest.raises(media.MediaError, match="media_hosted_intel_guard_failed"):
        media.prepare(tmp_path / "unused", report, audit_only=audit_only)
    assert report["installer_media"]["failure"] == "media_hosted_intel_guard_failed"
    assert report["installer_media"]["media_mounted"] is False
    if audit_only:
        assert report["installer_media"]["installer_execution_authorized"] is False
    assert list(tmp_path.iterdir()) == []


@pytest.fixture
def target(tmp_path, monkeypatch):
    image, mount = tmp_path / "owned.dmg", tmp_path / "mount"
    image.write_bytes(b"synthetic owned image")
    mount.mkdir()
    entities = [{"dev-entry": "/dev/disk88"},
                {"dev-entry": "/dev/disk88s2", "mount-point": str(mount)}]
    info = {"DeviceNode": "/dev/disk88s2", "MountPoint": str(mount), "WholeDisk": False}
    inventory = {"images": [{"image-path": str(image), "system-entities": entities}]}
    calls = []

    def read_plist(args, code):
        calls.append(args)
        if args == ["/usr/bin/hdiutil", "info", "-plist"]:
            return inventory
        assert args == ["/usr/sbin/diskutil", "info", "-plist", str(mount)]
        return info

    monkeypatch.setattr(media, "read_plist", read_plist)
    monkeypatch.setattr(media.os.path, "ismount", lambda path: path == mount)
    return SimpleNamespace(image=image, mount=mount, entities=entities, info=info,
                           inventory=inventory, calls=calls)


def test_foreign_image_mapping_cannot_authorize_erase(target):
    target.inventory["images"][0]["image-path"] = str(target.image.with_name("foreign.dmg"))
    with pytest.raises(media.MediaError, match="media_whole_device_ambiguous"):
        media.validate_erase_target(target.image, target.mount)
    assert target.calls == [["/usr/bin/hdiutil", "info", "-plist"]]


def test_duplicate_image_mappings_are_ambiguous(target):
    target.inventory["images"].append(dict(target.inventory["images"][0]))
    with pytest.raises(media.MediaError, match="media_image_mapping_ambiguous"):
        media.validate_erase_target(target.image, target.mount)


@pytest.mark.parametrize(("key", "value"), [
    ("DeviceNode", "/dev/disk0s1"), ("MountPoint", "/"), ("WholeDisk", True),
])
def test_owned_mount_mapping_still_requires_matching_diskutil_identity(target, key, value):
    target.info[key] = value
    with pytest.raises(media.MediaError, match="media_volume_identity_mismatch"):
        media.validate_erase_target(target.image, target.mount)
    assert len(target.calls) == 2


def test_host_filesystem_directory_is_rejected_even_with_claimed_mount_mapping(target):
    assert target.mount.stat().st_dev == target.mount.parent.stat().st_dev
    with pytest.raises(media.MediaError, match="media_mount_is_host_filesystem"):
        media.validate_erase_target(target.image, target.mount)
    assert target.image.read_bytes() == b"synthetic owned image"


def test_consistent_image_volume_and_distinct_filesystem_authorize_only_owned_disk(target, monkeypatch):
    original_stat = Path.stat

    def distinct_device(path, *args, **kwargs):
        result = original_stat(path, *args, **kwargs)
        if path == target.mount:
            values = list(result)
            values[2] += 1  # Synthetic mount device; no real mount is created.
            return os.stat_result(values)
        return result

    monkeypatch.setattr(Path, "stat", distinct_device)
    assert media.validate_erase_target(target.image, target.mount) == "/dev/disk88"


def test_failed_attach_and_detach_leave_conservative_report_and_owned_image(tmp_path, monkeypatch):
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    installer = tmp_path / "fake-installer.app"
    image = private / "apple-installer.dmg"
    original_stat = Path.stat
    calls = []

    def private_stat(path, *args, **kwargs):
        result = original_stat(path, *args, **kwargs)
        if path == private:
            values = list(result)
            values[0] &= ~0o022
            values[4] = 501
            return os.stat_result(values)
        return result

    def fake_run(args, code, timeout=60, *, diagnostics=None, diagnostic_installer=None):
        calls.append(args)
        if args[0] == "/usr/sbin/softwareupdate":
            tool = installer / "Contents/Resources/createinstallmedia"
            tool.parent.mkdir(parents=True)
            tool.write_bytes(b"synthetic signed-tool placeholder")
        elif args[0] in {"/usr/bin/codesign", "/usr/sbin/spctl"}:
            pass
        elif args[:2] == ["/usr/bin/hdiutil", "create"]:
            image.write_bytes(b"owned synthetic image")
        elif args[:2] == ["/usr/bin/hdiutil", "attach"]:
            raise media.MediaError("media_disk_image_attach_failed")
        elif args == ["/usr/bin/hdiutil", "info", "-plist"]:
            return plistlib.dumps({"images": [{"image-path": str(image),
                                   "system-entities": [{"dev-entry": "/dev/disk88"}]}]})
        elif args == ["/usr/bin/hdiutil", "detach", "/dev/disk88"]:
            raise media.MediaError("media_detach_failed")
        else:
            pytest.fail("Unexpected media operation: " + code)
        return b""

    monkeypatch.setattr(media.capacity, "hosted_intel_temp", lambda: tmp_path)
    monkeypatch.setattr(media.os, "geteuid", lambda: 501, raising=False)
    monkeypatch.setattr(Path, "stat", private_stat)
    monkeypatch.setattr(media, "INSTALLER", installer)
    monkeypatch.setattr(media.shutil, "disk_usage", lambda path: SimpleNamespace(free=100 * media.GIB))
    monkeypatch.setattr(media, "run", fake_run)
    report = {}
    with pytest.raises(media.MediaError, match="media_disk_image_attach_failed"):
        media.prepare(private, report)
    state = report["installer_media"]
    assert state["status"] == "fail" and state["media_mounted"] is True
    assert state["media_detached"] is False
    assert state["cleanup_failure"] == "media_detach_unconfirmed"
    assert state["failure"] == "media_disk_image_attach_failed"
    assert image.read_bytes() == b"owned synthetic image"
    assert calls[-1] == ["/usr/bin/hdiutil", "detach", "/dev/disk88"]
    assert all(args[0] != "/usr/bin/sudo" for args in calls)


def test_failed_signature_check_stays_fatal_and_records_bounded_redacted_reason(monkeypatch):
    stderr = (str(media.INSTALLER) + ": a sealed resource is missing or invalid\n"
              + str(Path.home()) + "/temporary\x1b\x00\n" + "x" * 5000).encode()
    monkeypatch.setattr(media.subprocess, "run", lambda *args, **kwargs:
                        SimpleNamespace(returncode=1, stdout=b"", stderr=stderr))
    diagnostics = {}
    with pytest.raises(media.MediaError, match="media_apple_signature_rejected"):
        media.run(["/usr/bin/codesign", "--verify", str(media.INSTALLER)],
                  "media_apple_signature_rejected", diagnostics=diagnostics)
    result = diagnostics["media_apple_signature_rejected"]
    assert result["exit_code"] == 1
    assert "$INSTALLER: a sealed resource is missing or invalid" in result["stderr"]
    assert "$HOME/temporary" in result["stderr"]
    assert str(media.INSTALLER) not in result["stderr"] and str(Path.home()) not in result["stderr"]
    assert len(result["stderr"]) <= 4096 and "\x1b" not in result["stderr"] and "\x00" not in result["stderr"]


def test_non_verification_failure_keeps_subprocess_output_private(monkeypatch):
    monkeypatch.setattr(media.subprocess, "run", lambda *args, **kwargs:
                        SimpleNamespace(returncode=1, stdout=b"private", stderr=b"private"))
    with pytest.raises(media.MediaError) as failure:
        media.run(["/usr/bin/hdiutil", "create"], "media_disk_image_create_failed")
    assert str(failure.value) == "media_disk_image_create_failed"


def test_monterey_uses_its_pinned_apple_path_version_and_diagnostic_redaction():
    installer, version = media.select_installer("monterey")
    assert installer == Path("/Applications/Install macOS Monterey.app") and version == "12.7.6"
    reason = (str(installer) + ": rejected").encode()
    assert media.verification_diagnostic(reason, installer) == "$INSTALLER: rejected"
    assert media.select_installer("catalina") == (media.INSTALLER, "10.15.7")


@pytest.mark.parametrize("release", ["latest", "12.7.6", "../../Applications/other.app"])
def test_unknown_installer_cannot_select_an_arbitrary_download_or_path(tmp_path, release):
    with pytest.raises(media.MediaError, match="media_unknown_release"):
        media.prepare(tmp_path, {}, release=release)
    assert list(tmp_path.iterdir()) == []


@pytest.fixture
def downloaded_installer(tmp_path, monkeypatch):
    """Fake only download/host facts; exercise the real audit and boot gates."""
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    installer = tmp_path / "Install macOS Test.app"
    tool = installer / "Contents/Resources/createinstallmedia"
    payload = installer / "Contents/SharedSupport/SharedSupport.dmg"
    original_stat = Path.stat

    def private_stat(path, *args, **kwargs):
        result = original_stat(path, *args, **kwargs)
        if path == private:
            values = list(result)
            values[0] &= ~0o022
            values[4] = 501
            return os.stat_result(values)
        return result

    def download(args):
        assert args == ["/usr/sbin/softwareupdate", "--fetch-full-installer",
                        "--full-installer-version", "10.15.7"]
        for path in (tool, payload):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"synthetic component; never execute")

    monkeypatch.setattr(media.capacity, "hosted_intel_temp", lambda: tmp_path)
    monkeypatch.setattr(media.os, "geteuid", lambda: 501, raising=False)
    monkeypatch.setattr(Path, "stat", private_stat)
    monkeypatch.setattr(media, "INSTALLER", installer)
    monkeypatch.setattr(media.shutil, "disk_usage", lambda path: SimpleNamespace(free=100 * media.GIB))
    return SimpleNamespace(private=private, installer=installer, tool=tool,
                           payload=payload, download=download)


def test_audit_collects_each_failed_verifier_without_authorizing_or_creating_media(
        downloaded_installer, monkeypatch):
    fixture = downloaded_installer
    calls = []
    diagnostic = (str(fixture.installer) + ": rejected\n" + str(Path.home())
                  + "/cache\x00\x1b\n" + "x" * 5000).encode()

    def fake_subprocess(args, **kwargs):
        calls.append(args)
        assert kwargs["stdin"] == media.subprocess.DEVNULL
        assert kwargs["capture_output"] is True and kwargs["check"] is False
        if args[0] == "/usr/sbin/softwareupdate":
            fixture.download(args)
            return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
        assert args[-1] in {str(fixture.installer), str(fixture.tool), str(fixture.payload)}
        assert 0 < kwargs["timeout"] <= 300
        assert args[0] in {"/usr/bin/codesign", "/usr/sbin/spctl", "/usr/bin/otool", "/usr/bin/hdiutil"}
        if args[0] == "/usr/bin/hdiutil":
            assert args == ["/usr/bin/hdiutil", "verify", str(fixture.payload)]
        return SimpleNamespace(returncode=7, stdout=diagnostic, stderr=diagnostic)

    monkeypatch.setattr(media.subprocess, "run", fake_subprocess)
    report = {}
    assert media.prepare(fixture.private, report, audit_only=True) is None
    state = report["installer_media"]
    assert state["status"] == "observed"
    assert state["installer_execution_authorized"] is False
    assert state["media_mounted"] is False and state["media_detached"] is False
    assert state["host_install_requested"] is False and state["host_reboot_requested"] is False
    assert "apple_signature_and_policy_verified" not in state
    assert "erase_target_ownership_verified" not in state
    checks = list(state["rejected_verification_diagnostics"].values())
    for component in state["component_observations"].values():
        checks.extend(value for key, value in component.items() if key != "bytes")
    assert len(calls) == 11 and len(checks) == 10
    for check in checks:
        assert check["exit_code"] == 7
        for field in ("stdout", "stderr"):
            text = check[field]
            assert "$INSTALLER: rejected" in text and "$HOME/cache" in text
            assert len(text) <= 4096 and "\x00" not in text and "\x1b" not in text
            assert str(fixture.installer) not in text and str(Path.home()) not in text
    assert list(fixture.private.iterdir()) == []


def test_component_audit_continues_after_unavailable_and_timed_out_verifiers(
        downloaded_installer, monkeypatch):
    fixture = downloaded_installer
    fixture.download(["/usr/sbin/softwareupdate", "--fetch-full-installer",
                      "--full-installer-version", "10.15.7"])
    calls = []

    def fake_subprocess(args, **kwargs):
        calls.append(args)
        if len(calls) == 1:
            raise OSError("private exception detail")
        if len(calls) == 2:
            raise media.subprocess.TimeoutExpired(args, kwargs["timeout"],
                                                  output=b"private output", stderr=b"private error")
        return SimpleNamespace(returncode=1, stdout=b"", stderr=b"rejected")

    monkeypatch.setattr(media.subprocess, "run", fake_subprocess)
    state = {"installer_execution_authorized": False}
    media.inspect_installer_components(state, fixture.installer)
    observations = state["component_observations"]
    assert observations["createinstallmedia"]["strict_apple_signature"] == {"failure": "OSError"}
    assert observations["createinstallmedia"]["signature_metadata"] == {"failure": "TimeoutExpired"}
    assert observations["shared_support_image"]["image_checksum_only"]["exit_code"] == 1
    assert state["installer_execution_authorized"] is False
    assert len(calls) == 7 and "private" not in json.dumps(state)
    assert list(fixture.private.iterdir()) == []


@pytest.mark.parametrize("reason", ["missing", "symlink", "resolved_outside_bundle"])
def test_component_audit_refuses_missing_linked_or_escaping_components(
        downloaded_installer, monkeypatch, reason):
    fixture = downloaded_installer
    fixture.download(["/usr/sbin/softwareupdate", "--fetch-full-installer",
                      "--full-installer-version", "10.15.7"])
    paths = {fixture.tool, fixture.payload}
    if reason == "missing":
        for path in paths:
            path.unlink()
    elif reason == "symlink":
        original_is_symlink = Path.is_symlink
        monkeypatch.setattr(Path, "is_symlink", lambda path:
                            path in paths or original_is_symlink(path))
    else:
        original_resolve = Path.resolve

        def outside_bundle(path, *args, **kwargs):
            if path in paths:
                return fixture.private / "outside-component"
            return original_resolve(path, *args, **kwargs)

        monkeypatch.setattr(Path, "resolve", outside_bundle)
    state = {"installer_execution_authorized": False}
    # The autouse subprocess prohibition proves no outside target is inspected.
    media.inspect_installer_components(state, fixture.installer)
    assert state["component_observations"] == {
        "createinstallmedia": {"failure": "component_missing_or_outside_installer"},
        "shared_support_image": {"failure": "component_missing_or_outside_installer"},
    }
    assert state["installer_execution_authorized"] is False
    assert str(fixture.private) not in json.dumps(state)


@pytest.mark.parametrize(("failed_gate", "code"), [
    (0, "media_apple_signature_rejected"),
    (1, "media_apple_policy_rejected"),
    (2, "media_apple_tool_signature_rejected"),
])
def test_boot_path_keeps_all_original_acceptance_gates_before_disk_creation(
        downloaded_installer, monkeypatch, failed_gate, code):
    fixture = downloaded_installer
    gates = [
        ["/usr/bin/codesign", "--verify", "--deep", "--strict", "--verbose=4",
         "-R", "=anchor apple", str(fixture.installer)],
        ["/usr/sbin/spctl", "--assess", "--type", "execute", "--verbose=4", str(fixture.installer)],
        ["/usr/bin/codesign", "--verify", "--strict", "--verbose=4",
         "-R", "=anchor apple", str(fixture.tool)],
    ]
    calls = []

    def fake_subprocess(args, **kwargs):
        calls.append(args)
        if args[0] == "/usr/sbin/softwareupdate":
            fixture.download(args)
            return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
        # Failure diagnostics may use only these read-only app verifier tools.
        assert args[0] in {"/usr/bin/codesign", "/usr/sbin/spctl"}
        return SimpleNamespace(returncode=int(args == gates[failed_gate]),
                               stdout=b"", stderr=b"verification rejected")

    monkeypatch.setattr(media.subprocess, "run", fake_subprocess)
    report = {}
    with pytest.raises(media.MediaError, match=code):
        media.prepare(fixture.private, report)
    assert calls[1:failed_gate + 2] == gates[:failed_gate + 1]
    state = report["installer_media"]
    assert state["status"] == "fail" and state["failure"] == code
    assert state["media_mounted"] is False
    assert "apple_signature_and_policy_verified" not in state
    assert list(fixture.private.iterdir()) == []


@pytest.mark.parametrize(("failure", "expected"), [
    ("package", "package_policy_rejected"),
    ("tool", "media_apple_tool_signature_rejected"),
    ("libraries", "media_tool_unexpected_libraries"),
    ("payload", "media_payload_checksum_failed"),
    (None, "media_disk_image_create_failed"),
])
def test_package_route_requires_package_tool_dependencies_and_payload_before_media(
        downloaded_installer, monkeypatch, failure, expected):
    fixture = downloaded_installer
    calls = []
    monkeypatch.setattr(media, "select_installer", lambda release: (fixture.installer, "12.7.6"))

    def populate(private, installer, state, release):
        assert private == fixture.private and installer == fixture.installer and release == "monterey"
        calls.append("package")
        if failure == "package":
            raise media.MediaError("package_policy_rejected")
        fixture.download(["/usr/sbin/softwareupdate", "--fetch-full-installer",
                          "--full-installer-version", "10.15.7"])

    def fake_subprocess(args, **kwargs):
        calls.append(args)
        if args[0] == "/usr/bin/codesign":
            if args[1] == "--display" or args[-1] == str(fixture.installer):
                return SimpleNamespace(returncode=1, stdout=b"", stderr=b"diagnostic only")
            assert args == ["/usr/bin/codesign", "--verify", "--strict", "--verbose=4",
                            "-R", "=anchor apple", str(fixture.tool)]
            return SimpleNamespace(returncode=int(failure == "tool"), stdout=b"", stderr=b"rejected")
        if args[0] == "/usr/sbin/spctl":
            # The tool-rejection handler may inspect the app; it cannot create media.
            assert failure == "tool" and args[-1] == str(fixture.installer)
            return SimpleNamespace(returncode=3, stdout=b"", stderr=b"diagnostic only")
        if args[0] == "/usr/bin/otool":
            libraries = ["/System/Library/Frameworks/Foundation.framework/Versions/C/Foundation",
                         "/System/Library/Frameworks/CoreFoundation.framework/Versions/A/CoreFoundation",
                         "/usr/lib/libobjc.A.dylib", "/usr/lib/libSystem.B.dylib"]
            if failure == "libraries":
                libraries.append("@executable_path/unsigned.dylib")
            output = str(fixture.tool) + ":\n" + "".join(
                "\t" + path + " (compatibility version 1.0.0, current version 1.0.0)\n" for path in libraries)
            return SimpleNamespace(returncode=0, stdout=output.encode(), stderr=b"")
        if args[:2] == ["/usr/bin/hdiutil", "verify"]:
            assert args[-1] == str(fixture.payload)
            return SimpleNamespace(returncode=int(failure == "payload"), stdout=b"", stderr=b"checksum invalid")
        if args[:2] == ["/usr/bin/hdiutil", "create"]:
            assert failure is None  # Reaching this point requires every earlier gate.
            return SimpleNamespace(returncode=1, stdout=b"", stderr=b"synthetic stop before creation")
        pytest.fail("Unexpected media operation")

    monkeypatch.setattr(media, "populate_verified_installer", populate)
    monkeypatch.setattr(media.subprocess, "run", fake_subprocess)
    report = {}
    with pytest.raises(media.MediaError, match=expected):
        media.prepare(fixture.private, report, release="monterey", installer_source="apple-package")
    state = report["installer_media"]
    assert state["status"] == "fail" and state["failure"] == expected
    assert calls[0] == "package" and state["media_mounted"] is False
    assert not (fixture.private / "apple-installer.dmg").exists()
    assert "apple_signature_and_policy_verified" not in state or failure is None
