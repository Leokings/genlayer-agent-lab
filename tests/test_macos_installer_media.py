"""Erase-target tests use synthetic inventories; no disk commands may execute."""

import importlib.util
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
def test_prepare_refuses_wrong_host_before_media_operations(
        tmp_path, monkeypatch, system, architecture, uid, changed_env):
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
        media.prepare(tmp_path / "unused", report)
    assert report["installer_media"]["failure"] == "media_hosted_intel_guard_failed"
    assert report["installer_media"]["media_mounted"] is False
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

    def fake_run(args, code, timeout=60):
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
