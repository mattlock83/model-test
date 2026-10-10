"""Bidirectional client for the native test framework; no browser imports."""

import json
import os
import queue
import shutil
import subprocess
import threading
import traceback
from collections import deque
from pathlib import Path

from .errors import Defect, Inconclusive, UncertainDecision

MAX_MESSAGE = 64 * 1024 * 1024


def core_executable(explicit=None):
    """Prefer an explicit property, then the wheel's binary, then a source build."""
    if explicit:
        candidates = [Path(explicit)]
    else:
        package = Path(__file__).resolve().parent
        name = "testwalker-core.exe" if os.name == "nt" else "testwalker-core"
        candidates = [package / "bin" / name, package.parent / "target" / "release" / name]
        candidates.append(package.parent / "target" / "debug" / name)
        if found := shutil.which(name):
            candidates.append(Path(found))
    for path in candidates:
        if path.is_file() and os.access(path, os.X_OK):
            return path.resolve()
    raise ValueError(
        "Build the Rust core with cargo build --release --locked, or set TESTWALKER_CORE_BIN "
        "in the properties file. Installed wheels include their platform's core executable."
    )


def fault(error):
    """Keep a definite defect distinct from transport or provider uncertainty."""
    data = error.get("data") or {}
    message = data.get("message") or error.get("message") or "JSON-RPC request failed"
    result = data.get("result")
    if data.get("status") == "FAIL":
        return Defect(message, result=result, case=(result or {}).get("case"))
    kind = (
        UncertainDecision if isinstance(result, dict) and result.get("uncertain_decision") else Inconclusive
    )
    return kind(message, result=result)


def rpc_error(error):
    return {
        "code": -32001,
        "message": str(error) or type(error).__name__,
        "data": {
            "status": "FAIL" if isinstance(error, (Defect, AssertionError)) else "INCONCLUSIVE",
            "message": str(error) or type(error).__name__,
            "result": getattr(error, "result", None),
        },
    }


class CoreClient:
    """One worker and one execution thread; nested requests service adapter callbacks."""

    def __init__(self, executable=None, *, handler=None, timeout=120):
        self.handler, self.timeout = handler, timeout
        self.sequence = 0
        self.interrupted = False
        self.exit_error = None
        self.debug = False
        self.adapter_tracebacks = []
        self.messages = queue.Queue()
        self.diagnostics = deque(maxlen=50)
        self.process = subprocess.Popen(
            [str(core_executable(executable))],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()
        self.stderr_reader = threading.Thread(target=self._read_stderr, daemon=True)
        self.stderr_reader.start()
        try:
            self.info = self.request("core.info")
            if self.info.get("protocol_version") != "1":
                raise Inconclusive("The Rust core and Python adapter use incompatible protocol versions")
        except BaseException:
            self.close()
            raise

    def _read(self):
        try:
            while line := self.process.stdout.readline(MAX_MESSAGE + 1):
                if len(line.encode("utf-8")) > MAX_MESSAGE or not line.endswith("\n"):
                    raise Inconclusive("Rust core message exceeds the JSON-RPC size limit")
                self.messages.put(json.loads(line))
        except Exception as error:
            self.messages.put(error)
        finally:
            self.messages.put(None)

    def _read_stderr(self):
        # Drain continuously: a native diagnostic cannot block the RPC worker.
        for line in self.process.stderr:
            self.diagnostics.append(line.rstrip()[-4000:])

    def _send(self, message):
        try:
            encoded = json.dumps(message, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError):
            raise Inconclusive("Adapter data must be finite, serializable JSON") from None
        if len(encoded.encode("utf-8")) > MAX_MESSAGE:
            raise Inconclusive("Adapter message exceeds the JSON-RPC size limit")
        try:
            self.process.stdin.write(encoded + "\n")
            self.process.stdin.flush()
        except (BrokenPipeError, OSError, ValueError):
            raise Inconclusive("The Rust core connection closed") from None

    def request(self, method, **params):
        self.sequence += 1
        identifier = f"python/{self.sequence}"
        self._send({"jsonrpc": "2.0", "id": identifier, "method": method, "params": params})
        while True:
            try:
                message = self.messages.get(timeout=self.timeout)
            except queue.Empty:
                raise Inconclusive(f"The Rust core did not respond within {self.timeout} seconds") from None
            if message is None:
                raise Inconclusive("The Rust core exited before completing the request")
            if isinstance(message, Exception):
                if isinstance(message, Inconclusive):
                    raise message
                raise Inconclusive(
                    f"Invalid Rust core protocol output: {type(message).__name__}"
                ) from message
            if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
                raise Inconclusive("Invalid Rust core JSON-RPC message")
            if "method" in message:
                self._dispatch(message)
                continue
            if message.get("id") != identifier:
                raise Inconclusive("Mismatched Rust core response")
            if "error" in message:
                raise fault(message["error"])
            if "result" not in message:
                raise Inconclusive("Missing Rust core result")
            return message["result"]

    def _dispatch(self, message):
        response = {"jsonrpc": "2.0", "id": message.get("id")}
        try:
            if self.handler is None:
                raise Inconclusive(f"No adapter handles {message['method']}")
            response["result"] = self.handler(message["method"], message.get("params", {}))
        except BaseException as error:
            if self.debug:
                self.adapter_tracebacks.append(traceback.format_exc())
            if isinstance(error, KeyboardInterrupt):
                self.interrupted = True
                error = Inconclusive("Interrupted by the user")
            elif not isinstance(error, Exception):
                self.exit_error = error
                error = Inconclusive(str(error) or type(error).__name__)
            response["error"] = rpc_error(error)
        if "id" in message:
            self._send(response)

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            stream.close()
        self.reader.join(timeout=1)
        self.stderr_reader.join(timeout=1)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class CoreDecisions:
    """Jev Ultrafast's client interface backed by Rust's provider and policy."""

    def __init__(self, core, *, api_key, model="jev-latest", max_calls=1000, threshold=0.85, endpoint=None):
        self.core, self.model = core, model
        self.threshold, self.max_calls = threshold, max_calls
        self.decisions = []
        self.core.request(
            "decision.configure",
            provider="jev",
            api_key=api_key,
            model=model,
            max_calls=max_calls,
            threshold=threshold,
            **({"endpoint": endpoint} if endpoint else {}),
        )

    @property
    def stats(self):
        return self.core.request("decision.stats")

    def ask(self, evidence, questions):
        audit = {"request": {"model": self.model, "state": evidence, "questions": questions}}
        self.decisions.append(audit)
        try:
            result = self.core.request("decision.ask", evidence=evidence, questions=questions)
        except Inconclusive as error:
            audit["error"] = str(error)
            raise
        audit["response"] = result
        return result

    def answer(self, result, name, choices):
        return self.core.request(
            "decision.answer", result=result, name=name, choices=choices, threshold=self.threshold
        )

    def post(self, url, key, body, *, cache=True):
        return self.core.request("decision.post", url=url, key=key, body=body, cache=cache)

    def audit(self):
        """Fetch the authoritative transcript in bounded JSON-RPC pages."""
        entries, offset = [], 0
        while True:
            page = self.core.request("decision.audit", offset=offset, limit=128)
            entries.extend(page["entries"])
            if page["next_offset"] is None:
                return entries
            offset = page["next_offset"]

    def close(self):
        # The caller owns the worker's lifetime, including report finalization.
        pass
