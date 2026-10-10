import copy

import pytest

from testwalker.rpc_contract import (
    ContractError,
    check,
    evaluate_rules,
    matching_states,
    pointer,
    resolve,
    validate_predicates,
    validate_rpc_model,
)


@pytest.fixture
def rpc_model():
    def state(identifier, response):
        return {
            "id": identifier,
            "properties": {
                "business": {"rules": ["The worker reports its phase."]},
                "rpc": {
                    "match": [{"path": "/response/result/phase", "equals": response}],
                    "rules": {
                        "The worker reports its phase.": [
                            {"path": "/response/result/phase", "equals": response}
                        ]
                    },
                },
            },
        }

    return {
        "properties": {
            "business": {"rules": ["Every response uses JSON-RPC 2.0."]},
            "rpc": {
                "version": 1,
                "reset": {"method": "core.info", "params": {}, "restart": True},
                "fixtures": {"seed": {"Places": 2}},
                "variables": {"phase": "ready"},
                "rules": {
                    "Every response uses JSON-RPC 2.0.": [{"path": "/response/jsonrpc", "equals": "2.0"}]
                },
                "callbacks": {
                    "target.execute": {
                        "set": {"phase": "done"},
                        "result": {"observed_state": {"$ref": "/variables/phase"}},
                    }
                },
            },
        },
        "vertices": [state("ready", "ready"), state("done", "done")],
        "edges": [
            {
                "id": "execute",
                "properties": {
                    "rpc": {
                        "request": {
                            "method": "run.start",
                            "params": {"places": {"$ref": "/input/Places"}},
                        },
                        "capture": {"last_phase": "/response/result/phase"},
                    }
                },
            }
        ],
    }


def test_references_preserve_invalid_inputs_and_copy_fixtures_without_interpolation():
    context = {
        "input": {"Places": None, "Enabled": False},
        "fixtures": {"model": {"a/b": [1, 2]}},
    }
    value = {
        "model": {"$ref": "/fixtures/model"},
        "values": [{"$ref": "/input/Places"}, {"$ref": "/input/Enabled"}],
        "literal": "${input/Places} {{do not execute}}",
    }
    result = resolve(value, context)
    assert result == {
        "model": {"a/b": [1, 2]},
        "values": [None, False],
        "literal": value["literal"],
    }
    result["model"]["a/b"].append(3)
    assert context["fixtures"]["model"]["a/b"] == [1, 2]
    assert value["model"] == {"$ref": "/fixtures/model"}


def test_json_pointer_escapes_empty_keys_and_exact_array_indexes():
    context = {"a/b": {"~": ["first", {"": "empty key"}]}}
    assert pointer(context, "/a~1b/~0/1/") == "empty key"
    assert pointer(context, "") is context
    for path in ["/a~1b/~0/-1", "/a~1b/~0/01", "/a~1b/~0/-", "/a~1b/~0/2"]:
        with pytest.raises(ContractError, match="Unresolved"):
            pointer(context, path)
        assert pointer(context, path, default=None) is None
    with pytest.raises(ContractError, match="escape"):
        pointer(context, "/a~2b")


def test_missing_references_raise_before_transport_but_fail_checks_with_diagnostics():
    with pytest.raises(ContractError, match="/input/Places"):
        resolve({"params": {"$ref": "/input/Places"}}, {"input": {}})
    outcome = check(
        [{"path": "/response/result", "equals": {"$ref": "/variables/missing"}}],
        {"response": {"result": 2}, "variables": {}},
    )[0]
    assert not outcome["passed"]
    assert outcome["actual"] == 2 and outcome["present"]
    assert "/variables/missing" in outcome["error"]


def test_absent_values_are_distinct_from_null_and_do_not_pass_negative_comparisons():
    outcomes = check(
        [
            {"path": "/response/missing", "exists": False},
            {"path": "/response/missing", "not_equals": 1},
            {"path": "/response/missing", "equals": None},
            {"path": "/response/result", "equals": None},
            {"path": "/response/result", "exists": True},
        ],
        {"response": {"result": None}},
    )
    assert [item["passed"] for item in outcomes] == [True, False, False, True, True]
    assert not outcomes[2]["present"] and outcomes[3]["present"]


