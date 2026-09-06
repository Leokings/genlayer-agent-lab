"""Prepare allowlisted unmodified Apple installation media on an Intel hosted runner.

Only Apple's full-installer download and createinstallmedia are used. The latter
may erase only a volume proven to belong to this helper's new private disk image.
The downloaded /Applications installer is left for disposable-runner cleanup.
"""

from __future__ import annotations

import importlib.util
import os
import plistlib
import re
import shutil
import stat
import subprocess
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "macos_media_capacity", Path(__file__).with_name("macos-reboot-preflight.py"))
capacity = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(capacity)

INSTALLER = Path("/Applications/Install macOS Catalina.app")
VERSION = "10.15.7"
GIB = 1024 ** 3
DEVICE = re.compile(r"/dev/disk[0-9]+(?:s[0-9]+)*")
WHOLE_DEVICE = re.compile(r"/dev/disk[0-9]+")


class MediaError(RuntimeError):
    """A fixed public failure code; subprocess output must remain private."""


def require(condition, code):
    if not condition:
        raise MediaError(code)


def verification_diagnostic(raw, installer=None):
    """Bound diagnostics from Apple verifier tools to the downloaded installer."""
    text = raw[:4096].decode("utf-8", errors="replace")
    text = text.replace(str(installer or INSTALLER), "$INSTALLER").replace(str(Path.home()), "$HOME")
    return "".join(char for char in text if char in "\n\t" or char.isprintable())


def run(args, code, timeout=60, *, diagnostics=None, diagnostic_installer=None):
    try:
        result = subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True,
                                timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        raise MediaError(code + "_timeout") from None
    except OSError:
        raise MediaError(code + "_unavailable") from None
    if diagnostics is not None:
        # Only the explicitly selected Apple verification commands use this.
        # VM/SMC logs and all other subprocess outputs remain private.
        diagnostics[code] = {"exit_code": result.returncode}
        if result.returncode:
            diagnostics[code]["stderr"] = verification_diagnostic(result.stderr, diagnostic_installer)
    require(result.returncode == 0, code)
    return result.stdout


def inspect_verification_failure(state, installer=None):
    """Read-only comparisons explain a rejected gate; they cannot authorize media creation."""
    installer = installer or INSTALLER
    commands = {
        "strict_integrity": ["/usr/bin/codesign", "--verify", "--deep", "--strict", "--verbose=4"],
        "signature_metadata": ["/usr/bin/codesign", "--display", "--verbose=4"],
        "gatekeeper": ["/usr/sbin/spctl", "--assess", "--type", "execute", "--verbose=4"],
    }
    evidence = state.setdefault("rejected_verification_diagnostics", {})
    for name, command in commands.items():
        try:
            result = subprocess.run([*command, str(installer)], stdin=subprocess.DEVNULL,
                                    capture_output=True, check=False, timeout=120)
            evidence[name] = {"exit_code": result.returncode,
                              "stderr": verification_diagnostic(result.stderr, installer),
                              "stdout": verification_diagnostic(result.stdout, installer)}
        except (OSError, subprocess.TimeoutExpired) as exc:
            evidence[name] = {"failure": type(exc).__name__}


def read_plist(args, code):
    raw = run(args, code)
    require(len(raw) <= 2 * 1024 * 1024, code + "_oversize")
    try:
        value = plistlib.loads(raw)
    except (ValueError, TypeError, plistlib.InvalidFileException):
        raise MediaError(code + "_invalid") from None
    require(isinstance(value, dict), code + "_invalid")
    return value


def image_entities(image):
    """Use hdiutil's image-to-device mapping, never a guessed disk number."""
    info = read_plist(["/usr/bin/hdiutil", "info", "-plist"], "media_image_inventory_failed")
    matches = []
    for entry in info.get("images", []):
        raw = entry.get("image-path") if isinstance(entry, dict) else None
        if isinstance(raw, str) and Path(raw).resolve() == image:
            matches.append(entry)
    require(len(matches) <= 1, "media_image_mapping_ambiguous")
    if not matches:
        return []
    entities = matches[0].get("system-entities")
    require(isinstance(entities, list), "media_image_mapping_invalid")
    require(all(isinstance(item, dict) for item in entities), "media_image_mapping_invalid")
    return entities


