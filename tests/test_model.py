import copy

import pytest

from testwalker.model import load_model, setup_path, validate_model, violations
from testwalker.resources import ROOT


@pytest.mark.parametrize("name", ["booking", "feedback"])
def test_bundled_models_are_business_only_and_have_reachable_data_journeys(name):
    model = load_model(ROOT / f"models/{name}.json")
    assert validate_model(model.document).digest == model.digest
    for edge in model.edges.values():
        spec = edge["properties"]["business"]
        assert "selector" not in spec and "actions" not in spec
        if "data set" in spec:
            setup_path(model.graph, model.edges, edge["sourceVertexId"])


@pytest.mark.parametrize(
    "mutation",
    [
        lambda g: g["edges"][0]["properties"]["business"].update(selector="#submit"),
        lambda g: g["edges"][0]["properties"]["business"].update(
            intent="document.querySelector('#x').click()"
        ),
        lambda g: g["edges"][0].update(actions=["click()"]),
        lambda g: g["edges"][0].update(guard="ready == true"),
        lambda g: g["edges"][0]["properties"]["business"].update(example={"Places": 5}),
        lambda g: g["properties"]["business"]["data sets"]["Reservation"]["Places"].update(example=0),
        lambda g: g["edges"][0]["properties"]["business"].update(**{"rejected at": "accepted"}),
        lambda g: g["edges"][0].update(sourceVertexId="missing"),
    ],
)
def test_invalid_or_scripted_contracts_fail_before_browser_use(model, mutation):
    document = copy.deepcopy(model.document)
    mutation(document["models"][0])
    with pytest.raises(ValueError):
        validate_model(document)


@pytest.mark.parametrize(
    "value,valid",
    [
        (1, True),
        (4, True),
        (" 2 ", True),
        ("", False),
        (0, False),
        (5, False),
        ("1.5", False),
        ("2.0", False),
        ("nonsense", False),
        (True, False),
    ],
)
def test_independent_numeric_contract(model, value, valid):
    assert (not violations(model.data_sets["Reservation"], {"Places": value})) is valid


def test_setup_can_reach_a_correction_state_using_nominal_invalid_data(model):
    path = setup_path(model.graph, model.edges, "rejected")
    assert [e["id"] for e in path] == ["submit_invalid"]
    assert model.example(path[0]) == {"Places": 0}


def test_expanded_demo_models_menu_from_every_distinct_state():
    model = load_model(ROOT / "models/booking.json")
    destinations = {"home", "workshops", "studio", "visit"}
    transitions = {(edge["sourceVertexId"], edge["targetVertexId"]) for edge in model.edges.values()}
    for source in model.states:
        for destination in destinations - {source}:
            assert (source, destination) in transitions
    assert ("workshops", "details") in transitions
    assert len(model.states) == 8 and len(model.edges) == 35


def test_decimal_boundaries_do_not_round_an_invalid_input_into_range():
    fields = {"Amount": {"type": "number", "minimum": 0.1, "maximum": 0.1}}
    assert not violations(fields, {"Amount": "0.1000000000000000000000"})
    assert violations(fields, {"Amount": "0.1000000000000000000001"})[0]["violations"] == ["above maximum"]
    assert violations(fields, {"Amount": "0.0999999999999999999999"})[0]["violations"] == ["below minimum"]
