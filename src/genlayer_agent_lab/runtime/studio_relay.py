"""Fixed-destination HTTP relay for Docker engines that cannot publish internal networks.

Executed as trusted inline code in its own read-only container. It has no keys,
mounts or contract execution. It can only forward the two listed routes to the
internal Studio RPC, never a client-supplied host, URL or proxy destination.
"""

import http.client
import http.server
import threading

UPSTREAM_HOST = "jsonrpc"
UPSTREAM_PORT = 4000
MAX_BYTES = 4 * 1024 * 1024
SOCKET_TIMEOUT = 15


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "AgentLabStudioRelay"

    def setup(self):
        self.request.settimeout(SOCKET_TIMEOUT)
        super().setup()

    def log_message(self, *_args):
        # Requests can carry signed transactions and responses can contain keys.
        pass

    def reply(self, status, body):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        self.forward()

    def do_GET(self):
        self.forward()

    def forward(self):
        lengths = self.headers.get_all("Content-Length", [])
        if (self.headers.get("Transfer-Encoding") is not None or len(lengths) > 1
                or (lengths and not lengths[0].isdigit())):
            self.reply(400, b'{"error":"invalid_length"}')
            return
        size = int(lengths[0]) if lengths else 0
        if size > MAX_BYTES or (self.command == "POST" and not size):
            self.reply(413, b'{"error":"body_limit"}')
            return
        upstream = None
        try:
            payload = self.rfile.read(size) if size else None
            if payload is not None and len(payload) != size:
                self.reply(400, b'{"error":"incomplete_body"}')
                return
            # Consume a valid bounded body before closing a rejected route.
            # Closing with unread TCP data can reset the connection on Windows
            # before the caller receives our response. No upstream is opened.
            if (self.command, self.path) not in {("POST", "/"), ("GET", "/health")}:
                self.reply(404, b'{"error":"route_unavailable"}')
                return
            upstream = http.client.HTTPConnection(UPSTREAM_HOST, UPSTREAM_PORT, timeout=SOCKET_TIMEOUT)
            # Deliberately omit all client-supplied headers, including Host,
            # Authorization, Forwarded and proxy-control headers.
            # Stable Studio serves JSON-RPC at /api; our advertised relay
            # endpoint is its root URL, as used by the bounded SDK provider.
            destination = "/api" if self.command == "POST" else "/health"
            upstream.request(self.command, destination, body=payload,
                             headers={"Content-Type": "application/json"})
            response = upstream.getresponse()
            body = response.read(MAX_BYTES + 1)
            if len(body) > MAX_BYTES or 300 <= response.status < 400:
                self.reply(502, b'{"error":"upstream_response_rejected"}')
                return
            self.reply(response.status, body)
        except (OSError, http.client.HTTPException, ValueError):
            try:
                self.reply(502, b'{"error":"upstream_unavailable"}')
            except OSError:
                pass
        finally:
            if upstream is not None:
                upstream.close()


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 8

    def __init__(self, address):
        self.slots = threading.BoundedSemaphore(8)
        super().__init__(address, Handler)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def handle_error(self, request, client_address):
        # Do not print tracebacks or request data from this boundary.
        pass


if __name__ == "__main__":
    with Server(("0.0.0.0", 4002)) as server:
        server.serve_forever()
