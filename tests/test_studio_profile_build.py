"""Modern build retries use command doubles and temporary owned files, never Docker."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from genlayer_agent_lab.runtime import studio_profiles as profiles
from genlayer_agent_lab.runtime import studio_stack
from genlayer_agent_lab.runtime.container import CommandResult


def command_timeout(returncode=-9):
    error = RuntimeError("Docker command timed out")
    error.stdout, error.stderr, error.returncode = b"downloading dependency", b"", returncode
    return error


@pytest.fixture
def build_lab(tmp_path, monkeypatch):
    for name in (profiles.BUILD_TIMEOUT_ENV, profiles.STARTUP_TIMEOUT_ENV):
        monkeypatch.delenv(name, raising=False)
    with profiles._lock(tmp_path):
        state = profiles._initialize(tmp_path, profiles.DEFAULT_PORT)
    root = profiles.profile_root(tmp_path)
    (root / "cache-marker").write_text("completed precompile")
    lab = SimpleNamespace(path=tmp_path, root=root, state=state, builds=[], commands=[],
                          archives=[], downloads=[], outcomes=[], events=[],
                          labels={studio_stack.COMMIT_LABEL: profiles.STUDIO_COMMIT,
                                  studio_stack.PATCH_LABEL: profiles.FIXTURE_SUPPORT,
                                  studio_stack.OWNER_LABEL: state["owner"]})
    monkeypatch.setattr(profiles, "_endpoint", lambda: "unix:///owned.sock")
    monkeypatch.setattr(profiles, "_linux_info", lambda endpoint: {"Architecture": "x86_64"})

    def process(args, **kwargs):
        if args[:2] == ["git", "clone"]:
            source = Path(args[-1])
            source.mkdir()
            (source / "pinned-source-marker").write_text(profiles.STUDIO_COMMIT)
            lab.downloads.append(source)
            return CommandResult(0, b"source downloaded", b"")
        assert args[:2] == ["git", "-C"] and args[-2:] == ["rev-parse", "HEAD"]
        return CommandResult(0, profiles.STUDIO_COMMIT.encode(), b"")

    def archive(source, target, context):
        lab.archives.append((source, context))
        for directory in ("backend/node", "third_party/genvm", "docker"):
            (context / directory).mkdir(parents=True, exist_ok=True)
        (context / "backend/node/llm.lua").write_text(
            '\t\t\tif mapped_prompt.format == "json" then\n'
            'data_value = mock_data.data\nlib.rs.json_parse(mock_data.data)'
            '\n\t\t\telse\n\t\t\t\t-- For text format, convert tables to JSON string')
        (context / "third_party/genvm/version").write_text(profiles.GENVM_VERSION)
        (context / "docker/Dockerfile.backend").write_text("FROM pinned\n")

    def command(endpoint, args, **kwargs):
        assert endpoint == "unix:///owned.sock"
        lab.commands.append(list(args))
        if args[0] == "build":
            assert Path(args[-1]).is_dir()
            assert callable(kwargs["on_output"])
            kwargs["on_output"](b"downloading dependency\n", "stdout")
            lab.builds.append((list(args), kwargs["timeout"]))
            lab.events.append("build")
            result = lab.outcomes.pop(0) if lab.outcomes else CommandResult(0, b"built", b"")
            if isinstance(result, BaseException):
                raise result
            return result
        if args[:2] == ["image", "inspect"]:
            lab.events.append("image_inspection")
            return CommandResult(0, json.dumps({"Id": "sha256:" + "1" * 64,
                "Config": {"Labels": lab.labels}}).encode(), b"")
        assert args[0] == "compose" and "up" in args
        assert callable(kwargs["on_output"])
        kwargs["on_output"](b"starting owned services\n", "stdout")
        lab.events.append("compose")
        return CommandResult(0, b"services started", b"")

    def status(data_dir):
        lab.events.append("readiness")
        return {**profiles.load_profile(data_dir), "ready": False, "runtime_verified": False}

    monkeypatch.setattr(profiles, "_bounded_process", process)
    monkeypatch.setattr(profiles, "_archive", archive)
    monkeypatch.setattr(studio_stack, "_command", command)
    monkeypatch.setattr(profiles, "modern_profile_status", status)
    return lab


@pytest.mark.parametrize("returncode", [-9, 0])
def test_build_timeout_retries_once_with_same_source_context_tag_and_cache(build_lab, returncode):
    lab = build_lab
    lab.outcomes = [command_timeout(returncode), CommandResult(0, b"cached build complete", b"")]
    progress = []
    result = profiles.build_profile(lab.path, progress=progress.append)
    assert len(lab.builds) == 2 and lab.builds[0] == lab.builds[1]
    assert lab.builds[0][1] == 1800
    assert len(lab.downloads) == len(lab.archives) == 1
    assert lab.archives[0][0] == lab.downloads[0] == lab.root / "source"
    assert (lab.root / "source/pinned-source-marker").read_text() == profiles.STUDIO_COMMIT
    assert (lab.root / "cache-marker").read_text() == "completed precompile"
    assert "--no-cache" not in lab.builds[0][0]
    assert [args[0] for args in lab.commands] == ["build", "build", "image"]
    assert lab.events == ["build", "build", "image_inspection", "readiness"]
    assert result["owner"] == lab.state["owner"] and result["ready"] is False
    logs = [json.loads(path.read_text()) for path in (lab.root / "operation-logs").glob("build_image-*.json")]
    assert sorted(item["status"] for item in logs) == ["completed", "failed"]
    assert next(item for item in logs if item["status"] == "failed")["category"] == "timeout"
    assert any("Automatically retrying once" in line and "time limit" in line for line in progress)


def test_second_build_timeout_is_reported_without_inspection_or_third_attempt(build_lab):
    lab = build_lab
    lab.outcomes = [command_timeout(0), command_timeout(0)]
    with pytest.raises(studio_stack.StudioOperationFailure) as caught:
        profiles.build_profile(lab.path)
    assert caught.value.diagnostic["category"] == "timeout"
    assert caught.value.diagnostic["exit_code"] == 0
    assert lab.events == ["build", "build"]
    assert "image_id" not in profiles.load_profile(lab.path)
    assert (lab.root / "source/pinned-source-marker").is_file()
    assert (lab.root / "cache-marker").read_text() == "completed precompile"


@pytest.mark.parametrize("failure", [
    CommandResult(1, b"", b"authentication failed; a previous request timed out"),
    CommandResult(17, b"", b"invalid Dockerfile"),
    RuntimeError("Authentication timed out"),
    RuntimeError("Docker command exceeded its output limit"),
    RuntimeError("Docker command left an unclosed child pipe"),
    RuntimeError("Container evaluation cancelled"),
    OSError("Cannot launch Docker command"),
    KeyboardInterrupt(),
])
def test_build_failure_or_cancellation_never_automatically_retries(build_lab, failure):
    lab = build_lab
    lab.outcomes = [failure]
    progress = []
    expected = KeyboardInterrupt if isinstance(failure, KeyboardInterrupt) else studio_stack.StudioOperationFailure
    with pytest.raises(expected):
        profiles.build_profile(lab.path, progress=progress.append)
    assert lab.events == ["build"]
    assert not any("Automatically retrying" in line for line in progress)
    assert "image_id" not in profiles.load_profile(lab.path)


@pytest.mark.parametrize("label", [studio_stack.COMMIT_LABEL, studio_stack.PATCH_LABEL, studio_stack.OWNER_LABEL])
def test_retry_success_still_requires_source_patch_and_image_owner(build_lab, label):
    lab = build_lab
    lab.outcomes = [command_timeout(), CommandResult(0, b"built", b"")]
    lab.labels[label] = "another installation"
    with pytest.raises(RuntimeError, match="image source, patch or ownership mismatch"):
        profiles.build_profile(lab.path)
    assert lab.events == ["build", "build", "image_inspection"]
    assert "image_id" not in profiles.load_profile(lab.path)


@pytest.mark.parametrize("value", ["", "0", "59", "3601", "-1", "infinite", "60.5", "1" * 100])
@pytest.mark.parametrize("operation", [profiles.build_profile, profiles.setup_modern_profile])
def test_invalid_build_budget_precedes_metadata_downloads_and_builds(tmp_path, monkeypatch, value, operation):
    monkeypatch.setenv(profiles.BUILD_TIMEOUT_ENV, value)
    target = tmp_path / "new-installation"
    with pytest.raises(ValueError, match=profiles.BUILD_TIMEOUT_ENV):
        operation(target)
    assert not target.exists()


def test_setup_rejects_invalid_startup_budget_before_build_mutations(tmp_path, monkeypatch):
    monkeypatch.setenv(profiles.BUILD_TIMEOUT_ENV, "1800")
    monkeypatch.setenv(profiles.STARTUP_TIMEOUT_ENV, "59")
    target = tmp_path / "new-installation"
    with pytest.raises(ValueError, match=profiles.STARTUP_TIMEOUT_ENV):
        profiles.setup_modern_profile(target)
    assert not target.exists()


@pytest.mark.parametrize("value", ["60", "1800", "3600"])
def test_build_budget_is_finite_and_independent_of_startup(monkeypatch, value):
    monkeypatch.setenv(profiles.BUILD_TIMEOUT_ENV, value)
    monkeypatch.setenv(profiles.STARTUP_TIMEOUT_ENV, "75")
    assert profiles.build_timeout() == int(value)
    assert profiles.startup_timeout() == 75


def test_retried_build_still_runs_compose_and_normal_readiness_checks(build_lab, monkeypatch):
    lab = build_lab
    lab.outcomes = [command_timeout(), CommandResult(0, b"built", b"")]
    monkeypatch.setenv(profiles.BUILD_TIMEOUT_ENV, "90")
    monkeypatch.setenv(profiles.STARTUP_TIMEOUT_ENV, "75")
    ownership = []
    monkeypatch.setattr(studio_stack, "_inventory", lambda *a: {"owned": True})
    monkeypatch.setattr(studio_stack, "_assert_owned", lambda *a, **kw: ownership.append(kw["config"]))
    result = profiles.setup_modern_profile(lab.path)
    assert all(timeout == 90 for _, timeout in lab.builds)
    compose = next(args for args in lab.commands if args[0] == "compose")
    assert compose[-5:] == ["up", "--detach", "--wait", "--wait-timeout", "75"]
    assert len(ownership) == 1
    assert lab.events == ["build", "build", "image_inspection", "readiness", "compose", "readiness", "readiness"]
    assert result["ready"] is False and result["runtime_verified"] is False
