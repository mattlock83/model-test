import json

import httpx
import pytest
from jev_ultrafast.browser import StalePage

from testwalker.errors import Inconclusive
from testwalker.jev import JevBrowser, JevClient, Oracle


def answer(choice, options, confidence=1):
    return {
        "choice": choice,
        "confidence": confidence,
        "probabilities": {key: float(key == choice) for key in options},
    }


class Decisions:
    """Fake remote choices, while exercising the real upstream answer validator."""

    answer = JevClient.answer
    threshold = 0.85

    def __init__(self, choose):
        self.choose, self.requests = choose, []

    def ask(self, state, questions):
        self.requests.append((state, questions))
        choices = self.choose(state, questions)
        return {
            "answers": {
                key: answer(choices.get(key, next(iter(q["criteria"]))), q["criteria"])
                for key, q in questions.items()
            }
        }


class DOM:
    def __init__(self, url):
        self.url, self.value, self.events = url, "old value", []
        self.closed, self.stale = False, False

    def observe(self, screenshot=False):
        return {
            "url": self.url,
            "title": "Reservations",
            "text": "Reserve a place",
            "actions": [
                {
                    "id": "fill-76",
                    "node": 76,
                    "kind": "fill",
                    "role": "textbox",
                    "label": "Party size",
                    "value": self.value,
                },
                {"id": "click-91", "node": 91, "kind": "click", "role": "button", "label": "Continue"},
            ],
        }

    def act(self, action, page, text=None):
        if self.stale:
            self.stale = False
            raise StalePage("changed before input")
        self.events.append((action["kind"], text))
        if action["kind"] == "fill":
            self.value = text

    def close(self):
        self.closed = True


def policy_for(value):
    def choose(state, questions):
        if "field_0" in questions:
            return {"field_0": "1"}
        return {
            "operation": "TYPE_TEXT" if state["elements"][0]["value"] != value else "CLICK",
            "type_text_target": "1",
            "click_target": "2",
            "submission": "2",
        }

    return choose


@pytest.mark.parametrize("value", ["", "5", "not a number"])
def test_invalid_data_is_typed_literally_and_stops_after_first_submission(model, value):
    client = Decisions(policy_for(value))
    driver = JevBrowser(client, browser_factory=DOM)
    driver.reset("http://demo.test")
    driver.pursue("Reserve places", fields=model.data_sets["Reservation"], data={"Places": value})
    assert driver.browser.events == [("fill", value), ("click", None)]
    assert driver.trace[-1]["submission"]
    # Controls are inferred from observed meaning; model's 'Places' differs from the visible 'Party size'.
    assert "Places" in client.requests[1][1]["field_0"]["instructions"]


def test_wrong_or_corrected_value_cannot_be_submitted_as_test_data(model):
    client = Decisions(
        lambda s, q: {"operation": "CLICK", "click_target": "2", "submission": "2", "field_0": "1"}
    )

    class CorrectingDOM(DOM):
        def act(self, action, page, text=None):
            super().act(action, page, text)
            if action["kind"] == "fill":
                self.value = "4"

    driver = JevBrowser(client, browser_factory=CorrectingDOM)
    driver.reset("http://demo.test")
    with pytest.raises(Inconclusive, match="exact test data"):
        driver.pursue("Reserve places", fields=model.data_sets["Reservation"], data={"Places": 5})
    assert driver.browser.events == [("fill", "5")]


def test_stale_before_mutation_is_reobserved_without_duplicate_submission(model):
    client = Decisions(policy_for("5"))
    driver = JevBrowser(client, browser_factory=DOM)
    driver.reset("http://demo.test")
    driver.browser.stale = True
    driver.pursue("Reserve places", fields=model.data_sets["Reservation"], data={"Places": 5})
    assert driver.browser.events == [("fill", "5"), ("click", None)]
    assert len(driver.trace) == 2


def test_no_submission_control_is_never_a_pass(model):
    driver = JevBrowser(Decisions(lambda *_: {"submission": "NONE"}), browser_factory=DOM)
    driver.reset("http://demo.test")
    with pytest.raises(Inconclusive, match="submission control"):
        driver.pursue("Reserve places", fields=model.data_sets["Reservation"], data={"Places": 2})


def test_reset_closes_only_owned_tab():
    driver = JevBrowser(Decisions(lambda *_: {}), browser_factory=DOM)
    driver.reset("http://demo.test")
    previous = driver.browser
    driver.reset("http://demo.test/next")
    assert previous.closed
    assert driver.browser is not previous and not driver.browser.closed


def test_duplicate_field_mapping_is_inconclusive(model):
    client = Decisions(lambda *_: {"field_0": "1", "field_1": "1"})
    driver = JevBrowser(client, browser_factory=DOM)
    driver.reset("http://demo.test")
    fields = {"One": {"description": "first"}, "Two": {"description": "second"}}
    with pytest.raises(Inconclusive, match="same browser control"):
        driver.bindings(driver.observe(), fields)


