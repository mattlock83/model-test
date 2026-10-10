"""Exercise the packaged commands against an independent native worker."""

import json
import os
import subprocess
import sys
from xml.etree import ElementTree as ET

import pytest

from testwalker import cli
from testwalker.core import core_executable
from testwalker.resources import asset


def invoke(*args):
    environment = {
        name: value
        for name, value in os.environ.items()
        if name not in {"TYPESAFE_API_KEY", "OPENAI_API_KEY", "JEV_API_KEY"}
    }
    result = subprocess.run(
        [sys.executable, "-m", "testwalker.cli", *map(str, args)],
        capture_output=True,
        text=True,
        timeout=60,
        env=environment,
    )
    return result


def read_report(output):
    (path,) = output.glob("*/report.json")
    return path.parent, json.loads(path.read_text())


def test_self_test_covers_graph_hegel_and_replays_without_provider(tmp_path):
    output = tmp_path / "full"
    result = invoke("self-test", "--output", output)
    assert result.returncode == 0, result.stdout + result.stderr
    directory, report = read_report(output)
    assert report["adapter"] == "json-rpc"
    assert report["status"] == "PASS"
    assert report["usage"]["calls"] == 0
    for category in ("edges", "states"):
        coverage = report["coverage"][category]
        assert coverage["verified"] == coverage["total"] > 20
    assert report["coverage"]["properties"] == {"completed": 3, "total": 3}
    assert report["input_attempts"] >= 30
    assert report["test_summary"]["SKIPPED"] == 0
    assert report["test_summary"]["FAIL"] == report["test_summary"]["INCONCLUSIVE"] == 0
    assert (directory / "report.html").is_file()
    assert not ET.parse(directory / "junit.xml").findall(".//failure")
    methods = {entry.get("request", {}).get("method") for entry in report["trace"]}
    assert {"run.start", "run.results", "core.shutdown", "decision.configure"} <= methods
    graph_case = next(t for t in report["tests"] if t.get("element", {}).get("id") == "read_success")
    property_case = next(c for c in report["cases"] if c["input"] == {"Cases": 201})
    for label, case in (("graph", graph_case), ("property", property_case)):
        replay_output = tmp_path / label
        result = invoke(
            "rpc",
            "--model",
            directory / "model.json",
            "--target",
            core_executable(),
            "--replay",
            directory / case["replay_file"],
            "--output",
            replay_output,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        _, replay = read_report(replay_output)
        assert replay["replay"] and replay["status"] == "PASS"
        assert len(replay["tests"]) == 1


def test_wrong_contract_is_a_failure_with_expected_actual_and_skips(tmp_path):
    model = json.loads(asset("models/rpc-selftest.json").read_text())
    ready = next(v for v in model["models"][0]["vertices"] if v["id"] == "ready")
    rule = next(iter(ready["properties"]["rpc"]["rules"]))
    ready["properties"]["rpc"]["rules"][rule] = [
        {"path": "/response/result/protocol_version", "equals": "deliberately-wrong"}
    ]
    path = tmp_path / "wrong.json"
    path.write_text(json.dumps(model))
    output = tmp_path / "run"
    result = invoke("rpc", "--model", path, "--target", core_executable(), "--output", output)
    assert result.returncode == 1, result.stdout + result.stderr
    directory, report = read_report(output)
    assert report["status"] == "FAIL" and report["test_summary"]["SKIPPED"] > 0
    failure = ET.parse(directory / "junit.xml").find(".//failure")
    assert failure is not None
    details = json.loads(failure.text)
    assert '"expected": "deliberately-wrong"' in json.dumps(details)
    assert '"actual": "1"' in json.dumps(details)
    assert report["trace"][0]["request"]["method"] == "core.info"


def test_plan_does_not_start_target_or_load_hooks(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli.Hooks, "load", lambda *_: pytest.fail("Planning must not load hooks"))
    assert (
        cli.main(
            [
                "self-test",
                "--target",
                str(tmp_path / "absent-executable"),
                "--hooks",
                str(tmp_path / "absent-hooks.py"),
                "--plan",
            ]
        )
        == 0
    )
    assert "property phases" in capsys.readouterr().out


@pytest.mark.parametrize("timeout", ["0", "-1", "nan", "inf"])
def test_bad_timeout_rejected_before_hooks_or_launch(timeout, monkeypatch, capsys):
    monkeypatch.setattr(cli.Hooks, "load", lambda *_: pytest.fail("Invalid config must not load hooks"))
    assert cli.main(["self-test", "--timeout", timeout]) == 2
    assert "positive finite" in capsys.readouterr().err


def test_rpc_cli_keeps_input_and_exploration_controls():
    args = cli.parser().parse_args(
        [
            "self-test",
            "--cases",
            "4",
            "--input-mode",
            "generated",
            "--max-input-attempts",
            "20",
            "--generator",
            "random",
            "--edge-coverage",
            "90",
            "--max-steps",
            "2000",
        ]
    )
    assert (args.cases, args.input_mode, args.max_input_attempts) == (4, "generated", 20)
    assert (args.generator, args.edge_coverage, args.max_steps) == ("random", 90, 2000)
