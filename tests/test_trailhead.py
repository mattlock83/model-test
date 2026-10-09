import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from testwalker.errors import Defect
from testwalker.graphwalker import generate_path
from testwalker.hooks import HookContext, Hooks
from testwalker.model import load_model, setup_path, violations
from testwalker.policy import exploration_model
from testwalker.properties import boundaries
from testwalker.resources import asset
from testwalker.trailhead_demo import CAPACITY, TrailheadStore, booking_data


@pytest.fixture
def trailhead():
    return load_model(asset("models/trailhead.json"))


@pytest.fixture
def extension(trailhead):
    hooks = Hooks.load(asset("examples/trailhead/hooks.py"))
    store = TrailheadStore()

    def api(_ctx, path="state", body=None):
        return store.reset(body.get("defect", "")) if path == "reset" else store.snapshot()

    hooks.module.api = api
    ctx = HookContext(trailhead, "http://localhost/trailhead/", scratch=hooks.scratch)
    hooks.fire("before_run", ctx)
    hooks.fire("before_reset", ctx)
    return hooks, store, ctx


def test_complete_native_route_covers_every_state_edge_and_can_return_home(
    trailhead, tmp_path, native_graphwalker
):
    document = tmp_path / "model.json"
    document.write_text(json.dumps(trailhead.document))
    route = generate_path(trailhead, document, max_steps=1000, binary=native_graphwalker)
    assert {e["id"] for e in route if e["kind"] == "edge"} == set(trailhead.edges)
    assert {e["id"] for e in route if e["kind"] == "state"} == set(trailhead.states)
    assert len({edge["weight"] for edge in trailhead.edges.values()}) >= 4
    # Every property campaign has a nominal setup path, including cancellation after confirmation.
    for edge in trailhead.edges.values():
        if "data set" in edge["properties"]["business"]:
            path = setup_path(trailhead.graph, trailhead.edges, edge["sourceVertexId"])
            assert not path or path[-1]["targetVertexId"] == edge["sourceVertexId"]


def test_predefined_route_covers_both_hook_scenarios_and_all_datasets(
    trailhead, tmp_path, native_graphwalker
):
    configured = exploration_model(trailhead, generator="predefined_path", edge_coverage=0, state_coverage=0)
    document = tmp_path / "model.json"
    document.write_text(json.dumps(configured.document))
    route = generate_path(configured, document, binary=native_graphwalker)
    ids = [e["id"] for e in route if e["kind"] == "edge"]
    assert ids == configured.graph["predefinedPathEdgeIds"]
    assert {"confirm_booking", "confirm_cancellation", "choose_marina_pickup"} <= set(ids)
    assert {configured.edges[i]["properties"]["business"].get("data set") for i in ids} - {None} == set(
        trailhead.data_sets
    )


@pytest.mark.parametrize("dataset", ["Adventure party", "Member profile", "Cancellation request"])
def test_app_accepts_and_rejects_dictionary_boundary_partitions(trailhead, dataset):
    fields = trailhead.data_sets[dataset]
    baseline = {name: field["example"] for name, field in fields.items()}
    for name, field in fields.items():
        for value in boundaries(field):
            data = {**baseline, name: value}
            store = TrailheadStore()
            if dataset == "Adventure party":

                def submit():
                    return booking_data(data)
            elif dataset == "Member profile":

                def submit():
                    return store.save_profile(data)
            else:
                party = {n: f["example"] for n, f in trailhead.data_sets["Adventure party"].items()}
                created = store.confirm(party, "boundary-fixture")

                def submit():
                    return store.cancel(created["id"], data)

            if violations(fields, data):
                with pytest.raises(ValueError):
                    submit()
            else:
                submit()


def test_confirmation_and_cancellation_are_idempotent_and_reset_is_isolated(trailhead):
    store, other = TrailheadStore(), TrailheadStore()
    data = {n: f["example"] for n, f in trailhead.data_sets["Adventure party"].items()}
    data.update({"Adventure": "River paddle", "Conservation contribution": "1.005", "Party size": 3})
    created = store.confirm(data, "unique", "Marina pier")
    assert store.confirm(data, "unique") == created
    assert created["total_cents"] == 36101 and created["pickup"] == "Marina pier"
    assert store.snapshot()["remaining"]["River paddle"] == CAPACITY - 3
    assert not other.snapshot()["bookings"]
    reason = {"Cancellation reason": "  Plans changed  "}
    refund = store.cancel(created["id"], reason)
    assert store.cancel(created["id"], reason) == refund
    assert len(store.snapshot()["refunds"]) == 1
    assert refund["amount_cents"] == 36101
    assert store.snapshot()["remaining"]["River paddle"] == CAPACITY
    assert not store.reset()["bookings"]