def test_call_budget_cache_and_http_failures(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "unit-test-key")
    sent = []

    def handler(request):
        sent.append(request)
        return httpx.Response(200, json={"answers": {}, "usage": {"input_tokens": 10}})

    client = JevClient(max_calls=1, http=httpx.Client(transport=httpx.MockTransport(handler)))
    assert client.ask({"text": "first"}, {}) == client.ask({"text": "first"}, {})
    with pytest.raises(Inconclusive, match="budget"):
        client.ask({"text": "changed"}, {})
    assert len(sent) == 1
    assert client.stats == {"calls": 1, "cache_hits": 1, "input_tokens": 10, "output_tokens": 0}
    assert json.loads(sent[0].content)["model"] == "jev-latest"
    client.close()


@pytest.mark.parametrize(
    "bad",
    [
        {"choice": "a", "confidence": 0.4, "probabilities": {"a": 1, "b": 0}},
        {"choice": "a", "confidence": 1, "probabilities": {"a": 0.6, "b": 0.4}},
        {"choice": "a", "confidence": 1, "probabilities": {"a": 1}},
        {"choice": "a", "confidence": float("nan"), "probabilities": {"a": 1, "b": 0}},
        {"choice": "made-up-target", "confidence": 1, "probabilities": {"a": 1, "b": 0}},
    ],
)
def test_untrusted_provider_choices_are_rejected(bad):
    with pytest.raises(Inconclusive):
        Decisions(lambda *_: {}).answer({"answers": {"target": bad}}, "target", {"a": "", "b": ""})


def test_provider_error_cannot_leak_response_secrets(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "secret-example")
    http = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(401, text="secret-example")))
    with JevClient(http=http).http:
        client = JevClient(http=http)
        with pytest.raises(Inconclusive, match="provider failed") as error:
            client.ask({}, {})
        assert "secret-example" not in str(error.value)


def test_oracle_identifies_state_without_receiving_expected_state(model):
    client = Decisions(lambda s, q: {"state": "s2"})
    result = Oracle(client, model).check(DOM("http://demo.test").observe(), "accepted", {"Places": 5})
    assert result["status"] == "FAIL" and result["observed"] == "rejected"
    state, questions = client.requests[0]
    assert "expected" not in state
    assert "submitted_business_data" not in state
    assert "goal" not in questions["state"]["instructions"]


@pytest.mark.parametrize(
    "verdict,expected", [("met", "PASS"), ("broken", "FAIL"), ("uncertain", "INCONCLUSIVE")]
)
def test_rule_oracle_distinguishes_failure_from_uncertainty(model, verdict, expected):
    client = Decisions(lambda s, q: {"state": "s0", **{k: verdict for k in q if k.startswith("rule_")}})
    oracle = Oracle(client, model)
    if expected == "INCONCLUSIVE":
        with pytest.raises(Inconclusive):
            oracle.check(DOM("http://demo.test").observe(), "form")
    else:
        assert oracle.check(DOM("http://demo.test").observe(), "form")["status"] == expected


def test_known_failure_is_not_hidden_by_another_uncertain_rule(model):
    client = Decisions(lambda *_: {"state": "s0", "rule_0": "broken", "rule_1": "uncertain"})
    result = Oracle(client, model).check(DOM("http://demo.test").observe(), "form")
    assert result["status"] == "FAIL"


def test_unknown_state_is_inconclusive(model):
    client = Decisions(lambda *_: {"state": "UNKNOWN"})
    with pytest.raises(Inconclusive, match="unknown or ambiguous"):
        Oracle(client, model).check(DOM("http://demo.test").observe(), "form")


def test_decision_evidence_survives_a_low_confidence_verdict(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "never-persist-this-key")
    payload = {"answers": {"state": answer("a", ["a", "b"], confidence=0.3)}}
    client = JevClient(
        http=httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload)))
    )
    result = client.ask({"text": "observed page"}, {"state": {"criteria": {"a": "One", "b": "Two"}}})
    with pytest.raises(Inconclusive):
        client.answer(result, "state", ["a", "b"])
    assert client.decisions[0]["response"] == payload
    assert "observed page" in json.dumps(client.decisions)
    assert "never-persist-this-key" not in json.dumps(client.decisions)
    client.close()


def test_visible_mode_foregrounds_every_new_property_tab():
    class VisibleDOM(DOM):
        def __init__(self, url):
            super().__init__(url)
            self.calls = []

        def call(self, method):
            self.calls.append(method)

    driver = JevBrowser(Decisions(lambda *_: {}), browser_factory=VisibleDOM, headed=True)
    driver.reset("http://demo.test")
    first = driver.browser
    driver.reset("http://demo.test")
    assert first.closed
    assert first.calls == ["Page.bringToFront"]
    assert driver.browser.calls == ["Page.bringToFront"]


def test_global_uncertainty_is_deferred_but_state_rules_cannot_be_skipped(model):
    client = Decisions(lambda *_: {"state": "s0", "rule_0": "uncertain", "rule_1": "met"})
    result = Oracle(client, model).check(DOM("http://demo.test").observe(), "form")
    assert result["status"] == "PASS"
    assert result["checks"][0]["scope"] == "global" and result["checks"][0]["status"] == "uncertain"
    questions = client.requests[-1][1]
    assert "not_applicable" not in questions["rule_1"]["criteria"]
    assert model.business["rules"][0] in questions["rule_0"]["criteria"]["broken"]


