import json

import pytest

from genlayer_agent_lab.cli import main
from genlayer_agent_lab.runtime import studio_stack as stack
from genlayer_agent_lab.runtime.container import CommandResult
from genlayer_agent_lab.runtime.studio_build_diagnostics import (
    StudioBuildFailure,
    studio_build_failure,
)

SECRET = b"https://private-user:provider-secret@private.invalid/?token=source-secret"
IMAGE_ID = "sha256:" + "a" * 64


@pytest.mark.parametrize(("raw", "category"), [
    (b"Could not resolve registry", "dns_failure"),
    (b"x509: certificate signed by unknown authority", "tls_failure"),
    (b"No matching distribution found for private-package", "package_resolution"),
    (b"THESE PACKAGES DO NOT MATCH THE HASHES", "dependency_integrity"),
    (b"no space left on device", "disk_full"),
    (b"pull access denied", "registry_access"),
    (b"Cannot connect to the Docker daemon", "docker_unavailable"),
    (b"unrecognized build failure", "build_failure"),
])
def test_studio_failures_save_only_bounded_metadata(tmp_path, raw, category):
    image_tag = stack.IMAGE_TAG + "-" + "a" * 32
    failure = studio_build_failure(12.345, image_tag, docker_exit=17,
                                   stdout=SECRET, stderr=raw + b"\n" + SECRET,
                                   log_dir=tmp_path / "build-logs")
    assert isinstance(failure, StudioBuildFailure)
    assert failure.log_path.name.startswith("studio-")
    saved = failure.log_path.read_text(encoding="utf-8")
    data = json.loads(saved)
    assert data["backend"] == "studio" and data["image_tag"] == image_tag
    assert data["stage"] == "build_image" and data["category"] == category
    assert data["docker_exit_code"] == 17 and data["elapsed_seconds"] == 12.35
    assert data["hint"] and len(saved) < 4096
    public = saved + str(failure) + json.dumps(failure.as_result())
    assert failure.as_result()["backend"] == "studio"
    for private in ("private-user", "provider-secret", "private.invalid", "source-secret",
                    "private-package", "worker", "container-glsim"):
        assert private not in public
    assert "stdout" not in data and "stderr" not in data and "error" not in data
    assert category in str(failure) and str(failure.log_path) in str(failure)


@pytest.mark.parametrize(("elapsed", "exit_code", "expected_elapsed", "expected_exit"), [
    (float("nan"), 2**64, None, None),
    (float("inf"), True, None, None),
    (10**20, -(2**40), 86_400, None),
    (-5, -9, 0, -9),
])
def test_metadata_cannot_retain_unbounded_numbers_or_arbitrary_image_tags(
        tmp_path, elapsed, exit_code, expected_elapsed, expected_exit):
    failure = studio_build_failure(elapsed, SECRET.decode(), docker_exit=exit_code,
                                   log_dir=tmp_path)
    assert failure.diagnostic["elapsed_seconds"] == expected_elapsed
    assert failure.diagnostic["docker_exit_code"] == expected_exit
    assert failure.diagnostic["image_tag"] == "genlayer-agent-lab-studio"
    assert "provider-secret" not in failure.log_path.read_text()


def test_unwritable_diagnostic_directory_preserves_fixed_failure(tmp_path):
    blocked = tmp_path / "build-logs"
    blocked.write_text("existing file")
    failure = studio_build_failure(0.1, stack.IMAGE_TAG, stderr=SECRET,
                                   log_dir=blocked)
    assert failure.log_path is None
    assert failure.diagnostic["category"] == "build_failure"
    assert "could not be saved" in str(failure)
    assert blocked.read_text() == "existing file"
    assert "provider-secret" not in str(failure)


