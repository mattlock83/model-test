"""Local plain HTML demo host. The framework itself never imports app behavior."""

from contextlib import contextmanager
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

from .resources import asset


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


@contextmanager
def serve(port=0):
    root = asset("dist")
    server = ThreadingHTTPServer(("127.0.0.1", port), partial(QuietHandler, directory=str(root)))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