def test_unresolved_state_requirement_keeps_verdict_and_evidence(model):
    client = Decisions(lambda *_: {"state": "s0", "rule_0": "not_applicable", "rule_1": "uncertain"})
    with pytest.raises(Inconclusive) as failure:
        Oracle(client, model).check(DOM("http://demo.test").observe(), "form")
    assert "The outcome is visible." in str(failure.value)
    assert failure.value.result["status"] == "INCONCLUSIVE"
    assert failure.value.result["checks"][1]["status"] == "uncertain"


def test_navigation_goal_includes_destination_without_submission_instructions():
    driver = JevBrowser(Decisions(lambda *_: {"operation": "DONE"}), browser_factory=DOM)
    driver.reset("http://demo.test")
    driver.pursue("Begin reservation", destination="The reservation details form is displayed.")
    instructions = driver.client.requests[0][1]["operation"]["instructions"]
    assert "The reservation details form is displayed." in instructions["goal"]
    assert "first submission" not in instructions["goal"]


def test_destination_check_stops_navigation_even_if_action_head_wants_to_fill():
    client = Decisions(
        lambda *_: {"destination": "reached", "operation": "TYPE_TEXT", "type_text_target": "1"}
    )
    driver = JevBrowser(client, browser_factory=DOM)
    driver.reset("http://demo.test")
    driver.pursue("Begin a reservation", destination="The details form is displayed.")
    assert driver.browser.events == []


def test_field_binding_ignores_invalid_contents_but_retains_exact_local_value(model):
    client = Decisions(lambda *_: {"field_0": "1"})
    driver = JevBrowser(client, browser_factory=DOM)
    driver.reset("http://demo.test")
    driver.browser.value = "not a number"
    mapping = driver.bindings(driver.observe(), model.data_sets["Reservation"])
    assert mapping["Places"]["value"] == "not a number"
    observed, questions = client.requests[-1]
    assert all("value" not in element for element in observed["elements"])
    assert "value" not in questions["field_0"]["criteria"]["1"]


def test_navigation_distinguishes_all_states_before_acting():
    states = ["Promotional welcome", "Reservation confirmation"]
    client = Decisions(lambda *_: {"state": "s1", "operation": "CLICK", "click_target": "2"})
    driver = JevBrowser(client, browser_factory=DOM)
    driver.reset("http://demo.test")
    operation, action, submission = driver._decision(
        driver.observe(), "Return to the welcome", [], {}, states[0], states
    )
    assert operation == "CLICK" and action["label"] == "Continue" and not submission
    observed, questions = client.requests[0]
    assert questions["state"]["criteria"]["s1"] == states[1]
    assert "recent_actions" not in observed
    assert "goal" not in questions["state"]["instructions"]
    assert "DONE" not in client.requests[1][1]["operation"]["criteria"]


def test_navigation_stops_at_matching_catalog_state_without_an_action_decision():
    client = Decisions(lambda *_: {"state": "s1"})
    driver = JevBrowser(client, browser_factory=DOM)
    driver.reset("http://demo.test")
    driver.pursue("Show confirmation", destination="Confirmed", states=["Welcome", "Confirmed"])
    assert len(client.requests) == 1 and driver.browser.events == []


def test_uncertain_best_target_can_use_an_independently_supported_control():
    class AmbiguousTargets(Decisions):
        def ask(self, state, questions):
            result = super().ask(state, questions)
            if "click_target" in questions:
                result["answers"]["click_target"]["confidence"] = 0.5
            return result

    client = AmbiguousTargets(
        lambda *_: {"operation": "CLICK", "click_target": "2", "candidate_0": "advance"}
    )
    driver = JevBrowser(client, browser_factory=DOM)
    driver.reset("http://demo.test")
    operation, action, _ = driver._decision(driver.observe(), "Continue this journey", [], {})
    assert operation == "CLICK" and action["label"] == "Continue"
    assert len(client.requests) == 2
    assert client.threshold == 0.85


def test_uncertain_best_target_does_not_allow_an_unsupported_action():
    class AmbiguousTargets(Decisions):
        def ask(self, state, questions):
            result = super().ask(state, questions)
            if "click_target" in questions:
                result["answers"]["click_target"]["confidence"] = 0.5
            return result

    client = AmbiguousTargets(
        lambda s, q: {"operation": "CLICK", **{k: "uncertain" for k in q if k.startswith("candidate_")}}
    )
    driver = JevBrowser(client, browser_factory=DOM)
    driver.reset("http://demo.test")
    with pytest.raises(Inconclusive, match="suitable navigation control"):
        driver._decision(driver.observe(), "Continue this journey", [], {})
    assert driver.browser.events == []


