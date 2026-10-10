"""Browser operations, web evidence preparation and Python lifecycle bindings.

The Rust core schedules calls. This adapter never chooses the next graph edge.
"""

from copy import deepcopy
from pathlib import Path

from .errors import Defect, Inconclusive
from .hooks import HookContext, Hooks


class WebAdapter:
    def __init__(self, model, browser, oracle, *, hooks=None, collector=None, strategy_provider=None):
        self.model, self.browser, self.oracle = model, browser, oracle
        self.hooks, self.collector = hooks or Hooks(), collector
        self.url = ""
        self.closed = False
        self.after_run = False
        self.strategy_provider = strategy_provider

    def __call__(self, method, params):
        if method == "run.event":
            return self.collector.event(params["type"], params["data"]) if self.collector else {}
        if method == "target.strategy":
            return self.strategy_provider.build(params["context"], params["default"])
        if method == "target.initialize":
            if params.get("protocol_version") != "1":
                raise Inconclusive("Unsupported target adapter protocol version")
            self.url = params["config"]["url"]
            return {
                "capabilities": {
                    "screenshots": hasattr(self.browser, "screenshot"),
                    "isolated_checkpoints": False,
                    "evaluator": "web",
                }
            }
        if method == "target.reset":
            self.browser.reset(self.url)
            return {}
        if method == "target.execute":
            fields = params["fields"]
            self.browser.pursue(
                params["edge"]["properties"]["business"]["intent"],
                fields=fields,
                data=params["input"] if fields else None,
                destination=params["destination"] if not fields else None,
                states=params["states"],
            )
            return {"executed": True}
        if method == "target.observe":
            return self.browser.observe()
        if method == "target.evaluate":
            try:
                return self.oracle.check(
                    params["observation"], params["expected"], params["input"], params["violations"] or None
                )
            except Inconclusive as error:
                if isinstance(error.result, dict) and "observed" in error.result:
                    return {**error.result, "reason": str(error)}
                raise
        if method == "target.audit":
            return (
                self.oracle.audit_globals(params["records"], params["rules"])
                if hasattr(self.oracle, "audit_globals")
                else []
            )
        if method == "target.hook":
            if params["event"] == "after_run":
                self.after_run = True
                if self.collector and isinstance(params["context"].get("result"), dict):
                    # Hooks receive the current core status/coverage as well as
                    # attachments captured by the report collector.
                    self.collector.report.update(params["context"]["result"])
            context = self.context(params["context"])
            self.hooks.fire(params["event"], context)
            return {}
        if method == "target.close":
            self.close(keep_open=params.get("keep_open", False))
            return {}
        raise Inconclusive(f"The web adapter does not implement {method}")

    def context(self, values):
        context = HookContext(
            model=self.model,
            url=self.url,
            browser=self.browser,
            target=self.browser,
            phase=values.get("phase", "graph"),
            walk=values.get("walk"),
            element=deepcopy(values.get("element")),
            data=deepcopy(values.get("data") or {}),
            invalid=deepcopy(values.get("invalid") or []),
            result=deepcopy(values.get("result")),
            report=self.collector.report if self.collector else None,
            scratch=self.hooks.scratch,
        )
        if error := values.get("error"):
            kind = Defect if error["status"] == "FAIL" else Inconclusive
            context.error = kind(error["message"], result=error.get("result"))
        return context

    def close(self, *, keep_open=False):
        if self.closed:
            return
        if keep_open and getattr(self.browser, "browser", self.browser) is not None:
            if self.collector:
                self.collector.report["browser_left_open"] = True
                self.collector.log("The last test tab has been left open for inspection.")
        else:
            self.browser.close()
        self.closed = True

    def screenshot(self, path):
        self.browser.screenshot(Path(path))