def submit(ctx, hooks, identifier, state):
    ctx.element = ctx.model.edges[identifier]
    if "data set" in ctx.element["properties"]["business"]:
        ctx.data = ctx.model.example(ctx.element)
    hooks.fire("before_transition", ctx)
    state()


def checkpoint(ctx, hooks, identifier):
    ctx.element = ctx.model.states[identifier]
    ctx.result = {"status": "PASS"}
    hooks.fire("after_state", ctx)


def confirm(extension):
    hooks, store, ctx = extension
    submit(ctx, hooks, "booking_details_submit_valid", lambda: None)
    checkpoint(ctx, hooks, "booking_review")
    submit(ctx, hooks, "confirm_booking", lambda: None)
    store.confirm(ctx.data, "test-booking")
    checkpoint(ctx, hooks, "booking_confirmed")
    return store.snapshot()["bookings"][-1]


def test_both_hook_scenarios_pass_and_write_an_audit(extension, tmp_path):
    hooks, store, ctx = extension
    booking = confirm(extension)
    submit(ctx, hooks, "cancellation_details_submit_valid", lambda: None)
    checkpoint(ctx, hooks, "cancellation_review")
    ctx.element = ctx.model.edges["confirm_cancellation"]
    hooks.fire("before_transition", ctx)
    store.cancel(booking["id"], ctx.data)
    checkpoint(ctx, hooks, "cancellation_done")
    ctx.report = {"directory": str(tmp_path)}
    hooks.fire("after_run", ctx)
    audit = json.loads((tmp_path / "trailhead-hook-audit.json").read_text())
    assert audit["resets"] == 1
    assert len(audit["checks"]) == 4
    assert audit["final_backend"]["remaining"]["Ridge walk"] == CAPACITY


@pytest.mark.parametrize(
    "defect, checkpoint_id, message",
    [
        ("inventory", "booking_confirmed", "reserves exactly"),
        ("refund", "cancellation_done", "exactly one refund"),
    ],
)
def test_hooks_detect_defects_while_the_browser_still_claims_success(
    extension, defect, checkpoint_id, message
):
    hooks, store, ctx = extension
    store.reset(defect)
    if defect == "inventory":
        with pytest.raises(Defect, match=message):
            confirm(extension)
    else:
        booking = confirm(extension)
        ctx.data = {"Cancellation reason": "Plans changed"}
        ctx.element = ctx.model.edges["confirm_cancellation"]
        hooks.fire("before_transition", ctx)
        store.cancel(booking["id"], ctx.data)
        with pytest.raises(Defect, match=message):
            checkpoint(ctx, hooks, checkpoint_id)


def test_review_hook_catches_premature_persistence(extension):
    hooks, store, ctx = extension
    submit(ctx, hooks, "booking_details_submit_valid", lambda: store.confirm(ctx.data, "too-early"))
    with pytest.raises(Defect, match="must not change inventory"):
        checkpoint(ctx, hooks, "booking_review")


def test_picker_hook_is_scoped_to_business_edges_and_checks_rendered_result(extension):
    hooks, _store, ctx = extension
    expressions = []

    def evaluate(expression):
        expressions.append(expression)
        if "option.click()" in expression:
            return {"selected": "Marina pier"}
        return {"state": "booking_pickup", "summary": "Selected pickup: Marina pier"}

    ctx.browser = SimpleNamespace(browser=SimpleNamespace(evaluate=evaluate))
    ctx.element = ctx.model.edges["choose_marina_pickup"]
    hooks.fire("before_transition", ctx)
    assert len(expressions) == 2
    assert "shadowRoot" in expressions[0] and "option.click()" in expressions[0]
    assert "Marina pier" in expressions[0]
    assert hooks.scratch["picker_selections"] == [{"phase": "graph", "value": "Marina pier"}]
    ctx.element = ctx.model.edges["home_to_catalogue"]
    hooks.fire("before_transition", ctx)
    assert len(expressions) == 2
    # The maintained specification carries no widget selectors or hook imports.
    model_text = Path(asset("models/trailhead.json")).read_text()
    assert (
        "pickup-picker" not in model_text and "shadowRoot" not in model_text and "hooks.py" not in model_text
    )


