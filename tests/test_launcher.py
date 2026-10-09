import json
import os
import shutil
import subprocess
import sys

import pytest

from model_test.resources import ROOT


@pytest.fixture
def launcher(tmp_path):
    checkout = tmp_path / "checkout with spaces"
    checkout.mkdir()
    shutil.copy2(ROOT / "run-demo.sh", checkout / "run-demo.sh")
    binary = checkout / ".tools/graphwalker-rs/target/release/graphwalker"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\nexit 0\n")
    binary.chmod(0o755)
    commands = tmp_path / "commands"
    commands.mkdir()
    log = tmp_path / "commands.jsonl"

    def command(name, source):
        compile(source, name, "exec")
        path = commands / name
        path.write_text(f"#!{sys.executable}\n" + source)
        path.chmod(0o755)

    command("uname", 'print("Darwin")\n')
    command(
        "open",
        r"""
import json, os, sys
with open(os.environ["LAUNCHER_LOG"], "a") as log:
    log.write(json.dumps({"command": "open", "args": sys.argv[1:]}) + "\n")
sys.exit(int(os.environ.get("FAKE_OPEN_STATUS", "0")))
""",
    )
    command(
        "uv",
        r"""
import json, os, subprocess, sys
args = sys.argv[1:]
with open(os.environ["LAUNCHER_LOG"], "a") as log:
    log.write(json.dumps({"command": "uv", "args": args, "cwd": os.getcwd(),
                         "name": os.environ.get("BU_NAME"),
                         "url": os.environ.get("BU_CDP_URL")}) + "\n")
if args[:3] == ["run", "--no-sync", "python"]:
    source = sys.stdin.read()
    if 'deadline = time.monotonic()' in source:
        if os.environ.get("FAKE_NOT_READY"):
            source = ("import time\n"
                      "times = iter([0, 21])\n"
                      "time.monotonic = lambda: next(times)\n") + source
        else:
            source = ("import io, urllib.request\n"
                      "class Ready:\n"
                      "    def open(self, *args, **kwargs):\n"
                      "        return io.StringIO('{\"webSocketDebuggerUrl\": \"ws://local-test\"}')\n"
                      "urllib.request.build_opener = lambda *args: Ready()\n") + source
    sys.exit(subprocess.run([sys.executable, "-"], input=source, text=True).returncode)
if args[:4] == ["run", "--no-sync", "model-test", "demo"]:
    sys.exit(int(os.environ.get("FAKE_DEMO_STATUS", "0")))
""",
    )
    env = {
        **os.environ,
        "PATH": str(commands) + os.pathsep + os.environ.get("PATH", ""),
        "LAUNCHER_LOG": str(log),
        "TYPESAFE_API_KEY": "unit-test-only",
        "MODEL_TEST_CHROME_PORT": "9333",
    }
    env.pop("GRAPHWALKER_BIN", None)

    def run(*args, **overrides):
        result = subprocess.run(
            [str(checkout / "run-demo.sh"), *args],
            cwd=tmp_path,
            env={**env, **overrides},
            capture_output=True,
            text=True,
            timeout=10,
        )
        events = [json.loads(line) for line in log.read_text().splitlines()]
        return result, events

    return run, checkout


def test_launcher_handles_spaces_passes_options_and_preserves_demo_exit(launcher):
    run, checkout = launcher
    result, events = run("--site", "feedback", "--cases", "1", FAKE_DEMO_STATUS="1")
    assert result.returncode == 1, result.stderr
    chrome = next(event for event in events if event["command"] == "open")
    assert f"--user-data-dir={checkout}/.tools/chrome-profile-9333" in chrome["args"]
    demo = events[-1]
    assert demo["args"] == [
        "run",
        "--no-sync",
        "model-test",
        "demo",
        "--headed",
        "--site",
        "feedback",
        "--cases",
        "1",
    ]
    assert demo["cwd"] == str(checkout)
    assert demo["name"] == "model-test-9333" and demo["url"] == "http://127.0.0.1:9333"


def test_missing_key_stops_before_opening_chrome(launcher):
    run, _ = launcher
    result, events = run(TYPESAFE_API_KEY="")
    assert result.returncode != 0 and "TYPESAFE_API_KEY" in result.stderr
    assert not any(event["command"] == "open" for event in events)
    assert not any("demo" in event["args"] for event in events)


@pytest.mark.parametrize("failure", [{"FAKE_OPEN_STATUS": "1"}, {"FAKE_NOT_READY": "1"}])
def test_browser_setup_failure_never_starts_paid_testing(launcher, failure):
    run, _ = launcher
    result, events = run(**failure)
    assert result.returncode != 0
    assert not any("demo" in event["args"] for event in events)


def test_launcher_builds_graphwalker_only_when_missing(launcher):
    run, checkout = launcher
    (checkout / ".tools/graphwalker-rs/target/release/graphwalker").unlink()
    result, events = run()
    assert result.returncode == 0, result.stderr
    assert any(event["args"] == ["run", "--no-sync", "model-test", "setup"] for event in events)
