"""CLI-owned isolated Chrome startup and explicit external debugging connections."""

import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.request import ProxyHandler, build_opener

from .config import debugging_url


def chrome_executable(config):
    if config.chrome_executable:
        return config.chrome_executable
    candidates = []
    if sys.platform == "darwin":
        candidates = [Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")]
    elif sys.platform == "win32":
        candidates = [
            Path(os.environ.get(root, "")) / "Google/Chrome/Application/chrome.exe"
            for root in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")
            if os.environ.get(root)
        ]
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        if located := shutil.which(name):
            candidates.append(Path(located))
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    raise ValueError("Chrome was not found; set CHROME_EXECUTABLE or CDP_URL in the properties file")


def ready(url):
    try:
        with build_opener(ProxyHandler({})).open(url.rstrip("/") + "/json/version", timeout=1) as response:
            return bool(json.load(response).get("webSocketDebuggerUrl"))
    except (OSError, ValueError, AttributeError):
        return False


def port_occupied(port):
    with socket.socket() as connection:
        connection.settimeout(0.2)
        return connection.connect_ex(("127.0.0.1", port)) == 0


def stop(process):
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


@contextmanager
def connect(config, *, headed=False, cdp_url=None, keep_browser_open=False):
    explicit = cdp_url or config.cdp_url
    url = debugging_url(explicit) if explicit else f"http://127.0.0.1:{config.chrome_port}"
    process = None
    started = False
    connection = {
        "BU_CDP_URL": url,
        "BU_NAME": "testwalker-" + hashlib.sha256(url.encode()).hexdigest()[:12],
        # Harness gives these precedence over an HTTP endpoint. Empty values
        # also prevent its dotenv loader from restoring an unrelated connection.
        "BU_CDP_WS": "",
        "BU_BROWSER_ID": "",
    }
    previous = {name: os.environ.get(name) for name in connection}
    try:
        if not ready(url):
            if explicit:
                raise ValueError("The configured Chrome debugging endpoint is unavailable")
            if port_occupied(config.chrome_port):
                raise ValueError(
                    "Chrome's debugging port is occupied; change CHROME_DEBUG_PORT or supply CDP_URL"
                )
            profile = (
                config.chrome_profile or Path.home() / ".cache/testwalker" / f"chrome-{config.chrome_port}"
            )
            profile.mkdir(parents=True, exist_ok=True)
            command = [
                str(chrome_executable(config)),
                f"--user-data-dir={profile}",
                "--remote-debugging-address=127.0.0.1",
                f"--remote-debugging-port={config.chrome_port}",
                "--no-first-run",
                "--no-default-browser-check",
            ]
            if not headed:
                command.append("--headless=new")
            process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            deadline = time.monotonic() + 20
            while not ready(url):
                if process.poll() is not None or time.monotonic() >= deadline:
                    raise ValueError(
                        "Chrome did not expose a debugging endpoint; check the isolated profile settings"
                    )
                time.sleep(0.1)
            started = True
        os.environ.update(connection)
        yield url
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        # Retain a started browser only when explicitly requested for debugging.
        # Never terminate an external connection or a browser reused from a prior run.
        if process is not None and (not keep_browser_open or not started):
            stop(process)
