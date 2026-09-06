"""Bounded local Docker execution of explicitly imported custom contracts.

The host checks image/container identity and input hashes. Code inside the
container can control its own output; reported execution evidence is diagnostic,
not an independent attestation or security certification.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .. import __version__
from ..bindings import extract_verdict, validate_snapshot
from .pins import BUNDLE_SHA256, GENVM_VERSION, RUNNER_HASH, TEST_SUITE_VERSION

IMAGE_TAG = f"genlayer-agent-lab-worker:{__version__}"
OWNER_LABEL = "io.genlayer.agent-lab.owner"
VERSION_LABEL = "io.genlayer.agent-lab.worker.version"
PROTOCOL_LABEL = "io.genlayer.agent-lab.worker.protocol"
SDK_LABEL = "io.genlayer.agent-lab.worker.sdk"
OUTPUT_LIMIT = 1_048_576
MEMORY_LIMIT = 536_870_912


@dataclass
class CommandResult:
    returncode: int
    stdout: bytes
    stderr: bytes


def _windows_job(process):
    """Keep even orphaned CLI-plugin descendants inside an owned kill-on-close job."""
    import ctypes
    from ctypes import wintypes

    class BasicLimits(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                    ("PerJobUserTimeLimit", ctypes.c_int64), ("LimitFlags", wintypes.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wintypes.DWORD), ("Affinity", ctypes.c_size_t),
                    ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

    class IOCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class ExtendedLimits(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", BasicLimits), ("IoInfo", IOCounters),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                               ctypes.c_void_p, wintypes.DWORD]
    kernel.SetInformationJobObject.restype = wintypes.BOOL
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.CreateJobObjectW(None, None)
    if not handle:
        raise RuntimeError("Could not create owned Windows subprocess job")
    limits = ExtendedLimits()
    limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if (not kernel.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits))
            or not kernel.AssignProcessToJobObject(handle, wintypes.HANDLE(int(process._handle)))):
        kernel.CloseHandle(handle)
        raise RuntimeError("Could not isolate Docker CLI descendants in an owned Windows job")
    return lambda: kernel.CloseHandle(handle)


def _bounded_process(args, *, payload=b"", timeout=8, output_limit=OUTPUT_LIMIT,
                     cancel_event=None, env=None):
    if cancel_event is not None and cancel_event.is_set():
        raise RuntimeError("Container evaluation cancelled")
    kwargs = ({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt"
              else {"start_new_session": True})
    try:
        process = subprocess.Popen(
            args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=env, **kwargs
        )
    except OSError as exc:
        raise RuntimeError(f"Cannot launch Docker command ({type(exc).__name__})") from exc
    try:
        close_job = _windows_job(process) if os.name == "nt" else None
    except RuntimeError:
        process.kill()
        process.wait(timeout=3)
        raise

    def terminate_group():
        nonlocal close_job
        if close_job is not None:
            close_job()
            close_job = None
        elif os.name != "nt":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if process.poll() is None:
            process.kill()
    output = [bytearray(), bytearray()]
    overflow = threading.Event()
    lock = threading.Lock()
    total = 0

    def drain(stream, index):
        nonlocal total
        try:
            while chunk := stream.read(16_384):
                with lock:
                    total += len(chunk)
                    remaining = output_limit - sum(map(len, output))
                    if remaining > 0:
                        output[index].extend(chunk[:remaining])
                    if total > output_limit:
                        overflow.set()
                        break
        except (OSError, ValueError):
            pass

    def feed():
        try:
            process.stdin.write(payload)
            process.stdin.close()
        except (OSError, ValueError):
            pass

    threads = [threading.Thread(target=drain, args=(process.stdout, 0), daemon=True),
               threading.Thread(target=drain, args=(process.stderr, 1), daemon=True),
               threading.Thread(target=feed, daemon=True)]
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + timeout
    failure = None
    try:
        while process.poll() is None:
            if overflow.is_set():
                failure = "Docker command exceeded its output limit"
                break
            if cancel_event is not None and cancel_event.is_set():
                failure = "Container evaluation cancelled"
                break
            if time.monotonic() >= deadline:
                failure = "Docker command timed out"
                break
            time.sleep(0.025)
        if failure:
            terminate_group()
        process.wait(timeout=3)
    finally:
        # A CLI plugin may hold pipes even after the docker parent has exited.
        # Terminate only this command's owned descendants before joining readers.
        terminate_group()
        for thread in threads:
            thread.join(timeout=1)
        # Never wait on a BufferedReader lock held by an unexpectedly live thread.
        for stream, thread in ((process.stdout, threads[0]), (process.stderr, threads[1]),
                               (process.stdin, threads[2])):
            if not thread.is_alive():
                stream.close()
        if any(thread.is_alive() for thread in threads):
            failure = failure or "Docker command left an unclosed child pipe"
    if failure or overflow.is_set():
        raise RuntimeError(failure or "Docker command exceeded its output limit")
    return CommandResult(process.returncode, bytes(output[0]), bytes(output[1]))


def _command(endpoint, args, **kwargs):
    command = ["docker"]
    env = os.environ.copy()
    if endpoint:
        command.extend(["--host", endpoint])
        # Once inspected, the explicit local endpoint wins over context env vars.
        env.pop("DOCKER_CONTEXT", None)
        env.pop("DOCKER_HOST", None)
    return _bounded_process([*command, *args], env=env, **kwargs)


def _json_output(result, description):
    if result.returncode:
        raise RuntimeError(f"{description} failed (Docker exit {result.returncode})")
    try:
        return json.loads(result.stdout)
    except (ValueError, TypeError) as exc:
        raise RuntimeError(f"{description} returned invalid metadata") from exc


def _endpoint(*, timeout=8):
    explicit = os.environ.get("DOCKER_HOST")
    if explicit:
        endpoint = explicit
    else:
        endpoint = _json_output(
            _command(None, ["context", "inspect", "--format", "{{json .Endpoints.docker.Host}}"],
                     timeout=timeout),
            "Docker context inspection",
        )
    if (not isinstance(endpoint, str)
            or not endpoint.startswith(("unix:///", "npipe:////./pipe/"))):
        raise RuntimeError("Select a local Docker Unix socket or Windows named-pipe context")
    return endpoint


def _linux_info(endpoint):
    info = _json_output(_command(endpoint, ["info", "--format", "{{json .}}"]), "Docker probe")
    if not isinstance(info, dict) or info.get("OSType") != "linux":
        raise RuntimeError("Custom contracts require a running Linux Docker engine")
    return info


def _image(endpoint):
    result = _command(endpoint, ["image", "inspect", IMAGE_TAG, "--format", "{{json .}}"])
    info = _json_output(result, "Worker image inspection; run worker build if missing")
    labels = info.get("Config", {}).get("Labels") or {}
    image_id = info.get("Id", "")
    if (not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id)
            or info.get("Os") != "linux"
            or labels.get(VERSION_LABEL) != __version__
            or labels.get(PROTOCOL_LABEL) != "2"
            or labels.get(SDK_LABEL) != BUNDLE_SHA256
            or info.get("Config", {}).get("User") != "10001:10001"):
        raise RuntimeError("Worker image is incompatible; rebuild the versioned worker image")
    return image_id


def doctor() -> dict:
    result = {"backend": "container-glsim", "image_tag": IMAGE_TAG,
              "ready": False, "status": "error", "docker_available": False, "image_ready": False}
    try:
        endpoint = _endpoint()
        info = _linux_info(endpoint)
        result.update(docker_available=True, docker_version=info.get("ServerVersion"),
                      architecture=info.get("Architecture"))
        image_id = _image(endpoint)
        result.update(ready=True, status="ready", image_ready=True, image_id=image_id)
    except RuntimeError as exc:
        result["error"] = str(exc)
    return result


def _inspect_container(endpoint, name):
    return _json_output(
        _command(endpoint, ["inspect", name, "--format", "{{json .}}"]), "Container inspection"
    )


def _owned(info, name, owner):
    return (info.get("Name", "").lstrip("/") == name
            and (info.get("Config", {}).get("Labels") or {}).get(OWNER_LABEL) == owner)


def _verify_isolation(info, name, owner, image_id):
    config, host = info.get("Config", {}), info.get("HostConfig", {})
    if (not _owned(info, name, owner) or info.get("Image") != image_id
            or config.get("User") != "10001:10001"
            or host.get("NetworkMode") != "none" or host.get("ReadonlyRootfs") is not True
            or host.get("Privileged") or host.get("Binds") or host.get("Devices")
            or host.get("Mounts")
            or "ALL" not in (host.get("CapDrop") or [])
            or not any(str(option).startswith("no-new-privileges")
                       for option in host.get("SecurityOpt", []))
            or host.get("Memory") != MEMORY_LIMIT
            or host.get("MemorySwap") != MEMORY_LIMIT
            or host.get("PidsLimit") != 64
            or host.get("NanoCpus") != 1_000_000_000
            or set(host.get("Tmpfs", {})) != {"/tmp"}):
        raise RuntimeError("Container did not receive the required isolation settings")


def _remove_owned(endpoint, name, owner):
    result = _command(endpoint, ["inspect", name, "--format", "{{json .}}"])
    if result.returncode:
        if b"No such" in result.stderr:
            return
        raise RuntimeError(f"Could not verify cleanup of owned container {name}")
    info = _json_output(result, "Cleanup ownership inspection")
    if not _owned(info, name, owner):
        raise RuntimeError("Refusing to remove a container with different ownership")
    container_id = info.get("Id", "")
    if not re.fullmatch(r"[0-9a-f]{64}", container_id):
        raise RuntimeError("Cleanup inspection returned an invalid container identifier")
    removed = _command(endpoint, ["rm", "--force", container_id])
    if removed.returncode:
        raise RuntimeError(f"Could not remove owned container {name}")


def evaluate_binding(snapshot: dict, context: dict, *, timeout: float = 60,
                     cancel_event: threading.Event | None = None) -> dict:
    import uuid

    if not isinstance(timeout, (int, float)) or not 0 < timeout <= 3600:
        raise ValueError("Container timeout must be between 0 and 3600 seconds")
    snapshot = validate_snapshot(snapshot)
    # Explicit fields only: no inherited provider/wallet credentials or arbitrary
    # run metadata enters the contract's container environment.
    required = ("evidence", "resource_id", "policy_version", "amount", "fixture_verdict")
    context = {key: context[key] for key in required}
    payload = json.dumps({"snapshot": snapshot, "context": context},
                         ensure_ascii=True, allow_nan=False).encode()
    if len(payload) > OUTPUT_LIMIT:
        raise ValueError("Custom worker input exceeds 1 MiB")
    if cancel_event is not None and cancel_event.is_set():
        raise RuntimeError("Container evaluation cancelled")
    endpoint = _endpoint()
    _linux_info(endpoint)
    image_id = _image(endpoint)  # Dispatch by immutable ID, never by the mutable tag.
    owner = uuid.uuid4().hex
    name = f"gl-agent-lab-{owner}"
    args = [
        "create", "--name", name, "--label", f"{OWNER_LABEL}={owner}",
        "--pull", "never", "--network", "none", "--read-only",
        "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m,uid=10001,gid=10001,mode=700",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
        "--user", "10001:10001", "--pids-limit", "64",
        "--memory", str(MEMORY_LIMIT), "--memory-swap", str(MEMORY_LIMIT),
        "--cpus", "1", "--ulimit", "nofile=256:256", "--log-driver", "none",
        "--interactive", image_id,
    ]
    try:
        created = _command(endpoint, args, timeout=15, cancel_event=cancel_event)
        if created.returncode:
            raise RuntimeError("Could not create the isolated worker container")
        before = _inspect_container(endpoint, name)
        _verify_isolation(before, name, owner, image_id)
        execution = _command(
            endpoint, ["start", "--attach", "--interactive", name], payload=payload,
            timeout=timeout, output_limit=OUTPUT_LIMIT, cancel_event=cancel_event,
        )
        after = _inspect_container(endpoint, name)
        _verify_isolation(after, name, owner, image_id)
        state = after.get("State", {})
        if state.get("Running") or state.get("OOMKilled") or state.get("ExitCode") != 0:
            raise RuntimeError("Custom worker failed, exhausted memory, or did not exit successfully")
        response = _json_output(execution, "Custom contract worker")
        if not isinstance(response, dict) or response.get("execution_success") is not True:
            raise RuntimeError("Custom worker did not report successful execution")
        consensus = response.get("consensus")
        if (response.get("error") or response.get("contract_executed") is not True
                or response.get("method") != snapshot["definition"]["method"]
                or response.get("runner_hash") != RUNNER_HASH
                or response.get("genlayer_test_version") != TEST_SUITE_VERSION
                or response.get("genvm_version") != GENVM_VERSION
                or response.get("mocked_io") is not True
                or response.get("strict_mocks") is not True
                or not isinstance(consensus, dict) or consensus.get("status") != "FINALIZED"):
            raise RuntimeError("Custom worker returned incomplete execution diagnostics")
        verdict = extract_verdict(response.get("raw_result"), snapshot["definition"])
        return {"verdict": verdict, "provenance": {
            "backend": "container-glsim", "image_id": image_id,
            "image_tag": IMAGE_TAG, "source_sha256": snapshot["source_sha256"],
            "binding_sha256": snapshot["binding_sha256"], "runner_hash": RUNNER_HASH,
            "bundle_sha256": BUNDLE_SHA256, "genvm_version": GENVM_VERSION,
            "genlayer_test_version": TEST_SUITE_VERSION,
            "method": snapshot["definition"]["method"],
            "raw_result": response["raw_result"], "execution_success": True,
            "contract_executed": True, "mocked_io": True, "isolated": True,
            "fresh_process": True, "consensus": consensus,
            "host_verified": ["image_id", "isolation_settings", "source_sha256", "binding_sha256"],
            "evidence_trust": "Contract-controlled process diagnostics; not independent attestation",
            "isolation": "nonroot Docker; no network or host binds; read-only root; bounded resources",
        }}
    finally:
        _remove_owned(endpoint, name, owner)


def _copy_verified_context(destination: Path) -> None:
    """Copy an explicit worker allowlist, never the repository or user config."""
    package = Path(__file__).resolve().parents[1]
    runtime = package / "runtime"
    target = destination / "package" / "genlayer_agent_lab"
    (target / "runtime").mkdir(parents=True)
    names = ["__init__.py", "bindings.py", "runtime/container_worker.py",
             "runtime/container_support.py", "runtime/pins.py"]
    for relative in names:
        source = package / relative
        if source.is_symlink() or not source.resolve().is_relative_to(package):
            raise RuntimeError("Worker source must remain inside the installed package")
        shutil.copyfile(source, target / relative)
    (target / "runtime" / "__init__.py").write_text("", encoding="utf-8")
    shutil.copyfile(runtime / "Dockerfile.worker", destination / "Dockerfile")
    requirements = runtime / "worker-requirements.txt"
    if not requirements.is_file():
        raise RuntimeError("Packaged worker dependency lock is missing")
    shutil.copyfile(requirements, destination / "requirements.txt")
    source_cache = Path.home() / ".cache" / "genlayer-agent-lab" / "gltest-direct"
    archive = source_cache / f"genvm-universal-{GENVM_VERSION}.tar.xz"
    with archive.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != BUNDLE_SHA256:
            raise RuntimeError("Build SDK archive checksum mismatch")
    target_cache = destination / "sdk"
    target_cache.mkdir()
    shutil.copyfile(archive, target_cache / archive.name)
    manifest_name = f"verified-sdk-{GENVM_VERSION}.json"
    source_manifest = json.loads((source_cache / manifest_name).read_text(encoding="utf-8"))
    manifest = {}
    for relative, digest in source_manifest.items():
        relative = relative.replace("\\", "/")
        source = source_cache / "extracted" / GENVM_VERSION / relative
        if (source.is_symlink()
                or not source.resolve().is_relative_to(source_cache / "extracted" / GENVM_VERSION)):
            raise RuntimeError("SDK build manifest contains an invalid path")
        with source.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
                raise RuntimeError("SDK build source checksum mismatch")
        target = target_cache / "extracted" / GENVM_VERSION / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        manifest[relative] = digest
    (target_cache / manifest_name).write_text(json.dumps(manifest), encoding="utf-8")


def build_worker(*, progress=None, log_dir: Path | None = None) -> dict:
    """Explicit image build with optional fixed stage events and structured failure logs."""
    from . import doctor as native_doctor
    from .build_diagnostics import BuildFailure, build_failure

    started = time.monotonic()
    stage = "prepare_sdk"

    def announce(name):
        nonlocal stage
        stage = name
        if progress is not None:
            progress(name)

    try:
        announce("prepare_sdk")
        endpoint = _endpoint()
        _linux_info(endpoint)
        native = native_doctor()
        if not native.get("ready"):
            # The doctor's arbitrary error text is not included in a build log.
            raise RuntimeError("Prepare the pinned SDK with the native runtime doctor before building")
        with tempfile.TemporaryDirectory(prefix="gl-agent-lab-build-") as directory:
            context = Path(directory)
            _copy_verified_context(context)
            announce("build_image")
            result = _command(
                endpoint, ["build", "--tag", IMAGE_TAG, "--build-arg", f"APP_VERSION={__version__}",
                           "--file", str(context / "Dockerfile"), str(context)],
                timeout=600, output_limit=4 * OUTPUT_LIMIT,
            )
            if result.returncode:
                raise build_failure(stage, time.monotonic() - started, IMAGE_TAG,
                                    docker_exit=result.returncode, stdout=result.stdout,
                                    stderr=result.stderr, log_dir=log_dir)
        announce("readiness_probe")
        image_id = _image(endpoint)
        # Successful docker build alone does not establish runtime readiness.
        from ..bindings import _definition, _snapshot

        source = (Path(__file__).parent / "contracts" / "evidence_decision.py").read_text("utf-8")
        definition = _definition({
            "id": "worker-readiness", "title": "Worker readiness", "source": "evidence_decision.py",
            "method": "evaluate", "arguments": [{"from_field": "evidence"}],
            "llm_pattern": r"^AGENT_LAB_EVIDENCE_V1\n",
            "llm_response": {"verdict": "$fixture_verdict"},
        })
        probe = evaluate_binding(_snapshot(definition, source), {
            "evidence": "Valid readiness evidence", "resource_id": "probe", "policy_version": "v1",
            "amount": 1, "fixture_verdict": "approve",
        })
        if probe["verdict"] != "approve":
            raise RuntimeError("Built worker failed the readiness verdict check")
        return {"ready": True, "status": "ready", "image_ready": True, "docker_available": True,
                "backend": "container-glsim", "image_id": image_id, "image_tag": IMAGE_TAG,
                "probe": probe["provenance"], "note": "Image built and contract readiness probe passed"}
    except BuildFailure:
        raise
    except (RuntimeError, OSError, ValueError) as exc:
        raise build_failure(stage, time.monotonic() - started, IMAGE_TAG,
                            error=exc, log_dir=log_dir) from None
