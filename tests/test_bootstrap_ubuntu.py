"""Run the real Bash bootstrap against local command shims; never change the host."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts/bootstrap-ubuntu.sh"
SHIM = r'''
import json, os, pathlib, subprocess, sys
sys.stdout.reconfigure(newline="\n")
state = pathlib.Path(os.environ["BOOTSTRAP_TEST_STATE"])
name, args = sys.argv[1], sys.argv[2:]
with (state / "calls.jsonl").open("a") as log:
    log.write(json.dumps([name, args]) + "\n")
def has(key): return (state / key).exists()
def mark(key): (state / key).touch()
def output(text): print(text); return 0
def dispatch(name, args, elevated=False):
    if name == "timeout":
        while args and args[0].startswith("--"): args = args[1:]
        args = args[1:]
        return dispatch(args[0], args[1:], elevated)
    if name == "env":
        while args and (args[0] == "-u" or "=" in args[0]):
            args = args[2:] if args[0] == "-u" else args[1:]
        return dispatch(args[0], args[1:], elevated)
    if name == "uname":
        return output(os.environ.get("FAKE_ARCH", "x86_64") if "-m" in args else "Linux")
    if name == "mktemp":
        target = state / "downloads"
        target.mkdir(exist_ok=True)
        return output(target.as_posix())
    if name == "id":
        if "-u" in args: return output(os.environ.get("FAKE_UID", "1000"))
        if "-un" in args: return output("linuxuser")
        return output("linuxuser docker" if has("active-group") or len(args)>1 and has("configured-group") else "linuxuser")
    if name == "cat":
        if not args: sys.stdout.write(sys.stdin.read()); return 0
        if args[0].replace("\\", "/").endswith("/etc/os-release"):
            return output('ID=' + os.environ.get("FAKE_OS", "ubuntu") + '\nVERSION_ID="' + os.environ.get("FAKE_VERSION", "24.04") + '"')
        return output(pathlib.Path(args[0]).read_text())
    if name == "dpkg-query":
        return output("install ok installed") if has("pkg-" + args[-1]) else 1
    if name in ("git", "tmux", "uv"):
        return output(name + " version 1") if has("tool-" + name) else 127
    if name == "curl":
        if not has("tool-curl"): return 127
        if "--version" in args: return output("curl version 1")
        mark("download")
        if has("fail-download"): return 28
        target = pathlib.Path(args[args.index("--output")+1])
        if args[-1] == "https://astral.sh/uv/install.sh":
            target.write_text('#!/bin/sh\ntouch "$BOOTSTRAP_TEST_STATE/tool-uv"\n')
        else: target.write_text("official mock signing key")
        return 0
    if name == "docker":
        if not has("tool-docker"): return 127
        if args == ["--version"]: return output("Docker version 29")
        if args[:2] == ["context", "inspect"]: return output(os.environ.get("FAKE_ENDPOINT", "unix:///var/run/docker.sock"))
        if args[:1] == ["--host"]: args = args[2:]
        if args[:1] == ["info"]:
            return output(os.environ.get("FAKE_ENGINE", "linux amd64")) if has("docker-running") and (elevated or has("docker-access")) else 1
        if args[:2] == ["compose", "version"]:
            return output(os.environ.get("FAKE_COMPOSE", "v2.39.1")) if has("compose") else 1
        raise AssertionError((name,args))
    if name == "awk":
        files = [p.as_posix() for p in state.iterdir() if p.suffix in (".list", ".sources")]
        if not files: return 1
        # Exercise the actual source-record parser, with only its input paths
        # redirected to fixture files. No host apt configuration is touched.
        # A file avoids Windows native-process quoting changing awk escapes.
        program = state / "source-parser.awk"
        program.write_text(args[2], newline="\n")
        return subprocess.run([os.environ["BOOTSTRAP_TEST_BASH"], "-c",
                               'exec /usr/bin/awk "$@"', "awk", *args[:2],
                               "-f", program.as_posix(), *files]).returncode
    if name == "snap": return 0 if has("docker-snap") else 1
    if name == "getent": return 0 if has("docker-group") else 2
    if name == "sudo":
        mark("sudo-called")
        if has("no-sudo"): return 1
        args = [a for a in args if a != "-n"]
        if args == ["-v"]: return 0
        return dispatch(args[0], args[1:], True)
    if name == "apt-get":
        assert elevated, "apt must go through sudo"
        mark("apt-called")
        if has("fail-apt"): return 100
        if "install" in args:
            assert "--no-remove" in args
            for package in args[args.index("install")+1:]:
                if package.startswith("-"): continue
                mark("pkg-"+package)
                if package in ("git","curl","tmux"): mark("tool-"+package)
                if package == "docker-ce-cli": mark("tool-docker")
                if package == "docker-ce": mark("docker-service"); mark("docker-group")
                if package == "docker-compose-plugin": mark("compose")
        return 0
    if name == "systemctl":
        if args[:1] == ["cat"]: return 0 if has("docker-service") else 1
        assert elevated and args == ["start", "docker.service"]
        mark("service-started")
        if has("fail-service"): return 1
        mark("docker-running")
        return 0
    if name in ("usermod", "groupadd"):
        assert elevated
        mark("configured-group" if name == "usermod" else "docker-group")
        return 0
    if name == "install":
        assert elevated and args == ["-d","-m","0755","/etc/apt/keyrings"]
        mark("repository-directory")
        return 0
    if name == "sh":
        if elevated:
            assert args[0] == "-c" and "set -C" in args[1]
            assert args[-1].endswith(("genlayer-agent-lab-docker.asc","genlayer-agent-lab-docker.sources"))
            target = state / ("repository.sources" if args[-1].endswith(".sources") else "repository.asc")
            content = pathlib.Path(args[-2]).read_bytes()
            if target.exists(): return 0 if target.read_bytes() == content else 3
            if target.suffix == ".sources" and has("fail-source-write"): return 5
            target.write_bytes(content)
            mark("repository-write")
            return 0
        # The downloaded installer is replaced by a shim; no real installer runs.
        assert args[0].endswith("uv-install.sh")
        mark("uv-installed-as-user")
        if not has("broken-uv-installer"): mark("tool-uv")
        return 0
    raise AssertionError("Unapproved shim command: " + repr((name,args,elevated)))
sys.exit(dispatch(name,args))
'''


@pytest.fixture(scope="module")
def bash():
    candidates = ([r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files\Git\usr\bin\bash.exe"]
                  if os.name == "nt" else [shutil.which("bash")])
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            check = subprocess.run([candidate, "--noprofile", "--norc", "-c", "command -v timeout"],
                                   capture_output=True, timeout=10)
            if check.returncode == 0:
                return candidate
    pytest.skip("A suitable local Bash with coreutils is unavailable (Git Bash works on Windows)")


@pytest.fixture
def host(tmp_path, bash):
    state = tmp_path / "state"
    state.mkdir()
    binary = tmp_path / "bin"
    binary.mkdir()
    shim = tmp_path / "shim.py"
    shim.write_text(SHIM, encoding="utf-8")
    launcher = '#!/usr/bin/env bash\nexec "$BOOTSTRAP_TEST_PYTHON" "$BOOTSTRAP_TEST_SHIM" "${0##*/}" "$@"\n'
    for command in ("uname", "id", "cat", "timeout", "dpkg-query", "git", "curl", "tmux", "uv",
                    "docker", "sudo", "apt-get", "systemctl", "getent", "awk", "sh", "mktemp", "snap"):
        path = binary / command
        path.write_text(launcher, encoding="utf-8", newline="\n")
        path.chmod(0o755)
    env = os.environ.copy()
    for key in ("DOCKER_HOST", "DOCKER_CONTEXT", "BASH_ENV", "ENV"):
        env.pop(key, None)
    env.update(BOOTSTRAP_TEST_STATE=state.as_posix(), BOOTSTRAP_TEST_SHIM=shim.as_posix(),
               BOOTSTRAP_TEST_PYTHON=Path(sys.executable).as_posix(),
               BOOTSTRAP_TEST_BASH=bash,
               HOME=(tmp_path / "home").as_posix(), MSYS_NO_PATHCONV="1")
    # MSYS translates PATH supplied by Windows; set its POSIX form inside Bash.
    command = 'shim_path=$1; if command -v cygpath >/dev/null; then shim_path=$(cygpath -u "$1"); fi; export PATH="$shim_path:/usr/bin:/bin"; shift; exec bash "$@"'

    def run(*arguments, **overrides):
        result = subprocess.run([bash, "--noprofile", "--norc", "-c", command, "bootstrap-test",
                                 binary.as_posix(), SCRIPT.as_posix(), *arguments],
                                env={**env, **overrides}, capture_output=True, text=True, timeout=30)
        return result

    def seed(*names):
        for name in names:
            (state / name).touch()

    def calls():
        path = state / "calls.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    return run, seed, calls, state


BASE = ("tool-git", "tool-curl", "tool-tmux", "tool-uv", "pkg-ca-certificates")
ENGINE = ("tool-docker", "docker-running", "docker-access", "compose", "docker-service")


@pytest.mark.parametrize("arguments", [(), ("--check",)])
def test_default_and_explicit_checks_make_no_mutations(host, arguments):
    run, _, calls, state = host
    result = run(*arguments)
    assert result.returncode == 2, result.stderr
    assert "No changes made" in result.stdout
    assert not any(name in {"sudo", "apt-get", "sh"} for name, _ in calls())
    assert not (state / "download").exists()


def test_healthy_existing_docker_is_reused_without_sudo_or_repository_changes(host):
    run, seed, calls, _ = host
    seed(*BASE, *ENGINE, "pkg-docker.io")
    result = run("--install")
    assert result.returncode == 0, result.stderr
    assert "Prerequisites ready" in result.stdout
    assert not any(name == "sudo" for name, _ in calls())
    assert not any(args[:2] == ["context", "use"] for name, args in calls() if name == "docker")


def test_missing_sudo_returns_specific_interactive_handoff(host):
    run, seed, _, state = host
    seed("no-sudo")
    result = run("--install")
    assert result.returncode == 4, result.stderr
    assert "--install --interactive" in result.stderr
    assert not (state / "apt-called").exists() and not (state / "download").exists()


def test_fresh_install_stops_for_new_login_and_preserves_agent_boundary(host):
    run, _, calls, state = host
    result = run("--install")
    assert result.returncode == 6, result.stderr
    assert (state / "configured-group").exists()
    assert (state / "uv-installed-as-user").exists()
    assert "user service manager" in result.stdout and "actual agent session" in result.stdout
    assert "Prerequisites ready" not in result.stdout
    sudo_calls = [args for name, args in calls() if name == "sudo"]
    assert sudo_calls and all(args[0] == "-n" for args in sudo_calls)
    assert not any("uv-install.sh" in " ".join(args) for args in sudo_calls)


def test_existing_stopped_engine_starts_without_package_replacement(host):
    run, seed, _, state = host
    seed(*BASE, "tool-docker", "compose", "docker-service", "docker-access", "pkg-docker.io")
    result = run("--install")
    assert result.returncode == 0, result.stderr
    assert (state / "service-started").exists()
    assert not (state / "apt-called").exists() and not (state / "repository-write").exists()
    assert not (state / "configured-group").exists()


@pytest.mark.parametrize("override", [{"FAKE_OS": "debian"}, {"FAKE_VERSION": "20.04"},
                                      {"FAKE_ARCH": "aarch64"}, {"FAKE_UID": "0"}])
def test_unsupported_hosts_refuse_before_mutation(host, override):
    run, _, calls, _ = host
    result = run("--install", **override)
    assert result.returncode == 3, result.stderr
    assert not any(name == "sudo" for name, _ in calls())


def test_remote_context_is_not_replaced_or_contacted(host):
    run, seed, calls, _ = host
    seed(*BASE, *ENGINE)
    result = run("--install", FAKE_ENDPOINT="ssh://someone@example.test")
    assert result.returncode == 3
    assert not any(name == "sudo" or name == "docker" and "info" in args for name, args in calls())


def test_conflicting_packages_are_reported_without_removal(host):
    run, seed, calls, _ = host
    seed("pkg-podman-docker")
    result = run("--install")
    assert result.returncode == 3 and "podman-docker" in result.stderr
    assert not any(name == "sudo" for name, _ in calls())


@pytest.mark.parametrize("failure", ["fail-apt", "fail-download", "broken-uv-installer", "fail-service"])
def test_failed_prerequisite_install_never_reports_ready(host, failure):
    run, seed, _, _ = host
    seed(failure)
    result = run("--install")
    assert result.returncode == 5, result.stderr
    assert "Prerequisites ready" not in result.stdout


def test_interactive_mode_is_explicit_and_only_valid_with_install(host):
    run, seed, calls, _ = host
    assert run("--interactive").returncode == 64
    seed(*BASE, "tool-docker", "compose", "docker-service", "docker-access")
    result = run("--install", "--interactive")
    assert result.returncode == 0, result.stderr
    assert all("-n" not in args for name, args in calls() if name == "sudo")


def test_ubuntu_2204_and_fresh_login_can_pass_check(host):
    run, seed, _, _ = host
    seed(*BASE, *ENGINE, "configured-group", "active-group")
    assert run("--check", FAKE_VERSION="22.04").returncode == 0


def test_official_cli_only_install_resumes_missing_engine(host):
    run, seed, _, state = host
    seed(*BASE, "tool-docker", "compose", "pkg-docker-ce-cli", "pkg-docker-compose-plugin")
    result = run("--install")
    assert result.returncode == 6, result.stderr
    assert (state / "pkg-docker-ce").exists()
    assert (state / "pkg-containerd.io").exists()


def test_interrupted_repository_write_reuses_identical_key(host):
    run, seed, _, state = host
    seed(*BASE, "fail-source-write")
    result = run("--install")
    assert result.returncode == 5, result.stderr
    assert (state / "repository.asc").exists()
    (state / "fail-source-write").unlink()
    result = run("--install")
    assert result.returncode == 6, result.stderr
    assert (state / "repository.sources").exists()


def test_unknown_repository_key_is_preserved(host):
    run, seed, _, state = host
    seed(*BASE)
    (state / "repository.asc").write_text("unrelated key")
    result = run("--install")
    assert result.returncode == 3, result.stderr
    assert (state / "repository.asc").read_text() == "unrelated key"
    assert not (state / "pkg-docker-ce").exists()


@pytest.mark.parametrize("filename, content", [
    ("docker.list", "# deb https://download.docker.com/linux/ubuntu noble stable\n"),
    ("docker.list.save", "deb https://download.docker.com/linux/ubuntu noble stable\n"),
    ("docker.sources", "Types: deb\nURIs: https://download.docker.com/linux/ubuntu\nSuites: noble\nComponents: stable\nEnabled: no\n"),
])
def test_inactive_repository_does_not_hide_missing_active_source(host, filename, content):
    run, seed, _, state = host
    seed(*BASE)
    (state / filename).write_text(content)
    result = run("--install")
    assert result.returncode == 6, result.stderr
    assert (state / "repository.sources").exists()


@pytest.mark.parametrize("filename, content", [
    ("docker.list", "deb [arch=amd64 signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu noble stable\n"),
    ("docker.sources", "Types: deb\nURIs: https://download.docker.com/linux/ubuntu\nSuites: noble\nComponents: stable\n"),
])
def test_active_official_repository_is_reused(host, filename, content):
    run, seed, _, state = host
    seed(*BASE)
    (state / filename).write_text(content)
    result = run("--install")
    assert result.returncode == 6, result.stderr
    assert not (state / "repository-write").exists()


def test_new_cli_rechecks_saved_remote_context_before_service_or_group_changes(host):
    run, seed, _, state = host
    seed(*BASE)
    result = run("--install", FAKE_ENDPOINT="ssh://other-server")
    assert result.returncode == 3, result.stderr
    assert not (state / "service-started").exists()
    assert not (state / "configured-group").exists()
