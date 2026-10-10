"""The selected test inventory and its execution, separate from coverage counters."""

import json
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .errors import Defect, Inconclusive
from .properties import plan_cases


class TestPlan:
    def __init__(self, report, browser, client):
        self.report, self.browser, self.client = report, browser, client
        self.tests = report["tests"] = []
        self.active = None
        self.graph = {}
        self.properties = {}
        self.screenshot_count = 0
        report["planning"] = {
            "complete": False,
            "graph_count": "exact for successfully planned walks",
            "generated_count": "phases only; draws and shrinking are determined at runtime",
        }

    def add(self, identifier, kind, **details):
        test = {"id": identifier, "kind": kind, "status": "NOT_RUN", **details}
        self.tests.append(test)
        return test

    def add_walk(self, model, path, walk, seed):
        for position, element in enumerate(path):
            if position == 0 or element["kind"] == "edge":
                state = element if position == 0 else model.states[element["targetVertexId"]]
                spec = element["properties"]["business"]
                details = {
                    "name": f"Walk {walk}, step {position}: {element['name']} → {state['name']}",
                    "walk": walk,
                    "seed": seed,
                    "position": position,
                    "element": {
                        key: element[key]
                        for key in ("id", "name", "kind", "sourceVertexId", "targetVertexId")
                        if key in element
                    },
                    "expected_state": state["id"],
                    "description": state["properties"]["business"]["description"],
                    "rules": state["properties"]["business"]["rules"],
                    "global_rules": model.business["rules"],
                }
                if element["kind"] == "edge":
                    details["intent"] = spec["intent"]
                    if "data set" in spec:
                        details["input"] = model.example(element)
                test = self.add(f"graph/{walk}/{position}/{element['id']}", "graph", **details)
            # An edge and the following state belong to the same verified journey.
            self.graph[walk, position] = test

    def add_campaigns(self, model, campaigns, *, mode, cases, max_attempts, input_strategies=None):
        """Select input scope before execution, in stable model/phase order."""
        selected = {}
        remaining = max_attempts
        selection = {
            "policy": "model order",
            "input_limit": max_attempts,
            "available_phases": 0,
            "selected_phases": 0,
            "excluded_phases": 0,
            "available_examples": 0,
            "selected_examples": 0,
        }
        self.report["scope"]["property_selection"] = selection
        omitted = self.report["planning"]["unselected_properties"] = []
        for edge in campaigns:
            spec = edge["properties"]["business"]
            fields = model.data_sets[spec["data set"]]
            for phase in plan_cases(
                fields, mode=mode, cases=cases, policy=input_strategies, data_set=spec["data set"]
            ):
                requested = phase.get("max_examples", 1)
                selection["available_phases"] += 1
                selection["available_examples"] += requested
                if not remaining:
                    selection["excluded_phases"] += 1
                    omitted.append(
                        {
                            "journey": edge["id"],
                            "phase": phase,
                            "reason": "Outside the configured property input scope",
                        }
                    )
                    continue
                allowance = min(requested, remaining)
                remaining -= allowance
                if phase["kind"] == "generated":
                    phase = {**phase, "requested_max_examples": requested, "max_examples": allowance}
                selection["selected_phases"] += 1
                selection["selected_examples"] += allowance
                selected.setdefault(edge["id"], []).append(phase)
                test = self.add(
                    f"property/{edge['id']}/{phase['id']}",
                    "property",
                    name=f"{edge['name']}: {phase['source']} [{phase['id']}]",
                    journey=edge["id"],
                    data_set=spec["data set"],
                    phase=phase,
                    fields=fields,
                    intent=spec["intent"],
                    accepted_at=spec.get("accepted at", edge["targetVertexId"]),
                    rejected_at=spec["rejected at"],
                    expected_states={
                        state: model.states[state]["properties"]["business"]
                        for state in {spec.get("accepted at", edge["targetVertexId"]), spec["rejected at"]}
                    },
                    global_rules=model.business["rules"],
                )
                self.properties[edge["id"], phase["id"]] = test
        self.report["scope"]["selected_campaigns"] = len(selected)
        return selected

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

    @contextmanager
    def phase(self, edge, phase):
        self.begin(self.properties[edge["id"], phase["id"]])
        try:
            yield
        except BaseException as error:
            self.fail(error)
            raise
        else:
            self.finish("PASS")

    def finalize(self):
        reason = self.report.get("error") or "Run ended before this planned test was reached"
        for test in self.tests:
            if test["status"] == "NOT_RUN":
                test.update(status="SKIPPED", reason=f"Not reached: {reason}")
        self.report["test_summary"] = {
            status: sum(test["status"] == status for test in self.tests)
            for status in ("PASS", "FAIL", "INCONCLUSIVE", "SKIPPED")
        }