def test_offscreen_business_field_is_found_then_exact_value_is_entered(model):
    class ScrollDOM(DOM):
        scrolled = False

        def observe(self, screenshot=False):
            page = super().observe(screenshot)
            if not self.scrolled:
                page["actions"] = [
                    page["actions"][1],
                    {"id": "scroll_down", "kind": "scroll", "label": "Scroll down", "delta": 560},
                ]
                page["semantics"] = {"editable_fields": [{"label": "Party size", "scroll_direction": "down"}]}
            return page

        def act(self, action, page, text=None):
            super().act(action, page, text)
            if action["kind"] == "scroll":
                self.scrolled = True

    client = Decisions(
        lambda s, q: {
            "field_0": "offscreen_0" if "offscreen_0" in q.get("field_0", {}).get("criteria", {}) else "1",
            "submission": "2",
        }
    )
    driver = JevBrowser(client, browser_factory=ScrollDOM)
    driver.reset("http://demo.test")
    driver.pursue("Reserve places", fields=model.data_sets["Reservation"], data={"Places": 5})
    assert driver.browser.events == [("scroll", None), ("fill", "5"), ("click", None)]


def test_offscreen_field_without_supported_scroll_stops_before_mutation(model):
    client = Decisions(lambda *_: {"field_0": "offscreen_0"})
    driver = JevBrowser(client, browser_factory=DOM)
    driver.reset("http://demo.test")
    page = driver.observe()
    page["semantics"] = {"editable_fields": [{"label": "Other field", "scroll_direction": "down"}]}
    with pytest.raises(Inconclusive, match="outside the supported scroll area"):
        driver._data_action(page, "Reserve", model.data_sets["Reservation"], {"Places": 5}, [], set())
    assert driver.browser.events == []


def test_visible_field_can_be_filled_while_another_binding_is_offscreen(model):
    class MixedDOM(DOM):
        def __init__(self, url):
            super().__init__(url)
            self.scrolled = False
            self.values = {76: "", 77: ""}

        def observe(self, screenshot=False):
            page = super().observe(screenshot)
            page["actions"][0].update(label="Customer", value=self.values[76])
            if self.scrolled:
                page["actions"].insert(
                    1,
                    {
                        "node": 77,
                        "kind": "fill",
                        "role": "textbox",
                        "label": "Party size",
                        "value": self.values[77],
                    },
                )
            else:
                page["actions"].append({"id": "scroll_down", "kind": "scroll", "label": "Scroll down"})
                page["semantics"] = {"editable_fields": [{"label": "Party size", "scroll_direction": "down"}]}
            return page

        def act(self, action, page, text=None):
            super().act(action, page, text)
            if action["kind"] == "fill":
                self.values[action["node"]] = text
            if action["kind"] == "scroll":
                self.scrolled = True

    def choose(state, questions):
        criteria = questions.get("field_1", {}).get("criteria", {})
        return {
            "field_0": "1",
            "field_1": "offscreen_0" if "offscreen_0" in criteria else "2",
            "submission": "3",
        }

    fields = {"Customer": {"description": "The customer's name"}, **model.data_sets["Reservation"]}
    driver = JevBrowser(Decisions(choose), browser_factory=MixedDOM)
    driver.reset("http://demo.test")
    driver.pursue("Reserve", fields=fields, data={"Customer": "Example", "Places": 5})
    assert driver.browser.events == [("fill", "Example"), ("scroll", None), ("fill", "5"), ("click", None)]


def test_outcome_checks_do_not_receive_irrelevant_input_comparison_context(model):
    model.states["accepted"]["properties"]["business"]["rules"] = [
        "The displayed count equals the submitted Places."
    ]

    def choose(state, questions):
        return {
            "state": "s1",
            **{
                key: "comparison" if "equals the submitted" in q["instructions"] else "outcome"
                for key, q in questions.items()
                if key.startswith("kind_")
            },
        }

    client = Decisions(choose)
    result = Oracle(client, model).check(DOM("http://demo.test").observe(), "accepted", {"Places": 2})
    assert result["status"] == "PASS"
    outcome = next(s for s, q in client.requests if "rule_0" in q)
    comparison = next(s for s, q in client.requests if "rule_1" in q)
    assert "submitted_business_data" not in outcome
    assert all("value" not in e for e in outcome["elements"])
    assert comparison["submitted_business_data"] == {"Places": 2}
    assert comparison["elements"][0]["value"] == "old value"


@pytest.mark.parametrize("choice,expected", [("message_0", "PASS"), ("NONE", "FAIL")])
def test_validation_check_uses_actual_messages_by_field_meaning(model, choice, expected):
    def choose(state, questions):
        return {
            "state": "s2",
            **{
                k: choice
                for k, q in questions.items()
                if k.startswith("rule_") and "message_0" in q["criteria"]
            },
        }

    client = Decisions(choose)
    page = DOM("http://demo.test").observe()
    page["semantics"] = {
        "invalid_fields": [
            {"label": "Party size", "validation_messages": ["Use a whole number from one to four."]}
        ]
    }
    result = Oracle(client, model).check(
        page, "rejected", {"Places": 5}, [{"field": "Places", "description": "How many people attend"}]
    )
    assert result["status"] == expected
    questions = next(q for s, q in client.requests if "rule_2" in q)
    assert "Party size" in questions["rule_2"]["criteria"]["message_0"]
    assert "How many people attend" in questions["rule_2"]["instructions"]


