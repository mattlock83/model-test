"""Non-web adapters share native execution, evidence and report lifecycle."""

import builtins
import json
from pathlib import Path
from types import SimpleNamespace

from testwalker.engine import run
from testwalker.errors import Inconclusive
from testwalker.hooks import HookContext, Hooks
from testwalker.model import violations


class StateTarget:
    """An in-memory service fixture with no browser or decision API objects."""

    name = "service-fixture"

    def __init__(self, model, *, broken=False):
        self.model, self.broken = model, broken
        self.trace = []
        self.closed, self.after_run = False, False
        self.hooks = Hooks()
        self.url = ""
        self.close_count = 0

    def __call__(self, method, params):
        if method == "run.event":
            return self.collector.event(params["type"], params["data"])
        if method == "target.strategy":
            return self.strategy_provider.build(params["context"], params["default"])
        if method == "target.initialize":
            self.url = params["config"]["url"]
            return {"capabilities": {"evaluator": "deterministic"}}
        if method == "target.reset":
            self.state = "form"
            return {}
        if method == "target.execute":
            if self.broken:
                raise Inconclusive("Service unavailable")
            fields, data = params["fields"], params["input"]
            self.state = ("rejected" if violations(fields, data) else "accepted") if fields else "form"
            self.trace.append({"method": params["edge"]["name"], "params": data})
            return {"executed": True}
        if method == "target.observe":
            return {"state": self.state}
        if method == "target.evaluate":
            return {
                "observed": params["observation"]["state"],
                "checks": [{"rule": "Invalid capacity is rejected.", "scope": "global", "status": "met"}],
            }
        if method == "target.hook":
            if params["event"] == "after_run":
                self.after_run = True
            self.hooks.fire(params["event"], self.context(params["context"]))
            return {}
        if method == "target.close":
            self.close(keep_open=params.get("keep_open", False))
            return {}
        raise AssertionError(method)

    def context(self, values):
        return HookContext(
            model=self.model, url=self.url, phase=values.get("phase", "graph"),
            element=values.get("element"), result=values.get("result"),
            scratch=self.hooks.scratch, report=self.collector.report,
        )

    def close(self, *, keep_open=False):
        if not self.closed:
            self.close_count += 1
            self.closed = True


def test_native_target_reports_without_web_or_decisions(model, tmp_path, monkeypatch):
    original_import = builtins.__import__

    def without_web(name, *args, **kwargs):
        assert name not in {"jev", "web_adapter"}, "Non-web execution attempted to load a web adapter"
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_web)
    target = StateTarget(model)
    hooks_seen = []
    hooks = Hooks(SimpleNamespace(after_run=lambda context: hooks_seen.append(context.result["status"])))
    report = run(
        model, "stdio://service-fixture", target_adapter=target, hooks=hooks,
        output=tmp_path, log=lambda *_: None,
    )
    assert report["status"] == "PASS"
    assert report["adapter"] == "service-fixture"
    assert report["url"] == "stdio://service-fixture"
    assert report["coverage"]["edges"]["verified"] == 4
    assert report["coverage"]["properties"]["completed"] == 1
    assert report["input_attempts"] > 0
    assert report["usage"]["calls"] == 0 and report["decisions"] == []
    assert report["trace"] == target.trace and target.trace
    assert report["limits"]["screenshots"] is False
    assert not any("screenshot_error" in test for test in report["tests"])
    assert target.closed and target.close_count == 1
    assert hooks_seen == ["PASS"]
    assert target.hooks is hooks
    directory = Path(report["directory"])
    assert {"report.html", "report.json", "junit.xml", "plan.json"} <= {p.name for p in directory.iterdir()}
    saved = json.loads((directory / "report.json").read_text())
    assert saved["status"] == "PASS"
    assert any(test.get("replay_file") for test in report["tests"])


def test_non_web_failure_preserves_evidence_and_cleanup(model, tmp_path):
    target = StateTarget(model, broken=True)
    report = run(
        model, "stdio://service-fixture", target_adapter=target, output=tmp_path,
        log=lambda *_: None,
    )
    assert report["status"] == "INCONCLUSIVE" and "Service unavailable" in report["error"]
    assert report["test_summary"]["SKIPPED"] > 0
    assert target.closed and target.after_run
    assert report["usage"]["calls"] == 0
    assert (Path(report["directory"]) / "junit.xml").exists()
