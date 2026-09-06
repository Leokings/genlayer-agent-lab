import json

import pytest

from genlayer_agent_lab import runtime
from genlayer_agent_lab.runtime import container
from genlayer_agent_lab.runtime.build_diagnostics import BuildFailure, build_failure


@pytest.mark.parametrize(("raw", "category"), [
    (b"Could not resolve registry", "dns_failure"),
    (b"x509: certificate signed by unknown authority", "tls_failure"),
    (b"no space left on device", "disk_full"),
    (b"No matching distribution found", "package_resolution"),
    (b"THESE PACKAGES DO NOT MATCH THE HASHES", "dependency_integrity"),
    (b"Docker command timed out", "timeout"),
    (b"Docker command exceeded its output limit", "output_limit"),
    (b"pull access denied", "registry_access"),
])
def test_known_failures_have_useful_structured_logs_without_raw_credentials(tmp_path, raw, category):
    secret = b"user:super-secret@registry.invalid?token=another-secret"
    failure = build_failure("build_image", 12.3, container.IMAGE_TAG, docker_exit=1,
                            stdout=secret, stderr=raw + b"\n" + secret, log_dir=tmp_path / "build-logs")
    assert isinstance(failure, BuildFailure)
    assert failure.diagnostic["category"] == category
    saved = failure.log_path.read_text(encoding="utf-8")
    data = json.loads(saved)
    assert data["docker_exit_code"] == 1
    assert "super-secret" not in saved and "another-secret" not in saved
    assert "registry.invalid" not in saved and "stdout" not in data and "stderr" not in data
    assert len(saved) < 4096


def test_unknown_output_and_unwritable_log_never_mask_original_failure(tmp_path):
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("existing file", encoding="utf-8")
    failure = build_failure("build_image", 0.1, container.IMAGE_TAG,
                            stderr=b"Unrecognized error containing top-secret", log_dir=blocked)
    assert failure.log_path is None
    assert failure.diagnostic["category"] == "build_failure"
    assert "top-secret" not in str(failure)
    assert blocked.read_text() == "existing file"


@pytest.fixture
def fake_build(monkeypatch):
    calls = []
    monkeypatch.setattr(container, "_endpoint", lambda: "unix:///fake.sock")
    monkeypatch.setattr(container, "_linux_info", lambda endpoint: {"OSType": "linux"})
    monkeypatch.setattr(runtime, "doctor", lambda: {"ready": True})
    monkeypatch.setattr(container, "_copy_verified_context", lambda context: None)
    monkeypatch.setattr(container, "_image", lambda endpoint: "sha256:" + "a" * 64)

    def command(endpoint, args, **options):
        calls.append((args, options))
        return container.CommandResult(0, b"", b"")

    monkeypatch.setattr(container, "_command", command)
    monkeypatch.setattr(container, "evaluate_binding", lambda *args, **options: {
        "verdict": "approve", "provenance": {"contract_executed": True},
    })
    return calls


def test_build_emits_three_stages_and_preserves_no_argument_api(fake_build, tmp_path):
    progress = []
    result = container.build_worker(progress=progress.append, log_dir=tmp_path / "build-logs")
    assert result["ready"] is True
    assert progress == ["prepare_sdk", "build_image", "readiness_probe"]
    assert fake_build[0][1]["timeout"] == 600
    assert not (tmp_path / "build-logs").exists(), "Successful builds do not write failure logs"
    assert container.build_worker()["ready"] is True


def test_failed_docker_build_logs_exit_and_stops_before_readiness(fake_build, monkeypatch, tmp_path):
    progress = []
    monkeypatch.setattr(container, "_command", lambda *args, **options: container.CommandResult(
        17, b"private registry credential", b"No matching distribution found for private-package",
    ))
    with pytest.raises(BuildFailure) as captured:
        container.build_worker(progress=progress.append, log_dir=tmp_path / "build-logs")
    assert progress == ["prepare_sdk", "build_image"]
    diagnostic = json.loads(captured.value.log_path.read_text())
    assert diagnostic["docker_exit_code"] == 17
    assert diagnostic["category"] == "package_resolution"
    assert "private-package" not in json.dumps(diagnostic)


def test_build_timeout_and_readiness_output_have_separate_fixed_diagnostics(fake_build, monkeypatch, tmp_path):
    def timed_out(*args, **options):
        raise RuntimeError("Docker command timed out")

    monkeypatch.setattr(container, "_command", timed_out)
    with pytest.raises(BuildFailure) as captured:
        container.build_worker(log_dir=tmp_path / "build-logs")
    assert captured.value.diagnostic["category"] == "timeout"
    monkeypatch.setattr(container, "_command", lambda *args, **options: container.CommandResult(0, b"", b""))

    def bad_probe(*args, **options):
        raise RuntimeError("Contract printed arbitrary-private-fixture")

    monkeypatch.setattr(container, "evaluate_binding", bad_probe)
    with pytest.raises(BuildFailure) as captured:
        container.build_worker(log_dir=tmp_path / "build-logs")
    assert captured.value.diagnostic["stage"] == "readiness_probe"
    assert captured.value.diagnostic["category"] == "readiness_failure"
    assert "arbitrary-private-fixture" not in captured.value.log_path.read_text()
