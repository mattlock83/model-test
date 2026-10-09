import json

import pytest

from testwalker.graphwalker import generate_path, parse_path
from testwalker.model import load_model
from testwalker.resources import ROOT


def transcript(model, ids):
    return "\n".join(
        json.dumps(
            {
                "currentElementName": identifier,
                "currentElementId": identifier,
                "modelId": model.graph["id"],
            }
        )
        for identifier in ids
    )


@pytest.fixture
def ids():
    return ["form", "submit", "accepted", "another", "form", "submit_invalid", "rejected", "correct", "form"]


def test_final_edge_gets_a_verification_checkpoint(model, ids):
    path = parse_path(model, transcript(model, ids[:-1]))
    assert path[-1]["id"] == "form" and path[-1]["kind"] == "state"


def test_unverified_short_or_disconnected_paths_are_rejected(model, ids):
    with pytest.raises(ValueError, match="coverage"):
        parse_path(model, transcript(model, ids[:3]))
    with pytest.raises(ValueError, match="disconnected"):
        parse_path(model, transcript(model, ["form", "correct"]))
    with pytest.raises(ValueError, match="step budget"):
        parse_path(model, transcript(model, ids), max_steps=3)


def test_graph_identity_cannot_be_spoofed_by_matching_name(model, ids):
    output = transcript(model, ids).replace('"currentElementId": "submit"', '"currentElementId": "other"')
    with pytest.raises(ValueError, match="mismatched"):
        parse_path(model, output)


@pytest.mark.parametrize("name", ["booking", "feedback"])
def test_actual_rust_graphwalker_reads_business_metadata_and_covers_every_edge(
    name, tmp_path, native_graphwalker
):
    model = load_model(ROOT / f"models/{name}.json")
    saved = tmp_path / "model.json"
    saved.write_text(json.dumps(model.document))
    first = generate_path(model, saved, seed=42, binary=native_graphwalker)
    second = generate_path(model, saved, seed=42, binary=native_graphwalker)
    assert [i["id"] for i in first] == [i["id"] for i in second]
    assert {i["id"] for i in first if i["kind"] == "edge"} == set(model.edges)
    assert {i["id"] for i in first if i["kind"] == "state"} == set(model.states)
