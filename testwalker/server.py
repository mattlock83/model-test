"""Local plain HTML demo host. The framework itself never imports app behavior."""

import json
from contextlib import contextmanager
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.parse import urlparse

from .resources import asset
from .trailhead_demo import TrailheadStore


class QuietHandler(SimpleHTTPRequestHandler):
    def json_response(self, value, status=200):
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if urlparse(self.path).path == "/api/trailhead/state":
            self.json_response(self.server.trailhead.snapshot())
        else:
            super().do_GET()

    def do_POST(self):
        path = urlparse(self.path).path
        if path not in {
            "/api/trailhead/reset",
            "/api/trailhead/bookings",
            "/api/trailhead/cancellations",
            "/api/trailhead/profile",
        }:
            self.json_response({"error": "Unknown demo endpoint"}, 404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 16384:
                raise ValueError("Supply a JSON request of at most 16 KiB")
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError("Supply a JSON object")
            store = self.server.trailhead
            if path.endswith("/reset"):
                value = store.reset(body.get("defect", ""))
            elif path.endswith("/bookings"):
                value = store.confirm(
                    body.get("data"), body.get("token"), body.get("pickup", "City visitor centre")
                )
            elif path.endswith("/cancellations"):
                value = store.cancel(body.get("booking_id"), body.get("data"))
            else:
                value = store.save_profile(body.get("data"))
            self.json_response(value)
        except (ValueError, TypeError) as error:
            self.json_response({"error": str(error)}, 400)

    def log_message(self, *_args):
        pass


@contextmanager
def serve(port=0):
    root = asset("dist")
    server = ThreadingHTTPServer(("127.0.0.1", port), partial(QuietHandler, directory=str(root)))
    server.trailhead = TrailheadStore()
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
