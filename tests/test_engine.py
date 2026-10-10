import json
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from testwalker.engine import run
from testwalker.errors import Inconclusive
from testwalker.graphwalker import parse_path
from testwalker.model import violations


def native_domain(monkeypatch, descriptor):
    """Supply native Hegel domains through the same extension binding as users."""
    from testwalker.input_strategies import StrategyProvider

    def build(self, context, default):
        if context["field_name"] is None:
            return {"kind": "object", "fields": {name: descriptor for name in context["fields"]}}
        return descriptor

    monkeypatch.setattr(StrategyProvider, "build", build)
    monkeypatch.setattr(
        StrategyProvider,
        "__init__",
        lambda self: (setattr(self, "factory", True), setattr(self, "metadata", None)) and None,
    )


def junit(report):
    root = ET.parse(Path(report["directory"]) / "junit.xml").getroot()
    cases = root.findall(".//testcase")
    assert int(root.get("tests")) == len(cases)
    for kind, tag in (("failures", "failure"), ("errors", "error"), ("skipped", "skipped")):
        assert int(root.get(kind)) == sum(case.find(tag) is not None for case in cases)
        assert int(root.get(kind)) == sum(int(suite.get(kind)) for suite in root.findall("testsuite"))
    return root


class Client:
    def __init__(self):
        self.stats = {"calls": 0}
        self.closed = False

    def close(self):
        self.closed = True


class Browser:
    """Deterministic fixture used only in framework unit tests."""

    def __init__(self, model, bug=False):
        self.model, self.bug, self.trace = model, bug, []
        self.closed = False

    def reset(self, url):
        self.url, self.state = url, "form"

    def observe(self):
        return {"url": self.url, "title": self.state, "text": self.state, "actions": []}

    def pursue(self, intent, *, fields=None, data=None, destination=None, states=None):
        if fields:
            invalid = violations(fields, data)
            self.state = "rejected" if invalid else "accepted"
            if self.bug and str(data["Places"]) == "5":
                self.state = "accepted"
        else:
            self.state = "form"
        self.trace.append({"intent": intent, "data": data})

    def close(self):
        self.closed = True


class Oracle:
    def check(self, page, expected, data=None, invalid=None):
        observed = page["text"]
        return {
            "status": "PASS" if observed == expected else "FAIL",
            "observed": observed,
            "expected": expected,
            "checks": [{"rule": "Invalid capacity is rejected.", "scope": "global", "status": "met"}],
        }


def planned(model, *_):
    ids = ["form", "submit", "accepted", "another", "form", "submit_invalid", "rejected", "correct", "form"]
    return parse_path(model, "\n".join(json.dumps({"currentElementName": i}) for i in ids))


def execute(model, tmp_path, **options):
    browser = options.pop("browser", Browser(model, bug=options.pop("bug", False)))
    return run(
        model,
        "http://example.test",
        output=tmp_path,
        browser=browser,
        client=Client(),
        oracle=options.pop("oracle", Oracle()),
        path_generator=planned,
        cases=1,
        log=lambda *_: None,
        **options,
    )


def test_complete_run_requires_verified_graph_and_property_coverage(model, tmp_path):
    browser = Browser(model)
    report = execute(model, tmp_path, browser=browser)
    assert report["status"] == "PASS"
    assert report["coverage"]["edges"]["verified"] == 4
    assert report["coverage"]["states"]["verified"] == 3
    # Valid and invalid nominal scenarios share one property campaign.
    assert report["coverage"]["properties"] == {"completed": 1, "total": 1}
    assert browser.closed
    persisted = json.loads((tmp_path / report["directory"].split("/")[-1] / "report.json").read_text())
    assert persisted["status"] == "PASS"


def test_defect_creates_replay_and_replay_does_not_claim_full_coverage(model, tmp_path):
    first = execute(model, tmp_path, bug=True)
    assert first["status"] == "FAIL"
    assert first["counterexample"]["input"] == {"Places": 5}
    assert first["coverage"]["properties"]["completed"] == 0
    replay = first["directory"] + "/replay.json"
    second = execute(model, tmp_path, bug=True, replay=replay)
    assert second["status"] == "FAIL"
    assert second["coverage"]["edges"]["verified"] == 0
    assert second["cases"][0]["input"] == {"Places": 5}
    fixed = execute(model, tmp_path, replay=replay)
    assert fixed["status"] == "PASS" and fixed["replay"]
    assert fixed["coverage"]["properties"]["completed"] == 0
    assert fixed["coverage"]["property phases"] == {"completed": 1, "total": 1, "available": 1}
    assert "one replay input" in (Path(fixed["directory"]) / "report.html").read_text()


def test_failed_destination_never_earns_edge_coverage(model, tmp_path):
    class WrongState(Oracle):
        def check(self, page, expected, *args):
            result = super().check(page, expected, *args)
            if expected == "accepted":
                result["checks"].append(
                    {"rule": "Destination is valid", "scope": "state", "status": "broken"}
                )
            return result

    report = execute(model, tmp_path, oracle=WrongState())
    assert report["status"] == "FAIL"
    assert report["coverage"]["edges"]["verified"] == 0
    assert report["coverage"]["states"]["verified"] == 1