@pytest.mark.parametrize(
    "actual, operation, expected, passed",
    [
        (True, "equals", 1, False),
        ({"value": True}, "equals", {"value": 1}, False),
        ([False], "contains", 0, False),
        (1.0, "equals", 1, True),
        (True, "type", "integer", False),
        (True, "type", "number", False),
        (False, "type", "boolean", True),
        (None, "type", "null", True),
        (1.5, "type", "number", True),
        (1.0, "type", "integer", False),
        ("abc", "length", {"min": 2, "max": 3}, True),
        ([1, 2], "length", {"exact": 2}, True),
        (3, "length", {"exact": 2}, False),
        ({"a": 1, "b": 2}, "length", {"max": 1}, False),
        ("capacity exceeded", "contains", "capacity", True),
        ({"status": "ready"}, "contains", "status", True),
        ("1", "contains", 1, False),
        ({"a": {"b": [1, 2]}, "c": 3}, "subset", {"a": {"b": [2]}}, True),
        ({"a": [True]}, "subset", {"a": [1]}, False),
        ([1, 2], "subset", [2, 3], False),
    ],
)
def test_json_predicates(actual, operation, expected, passed):
    outcome = check([{"path": "/response/result", operation: expected}], {"response": {"result": actual}})[0]
    assert outcome["passed"] is passed
    assert outcome["actual"] == actual and outcome["expected"] == expected


def test_assertion_expected_values_use_typed_refs_and_keep_all_failure_evidence():
    context = {
        "input": {"Places": 2},
        "variables": {"request_id": 7},
        "response": {"id": 8, "result": {"Places": 2}},
    }
    outcomes = check(
        [
            {"path": "/response/id", "equals": {"$ref": "/variables/request_id"}},
            {"path": "/response/result", "equals": {"Places": {"$ref": "/input/Places"}}},
        ],
        context,
    )
    assert [item["passed"] for item in outcomes] == [False, True]
    assert outcomes[0]["expected"] == 7 and outcomes[0]["actual"] == 8


@pytest.mark.parametrize(
    "predicate",
    [
        {"path": "/x"},
        {"path": "/x", "equals": 1, "type": "number"},
        {"path": "/x", "expression": "x == 1"},
        {"path": "x", "exists": True},
        {"path": "/x", "exists": 1},
        {"path": "/x", "type": "float"},
        {"path": "/x", "length": {}},
        {"path": "/x", "length": {"min": True}},
        {"path": "/x", "length": {"min": 2, "max": 1}},
        {"path": "/x", "length": {"exact": 2, "min": 1}},
        {"path": "/x", "equals": {"$ref": "/input/x", "fallback": 1}},
        {"path": "/x", "equals": {"$ref": "/environment/SECRET"}},
        {"path": "/x", "equals": float("nan")},
    ],
)
def test_malformed_predicates_fail_validation(predicate):
    with pytest.raises(ContractError):
        validate_predicates([predicate])


def test_model_validation_supports_raw_envelopes_and_binding_request_types(rpc_model):
    original = copy.deepcopy(rpc_model)
    assert validate_rpc_model(rpc_model) == rpc_model["properties"]["rpc"]
    assert rpc_model == original
    assert validate_rpc_model({"models": [rpc_model]}) == rpc_model["properties"]["rpc"]
    for request in [
        {"raw": "not valid json"},
        {"envelope": {"jsonrpc": "invalid version", "method": 7}},
        {"envelope": 2},
        {"method": "core.info", "params": None},
        {"method": "core.info", "params": [False]},
        {"method": "core.info", "restart": True},
        {"method": "core.shutdown", "wait_for_exit": True},
        {"raw": "not valid json", "restart": True, "wait_for_exit": False},
        {"envelope": {"method": "core.shutdown"}, "wait_for_exit": True},
    ]:
        rpc_model["edges"][0]["properties"]["rpc"]["request"] = request
        validate_rpc_model(rpc_model)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda graph: graph["properties"]["rpc"].update(version=True),
        lambda graph: graph["properties"]["rpc"].update(selector="something"),
        lambda graph: graph["properties"]["rpc"].update(rules={}),
        lambda graph: graph["properties"]["rpc"].update(variables=[]),
        lambda graph: graph["vertices"][0]["properties"]["rpc"].update(rules={}),
        lambda graph: graph["vertices"][0]["properties"].pop("rpc"),
        lambda graph: graph["edges"][0]["properties"]["rpc"].update(request={"method": "x", "extra": 1}),
        lambda graph: graph["edges"][0]["properties"]["rpc"].update(request={"method": "x", "restart": 1}),
        lambda graph: graph["edges"][0]["properties"]["rpc"].update(
            request={"method": "core.shutdown", "wait_for_exit": "true"}
        ),
        lambda graph: graph["edges"][0]["properties"]["rpc"].update(request={"raw": "{}\n{}"}),
        lambda graph: graph["edges"][0]["properties"]["rpc"].update(capture={"var": "response/result"}),
    ],
)
def test_incomplete_or_unsupported_bindings_fail_before_execution(rpc_model, mutation):
    mutation(rpc_model)
    with pytest.raises(ContractError):
        validate_rpc_model(rpc_model)