def whole_device(entities):
    devices = [item.get("dev-entry") for item in entities]
    roots = [item for item in devices if isinstance(item, str) and WHOLE_DEVICE.fullmatch(item)]
    require(len(roots) == 1, "media_whole_device_ambiguous")
    return roots[0]


def validate_erase_target(image, mount):
    """Prove both the hdiutil ownership and diskutil mount identity before erase."""
    require(image.is_file() and not image.is_symlink(), "media_image_not_regular")
    require(mount.resolve() == mount and os.path.ismount(mount), "media_mount_not_active")
    entities = image_entities(image)
    disk = whole_device(entities)
    volumes = [item for item in entities if item.get("mount-point") == str(mount)]
    require(len(volumes) == 1, "media_mount_not_owned")
    device = volumes[0].get("dev-entry")
    require(isinstance(device, str) and DEVICE.fullmatch(device)
            and device.startswith(disk + "s"), "media_volume_device_invalid")
    info = read_plist(["/usr/sbin/diskutil", "info", "-plist", str(mount)],
                      "media_volume_inspection_failed")
    require(info.get("DeviceNode") == device and info.get("MountPoint") == str(mount)
            and info.get("WholeDisk") is False, "media_volume_identity_mismatch")
    require(mount.stat().st_dev != mount.parent.stat().st_dev, "media_mount_is_host_filesystem")
    return disk


def detach_owned(image, state):
    entities = image_entities(image)
    if entities:
        disk = whole_device(entities)
        run(["/usr/bin/hdiutil", "detach", disk], "media_detach_failed", timeout=90)
    require(not image_entities(image), "media_remained_attached")
    state["media_mounted"] = False
    state["media_detached"] = True


def select_installer(release):
    if release == "catalina":
        return INSTALLER, VERSION
    if release == "monterey":
        return Path("/Applications/Install macOS Monterey.app"), "12.7.6"
    raise MediaError("media_unknown_release")


