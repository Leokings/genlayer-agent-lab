"""Live subprocess diagnostics without Docker or changes to an installation."""

import json
import sys
import threading
import time

import pytest

from genlayer_agent_lab.runtime import container
from genlayer_agent_lab.runtime import studio_stack as stack


def test_live_output_arrives_before_exit_and_preserves_cancellation():
    cancelled = threading.Event()
    observed = {"stdout": bytearray(), "stderr": bytearray()}

    def observe(chunk, channel):
        observed[channel].extend(chunk)
        if all(observed.values()):
            cancelled.set()

    command = ("import sys,time; print('downloading',flush=True); "
               "print('dependency',file=sys.stderr,flush=True); time.sleep(30)")
    start = time.monotonic()
    with pytest.raises(RuntimeError, match="cancelled") as caught:
        container._bounded_process([sys.executable, "-c", command], timeout=5,
                                   cancel_event=cancelled, on_output=observe)
    assert time.monotonic() - start < 3
    assert caught.value.failure_kind == "cancelled"
    assert bytes(observed["stdout"]) == caught.value.stdout
    assert bytes(observed["stderr"]) == caught.value.stderr


def test_observer_failure_does_not_break_command_or_leak_its_error():
    def broken_observer(chunk, channel):
        raise RuntimeError("private-observer-secret")

    result = container._bounded_process(
        [sys.executable, "-c", "print('finished',flush=True)"],
        timeout=5, on_output=broken_observer,
    )
    assert result.returncode == 0 and result.stdout.strip() == b"finished"


def test_observer_receives_no_more_than_combined_output_limit():
    observed = []
    command = "import sys; sys.stdout.write('a'*700); sys.stderr.write('b'*700)"
    with pytest.raises(RuntimeError, match="output limit") as caught:
        container._bounded_process([sys.executable, "-c", command], output_limit=1024,
                                   on_output=lambda chunk, channel: observed.append(chunk))
    assert caught.value.failure_kind == "output_limit"
    assert sum(map(len, observed)) == 1024


def test_live_redaction_preserves_pipe_and_chunk_boundaries(tmp_path, monkeypatch):
    root = tmp_path / "studio-modern"
    root.mkdir()
    (tmp_path / "admin.token").write_text("file-admin-secret")
    monkeypatch.setenv("TEST_API_KEY", "environment-secret")
    output = stack._StageOutput()
    output(b"API_KEY=partial-", "stdout")
    output(b"Downloading dependency\n", "stderr")
    assert "partial" not in json.dumps(output.snapshot(root))
    output(b"credential\nfile-admin-", "stdout")
    output(b"Waiting\n", "stderr")
    output(b"secret environment-secret Bearer hidden-bearer\n", "stdout")
    output(b"https://user:hidden-password@example.test\n", "stderr")
    output(b"\x1b[31mInstalling packages\x1b[0m\n", "stderr")
    snapshot = output.snapshot(root)
    serialized = json.dumps(snapshot)
    for secret in ("partial-credential", "file-admin-secret", "environment-secret",
                   "hidden-bearer", "hidden-password"):
        assert secret not in serialized
    assert "[redacted]" in serialized
    assert snapshot["last_output_line"] == "Installing packages"
    assert snapshot["last_output_at"] and snapshot["seconds_since_output"] >= 0


def test_live_tail_omits_overlong_partial_lines_and_stays_bounded(tmp_path):
    output = stack._StageOutput()
    for _ in range(8):
        output(b"secret-fragment" * 4096, "stdout")
    assert not output.snapshot(tmp_path)["output_tail"]
    assert len(output.pending["stdout"]) <= stack.OPERATION_OUTPUT_BYTES
    output(b"\nRecovered step\n", "stdout")
    snapshot = output.snapshot(tmp_path)
    assert "secret-fragment" not in snapshot["output_tail"]
    assert "overlong output line omitted" in snapshot["output_tail"]
    assert snapshot["output_tail_truncated"]
    assert snapshot["last_output_line"] == "Recovered step"
    assert len(snapshot["output_tail"].encode()) <= stack.OPERATION_OUTPUT_BYTES


