"""The selected test inventory and its execution, separate from coverage counters."""

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from .errors import Defect, Inconclusive


class TestPlan:
    def __init__(self, report, browser, client):
        self.report, self.browser, self.client = report, browser, client
        self.tests = report["tests"] = []
        self.active = None
        self.screenshot_count = 0
        report["planning"] = {
            "complete": False,
            "graph_count": "exact for successfully planned walks",
            "generated_count": "phases only; draws and shrinking are determined at runtime",
        }

    def save(self, directory):
        # Called before any test starts: this file remains an immutable inventory.
        inventory = {
            "model_hash": self.report["model_hash"],
            "exploration": self.report["exploration"],
            "limits": self.report["limits"],
            "scope": self.report["scope"],
            "planning": self.report["planning"],
            "walks": self.report.get("walks", []),
            "tests": self.tests,
        }
        (directory / "plan.json").write_text(json.dumps(inventory, indent=2, ensure_ascii=False) + "\n")

    def begin(self, test):
        if self.active is test:
            return
        if self.active is not None:
            raise RuntimeError("The previous planned test did not finish")
        self.active = test
        self.started = time.monotonic()
        test.update(
            status="RUNNING",
            step_indices=[],
            attempt_indices=[],
            trace_range=[len(self.browser.trace)],
            decision_range=[len(getattr(self.client, "decisions", []))],
        )

    def finish(self, status, reason=None):
        if self.active is None:
            return
        test = self.active
        test.update(status=status, duration=time.monotonic() - self.started)
        if reason:
            test["reason"] = reason
        if test["kind"] == "graph" and status != "SKIPPED":
            self.capture(test)
        elif test["kind"] == "property":
            attempts = [self.report["cases"][index] for index in test["attempt_indices"]]
            executed = [entry for entry in attempts if entry.get("attempted", True)]
            if executed:
                evidence = next(
                    (entry for entry in reversed(executed) if entry["status"] == status), executed[-1]
                )
                for key in ("screenshot", "screenshot_at", "screenshot_error"):
                    if key in evidence:
                        test[key] = evidence[key]
        test["trace_range"].append(len(self.browser.trace))
        test["decision_range"].append(len(getattr(self.client, "decisions", [])))
        self.active = None

    def capture(self, record):
        if not self.report["limits"].get("screenshots", True):
            return
        self.screenshot_count += 1
        relative = f"screenshots/{self.screenshot_count:06d}.png"
        destination = Path(self.report["directory"]) / relative
        try:
            destination.parent.mkdir(exist_ok=True)
            self.browser.screenshot(destination)
            record.update(screenshot=relative, screenshot_at=datetime.now(timezone.utc).isoformat())
        except Exception as error:
            # Evidence capture must never hide the original verdict or interrupt shrinking.
            record["screenshot_error"] = f"{type(error).__name__}: {error}"

    def fail(self, error):
        status = "FAIL" if isinstance(error, Defect) else "INCONCLUSIVE"
        if self.active and isinstance(error, Inconclusive) and (error.result or {}).get("attempted") is False:
            attempts = [self.report["cases"][index] for index in self.active["attempt_indices"]]
            if not any(entry.get("attempted", True) for entry in attempts):
                status = "SKIPPED"
        self.finish(status, str(error) or type(error).__name__)

    def finalize(self):
        reason = self.report.get("error") or "Run ended before this planned test was reached"
        for test in self.tests:
            if test["status"] == "NOT_RUN":
                test.update(status="SKIPPED", reason=f"Not reached: {reason}")
        self.report["test_summary"] = {
            status: sum(test["status"] == status for test in self.tests)
            for status in ("PASS", "FAIL", "INCONCLUSIVE", "SKIPPED")
        }