@pytest.mark.parametrize("allow_trim", [True, False])
def test_only_declared_trim_is_allowed_after_literal_entry(model, allow_trim):
    class TrimmingDOM(DOM):
        def act(self, action, page, text=None):
            super().act(action, page, text)
            if action["kind"] == "fill":
                self.value = text.strip()

    fields = {"Places": {**model.data_sets["Reservation"]["Places"], "trim spaces": allow_trim}}
    driver = JevBrowser(Decisions(policy_for("2")), browser_factory=TrimmingDOM)
    driver.reset("http://demo.test")
    if allow_trim:
        driver.pursue("Reserve", fields=fields, data={"Places": " 2 "})
        assert driver.browser.events == [("fill", " 2 "), ("click", None)]
    else:
        with pytest.raises(Inconclusive, match="exact test data"):
            driver.pursue("Reserve", fields=fields, data={"Places": " 2 "})
        assert driver.browser.events == [("fill", " 2 ")]


def test_trim_equivalent_prefilled_value_does_not_skip_testing_literal_spaces(model):
    driver = JevBrowser(Decisions(policy_for(" 2 ")), browser_factory=DOM)
    driver.reset("http://demo.test")
    driver.browser.value = "2"
    driver.pursue("Reserve", fields=model.data_sets["Reservation"], data={"Places": " 2 "})
    assert driver.browser.events == [("fill", " 2 "), ("click", None)]


def test_global_audit_uses_observed_scenarios_instead_of_a_pass_label(model):
    client = Decisions(lambda *_: {"global_0": "met"})
    oracle = Oracle(client, model)
    records = [
        {
            "status": "PASS",
            "observed": "rejected",
            "expected": "rejected",
            "input": {"Places": 5},
            "violations": [{"field": "Places", "violations": ["above maximum"]}],
            "evidence": {
                "text": "Seats must be between one and four.",
                "semantics": {"alerts": ["Seats must be between one and four."]},
            },
            "checks": [{"rule": "An explanation identifies the rejected field", "status": "met"}],
        }
    ]
    result = oracle.audit_globals(records, model.business["rules"])
    assert result[0]["status"] == "met"
    evidence, questions = client.requests[-1]
    scenario = evidence["executed_scenarios"][0]
    assert scenario["input_violations"][0]["field"] == "Places"
    assert scenario["validation_alerts"] == ["Seats must be between one and four."]
    assert "status" not in scenario
    assert "At least one applicable scenario" in questions["global_0"]["instructions"]


@pytest.mark.parametrize(
    "focused,confidence,status",
    [
        ("met", 1, "PASS"),
        ("broken", 1, "FAIL"),
        ("uncertain", 1, "INCONCLUSIVE"),
        ("met", 0.7, "INCONCLUSIVE"),
    ],
)
def test_uncertain_batched_state_rule_gets_one_focused_check(model, focused, confidence, status):
    class Focused(Decisions):
        def ask(self, state, questions):
            result = super().ask(state, questions)
            if "rule_1" in questions:
                result["answers"]["rule_1"] = answer(
                    focused if len(questions) == 1 else "met",
                    questions["rule_1"]["criteria"],
                    confidence=confidence if len(questions) == 1 else 0.83,
                )
            return result

    client = Focused(lambda *_: {"state": "s0"})
    oracle = Oracle(client, model)
    if status == "INCONCLUSIVE":
        with pytest.raises(Inconclusive) as error:
            oracle.check(DOM("http://demo.test").observe(), "form")
        result = error.value.result
    else:
        result = oracle.check(DOM("http://demo.test").observe(), "form")
    assert result["status"] == status
    assert len(client.requests) == 3
    batch_evidence, batch_questions = client.requests[1]
    focused_evidence, focused_questions = client.requests[2]
    assert focused_evidence == batch_evidence
    assert focused_questions == {"rule_1": batch_questions["rule_1"]}
    assert result["checks"][1]["initial_answer"]["confidence"] == 0.83
    assert result["checks"][1]["focused_check"] is True
    assert client.threshold == 0.85


def test_known_failure_never_gets_reconsidered_or_overridden(model):
    client = Decisions(lambda *_: {"state": "s0", "rule_0": "broken", "rule_1": "uncertain"})
    result = Oracle(client, model).check(DOM("http://demo.test").observe(), "form")
    assert result["status"] == "FAIL"
    assert len(client.requests) == 2


def test_focused_check_budget_failure_preserves_original_verdict(model):
    class Exhausted(Decisions):
        def ask(self, state, questions):
            if set(questions) == {"rule_1"}:
                raise Inconclusive("The model-call budget is exhausted")
            return super().ask(state, questions)

    client = Exhausted(lambda *_: {"state": "s0", "rule_1": "uncertain"})
    with pytest.raises(Inconclusive) as error:
        Oracle(client, model).check(DOM("http://demo.test").observe(), "form")
    check = error.value.result["checks"][1]
    assert check["status"] == "uncertain"
    assert "budget" in check["reason"]
    assert check["initial_answer"]["choice"] == "uncertain"