def prepare(private: Path, report: dict, *, release="catalina") -> Path:
    installer, version = select_installer(release)
    state = {"status": "running", "requested_release": release, "requested_version": version,
             "media_mounted": False, "media_detached": False,
             "host_install_requested": False, "host_reboot_requested": False,
             "hardware_check_overrides": False, "raw_logs_exported": False}
    report["installer_media"] = state

    def stage(value):
        state["stage"] = value
        print("GLAB_MACOS_INSTALLER_STAGE:" + value, flush=True)

    image = None
    attach_attempted = False
    try:
        try:
            runner_temp = capacity.hosted_intel_temp()
        except capacity.PreflightError:
            raise MediaError("media_hosted_intel_guard_failed") from None
        private = Path(private)
        require(private.is_absolute() and private.is_dir() and not private.is_symlink(),
                "media_private_directory_invalid")
        private = private.resolve(strict=True)
        require(private != runner_temp and private.is_relative_to(runner_temp),
                "media_private_directory_outside_runner_temp")
        directory = private.stat()
        require(directory.st_uid == os.geteuid() and not directory.st_mode & 0o022,
                "media_private_directory_not_owned")
        require(not installer.exists() and not installer.is_symlink(), "media_existing_apple_installer")
        require(shutil.disk_usage(private).free >= 60 * GIB, "media_insufficient_disk_space")
        image, iso = private / "apple-installer.dmg", private / "apple-installer.iso"
        master, mount = private / "apple-installer.cdr", private / "apple-installer-mount"
        for path in (image, iso, master, mount):
            require(not path.exists() and not path.is_symlink(), "media_private_destination_exists")

        stage("download_official_" + release + "_installer")
        run(["/usr/sbin/softwareupdate", "--fetch-full-installer", "--full-installer-version", version],
            "media_apple_download_failed", timeout=1500)
        require(installer.is_dir() and not installer.is_symlink(), "media_apple_installer_missing")
        state["official_installer_downloaded"] = True
        require(shutil.disk_usage(private).free >= 40 * GIB, "media_insufficient_conversion_space")

        stage("verify_apple_installer_signature")
        verification = state.setdefault("verification_checks", {})
        run(["/usr/bin/codesign", "--verify", "--deep", "--strict", "--verbose=4", "-R", "=anchor apple",
             str(installer)], "media_apple_signature_rejected", timeout=300, diagnostics=verification,
            diagnostic_installer=installer)
        run(["/usr/sbin/spctl", "--assess", "--type", "execute", "--verbose=4", str(installer)],
            "media_apple_policy_rejected", timeout=180, diagnostics=verification, diagnostic_installer=installer)
        tool = installer / "Contents/Resources/createinstallmedia"
        require(tool.is_file() and not tool.is_symlink(), "media_apple_tool_missing")
        require(tool.resolve().is_relative_to(installer), "media_apple_tool_outside_installer")
        run(["/usr/bin/codesign", "--verify", "--strict", "--verbose=4", "-R", "=anchor apple", str(tool)],
            "media_apple_tool_signature_rejected", timeout=120, diagnostics=verification,
            diagnostic_installer=installer)
        state["apple_signature_and_policy_verified"] = True

        stage("create_private_installer_disk_image")
        mount.mkdir(mode=0o700)
        run(["/usr/bin/hdiutil", "create", "-size", "16g", "-layout", "GPTSPUD", "-fs", "HFS+J",
             "-volname", "LabInstaller", "-format", "UDRW", str(image)],
            "media_disk_image_create_failed", timeout=180)
        require(stat.S_ISREG(image.lstat().st_mode), "media_image_not_regular")
        attach_attempted = True
        # Conservatively retain this flag even if attach times out after mounting.
        state["media_mounted"] = True
        run(["/usr/bin/hdiutil", "attach", "-nobrowse", "-mountpoint", str(mount), str(image)],
            "media_disk_image_attach_failed", timeout=120)
        validate_erase_target(image, mount)
        state["erase_target_ownership_verified"] = True

        stage("create_apple_bootable_installer")
        run(["/usr/bin/sudo", "-n", str(tool), "--volume", str(mount), "--nointeraction"],
            "media_createinstallmedia_failed", timeout=900)
        state["apple_createinstallmedia_completed"] = True
        # createinstallmedia renames/remounts the volume; rediscover only this image.
        detach_owned(image, state)
        attach_attempted = False

        stage("convert_apple_installer_to_dvd_image")
        run(["/usr/bin/hdiutil", "convert", str(image), "-format", "UDTO", "-o", str(master)],
            "media_dvd_conversion_failed", timeout=300)
        require(master.is_file() and not master.is_symlink(), "media_dvd_image_missing")
        size = master.stat().st_size
        require(512 * 1024 ** 2 < size <= 18 * GIB, "media_dvd_image_size_invalid")
        master.rename(iso)
        # Source is an owned regular file and its device has been verified detached.
        require(image.resolve() == image and not image.is_symlink(), "media_source_path_changed")
        image.unlink()
        state.update(status="pass", media_format="UDTO", media_bytes=size,
                     source_disk_image_removed=True, installer_left_for_runner_disposal=True)
        stage("apple_installer_media_ready")
        return iso
    except MediaError as exc:
        state.update(status="fail", failure=str(exc))
        if str(exc) in {"media_apple_signature_rejected", "media_apple_policy_rejected",
                        "media_apple_tool_signature_rejected"}:
            inspect_verification_failure(state, installer)
        raise
    except (OSError, ValueError, TypeError):
        state.update(status="fail", failure="media_preparation_error")
        raise MediaError("media_preparation_error") from None
    finally:
        if attach_attempted and image is not None:
            try:
                detach_owned(image, state)
            except (MediaError, OSError, ValueError, TypeError):
                state["media_mounted"] = True
                state["cleanup_failure"] = "media_detach_unconfirmed"
