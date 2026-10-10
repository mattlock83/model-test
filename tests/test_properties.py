import pytest

from testwalker import properties
from testwalker.errors import Defect, Inconclusive
from testwalker.input_strategies import StrategyProvider


def test_boundaries_include_every_partition_even_with_one_generated_example(model):
    recorded = []
    properties.exercise(
        model.data_sets["Reservation"],
        lambda *_: {"status": "PASS"},
        recorded.append,
        cases=1,
        mode="boundaries",
    )
    values = [r["input"]["Places"] for r in recorded if r["source"].startswith("boundary")]
    assert all(value in values for value in ["", 0, 1, 2, 3, 4, 5, "1.5", "not a number"])
    assert any(r["violations"] for r in recorded)
    assert any(not r["violations"] for r in recorded)


def test_real_hegel_shrinks_reproducible_failure(model):
    provider = StrategyProvider(
        lambda context, default: (
            {"kind": "integer", "minimum": 0, "maximum": 100}
            if context["field_name"]
            else {"kind": "object", "fields": {"Places": {"kind": "integer", "minimum": 0, "maximum": 100}}}
        )
    )
    recorded = []

    def execute(data, invalid):
        return {"status": "FAIL" if data["Places"] >= 5 else "PASS"}

    with pytest.raises(Defect) as failure:
        properties.exercise(
            model.data_sets["Reservation"],
            execute,
            recorded.append,
            cases=30,
            mode="generated",
            strategy_provider=provider,
        )
    assert failure.value.case["input"] == {"Places": 5}
    assert recorded[-1]["status"] == "FAIL"


def test_provider_failure_is_latched_without_more_calls_or_shrinking(model):
    calls, recorded = [], []

    def execute(*args):
        calls.append(args)
        raise Inconclusive("provider unavailable")

    with pytest.raises(Inconclusive, match="provider unavailable"):
        properties.exercise(model.data_sets["Reservation"], execute, recorded.append, mode="generated")
    assert len(calls) == 1
    assert [r["status"] for r in recorded] == ["INCONCLUSIVE"]


def test_email_length_boundaries_isolate_length_from_format():
    from testwalker.model import violations

    field = {
        "description": "Contact",
        "type": "email",
        "minimum length": 8,
        "maximum length": 12,
        "example": "a@ex.test",
    }
    values = properties.boundaries(field)
    for size in [7, 8, 9, 11, 12, 13]:
        sample = next(value for value in values if len(value) == size and "@" in value)
        problems = violations({"Contact": field}, {"Contact": sample})
        assert "valid email required" not in str(problems)
        assert bool(problems) == (size in [7, 13])


def test_exact_boundaries_are_unique_and_change_only_one_field(model):
    field = model.data_sets["Reservation"]["Places"]
    fields = {"Adults": field, "Children": field}
    plan = properties.plan_cases(fields, cases=200, mode="boundaries")
    assert all(phase["kind"] == "boundary" for phase in plan)
    inputs = [phase["input"] for phase in plan]
    assert len(inputs) == len({tuple(data.items()) for data in inputs})
    assert inputs.count({"Adults": 2, "Children": 2}) == 1
    assert all(sum(value != 2 for value in data.values()) <= 1 for data in inputs)
    for name in fields:
        assert any(data[name] == "   " for data in inputs)
        for value in [0, 1, 2, 3, 4, 5]:
            assert any(data[name] == value for data in inputs)
    assert any("minimum -1" in phase["source"] for phase in plan)


def test_boundaries_only_use_declared_text_limits():
    field = {"type": "text", "example": "Alex", "required": True, "minimum length": 2}
    values = properties.boundaries(field)
    assert values == ["Alex", "", "   ", "x", "xx", "xxx", "  Alex  "]
    assert properties.boundaries({"type": "text", "example": "Alex"}) == ["Alex", "", "  Alex  "]


def test_decimal_boundaries_do_not_add_random_floats_or_whole_number_constraint():
    field = {"type": "number", "example": 2, "minimum": 0.1, "maximum": 5.1}
    assert properties.boundaries(field) == [2, "", -0.9, 0.1, 1.1, 4.1, 5.1, 6.1, "not a number"]


