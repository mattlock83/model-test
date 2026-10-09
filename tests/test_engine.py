import json

import pytest

from model_test.engine import Runner, run
from model_test.errors import Inconclusive
from model_test.graphwalker import parse_path
from model_test.model import violations


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
        settle_seconds=0,
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


def test_failed_destination_never_earns_edge_coverage(model, tmp_path):
    class WrongState(Oracle):
        def check(self, page, expected, *args):
            result = super().check(page, expected, *args)
            if expected == "accepted":
                result["status"] = "FAIL"
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


def test_failed_property_setup_is_not_a_counterexample_of_generated_data(model):
    class WrongStart(Oracle):
        def check(self, *args):
            return {"status": "FAIL"}

    runner = Runner(model, Browser(model), WrongStart(), "http://example.test", settle_seconds=0)
    with pytest.raises(Inconclusive, match="property setup"):
        runner.case(model.edges["submit"], {"Places": 5}, [{"field": "Places"}])


def test_replay_requires_exact_model_hash(model, tmp_path):
    replay = tmp_path / "foreign.json"
    replay.write_text(json.dumps({"model_hash": "different", "journey": "submit", "input": {"Places": 2}}))
    report = execute(model, tmp_path, replay=replay)
    assert report["status"] == "INCONCLUSIVE"
    assert not report["cases"]


def test_reports_escape_browser_html(model, tmp_path):
    from model_test.report import write_report

    report = execute(model, tmp_path)
    report["cases"].append({"status": "FAIL", "source": "<script>alert(1)</script>"})
    write_report(tmp_path, report)
    html = (tmp_path / "report.html").read_text()
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


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


def test_visible_failed_run_keeps_last_tab_for_inspection(model, tmp_path):
    browser = Browser(model, bug=True)
    report = execute(model, tmp_path, browser=browser, headed=True)
    assert report["status"] == "FAIL" and report["browser_left_open"]
    assert not browser.closed


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

    from model_test.hooks import Hooks

    return Hooks(SimpleNamespace(**callbacks))


def test_hooks_surround_checks_actions_and_each_reset(model, tmp_path):
    events = []

    def track(event):
        def callback(ctx):
            events.append((event, ctx.phase, ctx.element_id, ctx.result, ctx.error))

        return callback

    from model_test.hooks import EVENTS

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
            return {"status": "FAIL"}

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


def test_total_input_budget_stops_campaign_without_claiming_completion(model, tmp_path):
    report = execute(model, tmp_path, input_mode="boundaries", max_input_attempts=2)
    assert report["status"] == "INCONCLUSIVE"
    assert report["input_attempts"] == 2
    assert "budget" in report["error"]
    assert report["coverage"]["properties"]["completed"] == 0


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
