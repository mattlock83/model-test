"""Turn synchronous core events into the existing screenshots, HTML and JUnit evidence."""

import hashlib
import json
from pathlib import Path

from .planning import TestPlan


class Collector:
    def __init__(self, report, browser, client, log):
        self.report, self.log = report, log
        self.plan = TestPlan(report, browser, client)
        self.saved = False
        self.by_id = {}

    def event(self, kind, data):
        if kind == "planned":
            self.report.update(data)
            self.plan.tests = self.report["tests"]
            self.by_id = {test["id"]: test for test in self.plan.tests}
            self._replays()
            self.plan.save(Path(self.report["directory"]))
            self.saved = True
            self.log(f"Planned {len(self.plan.tests)} tests. Inventory: {self.report['directory']}/plan.json")
        elif kind == "test.begin":
            self.plan.begin(self.by_id[data["id"]])
        elif kind == "test.end":
            test = self.plan.active
            self.plan.finish(data["status"], data.get("reason"))
            return test or {}
        elif kind == "step":
            self.plan.active["step_indices"].append(len(self.report["steps"]))
            self.report["steps"].append(data)
        elif kind == "step.result":
            self.report["steps"][data["index"]].update(data["entry"])
            self.log(f"{data['entry']['status']} · {data['entry']['name']}")
        elif kind == "case":
            if data.get("attempted", True):
                self.plan.capture(data)
            if self.plan.active:
                data["test_id"] = self.plan.active["id"]
                self.plan.active["attempt_indices"].append(len(self.report["cases"]))
            self.report["cases"].append(data)
            if recipe := data.get("replay_recipe"):
                data["replay_file"] = self._save_replay(recipe)
            self.log(f"{data['status']} · {data['source']} · {json.dumps(data['input'], ensure_ascii=False)}")
            return data
        else:
            raise ValueError(f"Unknown core execution event: {kind}")
        return {}

    def _save_replay(self, recipe):
        encoded = json.dumps(recipe, sort_keys=True, ensure_ascii=False)
        name = hashlib.sha256(encoded.encode()).hexdigest()[:24]
        relative = f"replays/{name}.json"
        path = Path(self.report["directory"]) / relative
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(recipe, indent=2, ensure_ascii=False) + "\n")
        return relative

    def _replays(self):
        for test in self.plan.tests:
            if recipe := test.get("replay_recipe"):
                test["replay_file"] = self._save_replay(recipe)