@pytest.mark.parametrize(
    "expression, edges, states",
    [
        ("weighted_random", 20, 50),
        ("a_star(reached_vertex(cancellation_done))", 0, 0),
        ("quick_random(edge_coverage(100)) a_star(reached_vertex(home))", 100, 100),
    ],
)
def test_additional_native_exploration_modes(
    trailhead, tmp_path, expression, edges, states, native_graphwalker
):
    configured = exploration_model(
        trailhead, generator=expression, edge_coverage=edges, state_coverage=states
    )
    path = tmp_path / "model.json"
    path.write_text(json.dumps(configured.document))
    route = generate_path(configured, path, max_steps=1000, binary=native_graphwalker)
    assert route[0]["id"] == "home"
    if expression.startswith("a_star"):
        assert route[-1]["id"] == "cancellation_done"
    elif expression.endswith("a_star(reached_vertex(home))"):
        assert route[-1]["id"] == "home"
    for state in trailhead.states:
        weights = [e["weight"] for e in trailhead.edges.values() if e["sourceVertexId"] == state]
        assert 0 < sum(weights) <= 1


def test_served_application_endpoints_and_fixture_hooks(trailhead, tmp_path, monkeypatch):
    from urllib.error import HTTPError
    from urllib.request import ProxyHandler, Request, build_opener

    from testwalker.server import serve

    opener = build_opener(ProxyHandler({}))

    def request(url, body=None):
        payload = json.dumps(body).encode() if body is not None else None
        with opener.open(
            Request(url, data=payload, headers={"Content-Type": "application/json"}), timeout=5
        ) as r:
            return r.headers["Content-Type"], r.read()

    monkeypatch.delenv("TRAILHEAD_DEMO_BUG", raising=False)
    with serve() as url:
        content_type, content = request(url + "/trailhead/")
        assert content_type.startswith("text/html") and b"TRAILHEAD" in content
        ctx = HookContext(trailhead, url + "/trailhead/", scratch={}, report={"directory": str(tmp_path)})
        hooks = Hooks.load(asset("examples/trailhead/hooks.py"))
        ctx.scratch = hooks.scratch
        hooks.fire("before_run", ctx)
        hooks.fire("before_reset", ctx)
        api = url + "/api/trailhead/"
        party = {n: f["example"] for n, f in trailhead.data_sets["Adventure party"].items()}
        _, body = request(api + "bookings", {"data": party, "token": "http-check", "pickup": "Marina pier"})
        booked = json.loads(body)
        _, body = request(
            api + "cancellations",
            {
                "booking_id": booked["id"],
                "data": {"Cancellation reason": "Plans changed"},
            },
        )
        assert json.loads(body)["amount_cents"] == booked["total_cents"]
        with pytest.raises(HTTPError) as error:
            request(api + "bookings", {"data": {**party, "Party size": 7}, "token": "bad"})
        assert error.value.code == 400
        hooks.fire("after_run", ctx)
        audit = json.loads((tmp_path / "trailhead-hook-audit.json").read_text())
        assert audit["resets"] == 1 and len(audit["final_backend"]["refunds"]) == 1
        with serve() as other:
            _, body = request(other + "/api/trailhead/state")
            assert not json.loads(body)["bookings"]


def test_lifecycle_audit_preserves_defects_in_walk_and_case_outcomes(extension):
    hooks, _store, ctx = extension
    ctx.walk = 1
    hooks.fire("before_walk", ctx)
    ctx.element = ctx.model.edges["booking_details_submit_invalid"]
    ctx.data = ctx.model.example(ctx.element)
    ctx.invalid = [{"field": "Party size"}]
    hooks.fire("before_case", ctx)
    ctx.error = Defect("Example backend bug")
    hooks.fire("after_case", ctx)
    hooks.fire("after_walk", ctx)
    assert hooks.scratch["cases"][-1]["status"] == "FAIL"
    assert hooks.scratch["cases"][-1]["expected_invalid"]
    assert hooks.scratch["walks"][-1]["status"] == "FAIL"