def test_provider_refusal_is_not_retried_as_a_focused_check(model):
    class Refusal(Decisions):
        def ask(self, state, questions):
            result = super().ask(state, questions)
            if "rule_1" in questions:
                result["answers"]["rule_1"] = {"refusal": "cannot judge"}
            return result

    client = Refusal(lambda *_: {"state": "s0"})
    with pytest.raises(Inconclusive):
        Oracle(client, model).check(DOM("http://demo.test").observe(), "form")
    assert len(client.requests) == 2


def test_uncertain_rule_kind_keeps_reference_data_without_inventing_a_comparison(model):
    rule = "The customer can supply a number of places."
    model.states["form"]["properties"]["business"]["rules"] = [rule]
    client = Decisions(lambda *_: {"state": "s0"})
    oracle = Oracle(client, model)
    oracle.rule_kinds[rule] = "uncertain"
    result = oracle.check(DOM("http://demo.test").observe(), "form", {"Places": 2})
    assert result["status"] == "PASS"
    context, questions = next((s, q) for s, q in client.requests if "rule_1" in q)
    assert context["submitted_business_data"] == {"Places": 2}
    assert "Expected submitted values" not in json.dumps(questions["rule_1"])
    assert "does not require it to contain a previously submitted value" in json.dumps(questions["rule_1"])


@pytest.mark.parametrize("value", ["5", "", "not a number"])
def test_offscreen_submit_scrolls_without_losing_or_repairing_entered_values(model, value):
    class LongForm(DOM):
        scrolled = False

        def observe(self, screenshot=False):
            page = super().observe(screenshot)
            if self.scrolled:
                page["actions"] = [page["actions"][1]]
                page["semantics"] = {
                    "editable_fields": [
                        {
                            "label": "Party size",
                            "scroll_direction": "up",
                            "current_value": self.value,
                        }
                    ]
                }
            else:
                page["actions"] = [
                    page["actions"][0],
                    {
                        "id": "scroll_down",
                        "kind": "scroll",
                        "label": "Scroll down",
                        "delta": 560,
                    },
                ]
                page["semantics"] = {
                    "available_buttons": [
                        {
                            "label": "Continue",
                            "scroll_direction": "down",
                            "in_viewport": False,
                        }
                    ]
                }
            return page

        def act(self, action, page, text=None):
            super().act(action, page, text)
            if action["kind"] == "scroll":
                self.scrolled = True

    def choose(state, questions):
        binding = questions.get("field_0", {}).get("criteria", {})
        submission = questions.get("submission", {}).get("criteria", {})
        return {
            "field_0": "offscreen_0" if "offscreen_0" in binding else "1",
            "submission": "offscreen_submit_0" if "offscreen_submit_0" in submission else "1",
        }

    client = Decisions(choose)
    driver = JevBrowser(client, browser_factory=LongForm)
    driver.reset("http://demo.test")
    driver.pursue("Reserve", fields=model.data_sets["Reservation"], data={"Places": value})
    assert driver.browser.events == [("fill", value), ("scroll", None), ("click", None)]
    assert driver.trace[-1]["submission"]
    for evidence, questions in client.requests:
        if "field_0" in questions:
            assert all("current_value" not in f for f in evidence["accessibility"].get("editable_fields", []))


def test_changed_offscreen_value_stops_before_submission(model):
    driver = JevBrowser(Decisions(lambda *_: {"field_0": "offscreen_0"}), browser_factory=DOM)
    driver.reset("http://demo.test")
    page = driver.observe()
    page["semantics"] = {
        "editable_fields": [
            {
                "label": "Party size",
                "scroll_direction": "up",
                "current_value": "4",
            }
        ]
    }
    with pytest.raises(Inconclusive, match="retain the exact test data"):
        driver._data_action(page, "Reserve", model.data_sets["Reservation"], {"Places": "5"}, [], {"Places"})
    assert driver.browser.events == []


def test_offscreen_submission_without_supported_scroll_never_clicks(model):
    client = Decisions(lambda *_: {"field_0": "1", "submission": "offscreen_submit_0"})
    driver = JevBrowser(client, browser_factory=DOM)
    driver.reset("http://demo.test")
    page = driver.observe()
    page["actions"][0]["value"] = "5"
    page["semantics"] = {"available_buttons": [{"label": "Continue", "scroll_direction": "down"}]}
    with pytest.raises(Inconclusive, match="submission control is outside"):
        driver._data_action(page, "Reserve", model.data_sets["Reservation"], {"Places": "5"}, [], {"Places"})
    assert driver.browser.events == []


