"""Generic, synchronous, bidirectional JSON-RPC 2.0 over subprocess stdio.

The client does not know any application methods. API error responses are returned
unchanged; only broken transport or protocol contracts raise exceptions. A handler
may make nested requests on the same client while servicing a target callback.
"""

import copy
import json
import math
import os
import queue
import subprocess
import threading
import time
from collections import deque
from collections.abc import Mapping, Sequence

MAX_MESSAGE = 64 * 1024 * 1024
_INFER = object()
_EOF = object()


class RpcTransportError(RuntimeError):
    """The subprocess connection or JSON-RPC envelope is invalid."""


class RpcTimeoutError(RpcTransportError):
    """A request did not finish before its deadline."""


def _id_key(identifier):
    if identifier is None or isinstance(identifier, str):
        return type(identifier), identifier
    if isinstance(identifier, (int, float)) and not isinstance(identifier, bool):
        if isinstance(identifier, float) and not math.isfinite(identifier):
            raise ValueError("JSON-RPC IDs must be finite")
        return type(identifier), identifier
    raise ValueError("JSON-RPC IDs must be strings, numbers, or null")


def _reject_constant(value):
    raise ValueError(f"Non-finite JSON number: {value}")


class RpcTransport:
    """One subprocess, with nested requests on a single calling thread.

    ``handler(method, params)`` returns the result of an incoming request. Incoming
    notifications are recorded in ``notifications`` and ``transcript``. ``env``
    overrides the inherited environment. Always use the context manager or close
    the client explicitly; a transport/protocol failure also closes it immediately.
    """

    def __init__(
        self, argv, *, handler=None, timeout=30, cwd=None, env=None,
        max_message=MAX_MESSAGE, stderr_limit=64 * 1024,
    ):
        if isinstance(argv, (str, bytes)) or not isinstance(argv, Sequence) or not argv:
            raise ValueError("argv must be a nonempty sequence of executable arguments")
        self.argv = [os.fspath(part) for part in argv]
        self.timeout = self._timeout(timeout)
        if isinstance(max_message, bool) or not isinstance(max_message, int) or max_message < 1:
            raise ValueError("max_message must be a positive integer")
        if isinstance(stderr_limit, bool) or not isinstance(stderr_limit, int) or stderr_limit < 0:
            raise ValueError("stderr_limit must be a nonnegative integer")
        self.handler, self.max_message, self.stderr_limit = handler, max_message, stderr_limit
        self.transcript, self.notifications = [], []
        self.sequence = 0
        self.closed = False
        self._messages = queue.Queue()
        self._writes = queue.Queue()
        self._deadlines = []
        self._active, self._responses = set(), {}
        self._stderr = deque()
        self._stderr_bytes = 0
        self._lock = threading.Lock()
        self._owner = None
        try:
            self.process = subprocess.Popen(
                self.argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                cwd=cwd, env=None if env is None else {**os.environ, **env}, bufsize=0,
            )
        except (OSError, ValueError) as error:
            raise RpcTransportError(f"Could not start JSON-RPC target: {error}") from error
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._stderr_reader = threading.Thread(target=self._read_stderr, daemon=True)
        self._writer = threading.Thread(target=self._write, daemon=True)
        self._reader.start()
        self._stderr_reader.start()
        self._writer.start()

    @staticmethod
    def _timeout(value):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("timeout must be a positive finite number")
        if not math.isfinite(value) or value <= 0:
            raise ValueError("timeout must be a positive finite number")
        return value

    @property
    def stderr(self):
        """The most recent stderr bytes, bounded by ``stderr_limit``."""
        with self._lock:
            return b"".join(self._stderr).decode("utf-8", errors="replace")

    def _record(self, direction, raw, message=_INFER):
        entry = {"direction": direction, "raw": raw}
        if message is not _INFER:
            entry["message"] = copy.deepcopy(message)
        with self._lock:
            self.transcript.append(entry)

    def _read(self):
        try:
            while line := self.process.stdout.readline(self.max_message + 2):
                raw = line.rstrip(b"\r\n").decode("utf-8", errors="replace")
                if len(line.removesuffix(b"\n").removesuffix(b"\r")) > self.max_message:
                    self._record("received", raw)
                    raise RpcTransportError("Target message exceeds the JSON-RPC size limit")
                if not line.endswith(b"\n"):
                    self._record("received", raw)
                    raise RpcTransportError("Target message is not newline terminated")
                try:
                    message = json.loads(line.decode("utf-8"), parse_constant=_reject_constant)
                except (UnicodeDecodeError, ValueError) as error:
                    self._record("received", raw)
                    raise RpcTransportError(f"Target returned invalid JSON: {error}") from error
                self._record("received", raw, message)
                self._messages.put(message)
        except Exception as error:
            self._messages.put(error)
        finally:
            self._messages.put(_EOF)

    def _read_stderr(self):
        try:
            while chunk := self.process.stderr.read(4096):
                with self._lock:
                    self._stderr.append(chunk)
                    self._stderr_bytes += len(chunk)
                    while self._stderr_bytes > self.stderr_limit:
                        excess = self._stderr_bytes - self.stderr_limit
                        first = self._stderr.popleft()
                        if len(first) > excess:
                            self._stderr.appendleft(first[excess:])
                            self._stderr_bytes -= excess
                        else:
                            self._stderr_bytes -= len(first)
        except (OSError, ValueError):
            pass  # close() may close the stream after terminating the worker.

    def _write(self):
        while (item := self._writes.get()) is not _EOF:
            payload, done, errors = item
            try:
                remaining = memoryview(payload)
                while remaining:
                    written = self.process.stdin.write(remaining)
                    if not written:
                        raise BrokenPipeError("Target stopped accepting input")
                    remaining = remaining[written:]
                self.process.stdin.flush()
            except (BrokenPipeError, OSError, ValueError) as error:
                errors.append(error)
            finally:
                done.set()

    def _send(self, raw, message=_INFER):
        if self.closed:
            raise RpcTransportError("The JSON-RPC target connection is closed")
        encoded = raw.encode("utf-8")
        if len(encoded) > self.max_message:
            raise ValueError("Outgoing message exceeds the JSON-RPC size limit")
        done, errors = threading.Event(), []
        self._record("sent", raw, message)
        self._writes.put((encoded + b"\n", done, errors))
        remaining = max(0, self._deadlines[-1] - time.monotonic())
        if not done.wait(timeout=remaining):
            raise RpcTimeoutError("JSON-RPC target did not accept the request before its deadline")
        if errors:
            raise RpcTransportError("The JSON-RPC target connection closed") from errors[0]

    def request(self, method, params=None, *, timeout=None):
        """Send a normal request and return its complete result/error envelope."""
        if not isinstance(method, str):
            raise ValueError("method must be a string")
        if params is not None and not isinstance(params, (dict, list)):
            raise ValueError("params must be an object or array; use exchange() to test invalid params")
        self.sequence += 1
        message = {"jsonrpc": "2.0", "id": f"testwalker/{self.sequence}", "method": method}
        if params is not None:
            message["params"] = params
        return self.exchange(message, timeout=timeout)

    def exchange(self, message, *, expected_id=_INFER, timeout=None):
        """Send any JSON value, including deliberately invalid request envelopes.

        For invalid or absent request IDs, explicitly provide the ID expected in
        the rejection response (usually ``None``).
        """
        if expected_id is _INFER:
            if not isinstance(message, Mapping) or "id" not in message:
                raise ValueError("expected_id is required when the request has no ID")
            expected_id = message["id"]
        try:
            raw = json.dumps(message, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as error:
            raise ValueError("Outgoing data must be finite, serializable JSON") from error
        return self._exchange(raw, json.loads(raw), expected_id, timeout)

    def exchange_raw(self, text, *, expected_id, timeout=None):
        """Send one raw line, including malformed JSON; expected_id is explicit."""
        if not isinstance(text, str) or "\n" in text or "\r" in text:
            raise ValueError("Raw requests must be strings containing exactly one line without its newline")
        try:
            message = json.loads(text, parse_constant=_reject_constant)
        except ValueError:
            message = _INFER
        return self._exchange(text, message, expected_id, timeout)

    def _exchange(self, raw, message, identifier, timeout):
        key = _id_key(identifier)
        duration = self.timeout if timeout is None else self._timeout(timeout)
        deadline = time.monotonic() + duration
        thread_id = threading.get_ident()
        if self._owner is not None and self._owner != thread_id:
            raise ValueError("Concurrent requests are unsupported; nested requests must use the same thread")
        if key in self._active:
            raise ValueError("A request with this ID is already active")
        timeout_message = f"JSON-RPC target did not respond within {duration} seconds"
        if self._deadlines:
            if self._deadlines[-1] < deadline:
                timeout_message = "JSON-RPC target did not respond before the outer request deadline"
            deadline = min(deadline, self._deadlines[-1])
        self._owner = thread_id
        self._active.add(key)
        self._deadlines.append(deadline)
        try:
            self._send(raw, message)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RpcTimeoutError(timeout_message)
                if key in self._responses:
                    return self._responses.pop(key)
                try:
                    response = self._messages.get(timeout=remaining)
                except queue.Empty:
                    raise RpcTimeoutError(timeout_message) from None
                if response is _EOF:
                    raise RpcTransportError("JSON-RPC target exited before completing the request")
                if isinstance(response, Exception):
                    raise response
                self._consume(response)
        except BaseException:
            self.close()
            raise
        finally:
            self._active.discard(key)
            self._deadlines.pop()
            if not self._active:
                self._owner = None

    def _consume(self, message):
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            raise RpcTransportError("Invalid JSON-RPC 2.0 response envelope")
        if "method" in message:
            if "result" in message or "error" in message or not isinstance(message["method"], str):
                raise RpcTransportError("Invalid JSON-RPC callback envelope")
            if "params" in message and not isinstance(message["params"], (dict, list)):
                raise RpcTransportError("Invalid JSON-RPC callback parameters")
            if "id" not in message:
                self.notifications.append(message)
                return
            try:
                _id_key(message["id"])
            except ValueError as error:
                raise RpcTransportError("Invalid JSON-RPC callback ID") from error
            self._dispatch(message)
            return
        if "id" not in message or ("result" in message) == ("error" in message):
            raise RpcTransportError("JSON-RPC response requires id and exactly one of result or error")
        try:
            key = _id_key(message["id"])
        except ValueError as error:
            raise RpcTransportError("Invalid JSON-RPC response ID") from error
        if key not in self._active or key in self._responses:
            raise RpcTransportError(f"Mismatched or duplicate JSON-RPC response ID: {message['id']!r}")
        if "error" in message:
            error = message["error"]
            if (
                not isinstance(error, dict)
                or type(error.get("code")) is not int
                or not isinstance(error.get("message"), str)
            ):
                raise RpcTransportError("Invalid JSON-RPC error object")
        self._responses[key] = message

    def _dispatch(self, message):
        response = {"jsonrpc": "2.0", "id": message["id"]}
        if self.handler is None:
            response["error"] = {"code": -32601, "message": f"No handler for {message['method']}"}
        else:
            try:
                response["result"] = self.handler(message["method"], message.get("params", {}))
                raw = json.dumps(response, ensure_ascii=False, allow_nan=False)
            except RpcTransportError:
                raise  # A failed nested request has already closed this connection.
            except Exception as error:
                response.pop("result", None)
                response["error"] = {"code": -32000, "message": str(error) or type(error).__name__}
            else:
                self._send(raw, json.loads(raw))
                return
        self._send(json.dumps(response, ensure_ascii=False), response)

    def close(self):
        """Terminate the owned worker, reap it and close all pipes; safe repeatedly."""
        if self.closed:
            return
        self.closed = True
        if self.process.poll() is None:
            try:
                self.process.terminate()
            except ProcessLookupError:
                pass
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)
        self._writes.put(_EOF)
        self._writer.join(timeout=1)
        self._reader.join(timeout=1)
        self._stderr_reader.join(timeout=1)
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            stream.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
