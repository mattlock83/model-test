import pytest
from hypothesis import strategies as st

from testwalker import properties
from testwalker.errors import Defect, Inconclusive


def test_boundaries_include_every_partition_even_with_one_generated_example(model):
    recorded = []
    properties.exercise(
        model.data_sets["Reservation"], lambda *_: {"status": "PASS"}, recorded.append, cases=1
    )
    values = [r["input"]["Places"] for r in recorded if r["source"].startswith("boundary")]
    assert all(value in values for value in ["", 0, 1, 2, 3, 4, 5, "1.5", "not a number"])
    assert any(r["violations"] for r in recorded)
    assert any(not r["violations"] for r in recorded)


def test_real_hypothesis_shrinks_reproducible_failure(model, monkeypatch):
    monkeypatch.setattr(properties, "strategy", lambda _: st.integers(0, 100))
    recorded = []

    def execute(data, invalid):
        return {"status": "FAIL" if data["Places"] >= 5 else "PASS"}

    with pytest.raises(Defect) as failure:
        properties.exercise(model.data_sets["Reservation"], execute, recorded.append, cases=30)
    assert failure.value.case["input"] == {"Places": 5}
    assert recorded[-1]["status"] == "FAIL"


def test_provider_failure_is_latched_without_more_calls_or_shrinking(model):
    calls, recorded = [], []

    def execute(*args):
        calls.append(args)
        raise Inconclusive("provider unavailable")

    with pytest.raises(Inconclusive, match="provider unavailable"):
        properties.exercise(model.data_sets["Reservation"], execute, recorded.append)
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


@pytest.mark.parametrize("mode", ["all", "generated", "boundaries", "none"])
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
            "none": set(),
        }[mode]
    )
