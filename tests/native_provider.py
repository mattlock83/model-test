"""Exercise the real Rust provider against a local, scripted HTTP endpoint."""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from testwalker.core import CoreClient, CoreDecisions


class JevClient(CoreDecisions):
    def __init__(self, *, http, api_key="unit-test-key", model="jev-latest", **kwargs):
        self.http = http
        self.owned_core = CoreClient()

        class Handler(BaseHTTPRequestHandler):
            def do_POST(handler):
                body = handler.rfile.read(int(handler.headers["Content-Length"]))
                response = http.post("http://mock.test/", headers=dict(handler.headers), content=body)
                handler.send_response(response.status_code)
                handler.send_header("Content-Type", "application/json")
                handler.end_headers()
                handler.wfile.write(response.content)

            def log_message(*args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        super().__init__(
            self.owned_core,
            api_key=api_key,
            model=model,
            endpoint=f"http://127.0.0.1:{self.server.server_port}/",
            **kwargs,
        )

    def close(self):
        self.owned_core.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.http.close()


def validate_answer(threshold, result, name, choices):
    with CoreClient() as core:
        client = CoreDecisions(core, api_key="unit-test-key", threshold=threshold)
        return client.answer(result, name, choices)
