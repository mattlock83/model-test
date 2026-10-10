"""Declarative RPC adaptation against an independent subprocess protocol peer."""

import copy
import sys
from types import SimpleNamespace

import pytest

from testwalker.errors import Defect, Inconclusive
from testwalker.hooks import Hooks
from testwalker.model import validate_model
from testwalker.rpc_adapter import RpcAdapter

GLOBAL = "Response IDs match request IDs."
ACCEPTED = "Accepted values echo the submitted input."

PEER = r'''
import json
import sys
import time


def send(message):
    print(json.dumps(message), flush=True)


def respond(identifier, result):
    send({"jsonrpc": "2.0", "id": identifier, "result": result})


for raw in sys.stdin:
    try:
        request = json.loads(raw)
    except ValueError:
        send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Malformed JSON"}})
        continue
    if not isinstance(request, dict):
        send({"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid Request"}})
        continue
    identifier, method, params = request.get("id"), request.get("method"), request.get("params", {})
    if method == "info":
        respond(identifier, {"state": "ready"})
    elif method == "echo":
        respond(identifier, {"state": "accepted", "value": params["value"], "receipt": params["token"]})
    elif method == "reject":
        send({"jsonrpc": "2.0", "id": identifier, "error": {"code": -32602, "message": "Invalid value"}})
    elif method == "bad-id":
        respond("other-request", {})
    elif method == "hang":
        time.sleep(10)
    elif method == "callback":
        send({"jsonrpc": "2.0", "id": "callback/1", "method": "target.ready",
              "params": {"token": "callback-token"}})
        nested = json.loads(next(sys.stdin))
        respond(nested["id"], {"echo": nested["params"]["token"]})
        reply = json.loads(next(sys.stdin))
        respond(identifier, {"state": "accepted", "value": params["value"],
                             "receipt": reply["result"]["receipt"]})
    elif method == "shutdown":
        respond(identifier, {"shutdown": True})
        break
'''


def contract_model():
    states = []
    for identifier in ("ready", "accepted", "rejected"):
        rule = ACCEPTED if identifier == "accepted" else f"{identifier.title()} outcome is present."
        predicates = (
            [{"path": "/response/result/value", "equals": {"$ref": "/input/Value"}}]
            if identifier == "accepted"
            else [{"path": "/response", "type": "object"}]
        )
        match = (
            [{"path": "/response/error/code", "equals": -32602}]
            if identifier == "rejected"
            else [{"path": "/response/result/state", "equals": identifier}]
        )
        states.append({
            "id": identifier, "name": identifier,
            "properties": {
                "business": {"description": f"The service is {identifier}.", "rules": [rule]},
                "rpc": {"match": match, "rules": {rule: predicates}},
            },
        })
    edges = []
    for identifier, source, destination, method in (
        ("accept", "ready", "accepted", "echo"),
        ("reject", "ready", "rejected", "reject"),
        ("restart_accepted", "accepted", "ready", "info"),
        ("restart_rejected", "rejected", "ready", "info"),
    ):
        rpc = {"request": {"method": method}}
        if method == "echo":
            rpc["request"]["params"] = {
                "value": {"$ref": "/input/Value"}, "token": {"$ref": "/variables/token"},
            }
            rpc["capture"] = {"receipt": "/response/result/receipt"}
        edges.append({
            "id": identifier, "name": identifier,
            "sourceVertexId": source, "targetVertexId": destination,
            "properties": {"business": {"intent": f"Invoke {method}."}, "rpc": rpc},
        })
    return validate_model({"models": [{
        "id": "rpc-fixture", "name": "RPC test fixture", "startElementId": "ready",
        "generator": "new_york_street_sweeper()",
        "properties": {
            "business": {"version": 3, "purpose": "Test the RPC adapter.", "rules": [GLOBAL]},
            "rpc": {
                "version": 1, "variables": {"token": "initial-token"},
                "reset": {"method": "info"},
                "rules": {GLOBAL: [{"path": "/response/id", "equals": {"$ref": "/request/id"}}]},
                "callbacks": {
                    "target.ready": {
                        "set": {"token": {"$ref": "/callback/params/token"}},
                        "request": {"method": "nested", "params": {"token": {"$ref": "/variables/token"}}},
                        "capture": {"nested": "/callback/response/result/echo"},
                        "result": {"receipt": {"$ref": "/variables/nested"}},
                    },
                },
            },
        },
        "vertices": states, "edges": edges,
    }]})