def test_agent_timeout_is_inconclusive_and_cleanup_still_happens(model, tmp_path):
    class Blocked(Browser):
        def pursue(self, *args, **kwargs):
            raise Inconclusive("navigation unavailable")

    browser = Blocked(model)
    report = execute(model, tmp_path, browser=browser)
    assert report["status"] == "INCONCLUSIVE" and browser.closed
    assert "counterexample" not in report
    assert report["coverage"]["edges"]["verified"] == 0


def test_failed_property_setup_is_not_a_counterexample_of_generated_data(model, tmp_path):
    class WrongStart(Oracle):
        def check(self, *args):
            return {"observed": "accepted", "checks": []}

    replay = tmp_path / "case.json"
    replay.write_text(json.dumps({"model_hash": model.digest, "journey": "submit", "input": {"Places": 5}}))
    report = execute(model, tmp_path, oracle=WrongStart(), replay=replay)
    assert report["status"] == "INCONCLUSIVE" and "property setup" in report["error"]
    assert "counterexample" not in report


def test_replay_requires_exact_model_hash(model, tmp_path):
    replay = tmp_path / "foreign.json"
    replay.write_text(json.dumps({"model_hash": "different", "journey": "submit", "input": {"Places": 2}}))
    report = execute(model, tmp_path, replay=replay)
    assert report["status"] == "INCONCLUSIVE"
    assert not report["cases"]


def test_reports_escape_browser_html(model, tmp_path):
    from testwalker.report import write_report

    report = execute(model, tmp_path)
    report["cases"].append({"status": "FAIL", "source": "<script>alert(1)</script>"})
    write_report(tmp_path, report)
    html = (tmp_path / "report.html").read_text()
    assert "<script>alert(1)</script>" not in html
    assert "\\u003cscript\\u003e" in html


def test_unverified_global_rules_prevent_overall_pass(model, tmp_path):
    class UnverifiedGlobal(Oracle):
        def check(self, *args):
            result = super().check(*args)
            result["checks"][0]["status"] = "uncertain"
            return result

    report = execute(model, tmp_path, oracle=UnverifiedGlobal())
    assert report["coverage"]["edges"]["verified"] == 4
    assert report["coverage"]["properties"]["completed"] == 1
    assert report["status"] == "INCONCLUSIVE"
    assert "Global requirements never verified" in report["error"]
    assert report["coverage"]["global requirements"] == {"verified": 0, "total": 1}


@pytest.mark.parametrize("keep_open", [False, True])
@pytest.mark.parametrize("bug", [False, True])
def test_visible_run_closes_tab_unless_explicitly_retained(model, tmp_path, keep_open, bug):
    browser = Browser(model, bug=bug)
    report = execute(model, tmp_path, browser=browser, headed=True, keep_browser_open=keep_open)
    assert report["status"] == ("FAIL" if bug else "PASS")
    assert report.get("browser_left_open", False) is keep_open
    assert browser.closed is not keep_open


def test_navigation_stop_reports_exact_edge_and_optional_debug_traceback(model, tmp_path):
    class Blocked(Browser):
        def pursue(self, *args, **kwargs):
            raise Inconclusive("navigation unavailable")

    browser = Blocked(model)
    report = execute(model, tmp_path, browser=browser, headed=True, debug=True)
    stop = report["stop"]
    assert stop["phase"] == "graph execution"
    assert stop["walk"] == 1
    assert stop["element"]["id"] == "submit"
    assert stop["element"]["sourceVertexId"] == "form"
    assert stop["element"]["targetVertexId"] == "accepted"
    assert stop["exception"] == "Inconclusive"
    assert "Inconclusive: navigation unavailable" in stop["traceback"]
    assert browser.closed
    html = (tmp_path / report["directory"].split("/")[-1] / "report.html").read_text()
    assert "Where execution stopped" in html and "navigation unavailable" in html


@pytest.mark.parametrize(
    "verdict,status", [("met", "PASS"), ("broken", "FAIL"), ("uncertain", "INCONCLUSIVE")]
)
def test_deferred_global_audit_requires_a_confident_supported_verdict(model, tmp_path, verdict, status):
    class DeferredOracle(Oracle):
        def check(self, *args, **kwargs):
            result = super().check(*args, **kwargs)
            result["checks"][0]["status"] = "uncertain"
            return result

        def audit_globals(self, records, rules):
            assert records and all(record.get("status") in {"PASS", "EXECUTED"} for record in records)
            return [{"rule": rules[0], "scope": "global", "status": verdict}]

    report = execute(model, tmp_path, oracle=DeferredOracle())
    assert report["status"] == status
    assert report["coverage"]["global requirements"]["verified"] == int(verdict == "met")


