import pytest
from hypothesis import find
from hypothesis import strategies as st

from testwalker.input_strategies import StrategyProvider, field_options, validate_policy


def test_per_field_policy_overrides_defaults_without_changing_model(model):
    policy = validate_policy(
        {
            "defaults": {"radius": 5},
            "data sets": {"Reservation": {"Places": {"cases": 3, "strategy": "boundary"}}},
        },
        model.data_sets,
    )
    options = field_options(policy, "Reservation", "Places", 2)
    assert options["cases"] == 3 and options["radius"] == 5 and options["strategy"] == "boundary"
    assert "cases" not in model.data_sets["Reservation"]["Places"]


@pytest.mark.parametrize(
    "policy",
    [
        [],
        {"unexpected": 1},
        {"defaults": {"cases": True}},
        {"defaults": {"radius": 0}},
        {"defaults": {"strategy": "unknown"}},
        {"defaults": {"alphabet": " "}},
        {"data sets": {"Wrong": {}}},
        {"data sets": {"Reservation": {"Wrong": {}}}},
    ],
)
def test_bad_strategy_configuration_is_rejected(model, policy):
    with pytest.raises(ValueError):
        validate_policy(policy, model.data_sets)


def test_python_provider_returns_a_real_strategy_and_has_provenance(tmp_path):
    path = tmp_path / "inputs.py"
    path.write_text("""from __future__ import annotations
from dataclasses import dataclass
from hypothesis import strategies as st
@dataclass
class Range:
    low: int = 6
    high: int = 9
def input_strategy(context, default):
    context["field"]["example"] = 999
    bounds = Range()
    return st.integers(min_value=bounds.low, max_value=bounds.high)
""")
    provider = StrategyProvider.load(path)
    context = {"field": {"example": 2}}
    domain = provider.build(context, st.just(5))
    assert find(domain, lambda value: True) == 6
    assert context["field"]["example"] == 2
    assert provider.metadata["path"] == str(path)
    assert len(provider.metadata["sha256"]) == 64
    with pytest.raises(TypeError, match="SearchStrategy"):
        StrategyProvider(lambda *_: [1, 2]).build(context, domain)