@pytest.fixture
def adapter():
    target = RpcAdapter(contract_model(), [sys.executable, "-u", "-c", PEER])
    target("target.initialize", {"protocol_version": "1", "config": {}})
    target("target.reset", {})
    yield target
    target.close()
    assert target.closed and target.transport.process.poll() is not None


def execute(adapter, edge="accept", *, data=None, request=None):
    binding = copy.deepcopy(adapter.model.edges[edge])
    if request is not None:
        binding["properties"]["rpc"]["request"] = request
    adapter("target.execute", {"edge": binding, "input": data or {"Value": 3}})
    return adapter("target.observe", {})


def evaluate(adapter, observation, expected):
    return adapter("target.evaluate", {"observation": observation, "expected": expected})


def test_observed_state_is_classified_from_response_independently_of_expected(adapter):
    ready = adapter("target.observe", {})
    assert evaluate(adapter, ready, "accepted")["observed"] == "ready"
    accepted = execute(adapter)
    result = evaluate(adapter, accepted, "rejected")
    assert result["observed"] == "accepted"
    assert result["matching_states"] == ["accepted"]
    assert all(check["status"] == "met" for check in result["checks"])


def test_false_assertion_retains_predicate_evidence_and_marks_broken(adapter):
    observed = execute(adapter)
    observed["response"]["result"]["value"] = 99
    result = evaluate(adapter, observed, "accepted")
    assert result["observed"] == "accepted"
    failed = next(check for check in result["checks"] if check["rule"] == ACCEPTED)
    assert failed["status"] == "broken"
    assert failed["predicates"][0]["actual"] == 99
    assert failed["predicates"][0]["expected"] == 3
    assert adapter.observation["response"]["result"]["value"] == 3


def test_missing_and_ambiguous_state_matches_do_not_invent_observed_state(adapter):
    observation = execute(adapter)
    observation["response"]["result"]["state"] = "unknown"
    missing = evaluate(adapter, observation, "accepted")
    assert missing["observed"] is None and missing["matching_states"] == []
    assert missing["state_diagnostics"]["accepted"][0]["passed"] is False
    duplicate = copy.deepcopy(adapter.model.states["accepted"]["properties"]["rpc"]["match"])
    adapter.model.states["ready"]["properties"]["rpc"]["match"] = duplicate
    ambiguous = evaluate(adapter, adapter.observation, "accepted")
    assert ambiguous["observed"] is None
    assert set(ambiguous["matching_states"]) == {"ready", "accepted"}


def test_capture_and_references_preserve_types_without_mutating_input_or_model(adapter):
    data = {"Value": {"typed": [True, None, 2]}}
    observation = execute(adapter, data=data)
    assert observation["response"]["result"]["value"] == data["Value"]
    assert adapter.variables["receipt"] == "initial-token"
    assert observation["variables"]["receipt"] == "initial-token"
    data["Value"]["typed"].append("caller mutation")
    assert adapter.input["Value"]["typed"] == [True, None, 2]
    assert adapter.model.edges["accept"]["properties"]["rpc"]["request"]["params"]["value"] == {
        "$ref": "/input/Value",
    }
    adapter.variables["token"] = adapter.variables["receipt"] + "/next"
    next_observation = execute(adapter, data={"Value": 4})
    assert next_observation["response"]["result"]["receipt"] == "initial-token/next"


def test_nested_callback_set_capture_and_result_share_the_declared_context(adapter):
    observation = execute(
        adapter, request={"method": "callback", "params": {"value": {"$ref": "/input/Value"}}},
    )
    assert observation["callback_methods"] == ["target.ready"]
    assert observation["response"]["result"]["receipt"] == "callback-token"
    assert observation["variables"] == {
        "token": "callback-token", "nested": "callback-token", "receipt": "callback-token",
    }
    sent = [entry["message"] for entry in adapter.trace[-1]["messages"] if entry["direction"] == "sent"]
    assert [message.get("method") for message in sent] == ["callback", "nested", None]
    assert sent[1]["params"]["token"] == "callback-token"
    assert evaluate(adapter, observation, "accepted")["observed"] == "accepted"