def hook_functions(**callbacks):
    from types import SimpleNamespace

    from testwalker.hooks import Hooks

    return Hooks(SimpleNamespace(**callbacks))


def test_hooks_surround_checks_actions_and_each_reset(model, tmp_path):
    events = []

    def track(event):
        def callback(ctx):
            events.append((event, ctx.phase, ctx.element_id, ctx.result, ctx.error))

        return callback

    from testwalker.hooks import EVENTS

    hooks = hook_functions(**{event: track(event) for event in EVENTS})
    report = execute(model, tmp_path, hooks=hooks, input_mode="boundaries")
    assert report["status"] == "PASS"
    assert events[0][0] == "before_run" and events[-1][0] == "after_run"
    names = [event[0] for event in events]
    assert names.count("before_case") == report["input_attempts"]
    assert names.count("before_reset") == report["input_attempts"] + 1
    assert names.count("after_case") == names.count("before_case")
    assert any(event[:3] == ("before_state", "setup", "form") for event in events)
    graph = [(event, element) for event, phase, element, *_ in events if phase == "graph"]
    assert graph[:10] == [
        ("before_run", None),
        ("before_walk", None),
        ("before_reset", None),
        ("after_reset", None),
        ("before_state", "form"),
        ("after_state", "form"),
        ("before_transition", "submit"),
        ("after_transition", "submit"),
        ("before_state", "accepted"),
        ("after_state", "accepted"),
    ]
    assert all(event[3]["status"] == "PASS" for event in events if event[0] == "after_state")
    assert len(report["hooks"]["events"]) == len(events)


def test_hook_assertion_prevents_edge_coverage(model, tmp_path):
    def verify(ctx):
        if ctx.element_id == "accepted":
            ctx.check(False, "backend record missing")

    report = execute(model, tmp_path, hooks=hook_functions(after_state=verify))
    assert report["status"] == "FAIL"
    assert "backend record missing" in report["error"]
    assert report["coverage"]["edges"]["verified"] == 0


def test_cleanup_runs_when_before_hook_fails_and_preserves_original_failure(model, tmp_path):
    events = []

    def fail(ctx):
        raise RuntimeError("backend reset unavailable")

    def cleanup(ctx):
        events.append(ctx.error)
        raise AssertionError("secondary cleanup error")

    report = execute(model, tmp_path, hooks=hook_functions(before_reset=fail, after_reset=cleanup))
    assert report["status"] == "INCONCLUSIVE"
    assert "backend reset unavailable" in report["error"]
    assert len(events) == 1 and isinstance(events[0], Inconclusive)
    assert len(report["hooks"]["events"]) == 2


def test_failed_browser_check_is_not_masked_by_after_hook_error(model, tmp_path):
    class WrongState(Oracle):
        def check(self, *args):
            return {"observed": "accepted", "checks": []}

    def cleanup(ctx):
        raise RuntimeError("secondary hook failure")

    report = execute(model, tmp_path, oracle=WrongState(), hooks=hook_functions(after_state=cleanup))
    assert report["status"] == "FAIL"
    assert report["failure"]["status"] == "FAIL"


def test_after_run_assertion_changes_pass_and_resources_are_closed(model, tmp_path):
    browser = Browser(model)

    def final_check(ctx):
        ctx.check(False, "audit failed")

    report = execute(model, tmp_path, browser=browser, hooks=hook_functions(after_run=final_check))
    assert report["status"] == "FAIL" and browser.closed
    assert "audit failed" in report["error"]


def test_property_hook_defect_has_replay_input(model, tmp_path):
    def verify(ctx):
        ctx.check(ctx.data["Places"] != 5, "capacity audit failed")

    report = execute(model, tmp_path, hooks=hook_functions(after_case=verify), input_mode="boundaries")
    assert report["status"] == "FAIL"
    assert report["counterexample"]["input"] == {"Places": 5}
    assert (tmp_path / report["directory"] / "replay.json").exists()


def test_reset_hooks_share_state_without_changing_generated_data(model, tmp_path):
    def before(ctx):
        ctx.scratch["resets"] = ctx.scratch.get("resets", 0) + 1
        ctx.data["Places"] = 999  # Context input is a snapshot, not an input override.

    hooks = hook_functions(before_reset=before)
    report = execute(model, tmp_path, hooks=hooks)
    assert report["status"] == "PASS"
    assert hooks.scratch["resets"] == report["input_attempts"] + 1
    assert all(case["input"]["Places"] != 999 for case in report["cases"])


def test_input_limit_selects_scope_without_claiming_full_campaign_coverage(model, tmp_path):
    report = execute(model, tmp_path, input_mode="boundaries", max_input_attempts=2)
    assert report["status"] == "PASS"
    assert report["input_attempts"] == 2
    assert "error" not in report and "stop" not in report
    assert report["coverage"]["properties"]["completed"] == 0
    assert report["coverage"]["property phases"] == {"completed": 2, "total": 2, "available": 10}
    assert report["scope"]["selected_campaigns"] == 1
    assert report["scope"]["property_selection"] == {
        "policy": "model order",
        "input_limit": 2,
        "available_phases": 10,
        "selected_phases": 2,
        "excluded_phases": 8,
        "available_examples": 10,
        "selected_examples": 2,
    }