def test_explicit_client_credentials_and_model_override_ambient_environment(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "wrong-ambient-key")
    monkeypatch.setenv("TYPESAFE_MODEL", "wrong-ambient-model")
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"answers": {}})

    client = JevClient(
        api_key="explicit-property-key",
        model="explicit-property-model",
        http=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    try:
        client.ask({"text": "synthetic page"}, {})
        assert requests[0].headers["Authorization"] == "Bearer explicit-property-key"
        assert json.loads(requests[0].content)["model"] == "explicit-property-model"
        assert "explicit-property-key" not in json.dumps(client.decisions)
    finally:
        client.close()


def test_malformed_target_answer_is_not_retried_as_an_equivalent_control():
    class RefusedTarget(Decisions):
        def ask(self, state, questions):
            result = super().ask(state, questions)
            if "click_target" in questions:
                result["answers"]["click_target"] = {"refusal": "cannot decide"}
            return result

    client = RefusedTarget(lambda *_: {"operation": "CLICK", "click_target": "2"})
    driver = JevBrowser(client, browser_factory=DOM)
    driver.reset("http://demo.test")
    with pytest.raises(Inconclusive, match="Invalid or refused"):
        driver._decision(driver.observe(), "Continue", [], {})
    assert len(client.requests) == 1
    assert not driver.browser.events


def test_global_audit_preserves_different_observations_of_the_same_state(model):
    client = Decisions(lambda *_: {})
    common = {"status": "PASS", "observed": "accepted", "expected": "accepted", "checks": []}
    records = [
        {**common, "evidence": {"text": "First observed total: 90"}},
        {**common, "evidence": {"text": "Later observed total: 135"}},
    ]
    Oracle(client, model).audit_globals(records + [records[0]], model.business["rules"])
    scenarios = client.requests[0][0]["executed_scenarios"]
    assert len(scenarios) == 2
    assert [s["visible_text"] for s in scenarios] == ["First observed total: 90", "Later observed total: 135"]


def test_screenshot_uses_owned_tab_and_never_calls_jev(tmp_path):
    import base64

    calls = []
    png = b"\x89PNG\r\n\x1a\nunit-image"

    class CapturingDOM(DOM):
        def call(self, method, **kwargs):
            calls.append((method, kwargs))
            return {"data": base64.b64encode(png).decode()}

    client = Decisions(lambda *_: pytest.fail("Screenshot must not use Jev"))
    driver = JevBrowser(client, browser_factory=CapturingDOM)
    with pytest.raises(RuntimeError, match="No active test tab"):
        driver.screenshot(tmp_path / "empty.png")
    driver.reset("http://example.test")
    driver.screenshot(tmp_path / "shot.png")
    assert (tmp_path / "shot.png").read_bytes() == png
    assert calls == [("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})]
    assert not client.requests


def test_uncertain_navigation_can_scroll_to_observed_link_without_editing_values():
    class NavigationDOM(DOM):
        scrolled = False
        arrived = False

        def observe(self, screenshot=False):
            page = super().observe(screenshot=False)
            if self.arrived:
                page["text"] = "Edit your profile"
            elif self.scrolled:
                page["actions"] = [
                    {
                        "id": "revise",
                        "node": 20,
                        "kind": "click",
                        "role": "link",
                        "label": "Clear errors and revise",
                    }
                ]
            else:
                page["actions"].append({"id": "scroll_down", "kind": "scroll", "label": "Scroll down"})
                page["semantics"] = {
                    "available_links": [
                        {"label": "Clear errors and revise", "in_viewport": False, "scroll_direction": "down"}
                    ]
                }
            return page

        def act(self, action, page, text=None):
            super().act(action, page, text)
            if action["kind"] == "scroll":
                self.scrolled = True
            elif action["node"] == 20:
                self.arrived = True

    class UncertainOperation(Decisions):
        def ask(self, state, questions):
            result = super().ask(state, questions)
            if "operation" in questions and state["accessibility"].get("available_links"):
                result["answers"]["operation"]["confidence"] = 0.7
            return result

    def choose(state, questions):
        if "navigation_control" in questions:
            return {"navigation_control": "offscreen_0"}
        if state["page"]["text"] == "Edit your profile":
            return {"operation": "DONE"}
        return {"operation": "CLICK", "click_target": "1"}

    client = UncertainOperation(choose)
    driver = JevBrowser(client, browser_factory=NavigationDOM)
    driver.reset("http://demo.test")
    driver.pursue("Clear errors and revise the profile")
    assert driver.browser.events == [("scroll", None), ("click", None)]
    assert driver.browser.value == "old value"
    assert driver.browser.arrived
    assert sum("navigation_control" in q for _, q in client.requests) == 1


@pytest.mark.parametrize("focused", ["uncertain", "refused", "unavailable"])
def test_focused_navigation_never_promotes_uncertainty_or_refusal_to_success(focused):
    class Client(Decisions):
        def ask(self, state, questions):
            result = super().ask(state, questions)
            if "operation" in questions:
                result["answers"]["operation"]["confidence"] = 0.7
            elif focused == "uncertain":
                result["answers"]["navigation_control"]["confidence"] = 0.7
            elif focused == "refused":
                result["answers"]["navigation_control"] = {"refusal": "no answer"}
            return result

    client = Client(
        lambda *_: {
            "operation": "CLICK",
            "navigation_control": "UNAVAILABLE" if focused == "unavailable" else "2",
        }
    )
    driver = JevBrowser(client, browser_factory=DOM)
    driver.reset("http://demo.test")
    with pytest.raises(Inconclusive):
        driver.pursue("Continue")
    assert not driver.browser.events
    assert len(client.requests) == 2


@pytest.mark.parametrize("refused", [True, False])
def test_navigation_focus_does_not_retry_refused_operations_or_data_entry_decisions(refused):
    class Client(Decisions):
        def ask(self, state, questions):
            result = super().ask(state, questions)
            result["answers"]["operation"] = (
                {"refusal": "no answer"} if refused else {**result["answers"]["operation"], "confidence": 0.7}
            )
            return result

    client = Client(lambda *_: {"operation": "CLICK"})
    driver = JevBrowser(client, browser_factory=DOM)
    driver.reset("http://demo.test")
    with pytest.raises(Inconclusive):
        driver._decision(driver.observe(), "Submit the supplied data", [], {} if refused else {"Places": {}})
    assert len(client.requests) == 1
    assert not driver.browser.events


def test_large_global_audit_shares_rule_text_without_losing_observations(model):
    rule = "A detailed verified business requirement. " * 30
    records = [
        {
            "status": "PASS",
            "observed": "accepted",
            "expected": "accepted",
            "input": {"Places": i},
            "evidence": {"text": f"Observation {i}: " + "x" * 1200},
            "checks": [{"rule": rule, "status": "met"}],
        }
        for i in range(80)
    ]
    client = Decisions(lambda *_: {"global_0": "met"})
    Oracle(client, model).audit_globals(records, model.business["rules"])
    saved_observations = []
    for context, _ in client.requests:
        if "executed_scenarios" not in context:
            continue
        assert len(json.dumps(context)) <= 16000
        definitions = context["verified_requirement_definitions"]
        for saved in context["executed_scenarios"]:
            assert [definitions[r] for r in saved["verified_requirement_ids"]] == [rule]
            saved_observations.append(saved)
    assert len(saved_observations) == len(records)
    for original, saved in zip(records, saved_observations, strict=True):
        assert saved["submitted_values"] == original["input"]
        assert saved["visible_text"] == original["evidence"]["text"]


def test_global_audit_still_stops_when_distinct_evidence_exceeds_budget(model):
    records = [
        {
            "status": "PASS",
            "observed": "accepted",
            "expected": "accepted",
            "input": {"Places": i, "Extra": "x" * 1000},
            "evidence": {"text": f"Observation {i}: " + "x" * 1700},
            "checks": [{"rule": "A verified requirement", "status": "met"}],
        }
        for i in range(80)
    ]
    client = Decisions(lambda *_: {})
    with pytest.raises(Inconclusive, match="evidence budget"):
        Oracle(client, model).audit_globals(records, model.business["rules"])
    assert not client.requests


@pytest.mark.parametrize(
    "verdicts, expected",
    [
        (["met", "broken"], "broken"),
        (["met", "uncertain"], "uncertain"),
        (["not_applicable", "met"], "met"),
        (["not_applicable", "not_applicable"], "uncertain"),
        (["met", "met"], "met"),
    ],
)
def test_global_audit_aggregates_batches_without_hiding_later_failures(model, verdicts, expected):
    records = [
        {
            "status": "PASS",
            "observed": "accepted",
            "expected": "accepted",
            "evidence": {"text": f"Observation {i}: " + "x" * 1500},
        }
        for i in range(16)
    ]
    choices = iter(verdicts)
    client = Decisions(lambda _, q: {"global_0": next(choices)} if "global_0" in q else {})
    result = Oracle(client, model).audit_globals(records, model.business["rules"])
    assert sum("global_0" in q for _, q in client.requests) == 2
    assert result[0]["status"] == expected
    assert [b["status"] for b in result[0]["batch_answers"]] == verdicts


def test_validation_audit_keeps_accepted_inputs_and_does_not_assume_rejection(model):
    records = [
        {
            "status": "PASS",
            "observed": "accepted" if i % 2 == 0 else "rejected",
            "expected": "accepted" if i % 2 == 0 else "rejected",
            "input": {"Places": 5},
            "violations": [{"field": "Places", "violations": ["above maximum"]}],
            "evidence": {"text": f"Observation {i}: " + "x" * 1500},
        }
        for i in range(16)
    ]

    def choose(state, questions):
        if "input_scope_0" in questions:
            return {"input_scope_0": "data_0"}
        return {"global_0": "broken"}

    client = Decisions(choose)
    result = Oracle(client, model).audit_globals(records, model.business["rules"])
    assert result[0]["status"] == "broken"
    assert result[0]["evidence_states"] == ["accepted", "rejected"]
    audited = [state for state, q in client.requests if "global_0" in q]
    assert any(s["observed_state"] == "accepted" for state in audited for s in state["executed_scenarios"])


def test_global_audit_with_no_observations_cannot_pass(model):
    client = Decisions(lambda *_: {"global_0": "met"})
    result = Oracle(client, model).audit_globals([], model.business["rules"])
    assert all(check["status"] == "uncertain" for check in result)
    assert not client.requests


def test_uncertain_validation_scope_preserves_generic_evidence_selection(model):
    class Client(Decisions):
        def ask(self, state, questions):
            result = super().ask(state, questions)
            result["answers"]["input_scope_0"]["confidence"] = 0.7
            return result

    client = Client(lambda *_: {"input_scope_0": "data_0"})
    assert Oracle(client, model)._validation_scopes(model.business["rules"]) == [None]