def test_state_classification_returns_ambiguity_instead_of_using_expected_state(rpc_model):
    context = {"response": {"jsonrpc": "2.0", "result": {"phase": "done"}}}
    assert matching_states(rpc_model, context) == ["done"]
    rpc_model["vertices"][0]["properties"]["rpc"]["match"] = [{"path": "/response/result", "exists": True}]
    assert matching_states(rpc_model, context) == ["ready", "done"]
    assert matching_states(rpc_model, {"response": {}}) == []
    rules = evaluate_rules(rpc_model["properties"]["rpc"]["rules"], context)
    assert rules["Every response uses JSON-RPC 2.0."]["passed"]
    assert rules["Every response uses JSON-RPC 2.0."]["predicates"][0]["actual"] == "2.0"


def _bound_document(model):
    document = copy.deepcopy(model.document)
    graph = document["models"][0]
    graph["properties"]["rpc"] = {
        "version": 1,
        "reset": {"method": "core.info"},
        "fixtures": {"sample": {"unicode": "雪", "tiny": 0.000001}},
        "rules": {
            rule: [{"path": "/response/jsonrpc", "equals": "2.0"}]
            for rule in graph["properties"]["business"]["rules"]
        },
    }
    for state in graph["vertices"]:
        state["properties"]["rpc"] = {
            "match": [{"path": "/response/result/state", "equals": state["id"]}],
            "rules": {
                rule: [{"path": "/response/result/state", "equals": state["id"]}]
                for rule in state["properties"]["business"]["rules"]
            },
        }
    for edge in graph["edges"]:
        edge["properties"]["rpc"] = {"request": {"method": "core.info"}}
    return document


def test_business_model_accepts_full_rpc_bindings_and_native_hash_matches(model):
    from testwalker.core import CoreClient
    from testwalker.model import validate_model

    document = _bound_document(model)
    bound = validate_model(document)
    assert bound.document == document
    assert bound.digest != model.digest
    with CoreClient() as core:
        assert core.request("model.validate", model=document)["model_hash"] == bound.digest
    document["models"][0]["properties"]["rpc"]["fixtures"]["sample"]["unicode"] = "new fixture"
    assert validate_model(document).digest != bound.digest


@pytest.mark.parametrize("element_kind", ["vertices", "edges"])
def test_rpc_bindings_cannot_silently_attach_to_a_web_model(model, element_kind):
    from testwalker.model import validate_model

    document = copy.deepcopy(model.document)
    document["models"][0][element_kind][0]["properties"]["rpc"] = {}
    with pytest.raises(ValueError, match="graph RPC profile"):
        validate_model(document)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda graph: graph["properties"].update(browser={}),
        lambda graph: graph["properties"]["business"].update(selector="#ready"),
        lambda graph: graph["vertices"][0]["properties"]["business"].update(selector="#ready"),
        lambda graph: graph["edges"][0]["properties"]["business"].update(selector="#submit"),
        lambda graph: graph["vertices"][0]["properties"]["rpc"].update(selector="#ready"),
        lambda graph: graph["properties"]["rpc"].update(rules={}),
    ],
)
def test_rpc_profile_keeps_business_and_binding_keys_strict(model, mutation):
    from testwalker.model import validate_model

    document = _bound_document(model)
    mutation(document["models"][0])
    with pytest.raises(ValueError):
        validate_model(document)