def test_symlinked_diagnostic_directory_is_not_used(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    link = tmp_path / "build-logs"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("This host does not permit symlink creation")
    failure = studio_build_failure(0.1, stack.IMAGE_TAG, log_dir=link)
    assert failure.log_path is None and not any(outside.iterdir())


@pytest.fixture
def fake_build(tmp_path, monkeypatch):
    data_dir = tmp_path / "lab"
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    calls = []
    monkeypatch.setattr(stack, "_endpoint", lambda: "unix:///owned.sock")
    monkeypatch.setattr(stack, "_linux_info", lambda _: {})
    monkeypatch.setattr(stack, "_bounded_process", lambda args, **kwargs: CommandResult(
        0, stack.STUDIO_COMMIT.encode() if "rev-parse" in args else b"", b""))

    def extract_source(archive, context):
        (context / "docker").mkdir()
        (context / "docker/Dockerfile.backend").write_text("FROM example")

    monkeypatch.setattr(stack, "_extract_source", extract_source)
    # Existing Studio stack tests independently verify the archived source and both patches.
    monkeypatch.setattr(stack, "apply_fixture_config_patch", lambda context: None)
    monkeypatch.setattr(stack, "apply_appeal_snapshot_patch", lambda context, **kwargs: None)

    def command(endpoint, args, **kwargs):
        calls.append((args, kwargs))
        if args[0] == "build":
            return CommandResult(0, SECRET, b"")
        assert args[:2] == ["image", "inspect"]
        return CommandResult(0, json.dumps({"Id": IMAGE_ID, "Os": "linux", "Config": {
            "Labels": {stack.COMMIT_LABEL: stack.STUDIO_COMMIT,
                       stack.PATCH_LABEL: stack.FIXTURE_CONFIG_PATCH}}}).encode(), b"")

    monkeypatch.setattr(stack, "_command", command)
    monkeypatch.setattr(stack, "status", lambda root: stack._load(root / "studio"))
    return data_dir, checkout, calls


def test_successful_studio_build_keeps_bounds_provenance_and_writes_no_failure_log(fake_build):
    data_dir, checkout, calls = fake_build
    progress = []
    result = stack.build(data_dir, checkout=checkout, progress=progress.append)
    assert result["image_id"] == IMAGE_ID
    assert result["source_commit"] == stack.STUDIO_COMMIT
    assert result["fixture_config_patch"] == stack.FIXTURE_CONFIG_PATCH
    assert result["packaging_overlay"] == "upstream-license-notice-v1"
    assert len(progress) == 2
    assert calls[0][1] == {"timeout": 1200, "output_limit": 8_388_608}
    assert calls[0][0][:5] == ["build", "--quiet", "--target", "prod", "--label"]
    assert calls[0][0][calls[0][0].index("--tag") + 1] == (
        stack.IMAGE_TAG + "-" + result["owner"])
    assert not (data_dir / "studio/build-logs").exists()
    assert not list((data_dir / "studio").glob("studio-build-*"))


def test_failed_studio_build_keeps_existing_image_and_stops_before_inspection(
        fake_build, monkeypatch):
    data_dir, checkout, calls = fake_build
    old_state = stack.initialize(data_dir)
    old_state["image_id"] = "sha256:" + "b" * 64
    stack._save(data_dir / "studio", old_state)

    def command(endpoint, args, **kwargs):
        assert args[0] == "build", "A failed build must not inspect or adopt an image"
        return CommandResult(17, SECRET, b"Could not resolve host\n" + SECRET)

    monkeypatch.setattr(stack, "_command", command)
    with pytest.raises(StudioBuildFailure) as captured:
        stack.build(data_dir, checkout=checkout)
    assert captured.value.diagnostic["category"] == "dns_failure"
    assert captured.value.diagnostic["docker_exit_code"] == 17
    assert captured.value.log_path.parent == data_dir / "studio/build-logs"
    assert stack._load(data_dir / "studio") == old_state
    assert not list((data_dir / "studio").glob("studio-build-*"))
    with stack._operation_lock(data_dir):
        pass


@pytest.mark.parametrize(("message", "category"), [
    ("Docker command timed out", "timeout"),
    ("Docker command exceeded its output limit", "output_limit"),
    ("Cannot launch Docker command", "docker_unavailable"),
])
def test_bounded_command_exceptions_are_classified_without_retaining_the_raw_cause(
        fake_build, monkeypatch, message, category):
    data_dir, checkout, _ = fake_build

    def command(*args, **kwargs):
        raise RuntimeError(message + " " + SECRET.decode())

    monkeypatch.setattr(stack, "_command", command)
    with pytest.raises(StudioBuildFailure) as captured:
        stack.build(data_dir, checkout=checkout)
    failure = captured.value
    assert failure.diagnostic["category"] == category
    assert failure.diagnostic["docker_exit_code"] is None
    assert failure.__cause__ is None and failure.__context__ is None
    assert "provider-secret" not in failure.log_path.read_text() + str(failure)


def test_cli_reports_studio_category_hint_and_diagnostic_path(tmp_path, monkeypatch, capsys):
    failure = studio_build_failure(0.5, stack.IMAGE_TAG, docker_exit=1,
                                   stderr=b"x509: certificate signed by unknown authority\n" + SECRET,
                                   log_dir=tmp_path / "studio/build-logs")

    def build(*args, **kwargs):
        raise failure

    monkeypatch.delenv("LAB_URL", raising=False)
    monkeypatch.setattr(stack, "build", build)
    assert main(["studio", "build", "--data-dir", str(tmp_path)]) == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert "Studio image build failed" in output.err and "tls_failure" in output.err
    assert "exit=1" in output.err and "keep verification enabled" in output.err
    assert str(failure.log_path) in output.err
    assert "provider-secret" not in output.err and "worker" not in output.err