def test_focused_strategy_settings_are_planned_recorded_and_limited(model, tmp_path):
    policy = {"data sets": {"Reservation": {"Places": {"cases": 3, "radius": 5}}}}
    report = execute(model, tmp_path, input_strategies=policy, max_input_attempts=2)
    assert report["status"] == "PASS"
    assert report["limits"]["input_strategies"] == policy
    phase = next(test["phase"] for test in report["tests"] if test["kind"] == "property")
    assert phase["max_examples"] == 2 and phase["requested_max_examples"] == 3
    assert phase["strategy"]["radius"] == 5
    assert report["input_attempts"] == 1  # singleton valid example exhausts naturally
    assert report["coverage"]["properties"]["completed"] == 0
    inventory = json.loads((Path(report["directory"]) / "plan.json").read_text())
    assert inventory["limits"]["input_strategies"] == policy


def test_focused_failure_survives_limit_during_hegel_reproduction(model, tmp_path):
    report = execute(model, tmp_path, bug=True, max_input_attempts=8)
    assert report["status"] == "FAIL"
    assert report["counterexample"]["input"] == {"Places": 5}
    assert report["counterexample"]["minimization_limited"]


def test_graph_only_run_reports_that_properties_were_not_tested(model, tmp_path):
    report = execute(model, tmp_path, input_mode="none", walks=2)
    assert report["status"] == "PASS"
    assert report["input_attempts"] == 0 and not report["cases"]
    assert report["scope"]["selected_campaigns"] == 0
    assert report["scope"]["available_campaigns"] == 1
    assert [walk["seed"] for walk in report["walks"]] == [42, 43]
    assert len(report["steps"]) == 18
    assert report["coverage"]["edges"]["verified"] == 4
    html = (tmp_path / report["directory"] / "report.html").read_text()
    assert "Input campaigns were disabled" in html


def test_keyboard_interrupt_keeps_inconclusive_report_and_runs_teardown(model, tmp_path):
    events = []

    def stop(ctx):
        raise KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        execute(
            model, tmp_path, hooks=hook_functions(before_state=stop, after_run=lambda ctx: events.append(1))
        )
    reports = list(tmp_path.glob("*/report.json"))
    assert len(reports) == 1 and json.loads(reports[0].read_text())["status"] == "INCONCLUSIVE"
    assert events == [1]
    report = json.loads(reports[0].read_text())
    root = junit(report)
    assert int(root.get("errors")) >= 1
    assert int(root.get("skipped")) > 0


def test_failed_checkpoint_is_not_retried_into_a_pass(model, tmp_path):
    class ChangingVerdict(Oracle):
        calls = 0

        def check(self, *args):
            self.calls += 1
            return {"observed": "accepted" if self.calls == 1 else "form", "checks": []}

    browser, oracle = Browser(model), ChangingVerdict()
    report = execute(model, tmp_path, browser=browser, oracle=oracle, input_mode="none")
    assert report["status"] == "FAIL"
    assert oracle.calls == 1


@pytest.mark.parametrize("status", ["FAIL", "INCONCLUSIVE"])
def test_replay_retains_case_and_hook_evidence_when_hook_raises(model, tmp_path, status):
    from testwalker.errors import Defect

    replay = tmp_path / "input.json"
    replay.write_text(json.dumps({"model_hash": model.digest, "journey": "submit", "input": {"Places": 5}}))
    evidence = {"status": status, "observed": "backend ledger", "checks": []}

    def verify(ctx):
        error_type = Defect if status == "FAIL" else Inconclusive
        raise error_type("backend check failed", result=evidence)

    report = execute(model, tmp_path, replay=replay, hooks=hook_functions(after_case=verify))
    assert report["status"] == status
    assert len(report["cases"]) == 1
    assert report["cases"][0]["input"] == {"Places": 5}
    assert report["cases"][0]["source"] == "replay"
    assert report["cases"][0]["observed"] == "backend ledger"
    if status == "FAIL":
        assert report["counterexample"]["input"] == {"Places": 5}


def test_checkpoint_retains_independent_input_evidence_as_a_snapshot(model, tmp_path):
    browser = Browser(model)
    report = execute(model, tmp_path, browser=browser, input_mode="none")
    result = next(step for step in report["steps"] if step["id"] == "rejected")
    browser.trace[-2]["data"]["Places"] = 2
    assert result["input"] == {"Places": 0}
    assert result["violations"][0]["field"] == "Places"


