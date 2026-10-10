"""Transport, native contracts, cancellation and cross-language replay identity."""

import copy
import io
import json
import queue
from types import SimpleNamespace

import pytest

from testwalker.core import CoreClient
from testwalker.errors import Inconclusive
from testwalker.model import validate_model


@pytest.mark.parametrize("value", [0.0000001, 0.00001, 0.0001, 1.0, 0.1])
def test_native_fingerprint_matches_python_for_decimal_limits(model, value):
    document = copy.deepcopy(model.document)
    field = document["models"][0]["properties"]["business"]["data sets"]["Reservation"]["Places"]
    field.update(type="number", minimum=value)
    document["models"][0]["edges"][1]["properties"]["business"]["example"]["Places"] = ""
    updated = validate_model(document)
    with CoreClient() as core:
        assert core.request("model.validate", model=updated.document)["model_hash"] == updated.digest


def test_worker_survives_malformed_models_and_unknown_methods():
    with CoreClient() as core:
        for model in ("bad", [], None, {"models": [{}]}):
            with pytest.raises(Inconclusive):
                core.request("model.validate", model=model)
        with pytest.raises(Inconclusive, match="Unknown JSON-RPC"):
            core.request("does.not.exist")
        assert core.request("core.info")["protocol_version"] == "1"


def test_nested_cancellation_still_runs_cleanup_and_reports_skipped_inventory(model):
    events = []
    with CoreClient() as core:

        def target(method, params):
            events.append((method, params))
            if method == "target.initialize":
                core.request("run.cancel")
                return {"capabilities": {}}
            return {}

        core.handler = target
        report = core.request(
            "run.start", model=model.document, options={"max_steps": 1000, "evaluator": "adapter"}
        )
        assert report["status"] == "INCONCLUSIVE" and "cancelled" in report["error"]
        assert all(test["status"] == "SKIPPED" for test in report["tests"])
        assert any(m == "target.close" for m, _ in events)
        assert any(m == "target.hook" and p["event"] == "after_run" for m, p in events)
        assert core.request("run.results") == report


def test_adapter_invalid_json_is_a_contract_error_before_sending():
    with CoreClient() as core:
        with pytest.raises(Inconclusive, match="finite, serializable"):
            core.request("model.validate", model={"value": float("nan")})
        assert core.request("core.info")["name"] == "testwalker-core"


def test_run_plan_has_exact_graph_recipes_and_native_property_inventory(model):
    with CoreClient() as core:
        report = core.request(
            "run.plan", model=model.document, options={"max_steps": 1000, "max_input_attempts": 2}
        )
    assert report["runtime"]["properties"] == "hegel"
    assert report["planning"]["complete"]
    graph = [test for test in report["tests"] if test["kind"] == "graph"]
    assert all(test["replay_recipe"]["kind"] == "graph" for test in graph)
    assert sum(test["kind"] == "property" for test in report["tests"]) == 2
    assert json.dumps(report)  # portable, no browser object or callable strategies


@pytest.mark.parametrize("options", [{"max_calls": True}, {"max_calls": "2"}, {"threshold": "0.75"}])
def test_provider_budget_configuration_never_silently_falls_back(options):
    with CoreClient() as core:
        with pytest.raises(Inconclusive):
            core.request("decision.configure", api_key="synthetic-key", **options)
        assert core.request("decision.stats")["calls"] == 0


def test_protocol_size_fault_keeps_its_actionable_reason(monkeypatch):
    import testwalker.core as transport

    monkeypatch.setattr(transport, "MAX_MESSAGE", 32)
    core = CoreClient.__new__(CoreClient)
    core.process = SimpleNamespace(stdout=io.StringIO("x" * 33 + "\n"))
    core.messages = queue.Queue()
    core._read()
    core.sequence, core.timeout = 0, 1
    core._send = lambda _: None
    with pytest.raises(Inconclusive, match="size limit"):
        core.request("run.results")


def test_decision_transcript_pages_are_bounded_and_preserve_every_entry():
    import httpx
    from native_provider import JevClient

    client = JevClient(
        http=httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"answers": {}})))
    )
    try:
        for index in range(6):
            client.ask({"observed": "x" * (1024 * 1024), "index": index}, {})
        page = client.core.request("decision.audit", offset=0, limit=128)
        assert len(json.dumps(page).encode()) < 4 * 1024 * 1024
        assert page["next_offset"] is not None
        assert page["total"] == 6
        assert [entry["request"]["state"]["index"] for entry in client.audit()] == list(range(6))
        with pytest.raises(Inconclusive, match="offset"):
            client.core.request("decision.audit", offset=7)
        with pytest.raises(Inconclusive, match="limit"):
            client.core.request("decision.audit", limit=0)
    finally:
        client.close()