def test_multiline_known_secret_stays_redacted_when_first_line_is_evicted(tmp_path, monkeypatch):
    monkeypatch.setenv("LAB_REVIEW_SECRET", "synthetic-first-line\nsynthetic-second-line")
    output = stack._StageOutput()
    output(b"synthetic-first-line\nsynthetic-second-line\n", "stderr")
    assert "synthetic-" not in json.dumps(output.snapshot(tmp_path))
    padding = stack.OPERATION_OUTPUT_BYTES - len(b"synthetic-second-line\n") - 1
    output(b"x" * padding + b"\n", "stdout")
    snapshot = output.snapshot(tmp_path)
    assert snapshot["output_tail_truncated"]
    assert "synthetic-" not in json.dumps(snapshot)
    assert "[redacted]" in snapshot["output_tail"]


def test_real_output_then_silence_updates_live_log_before_timeout(tmp_path, monkeypatch):
    root = tmp_path / "studio-modern"
    root.mkdir()
    monkeypatch.setattr(stack, "OPERATION_HEARTBEAT_SECONDS", .05)
    messages, running = [], []

    def progress(message):
        messages.append(message)
        if "still running" in message:
            path = next((root / "operation-logs").glob("*.json"))
            running.append(json.loads(path.read_text()))

    command = ("import time; print('Downloading dependency API_KEY=private-key',flush=True); "
               "time.sleep(30)")
    with pytest.raises(stack.StudioOperationFailure) as caught:
        stack._run_stage(root, "build_image", lambda output: container._bounded_process(
            [sys.executable, "-c", command], timeout=.7, on_output=output),
            timeout=.7, progress=progress, live_output=True)
    observed = [item for item in running if item["last_output_line"]]
    assert len(observed) >= 2
    assert all(item["status"] == "running" for item in observed)
    assert observed[-1]["seconds_since_output"] > observed[0]["seconds_since_output"]
    assert all(item["last_output_at"] == observed[0]["last_output_at"] for item in observed)
    assert any("Last command output" in message for message in messages)
    assert "private-key" not in json.dumps(running) + "\n".join(messages)
    final = json.loads(caught.value.log_path.read_text())
    assert final["status"] == "failed" and final["category"] == "timeout"
    assert final["last_output_line"] == "Downloading dependency API_KEY=[redacted]"
    assert "private-key" not in json.dumps(final)
    assert "cleanup" in final["exit_code_note"]


def test_successful_live_stage_finalizes_log(tmp_path):
    root = tmp_path / "studio-modern"
    root.mkdir()

    def complete(output):
        output(b"#38 DONE 64s\n", "stderr")
        return container.CommandResult(0, b"", b"#38 DONE 64s\n")

    result = stack._run_stage(root, "build_image", complete, timeout=5, live_output=True)
    final = json.loads(next((root / "operation-logs").glob("*.json")).read_text())
    assert result.returncode == 0
    assert final["status"] == "completed" and final["last_output_line"] == "#38 DONE 64s"


@pytest.mark.parametrize("message,category", [
    ("Docker command timed out", "timeout"),
    ("Authentication timed out", "command_failed"),
])
def test_timeout_exit_zero_is_not_presented_as_success(tmp_path, message, category):
    root = tmp_path / "studio-modern"
    root.mkdir()

    def fail():
        error = RuntimeError(message)
        error.returncode = 0
        raise error

    with pytest.raises(stack.StudioOperationFailure) as caught:
        stack._run_stage(root, "build_image", fail, timeout=5)
    diagnostic = caught.value.diagnostic
    assert diagnostic["category"] == category
    assert diagnostic["exit_code"] == 0
    if category == "timeout":
        assert "exit 0" not in str(caught.value)
        assert "before completion was verified" in str(caught.value)
        assert "does not establish successful completion" in diagnostic["exit_code_note"]