def test_interrupted_property_records_the_attempt_and_preserves_keyboard_interrupt(model, tmp_path):
    def interrupt(ctx):
        raise KeyboardInterrupt()

    browser = Browser(model)
    with pytest.raises(KeyboardInterrupt):
        execute(model, tmp_path, browser=browser, hooks=hook_functions(before_case=interrupt))
    report = json.loads(next(tmp_path.glob("*/report.json")).read_text())
    assert report["status"] == "INCONCLUSIVE"
    assert report["input_attempts"] == 1
    assert report["cases"][0]["status"] == "INCONCLUSIVE"
    assert browser.closed


def test_input_selection_does_not_record_a_fictitious_unstarted_case(model, tmp_path):
    messages = []
    report = run(
        model,
        "http://example.test",
        output=tmp_path,
        browser=Browser(model),
        client=Client(),
        oracle=Oracle(),
        path_generator=planned,
        input_mode="boundaries",
        max_input_attempts=2,
        log=messages.append,
    )
    assert report["input_attempts"] == 2
    assert len(report["cases"]) == 2
    assert all(case["status"] == "PASS" for case in report["cases"])
    assert any("2 input attempts" in message for message in messages)


def test_all_walks_are_planned_and_saved_before_browser_execution(model, tmp_path):
    seeds = []

    def generate(*args):
        seeds.append(args[2])
        return planned(*args)

    class InspectPlan(Browser):
        def reset(self, url):
            assert seeds == [42, 43]
            inventory = json.loads(next(tmp_path.glob("*/plan.json")).read_text())
            assert inventory["planning"]["complete"]
            assert len(inventory["tests"]) == 10
            assert all(test["status"] == "NOT_RUN" for test in inventory["tests"])
            super().reset(url)

    report = run(
        model,
        "http://example.test",
        output=tmp_path,
        client=Client(),
        browser=InspectPlan(model),
        oracle=Oracle(),
        path_generator=generate,
        walks=2,
        input_mode="none",
        log=lambda *_: None,
    )
    root = junit(report)
    assert report["status"] == "PASS"
    assert len(root.findall("./testsuite[@name='testwalker.graph']/testcase")) == 10
    assert len({case.get("name") for case in root.findall(".//testcase")}) == 11
    assert root.get("skipped") == root.get("errors") == root.get("failures") == "0"


def test_junit_failed_destination_fails_journey_and_lists_every_unreached_test(model, tmp_path):
    class WrongState(Oracle):
        def check(self, page, expected, *args):
            result = super().check(page, expected, *args)
            if expected == "accepted":
                result.update(status="FAIL", observed="rejected")
            return result

    report = execute(model, tmp_path, oracle=WrongState(), walks=2, input_mode="generated")
    root = junit(report)
    graph = root.find("./testsuite[@name='testwalker.graph']")
    assert graph.get("tests") == "10"
    assert graph.get("failures") == "1" and graph.get("skipped") == "8"
    failure = graph.find("./testcase/failure")
    evidence = json.loads(failure.text)
    assert evidence["test"]["element"]["id"] == "submit"
    assert evidence["test"]["expected_state"] == "accepted"
    assert evidence["test"]["input"] == {"Places": 2}
    assert evidence["checkpoints"][-1]["observed"] == "rejected"
    assert evidence["checkpoints"][-1]["evidence"]["text"] == "accepted"
    skipped = json.loads(graph.find("./testcase/skipped").text)
    assert skipped["blocked_by"][0]["id"] == evidence["test"]["id"]
    assert skipped["stop"]["element"]["id"] == "accepted"
    assert skipped["artifacts"]["model.json"] == "model.json"
    assert root.find("./testsuite[@name='testwalker.property']").get("skipped") == "2"


def test_junit_navigation_uncertainty_is_error_with_actions_and_decisions(model, tmp_path):
    client = Client()
    client.decisions = []

    class Blocked(Browser):
        def pursue(self, *args, **kwargs):
            self.trace.append({"operation": "CLICK", "action": "Submit"})
            client.decisions.append({"request": {"intent": "submit"}, "response": {"confidence": 0.5}})
            raise Inconclusive("Confidence 0.5 below 0.85")

    report = run(
        model,
        "http://example.test",
        output=tmp_path,
        client=client,
        browser=Blocked(model),
        oracle=Oracle(),
        path_generator=planned,
        input_mode="none",
        log=lambda *_: None,
    )
    root = junit(report)
    graph = root.find("./testsuite[@name='testwalker.graph']")
    assert graph.get("errors") == "1" and graph.get("skipped") == "3"
    evidence = json.loads(graph.find("./testcase/error").text)
    assert evidence["actions"] == [{"operation": "CLICK", "action": "Submit"}]
    assert evidence["decisions"][0]["response"]["confidence"] == 0.5
    assert "0.85" in graph.find("./testcase/error").get("message")


