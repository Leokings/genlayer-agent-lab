"""Real Windows pipe regressions; no Docker, Studio, or installation mutations."""

import hashlib
import os
import sys
import threading
import time

import pytest

from genlayer_agent_lab.runtime import container

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows nonblocking pipe runner")


def test_success_does_not_require_inherited_output_handles_to_close(monkeypatch):
    # Keep the job open until after the assertion, deliberately reproducing a
    # helper that still owns stdout/stderr after the successful leader exits.
    # The test finally closes the real owned job, so its helper cannot leak.
    close_jobs = []
    original_job = container._windows_job

    def delayed_job(process):
        close_jobs.append(original_job(process))
        return lambda: None

    monkeypatch.setattr(container, "_windows_job", delayed_job)
    before_threads = set(threading.enumerate())
    parent = (
        "import subprocess,sys,time; time.sleep(.1); "
        "subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); "
        "print('build complete',flush=True); print('last diagnostic',file=sys.stderr,flush=True)"
    )
    try:
        start = time.monotonic()
        result = container._bounded_process([sys.executable, "-c", parent], timeout=5)
        assert result.returncode == 0
        assert result.stdout.strip() == b"build complete"
        assert result.stderr.strip() == b"last diagnostic"
        assert time.monotonic() - start < 2
        assert set(threading.enumerate()) == before_threads
    finally:
        for close in close_jobs:
            close()


def test_success_still_terminates_owned_descendants(tmp_path):
    marker = tmp_path / "helper-survived.txt"
    child = (
        "import time; from pathlib import Path; time.sleep(2); "
        f"Path({str(marker)!r}).write_text('survived')"
    )
    parent = (
        "import subprocess,sys,time; time.sleep(.1); "
        f"subprocess.Popen([sys.executable,'-c',{child!r}]); "
        "print('done',flush=True)"
    )
    result = container._bounded_process([sys.executable, "-c", parent], timeout=5)
    assert result.returncode == 0 and result.stdout.strip() == b"done"
    time.sleep(2.1)
    assert not marker.exists()


def test_job_is_assigned_before_child_can_create_descendants(tmp_path, monkeypatch):
    # Deterministically widen the gap seen during loaded-host setup. A child
    # created before job assignment is not retroactively added to the job.
    marker = tmp_path / "escaped-descendant.txt"
    job = container._windows_job

    def delayed_assignment(process):
        time.sleep(.5)
        return job(process)

    monkeypatch.setattr(container, "_windows_job", delayed_assignment)
    child = ("import time; from pathlib import Path; time.sleep(1); "
             f"Path({str(marker)!r}).write_text('escaped')")
    parent = ("import subprocess,sys,time; "
              f"subprocess.Popen([sys.executable,'-c',{child!r}]); "
              "time.sleep(.7); print('done',flush=True)")
    result = container._bounded_process([sys.executable, "-c", parent], timeout=5)
    assert result.returncode == 0
    time.sleep(1.1)
    assert not marker.exists(), "Descendant executed before Windows job ownership was established"


def test_resume_failure_cleans_up_suspended_process_and_job(tmp_path, monkeypatch):
    marker = tmp_path / "must-not-execute.txt"
    processes, closed = [], []
    popen, job = container.subprocess.Popen, container._windows_job

    def track(*args, **kwargs):
        process = popen(*args, **kwargs)
        processes.append(process)
        return process

    def track_job(process):
        close = job(process)

        def close_owned():
            closed.append(True)
            close()

        return close_owned

    def fail_resume(process):
        raise RuntimeError("Synthetic resume failure")

    monkeypatch.setattr(container.subprocess, "Popen", track)
    monkeypatch.setattr(container, "_windows_job", track_job)
    monkeypatch.setattr(container, "_resume_windows_process", fail_resume)
    command = f"from pathlib import Path; Path({str(marker)!r}).write_text('executed')"
    with pytest.raises(RuntimeError, match="resume failure"):
        container._bounded_process([sys.executable, "-c", command])
    assert not marker.exists() and closed == [True]
    assert processes[0].poll() is not None
    assert all(stream.closed for stream in
               (processes[0].stdin, processes[0].stdout, processes[0].stderr))


def test_large_stdin_partial_writes_and_eof_preserve_bytes():
    payload = bytes(range(256)) * 1024
    command = (
        "import sys,hashlib; data=sys.stdin.buffer.read(); "
        "print(hashlib.sha256(data).hexdigest()); "
        "sys.stderr.write('input complete'); sys.exit(7)"
    )
    result = container._bounded_process([sys.executable, "-c", command], payload=payload, timeout=10)
    assert result.returncode == 7
    assert result.stdout.strip().decode() == hashlib.sha256(payload).hexdigest()
    assert result.stderr == b"input complete"


def test_full_stdin_pipe_does_not_block_timeout():
    start = time.monotonic()
    with pytest.raises(RuntimeError, match="timed out") as caught:
        container._bounded_process(
            [sys.executable, "-c", "import time; print('ready',flush=True); time.sleep(30)"],
            payload=b"x" * 262_144, timeout=.5,
        )
    assert time.monotonic() - start < 3
    assert b"ready" in caught.value.stdout
    assert caught.value.returncode is not None


def test_combined_output_limit_catches_fast_exit():
    command = (
        "import sys; sys.stdout.buffer.write(b'a'*700); "
        "sys.stderr.buffer.write(b'b'*700)"
    )
    with pytest.raises(RuntimeError, match="output limit") as caught:
        container._bounded_process([sys.executable, "-c", command], output_limit=1024, timeout=5)
    assert len(caught.value.stdout) + len(caught.value.stderr) == 1024


def test_active_cancellation_with_unread_input_is_bounded():
    cancelled = threading.Event()
    timer = threading.Timer(.5, cancelled.set)
    timer.start()
    try:
        start = time.monotonic()
        with pytest.raises(RuntimeError, match="cancelled"):
            container._bounded_process(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                payload=b"x" * 262_144, timeout=20, cancel_event=cancelled,
            )
        assert time.monotonic() - start < 3
    finally:
        timer.cancel()
        timer.join()


def test_pipe_initialization_failure_kills_process_without_blocking_drain(monkeypatch):
    processes = []
    original_popen = container.subprocess.Popen

    def track(*args, **kwargs):
        process = original_popen(*args, **kwargs)
        processes.append(process)
        return process

    def fail(*args):
        raise OSError("synthetic pipe setup error")

    monkeypatch.setattr(container.subprocess, "Popen", track)
    monkeypatch.setattr(container.os, "set_blocking", fail)
    with pytest.raises(RuntimeError, match="pipe I/O failed"):
        container._bounded_process([sys.executable, "-c", "import time; time.sleep(30)"], timeout=5)
    assert processes[0].poll() is not None
    assert all(stream.closed for stream in
               (processes[0].stdin, processes[0].stdout, processes[0].stderr))