def test_historical_checks_use_captured_inputs_and_variables(adapter):
    first = execute(adapter, data={"Value": 3})
    adapter.variables["token"] = "later-token"
    second = execute(adapter, data={"Value": 9})
    historical = evaluate(adapter, first, "accepted")
    assert all(check["status"] == "met" for check in historical["checks"])
    assert adapter._context(first)["input"] == {"Value": 3}
    assert adapter._context(first)["variables"]["token"] == "initial-token"
    adapter.spec["rules"][GLOBAL] = [
        {"path": "/response/result/value", "equals": {"$ref": "/input/Value"}},
        {"path": "/response/result/receipt", "equals": {"$ref": "/variables/token"}},
    ]
    audit = adapter("target.audit", {
        "records": [{"evidence": first}, {"evidence": second}], "rules": [GLOBAL],
    })
    assert audit[0]["status"] == "met"


def test_expected_api_error_remains_an_observable_response(adapter):
    response = execute(adapter, edge="reject")
    assert response["response"]["error"]["code"] == -32602
    result = evaluate(adapter, response, "accepted")
    assert result["observed"] == "rejected"
    assert all(check["status"] == "met" for check in result["checks"])
    assert adapter.transport.process.poll() is None


@pytest.mark.parametrize("envelope", [None, [], "invalid request"])
def test_invalid_scalar_envelopes_receive_expected_api_rejections(adapter, envelope):
    adapter._invoke({"envelope": envelope})
    assert adapter.observation["response"]["id"] is None
    assert adapter.observation["response"]["error"]["code"] == -32600


def test_protocol_violation_is_a_defect_and_timeout_is_inconclusive(adapter):
    with pytest.raises(Defect, match="protocol violation"):
        adapter._invoke({"method": "bad-id"})
    assert adapter.trace[-1]["request"]["method"] == "bad-id"
    assert adapter.transport.process.poll() is not None
    adapter.timeout = 0.1
    adapter("target.reset", {})
    with pytest.raises(Inconclusive, match="respond"):
        adapter._invoke({"method": "hang"})
    assert adapter.trace[-1]["request"]["method"] == "hang"
    assert adapter.trace[-1]["process"]["running"] is False
    assert adapter.transport.process.poll() is not None


def test_reset_restarts_process_and_restores_variables_but_keeps_trace_history(adapter):
    execute(adapter)
    previous = adapter.transport.process
    before = len(adapter.trace)
    adapter.variables["token"] = "mutated"
    adapter("target.reset", {})
    assert previous.poll() is not None
    assert adapter.transport.process.pid != previous.pid
    assert adapter.variables == {"token": "initial-token"}
    assert adapter.input == {}
    assert len(adapter.trace) == before + 1
    assert adapter.observation["response"]["result"]["state"] == "ready"


def test_hooks_receive_generic_target_and_share_scratch(adapter):
    contexts = []

    def before_transition(context):
        context.scratch["visited"] = context.element_id
        contexts.append(context)

    def after_run(context):
        contexts.append(context)

    adapter.hooks = Hooks(SimpleNamespace(before_transition=before_transition, after_run=after_run))
    adapter.collector = SimpleNamespace(report={})
    adapter("target.hook", {"event": "before_transition", "context": {
        "element": adapter.model.edges["accept"], "phase": "graph", "data": {"Value": 3},
    }})
    adapter("target.hook", {"event": "after_run", "context": {"result": {"status": "PASS"}}})
    assert contexts[0].target is adapter and contexts[0].browser is None
    assert contexts[1].scratch == {"visited": "accept"}
    assert adapter.after_run and adapter.collector.report["status"] == "PASS"
    assert contexts[0].data == {"Value": 3}


def test_acknowledged_shutdown_records_exit_and_close_is_idempotent(adapter):
    adapter._invoke({"method": "shutdown", "wait_for_exit": True})
    assert adapter.observation["process"] == {"running": False, "exit_code": 0}
    adapter.close()
    adapter.close()
    assert adapter.closed