def test_junit_limited_boundaries_records_only_selected_tests(model, tmp_path):
    from testwalker.properties import plan_cases

    report = execute(model, tmp_path, input_mode="boundaries", max_input_attempts=2)
    root = junit(report)
    planned_inputs = plan_cases(model.data_sets["Reservation"], mode="boundaries")
    suite = root.find("./testsuite[@name='testwalker.property']")
    assert suite.get("tests") == "2" and suite.get("skipped") == "0"
    assert suite.get("errors") == suite.get("failures") == "0"
    assert root.find("./testsuite[@name='testwalker.run']/testcase/error") is None
    assert report["test_summary"]["PASS"] == 7  # five graph checks plus two property inputs
    inventory = json.loads((Path(report["directory"]) / "plan.json").read_text())
    unselected = inventory["planning"]["unselected_properties"]
    assert len(unselected) == len(planned_inputs) - 2
    assert unselected[0]["phase"]["input"] == planned_inputs[2]["input"]
    assert inventory["scope"] == report["scope"]
    assert all(test["status"] == "NOT_RUN" for test in inventory["tests"])


def test_generated_scope_reduces_examples_before_execution(model, tmp_path, monkeypatch):
    report = execute(model, tmp_path, input_mode="generated", max_input_attempts=1)
    suite = junit(report).find("./testsuite[@name='testwalker.property']")
    # The combined phase is outside the selected scope, so it is not a skipped test.
    assert report["status"] == "PASS"
    assert suite.get("tests") == "1" and suite.get("skipped") == "0"
    assert report["input_attempts"] == 1
    native_domain(monkeypatch, {"kind": "integer", "minimum": 1, "maximum": 4})
    report = run(
        model,
        "http://example.test",
        output=tmp_path,
        client=Client(),
        browser=Browser(model),
        oracle=Oracle(),
        path_generator=planned,
        input_mode="generated",
        cases=5,
        max_input_attempts=2,
        log=lambda *_: None,
    )
    suite = junit(report).find("./testsuite[@name='testwalker.property']")
    assert report["status"] == "PASS"
    assert suite.get("tests") == "1" and suite.get("errors") == "0" and suite.get("skipped") == "0"
    detail = json.loads(suite.find("./testcase/system-out").text)
    assert detail["test"]["phase"]["requested_max_examples"] == 5
    assert detail["test"]["phase"]["max_examples"] == 2
    assert len(detail["attempts"]) == report["input_attempts"]
    assert 1 <= report["input_attempts"] <= 2
    assert report["coverage"]["properties"]["completed"] == 0
    inventory = json.loads((Path(report["directory"]) / "plan.json").read_text())
    assert inventory["tests"][0]["phase"]["max_examples"] == 2


@pytest.mark.parametrize("shrink", [True, False])
@pytest.mark.parametrize("limit", [1, 2])
def test_observed_defect_survives_limit_during_reproduction(model, tmp_path, monkeypatch, shrink, limit):
    native_domain(monkeypatch, {"kind": "literal", "value": 5})
    report = execute(
        model, tmp_path, bug=True, input_mode="generated", max_input_attempts=limit, shrink=shrink
    )
    assert report["status"] == "FAIL"
    assert report["input_attempts"] <= limit
    assert all(case["status"] == "FAIL" for case in report["cases"])
    assert report["counterexample"]["input"] == {"Places": 5}
    if limit == 1:
        assert report["counterexample"]["minimization_limited"]
    replay = json.loads((Path(report["directory"]) / "replay.json").read_text())
    assert replay["input"] == {"Places": 5}
    root = junit(report)
    assert root.get("failures") == "2" and root.get("errors") == "0"


def test_uncertain_selected_input_remains_inconclusive(model, tmp_path):
    def uncertain(ctx):
        raise Inconclusive("The input outcome is ambiguous")

    report = execute(
        model,
        tmp_path,
        input_mode="generated",
        max_input_attempts=2,
        hooks=hook_functions(after_case=uncertain),
    )
    assert report["status"] == "INCONCLUSIVE"
    assert report["input_attempts"] == 1
    suite = junit(report).find("./testsuite[@name='testwalker.property']")
    assert suite.get("errors") == "1" and suite.get("skipped") == "1"
    assert "ambiguous" in report["error"]


def test_small_input_selection_still_requires_global_audit(model, tmp_path):
    class DeferredOracle(Oracle):
        def check(self, *args):
            result = super().check(*args)
            result["checks"] = [{"rule": "The outcome is visible.", "scope": "state", "status": "met"}]
            return result

        def audit_globals(self, observations, rules):
            assert len([entry for entry in observations if "source" in entry]) == 2
            return [{"rule": rule, "scope": "global", "status": "met"} for rule in rules]

    report = execute(model, tmp_path, input_mode="generated", max_input_attempts=2, oracle=DeferredOracle())
    assert report["status"] == "PASS"
    assert report["coverage"]["global requirements"]["verified"] == 1
    assert report["global_audit"]


