"""Container isolation boundary tests and optional actual Docker execution."""

import copy
import hashlib
import json
import sys
import threading
import time

import pytest

from genlayer_agent_lab import __version__
from genlayer_agent_lab.bindings import _definition, _snapshot
from genlayer_agent_lab.runtime import container
from genlayer_agent_lab.runtime.container_worker import require_public_write
from genlayer_agent_lab.runtime.pins import (
    BUNDLE_SHA256,
    GENVM_VERSION,
    RUNNER_HASH,
    TEST_SUITE_VERSION,
)
from genlayer_agent_lab.runtime.worker import CONTRACT

IMAGE = "sha256:" + "a" * 64
CONTAINER_ID = "b" * 64
CONTEXT = {"evidence": "Valid delivery", "resource_id": "escrow-1", "policy_version": "v1",
           "amount": 100, "fixture_verdict": "approve"}


def test_public_write_check_follows_bound_proxy_method_instead_of_empty_class_schema():
    import functools

    class ActualContract:
        def evaluate(self, evidence):
            return evidence

    ActualContract.evaluate.__gl_public__ = True
    ActualContract.evaluate.__gl_readonly__ = False

    class CalldataProxy:
        # Like the installed GLSim proxy: no methods on the proxy class itself.
        def __getattr__(self, name):
            method = getattr(ActualContract(), name)

            @functools.wraps(method)
            def wrapped(*args, **kwargs):
                return method(*args, **kwargs)

            return wrapped

    assert "evaluate" not in dir(CalldataProxy)
    require_public_write(CalldataProxy(), "evaluate")


@pytest.mark.parametrize("public,readonly", [(False, False), (True, True), (1, False)])
def test_public_write_check_rejects_private_or_readonly_methods(public, readonly):
    class Contract:
        def method(self):
            pass

    Contract.method.__gl_public__ = public
    Contract.method.__gl_readonly__ = readonly
    with pytest.raises(RuntimeError, match="public write"):
        require_public_write(Contract(), "method")


@pytest.fixture
def binding():
    definition = _definition({
        "id": "custom-escrow", "title": "Custom escrow", "source": "custom.py",
        "method": "evaluate", "arguments": [{"from_field": "evidence"}],
        "llm_pattern": r"^AGENT_LAB_EVIDENCE_V1\n",
        "llm_response": {"verdict": "$fixture_verdict"},
    })
    return _snapshot(definition, CONTRACT.read_text(encoding="utf-8"))


class FakeDocker:
    def __init__(self):
        self.calls = []
        self.info = None
        self.start_error = None
        self.privileged = False
        self.wrong_owner = False
        self.response = {
            "raw_result": "approve", "execution_success": True, "contract_executed": True,
            "method": "evaluate", "runner_hash": RUNNER_HASH,
            "genlayer_test_version": TEST_SUITE_VERSION, "genvm_version": GENVM_VERSION,
            "mocked_io": True, "strict_mocks": True,
            "image_id": "forged-inside-container",
            "consensus": {"status": "FINALIZED", "votes": ["agree"] * 3,
                          "captured_validators": 1, "llm_fixture_calls": 4},
        }

    @staticmethod
    def result(value):
        return container.CommandResult(0, json.dumps(value).encode(), b"")

    def __call__(self, endpoint, args, **kwargs):
        self.calls.append((endpoint, args, kwargs))
        if args[:2] == ["context", "inspect"]:
            return self.result("unix:///var/run/docker.sock")
        if args[0] == "info":
            return self.result({"OSType": "linux", "ServerVersion": "test"})
        if args[:2] == ["image", "inspect"]:
            return self.result({"Id": IMAGE, "Os": "linux", "Config": {
                "User": "10001:10001", "Labels": {
                    container.VERSION_LABEL: __version__, container.PROTOCOL_LABEL: "2",
                    container.SDK_LABEL: BUNDLE_SHA256,
                }}})
        if args[0] == "create":
            name = args[args.index("--name") + 1]
            owner = args[args.index("--label") + 1].split("=", 1)[1]
            self.info = {
                "Id": CONTAINER_ID, "Name": "/" + name, "Image": IMAGE,
                "Config": {"User": "10001:10001", "Labels": {
                    container.OWNER_LABEL: "not-ours" if self.wrong_owner else owner}},
                "HostConfig": {
                    "NetworkMode": "none", "ReadonlyRootfs": True,
                    "Privileged": self.privileged, "Binds": None, "Devices": [],
                    "CapDrop": ["ALL"], "SecurityOpt": ["no-new-privileges:true"],
                    "Memory": container.MEMORY_LIMIT, "MemorySwap": container.MEMORY_LIMIT,
                    "PidsLimit": 64, "NanoCpus": 1_000_000_000,
                    "Tmpfs": {"/tmp": "rw,noexec,nosuid,size=64m"},
                }, "State": {"Running": False, "ExitCode": 0, "OOMKilled": False},
            }
            return container.CommandResult(0, CONTAINER_ID.encode(), b"")
        if args[0] == "inspect":
            return self.result(self.info)
        if args[0] == "start":
            if self.start_error:
                raise RuntimeError(self.start_error)
            return self.result(self.response)
        if args[0] == "rm":
            return container.CommandResult(0, CONTAINER_ID.encode(), b"")
        raise AssertionError(args)


