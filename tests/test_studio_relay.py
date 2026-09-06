import http.client
import http.server
import threading
from contextlib import closing

import pytest

from genlayer_agent_lab.runtime import studio_relay


@pytest.fixture
def relay(monkeypatch):
    received = []

    class Upstream(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            received.append({"path": self.path, "headers": dict(self.headers),
                             "body": self.rfile.read(int(self.headers["Content-Length"]))})
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"jsonrpc":"2.0","result":1,"id":1}')

        def log_message(self, *_args):
            pass

    upstream = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    monkeypatch.setattr(studio_relay, "UPSTREAM_HOST", "127.0.0.1")
    monkeypatch.setattr(studio_relay, "UPSTREAM_PORT", upstream.server_port)
    server = studio_relay.Server(("127.0.0.1", 0))
    threads = [threading.Thread(target=app.serve_forever, daemon=True) for app in (upstream, server)]
    for thread in threads:
        thread.start()
    try:
        yield server.server_port, received
    finally:
        for app in (upstream, server):
            app.shutdown()
            app.server_close()
        for thread in threads:
            thread.join(2)


def test_relay_forwards_only_fixed_rpc_and_strips_supplied_headers(relay):
    port, received = relay
    with closing(http.client.HTTPConnection("127.0.0.1", port, timeout=2)) as client:
        client.request("POST", "/", b"{}", headers={"Host": "evil.invalid", "Authorization": "secret"})
        response = client.getresponse()
        assert response.status == 200
        assert b'"result":1' in response.read()
    assert received[0]["path"] == "/api"
    assert received[0]["headers"]["Host"].startswith("127.0.0.1:")
    assert "Authorization" not in received[0]["headers"]


@pytest.mark.parametrize("path", ["http://evil.invalid/", "/other", "/?host=evil.invalid", "//evil.invalid/"])
def test_relay_cannot_be_used_as_forward_proxy(relay, path):
    port, received = relay
    with closing(http.client.HTTPConnection("127.0.0.1", port, timeout=2)) as client:
        client.request("POST", path, b"{}")
        assert client.getresponse().status == 404
    assert not received


def test_relay_rejects_oversized_body_before_reading_or_forwarding(relay):
    port, received = relay
    with closing(http.client.HTTPConnection("127.0.0.1", port, timeout=2)) as client:
        client.request("POST", "/", b"", headers={"Content-Length": str(studio_relay.MAX_BYTES + 1)})
        assert client.getresponse().status == 413
    assert not received