def test_limited_shrinking_retains_failing_input_and_screenshot_after_passing_candidate(
    model, tmp_path, monkeypatch
):
    native_domain(monkeypatch, {"kind": "integer", "minimum": 0, "maximum": 100})

    class BuggyBrowser(Browser):
        def pursue(self, *args, **kwargs):
            super().pursue(*args, **kwargs)
            if kwargs.get("fields") and int(kwargs["data"]["Places"]) >= 5:
                self.state = "accepted"

        def screenshot(self, path):
            Path(path).write_bytes(b"\x89PNG\r\n\x1a\n" + self.state.encode())

    report = run(
        model,
        "http://example.test",
        output=tmp_path,
        client=Client(),
        browser=BuggyBrowser(model),
        oracle=Oracle(),
        path_generator=planned,
        input_mode="generated",
        cases=30,
        max_input_attempts=8,
        log=lambda *_: None,
    )
    assert report["status"] == "FAIL"
    assert report["input_attempts"] == 8
    assert report["cases"][-1]["status"] == "PASS"
    failed = report["counterexample"]
    assert failed["status"] == "FAIL" and failed["minimization_limited"]
    phase = next(test for test in report["tests"] if test["kind"] == "property")
    assert phase["screenshot"] == failed["screenshot"] != report["cases"][-1]["screenshot"]
    replay = json.loads((Path(report["directory"]) / "replay.json").read_text())
    assert replay["input"] == failed["input"]


def test_finite_domain_can_complete_selected_scope_below_input_limit(model, tmp_path, monkeypatch):
    native_domain(monkeypatch, {"kind": "literal", "value": 2})
    report = run(
        model,
        "http://example.test",
        output=tmp_path,
        client=Client(),
        browser=Browser(model),
        oracle=Oracle(),
        path_generator=planned,
        input_mode="generated",
        cases=5,
        max_input_attempts=2,
        log=lambda *_: None,
    )
    assert report["status"] == "PASS" and report["input_attempts"] == 1
    assert report["coverage"]["property phases"] == {"completed": 1, "total": 1, "available": 2}
    assert report["test_summary"]["SKIPPED"] == 0


def test_junit_generated_failure_groups_shrink_attempts_and_links_replay(model, tmp_path, monkeypatch):
    native_domain(monkeypatch, {"kind": "literal", "value": 5})
    report = execute(model, tmp_path, bug=True, input_mode="generated")
    suite = junit(report).find("./testsuite[@name='testwalker.property']")
    assert suite.get("tests") == "2" and suite.get("failures") == "1" and suite.get("skipped") == "1"
    detail = json.loads(suite.find("./testcase/failure").text)
    assert detail["attempts"][-1]["input"] == {"Places": 5}
    assert detail["artifacts"]["replay.json"] == "replay.json"
    replay = execute(model, tmp_path, bug=True, replay=Path(report["directory"]) / "replay.json")
    root = junit(replay)
    assert root.find("./testsuite[@name='testwalker.graph']") is None
    assert root.find("./testsuite[@name='testwalker.property']").get("tests") == "1"


def test_junit_planning_failure_retains_partial_inventory_without_running_browser(model, tmp_path):
    def generate(model, path, seed, limit):
        if seed == 43:
            raise ValueError("second walk cannot be planned")
        return planned(model)

    class NoExecution(Browser):
        def reset(self, url):
            pytest.fail("All planning must finish before the first reset")

    report = run(
        model,
        "http://example.test",
        output=tmp_path,
        client=Client(),
        browser=NoExecution(model),
        oracle=Oracle(),
        path_generator=generate,
        walks=2,
        input_mode="none",
        log=lambda *_: None,
    )
    root = junit(report)
    assert report["planning"]["complete"] is False
    assert report["stop"]["walk"] == 2
    assert root.find("./testsuite[@name='testwalker.graph']").get("skipped") == "5"
    assert root.find("./testsuite[@name='testwalker.run']/testcase/error") is not None


def test_junit_startup_hook_failure_marks_selected_inventory_skipped(model, tmp_path):
    def fail(ctx):
        raise RuntimeError("fixture service unavailable")

    report = execute(model, tmp_path, hooks=hook_functions(before_run=fail), input_mode="generated")
    root = junit(report)
    assert root.get("skipped") == "7"
    assert root.get("errors") == "1"
    detail = json.loads(root.find("./testsuite[@name='testwalker.run']/testcase/error").text)
    assert detail["hook_errors"][0]["event"] == "before_run"


def test_junit_handles_xml_characters_and_illegal_control_characters(model, tmp_path):
    def fail(ctx):
        raise RuntimeError('<broken> & "quoted" \x00 \ufffe')

    report = execute(model, tmp_path, hooks=hook_functions(before_run=fail), input_mode="none")
    root = junit(report)
    error = root.find("./testsuite[@name='testwalker.run']/testcase/error")
    assert '<broken> & "quoted"' in error.get("message")
    assert "\x00" not in error.get("message") and "\ufffe" not in error.get("message")