@pytest.fixture
def docker(monkeypatch):
    fake = FakeDocker()
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    monkeypatch.setattr(container, "_command", fake)
    return fake


def test_container_pins_image_and_source_with_no_host_secrets(binding, docker, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "secret-not-for-container")
    result = container.evaluate_binding(binding, {**CONTEXT, "provider_key": "secret-not-for-container"})
    provenance = result["provenance"]
    assert result["verdict"] == "approve"
    assert provenance["image_id"] == IMAGE
    assert provenance["source_sha256"] == binding["source_sha256"]
    assert "not independent attestation" in provenance["evidence_trust"]
    creation = next(args for _, args, _ in docker.calls if args[0] == "create")
    assert creation[-1] == IMAGE
    assert "--network" in creation and "none" in creation
    assert "--read-only" in creation and "--cap-drop" in creation
    assert not any(arg in creation for arg in ("--volume", "--mount", "--env", "--privileged"))
    start = next(kwargs for _, args, kwargs in docker.calls if args[0] == "start")
    payload = json.loads(start["payload"])
    assert payload["snapshot"] == binding
    assert "secret-not-for-container" not in start["payload"].decode()
    assert "Valid delivery" not in str(creation)
    assert docker.calls[-1][1] == ["rm", "--force", CONTAINER_ID]


@pytest.mark.parametrize("failure", ["Docker command timed out", "Container evaluation cancelled",
                                      "Docker command exceeded its output limit"])
def test_failure_removes_only_the_owned_container(binding, docker, failure):
    docker.start_error = failure
    with pytest.raises(RuntimeError, match=failure):
        container.evaluate_binding(binding, CONTEXT)
    assert docker.calls[-1][1] == ["rm", "--force", CONTAINER_ID]
    assert not any("prune" in args for _, args, _ in docker.calls)


def test_incorrect_isolation_is_rejected_before_source_runs(binding, docker):
    docker.privileged = True
    with pytest.raises(RuntimeError, match="isolation"):
        container.evaluate_binding(binding, CONTEXT)
    assert not any(args[0] == "start" for _, args, _ in docker.calls)
    assert docker.calls[-1][1] == ["rm", "--force", CONTAINER_ID]


def test_cleanup_never_removes_an_unowned_container(binding, docker):
    docker.wrong_owner = True
    with pytest.raises(RuntimeError, match="different ownership"):
        container.evaluate_binding(binding, CONTEXT)
    assert not any(args[0] == "rm" for _, args, _ in docker.calls)


def test_altered_source_is_rejected_before_docker(binding, docker):
    tampered = copy.deepcopy(binding)
    tampered["source"] += "\n# changed after import"
    with pytest.raises(ValueError, match="hash"):
        container.evaluate_binding(tampered, CONTEXT)
    assert not docker.calls


@pytest.mark.parametrize("result", ["maybe", True, {}, {"verdict": "approve"}])
def test_unknown_custom_result_never_defaults_to_approval(binding, docker, result):
    docker.response["raw_result"] = result
    with pytest.raises(ValueError):
        container.evaluate_binding(binding, CONTEXT)
    assert docker.calls[-1][1] == ["rm", "--force", CONTAINER_ID]


def test_pre_cancel_does_not_create_container(binding, docker):
    cancelled = threading.Event()
    cancelled.set()
    with pytest.raises(RuntimeError, match="cancelled"):
        container.evaluate_binding(binding, CONTEXT, cancel_event=cancelled)
    assert not docker.calls


def test_doctor_is_read_only(docker):
    assert container.doctor()["ready"] is True
    assert not any(args[0] in {"create", "run", "build", "pull"} for _, args, _ in docker.calls)