def test_unknown_choice_cannot_accidentally_be_allowed():
    field = {"type": "choice", "example": "unlisted choice", "options": ["unlisted choice", "other"]}
    plan = properties.plan_cases({"Option": field}, mode="boundaries")
    assert any(phase["violations"] for phase in plan)
    assert plan[-1]["input"] == {"Option": "unlisted choice!"}


@pytest.mark.parametrize("mode", ["all", "generated", "boundaries", "focused", "none"])
def test_input_modes_select_only_requested_work(model, mode):
    recorded = []
    properties.exercise(
        model.data_sets["Reservation"], lambda *_: {"status": "PASS"}, recorded.append, cases=1, mode=mode
    )
    sources = {entry["source"].split(":")[0] for entry in recorded}
    assert (
        sources
        == {
            "all": {"generated", "boundary"},
            "generated": {"generated"},
            "boundaries": {"boundary"},
            "focused": {"focused"},
            "none": set(),
        }[mode]
    )


def test_default_uses_native_hegel_and_preserves_each_partition(model):
    fields = model.data_sets["Reservation"]
    plan = properties.plan_cases(fields)
    recorded = []

    def execute(data, invalid):
        return {"status": "PASS"}

    properties.exercise(fields, execute, recorded.append)
    assert len(recorded) == len(plan) == 10
    assert all(phase["kind"] == "generated" and "input" not in phase for phase in plan)
    assert [row["violations"] for row in recorded] == [phase["violations"] for phase in plan]
    assert [row["input"]["Places"] for row in recorded][3:8] == [0, 1, 3, 4, 5]


def test_focused_generation_varies_inputs_and_shrinks_within_selected_violation(model):
    fields = model.data_sets["Reservation"]
    policy = {"defaults": {"radius": 50}}
    phase = next(
        p for p in properties.plan_cases(fields, policy=policy, cases=20) if p["partition"] == "maximum +1"
    )
    recorded = []
    properties.exercise(fields, lambda *_: {"status": "PASS"}, recorded.append, planned=[phase])
    values = {row["input"]["Places"] for row in recorded}
    assert len(values) > 1 and all(5 <= value <= 54 for value in values)
    recorded.clear()
    with pytest.raises(Defect) as failure:
        properties.exercise(
            fields,
            lambda data, _: {"status": "FAIL" if data["Places"] >= 8 else "PASS"},
            recorded.append,
            planned=[phase],
        )
    assert failure.value.case["input"] == {"Places": 8}
    assert all(row["violations"] == phase["violations"] for row in recorded)


def test_all_bundled_datasets_have_satisfiable_focused_strategies():
    from testwalker.model import load_model
    from testwalker.resources import asset

    for name in ("booking", "feedback", "trailhead"):
        model = load_model(asset(f"models/{name}.json"))
        for fields in model.data_sets.values():
            plan = properties.plan_cases(fields, cases=3)
            recorded = []
            properties.exercise(fields, lambda *_: {"status": "PASS"}, recorded.append, planned=plan)
            for phase in plan:
                attempts = [row for row in recorded if row["source"] == phase["source"]]
                assert attempts
                assert all(row["violations"] == phase["violations"] for row in attempts)


def test_provider_cannot_change_the_intended_violation_or_spend_calls_on_discarded_draws(model):
    from testwalker.input_strategies import StrategyProvider

    fields = model.data_sets["Reservation"]
    phase = next(p for p in properties.plan_cases(fields) if p["partition"] == "maximum +1")
    provider = StrategyProvider(lambda context, default: {"kind": "literal", "value": 2})
    recorded = []
    with pytest.raises(Inconclusive, match="Hegel could not establish"):
        properties.exercise(
            fields,
            lambda *_: pytest.fail("Filtered input must not reach browser"),
            recorded.append,
            planned=[phase],
            strategy_provider=provider,
        )
    assert recorded == []
