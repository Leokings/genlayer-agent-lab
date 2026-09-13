"""Public health probes against isolated local sockets, never a real Lab."""

import json
import socketserver
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

from genlayer_agent_lab.runtime import loopback_health as health
from genlayer_agent_lab.runtime.container import CommandResult


@contextmanager
def endpoint(body=b'{"status":"ok"}', *, status=200, encoding=None, trickle=None):
    requests = []

    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.settimeout(1)
            try:
                request = b""
                while b"\r\n\r\n" not in request and len(request) < 8192:
                    chunk = self.request.recv(4096)
                    if not chunk:
                        return
                    request += chunk
                requests.append(request)
                head = f"HTTP/1.1 {status} Response\r\nConnection: close\r\n".encode()
                if encoding:
                    head += b"Content-Encoding: " + encoding.encode() + b"\r\n"
                if trickle == "headers":
                    self.request.sendall(head + b"X-Trickle: ")
                elif trickle == "body":
                    self.request.sendall(head + b"Content-Length: 1024\r\n\r\n")
                else:
                    self.request.sendall(head + f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
                    return
                # Every byte beats the socket idle timeout; only the parent
                # command deadline can terminate this response promptly.
                for _ in range(50):
                    self.request.sendall(b" ")
                    time.sleep(.15)
            except OSError:
                pass

    class Server(socketserver.ThreadingTCPServer):
        daemon_threads = True
        allow_reuse_address = True

    with Server(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server.server_address[1], requests
        finally:
            server.shutdown()
            thread.join(2)


def test_public_setup_probe_has_no_credentials_and_ignores_proxy_environment(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("LAB_TOKEN", "must-not-send-this-token")
    nonce = "a" * 64
    with endpoint() as (port, requests):
        assert health.fetch_health(port, nonce=nonce) == {"status": "ok"}
    assert len(requests) == 1
    assert requests[0].startswith(f"GET /health?setup_nonce={nonce} HTTP/1.1\r\n".encode())
    assert b"Accept-Encoding: identity" in requests[0]
    assert b"authorization" not in requests[0].lower()
    assert b"must-not-send-this-token" not in requests[0]


@pytest.mark.parametrize("trickle", ["headers", "body"])
def test_trickling_peer_is_bounded_across_headers_and_body(trickle):
    with endpoint(trickle=trickle) as (port, requests):
        started = time.monotonic()
        assert health.fetch_health(port) is None
        assert time.monotonic() - started < 5
    assert len(requests) == 1
    assert requests[0].startswith(b"GET /service/health HTTP/1.1\r\n")


@pytest.mark.parametrize("options", [
    {"body": json.dumps({"padding": "x" * 5000}).encode()},
    {"body": b"[]"}, {"status": 302}, {"encoding": "gzip"}, {"body": b"not-json"},
])
def test_public_probe_rejects_unbounded_or_unrecognized_responses(options):
    with endpoint(**options) as (port, requests):
        assert health.fetch_health(port) is None
    assert len(requests) == 1


def test_pythonw_probe_selects_adjacent_python_and_keeps_fixed_budgets(monkeypatch):
    calls = []
    monkeypatch.setattr(health.sys, "executable", str(Path("trusted") / "pythonw.exe"))
    monkeypatch.setattr(health, "_bounded_process", lambda args, **kwargs:
                        calls.append((args, kwargs)) or CommandResult(0, b'{}', b''))
    assert health.fetch_health(8765) == {}
    args, kwargs = calls[0]
    assert Path(args[0]) == Path("trusted") / "python.exe"
    assert args[1] == "-I" and args[-2:] == ["8765", "/service/health"]
    assert kwargs == {"timeout": 2, "output_limit": 4096}


@pytest.mark.parametrize("port,nonce", [(0, None), (True, None), (65536, None),
                                        (8765, "../other"), (8765, "a" * 65)])
def test_invalid_probe_targets_never_launch(port, nonce, monkeypatch):
    monkeypatch.setattr(health, "_bounded_process", lambda *args, **kwargs:
                        pytest.fail("Invalid target launched a process"))
    assert health.fetch_health(port, nonce=nonce) is None