def test_unavailable_docker_has_no_native_fallback(binding, monkeypatch):
    monkeypatch.setattr(container, "_endpoint", lambda: (_ for _ in ()).throw(RuntimeError("missing Docker")))
    assert container.doctor()["ready"] is False
    with pytest.raises(RuntimeError, match="missing Docker"):
        container.evaluate_binding(binding, CONTEXT)


def test_remote_docker_context_is_rejected(monkeypatch):
    monkeypatch.setenv("DOCKER_HOST", "tcp://remote.example:2376")
    assert container.doctor()["ready"] is False


def test_build_context_allowlist_omits_user_files_and_normalizes_sdk_paths(monkeypatch, tmp_path):
    fake_home = tmp_path / "home"
    cache = fake_home / ".cache" / "genlayer-agent-lab" / "gltest-direct"
    sdk = cache / "extracted" / GENVM_VERSION / "runner" / "module.py"
    sdk.parent.mkdir(parents=True)
    sdk.write_bytes(b"# verified SDK source")
    archive = cache / f"genvm-universal-{GENVM_VERSION}.tar.xz"
    archive.write_bytes(b"test archive")
    (fake_home / ".env").write_text("PRIVATE_KEY=do-not-copy")
    (cache / "unrelated-secret.txt").write_text("do-not-copy")
    manifest = {"runner\\module.py": hashlib.sha256(sdk.read_bytes()).hexdigest()}
    (cache / f"verified-sdk-{GENVM_VERSION}.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(container, "BUNDLE_SHA256", hashlib.sha256(archive.read_bytes()).hexdigest())
    monkeypatch.setattr(container.Path, "home", classmethod(lambda cls: fake_home))
    destination = tmp_path / "context"
    destination.mkdir()
    container._copy_verified_context(destination)
    names = {path.relative_to(destination).as_posix() for path in destination.rglob("*") if path.is_file()}
    assert not any("secret" in name or name.endswith(".env") for name in names)
    assert "package/genlayer_agent_lab/bindings.py" in names
    assert "package/genlayer_agent_lab/api.py" not in names
    copied_manifest = json.loads((destination / "sdk" / f"verified-sdk-{GENVM_VERSION}.json").read_text())
    assert copied_manifest == {"runner/module.py": manifest["runner\\module.py"]}
    dockerfile = (destination / "Dockerfile").read_text()
    assert "--require-hashes" in dockerfile and "@sha256:" in dockerfile
    # Restrictive host umasks must not leave imported SDK/package files readable
    # only by root inside the image, where the worker runs as uid10001.
    assert "COPY --chown=10001:10001 package/" in dockerfile
    assert "COPY --chown=10001:10001 sdk " in dockerfile


def test_output_limit_terminates_real_client_process():
    with pytest.raises(RuntimeError, match="output limit"):
        container._bounded_process(
            [sys.executable, "-c", "import sys; sys.stdout.write('x'*2000000)"],
            output_limit=1024, timeout=5,
        )


def test_timeout_terminates_real_client_process():
    with pytest.raises(RuntimeError, match="timed out"):
        container._bounded_process([sys.executable, "-c", "import time; time.sleep(10)"], timeout=.1)


def test_timeout_kills_owned_grandchild_without_pipe_deadlock(tmp_path):
    marker = tmp_path / "orphan-survived.txt"
    child = ("import time; from pathlib import Path; time.sleep(2); "
             f"Path({str(marker)!r}).write_text('survived')")
    parent = ("import subprocess,sys,time; "
              f"subprocess.Popen([sys.executable,'-c',{child!r}]); time.sleep(10)")
    start = time.monotonic()
    with pytest.raises(RuntimeError, match="timed out"):
        container._bounded_process([sys.executable, "-c", parent], timeout=.7)
    assert time.monotonic() - start < 3
    time.sleep(1.6)
    assert not marker.exists(), "Timed-out Docker CLI descendant survived process cleanup"


@pytest.fixture(scope="module")
def actual_docker():
    state = container.doctor()
    if not state["ready"]:
        pytest.skip("Actual Linux worker image unavailable: " + state.get("error", "not ready"))
    return state


@pytest.fixture
def actual_owned_containers(monkeypatch):
    """Track only this test's UUIDs, then verify absence independently of the worker."""
    original_command = container._command
    owned = []

    def record(endpoint, args, **kwargs):
        if args[0] == "create":
            name = args[args.index("--name") + 1]
            owner = args[args.index("--label") + 1].split("=", 1)[1]
            owned.append((endpoint, name, owner))
        return original_command(endpoint, args, **kwargs)

    monkeypatch.setattr(container, "_command", record)
    yield owned
    assert owned, "The live test never created a worker container"
    leftovers = []
    for endpoint, name, owner in owned:
        inspection = original_command(endpoint, ["inspect", name, "--format", "{{json .}}"])
        if inspection.returncode == 0:
            info = json.loads(inspection.stdout)
            leftovers.append(name)
            # If a regression left a runaway test running, remove only the
            # independently verified test-owned resource before reporting failure.
            assert container._owned(info, name, owner), "Refusing to remove unrelated container"
            removed = original_command(endpoint, ["rm", "--force", info["Id"]])
            assert removed.returncode == 0, "Could not remove leaked test-owned container"
        else:
            assert b"no such" in inspection.stderr.lower(), "Daemon failure cannot prove cleanup"
        by_label = original_command(endpoint, [
            "ps", "--all", "--quiet", "--filter", f"label={container.OWNER_LABEL}={owner}",
        ])
        assert by_label.returncode == 0 and not by_label.stdout.strip()
    assert not leftovers, f"Worker cleanup left test-owned containers: {leftovers}"


@pytest.mark.container
def test_actual_custom_contract_approve_deny_and_clean_runs(
    actual_docker, binding, actual_owned_containers,
):
    approved = container.evaluate_binding(binding, CONTEXT)
    denied = container.evaluate_binding(binding, {**CONTEXT, "fixture_verdict": "deny"})
    repeated = container.evaluate_binding(binding, CONTEXT)
    assert [approved["verdict"], denied["verdict"], repeated["verdict"]] == ["approve", "deny", "approve"]
    assert approved["provenance"]["consensus"]["llm_fixture_calls"] == 4
    assert approved["provenance"]["image_id"] == actual_docker["image_id"]
    assert len(actual_owned_containers) == 3
    assert len({name for _, name, _ in actual_owned_containers}) == 3


@pytest.mark.container
def test_actual_network_access_fails_closed(actual_docker, binding, actual_owned_containers):
    source = binding["source"].splitlines()[0] + '''
from genlayer import gl
import socket
class NetworkAttempt(gl.Contract):
    def __init__(self): pass
    @gl.public.write
    def evaluate(self, evidence: str) -> str:
        try:
            connection = socket.create_connection(("1.1.1.1", 443), timeout=1)
            connection.close()
        except OSError:
            return "deny"
        return "approve"
'''
    snapshot = _snapshot(binding["definition"], source)
    result = container.evaluate_binding(snapshot, CONTEXT)
    assert result["verdict"] == "deny"
    assert result["provenance"]["execution_success"] is True
    assert result["provenance"]["contract_executed"] is True
    # A startup failure must not count as blocked networking. The known source
    # must complete its socket attempt and return the mapped denial successfully.
    assert result["provenance"]["consensus"]["status"] == "FINALIZED"


@pytest.mark.container
def test_actual_runaway_custom_code_times_out_and_cleans_up(
    actual_docker, binding, actual_owned_containers, monkeypatch,
):
    source = binding["source"].splitlines()[0] + '''
from genlayer import gl
from pathlib import Path
class Runaway(gl.Contract):
    def __init__(self): pass
    @gl.public.write
    def evaluate(self, evidence: str) -> str:
        Path("/tmp/lab-loop-entered").write_text("entered")
        while True: pass
'''
    snapshot = _snapshot(binding["definition"], source)
    original_remove = container._remove_owned
    witnessed = []

    def witness_then_cleanup(endpoint, name, owner):
        try:
            info = container._inspect_container(endpoint, name)
            assert container._owned(info, name, owner)
            # The timed-out docker attach client has been terminated, but the
            # container remains alive until normal cleanup. Inspect our marker
            # as the same nonroot user, without any host mounts or network.
            witness = container._command(endpoint, [
                "exec", "--user", "10001:10001", name, "python", "-I", "-c",
                "from pathlib import Path; "
                "print('ENTERED' if Path('/tmp/lab-loop-entered').is_file() else 'NOT_ENTERED')",
            ], timeout=5)
            witnessed.append(witness.returncode == 0 and witness.stdout.strip() == b"ENTERED")
        finally:
            original_remove(endpoint, name, owner)

    monkeypatch.setattr(container, "_remove_owned", witness_then_cleanup)
    with pytest.raises(RuntimeError, match="timed out"):
        container.evaluate_binding(snapshot, CONTEXT, timeout=10)
    assert witnessed == [True], "The timeout occurred before the test reached its runaway loop"
