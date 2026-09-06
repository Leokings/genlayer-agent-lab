"""Preflight guards never compile, invoke Hypervisor or change the host."""

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts/macos-reboot-preflight.py"
SPEC = importlib.util.spec_from_file_location("macos_reboot_preflight", SCRIPT)
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


@pytest.fixture
def hosted_intel(tmp_path, monkeypatch):
    monkeypatch.setattr(probe.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(probe.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(probe.os, "geteuid", lambda: 501, raising=False)
    for key, value in {"GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "github-hosted",
                       "RUNNER_OS": "macOS", "RUNNER_TEMP": str(tmp_path)}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(probe, "inspect", lambda *_: pytest.fail("Guard allowed an inspection"))
    return tmp_path


@pytest.mark.parametrize(("key", "value"), [
    ("GITHUB_ACTIONS", "false"), ("RUNNER_ENVIRONMENT", "self-hosted"),
    ("RUNNER_OS", "Windows"), ("RUNNER_TEMP", ""), ("RUNNER_TEMP", "relative/path"),
])
def test_non_hosted_or_invalid_temp_is_refused_before_operations(hosted_intel, monkeypatch, key, value):
    monkeypatch.setenv(key, value)
    with pytest.raises(probe.PreflightError):
        probe.main([])
    assert list(hosted_intel.iterdir()) == []


@pytest.mark.parametrize(("system", "architecture", "uid"), [
    ("Windows", "x86_64", 501), ("Linux", "x86_64", 501),
    ("Darwin", "arm64", 501), ("Darwin", "x86_64", 0),
])
def test_wrong_os_architecture_or_root_is_refused(hosted_intel, monkeypatch, system, architecture, uid):
    monkeypatch.setattr(probe.platform, "system", lambda: system)
    monkeypatch.setattr(probe.platform, "machine", lambda: architecture)
    monkeypatch.setattr(probe.os, "geteuid", lambda: uid)
    with pytest.raises(probe.PreflightError):
        probe.main([])
    assert list(hosted_intel.iterdir()) == []


def test_report_cannot_overwrite_or_leave_runner_temp(hosted_intel):
    existing = hosted_intel / "existing.json"
    existing.write_text("keep", encoding="utf-8")
    for output in (existing, hosted_intel.parent / "outside.json"):
        with pytest.raises(probe.PreflightError):
            probe.main(["--output", str(output)])
    assert existing.read_text() == "keep"