def test_finite_hegel_domains_do_not_invent_skipped_examples(model, tmp_path, monkeypatch):
    native_domain(monkeypatch, {"kind": "literal", "value": 2})
    report = run(
        model,
        "http://example.test",
        output=tmp_path,
        client=Client(),
        browser=Browser(model),
        oracle=Oracle(),
        path_generator=planned,
        input_mode="generated",
        cases=20,
        log=lambda *_: None,
    )
    suite = junit(report).find("./testsuite[@name='testwalker.property']")
    assert report["status"] == "PASS"
    assert report["input_attempts"] == 2
    assert suite.get("tests") == "2" and suite.get("skipped") == "0"


def test_hook_system_exit_preserves_exit_and_reports_inconclusive_skipped_work(model, tmp_path):
    def stop(ctx):
        raise SystemExit("hook exited")

    with pytest.raises(SystemExit, match="hook exited"):
        execute(model, tmp_path, hooks=hook_functions(before_state=stop), input_mode="none")
    report = json.loads(next(tmp_path.glob("*/report.json")).read_text())
    root = junit(report)
    assert report["status"] == "INCONCLUSIVE"
    assert root.get("errors") == "2" and root.get("skipped") == "4"


def test_screenshots_capture_graph_checks_and_each_executed_input_before_cleanup(model, tmp_path):
    class Capturing(Browser):
        def screenshot(self, path):
            assert not self.closed
            Path(path).write_bytes(b"\x89PNG\r\n\x1a\n" + self.state.encode())

    browser = Capturing(model)
    report = execute(model, tmp_path, browser=browser, input_mode="boundaries", max_input_attempts=2)
    folder = Path(report["directory"])
    assert browser.closed
    assert len(list((folder / "screenshots").glob("*.png"))) == 7
    graph = [t for t in report["tests"] if t["kind"] == "graph"]
    assert all((folder / t["screenshot"]).is_file() for t in graph)
    for case in report["cases"]:
        if case.get("attempted", True):
            assert (folder / case["screenshot"]).is_file()
            assert (folder / case["screenshot"]).read_bytes().endswith(case["observed"].encode())
        else:
            assert "screenshot" not in case
    assert all("screenshot" not in test for test in report["tests"] if test["status"] == "SKIPPED")
    assert all("screenshot" not in test for test in json.loads((folder / "plan.json").read_text())["tests"])
    assert "screenshots/" in (folder / "junit.xml").read_text()


def test_screenshot_failure_does_not_change_pass_or_hide_original_error(model, tmp_path):
    class BrokenCapture(Browser):
        def screenshot(self, path):
            raise RuntimeError("capture unavailable")

    report = execute(model, tmp_path, browser=BrokenCapture(model), input_mode="none")
    assert report["status"] == "PASS"
    assert all("capture unavailable" in test["screenshot_error"] for test in report["tests"])
    report = execute(model, tmp_path, browser=BrokenCapture(model, bug=True), input_mode="boundaries")
    assert report["status"] == "FAIL"
    assert report["counterexample"]["input"] == {"Places": 5}
    assert "capture unavailable" in report["counterexample"]["screenshot_error"]


def test_disabled_screenshots_never_call_browser_capture(model, tmp_path):
    captured = []

    class Capturing(Browser):
        def screenshot(self, path):
            captured.append(path)

    report = execute(model, tmp_path, browser=Capturing(model), screenshots=False)
    assert report["status"] == "PASS" and not captured
    assert not (Path(report["directory"]) / "screenshots").exists()
    assert all("screenshot_error" not in test for test in report["tests"])


def test_failed_navigation_is_captured_before_browser_cleanup(model, tmp_path):
    class Blocked(Browser):
        def pursue(self, *args, **kwargs):
            self.state = "navigation blocked"
            raise Inconclusive("Cannot navigate")

        def screenshot(self, path):
            assert not self.closed
            Path(path).write_text(self.state)

    browser = Blocked(model)
    report = execute(model, tmp_path, browser=browser, input_mode="none")
    failed = next(test for test in report["tests"] if test["status"] == "INCONCLUSIVE")
    assert (Path(report["directory"]) / failed["screenshot"]).read_text() == "navigation blocked"
    assert browser.closed


def test_viewer_embeds_graph_and_data_safely_without_template_substitution_in_evidence(model, tmp_path):
    import re

    from testwalker.report import write_report

    report = execute(model, tmp_path, input_mode="none", screenshots=False)
    attack = "</script><script>window.untrustedExecuted=true</script> __TITLE__ & <div>"
    report["cases"].append({"source": attack, "status": "FAIL", "input": {"note": attack}})
    write_report(tmp_path, report)
    rendered = (tmp_path / "report.html").read_text()
    embedded = re.search(r'<script id="run-data" type="application/json">(.*?)</script>', rendered, re.S)[1]
    restored = json.loads(embedded)
    assert restored["cases"][-1]["input"]["note"] == attack
    assert restored["graph"]["start"] == "form"
    assert len(restored["graph"]["edges"]) == 4
    assert "<script>window.untrustedExecuted=true</script>" not in rendered
    assert 'id="graph"' in rendered and 'id="case-list"' in rendered
