"""Optional Trailhead integration: fixtures, custom picker, inventory and refunds.

Application selectors are deliberately confined to this explicitly loaded file.
The model contains business states, intentions and rules only.
"""

import json
import os
import time
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

from testwalker.errors import Defect, Inconclusive


def api(ctx, path="state", body=None):
    origin = urlsplit(ctx.url)
    url = f"{origin.scheme}://{origin.netloc}/api/trailhead/{path}"
    payload = json.dumps(body).encode() if body is not None else None
    request = Request(url, data=payload, headers={"Content-Type": "application/json"})
    # Fixtures are local; do not send their API requests through a configured proxy.
    with build_opener(ProxyHandler({})).open(request, timeout=5) as response:
        return json.load(response)


def ledger(state):
    return {key: state[key] for key in ("bookings", "refunds", "remaining")}


def before_run(ctx):
    ctx.scratch.update(resets=0, checkpoints=[], picker_selections=[], pending=None, walks=[], cases=[])


def before_walk(ctx):
    ctx.scratch["walks"].append({"walk": ctx.walk, "status": "RUNNING"})


def after_walk(ctx):
    status = "FAIL" if isinstance(ctx.error, Defect) else "INCONCLUSIVE" if ctx.error else "COMPLETED"
    ctx.scratch["walks"][-1]["status"] = status


def before_case(ctx):
    ctx.scratch["cases"].append(
        {
            "journey": ctx.element_id,
            "input": dict(ctx.data),
            "expected_invalid": bool(ctx.invalid),
            "status": "RUNNING",
        }
    )


def after_case(ctx):
    status = (
        ctx.result.get("status", "INCONCLUSIVE")
        if ctx.result
        else ("FAIL" if isinstance(ctx.error, Defect) else "INCONCLUSIVE")
    )
    ctx.scratch["cases"][-1]["status"] = status


def before_reset(ctx):
    seeded = api(ctx, "reset", {"defect": os.environ.get("TRAILHEAD_DEMO_BUG", "")})
    ctx.check(not seeded["bookings"] and not seeded["refunds"], "Fixture reset must empty the ledger")
    ctx.check(
        all(v == seeded["capacity"] for v in seeded["remaining"].values()), "Fixture capacity is restored"
    )
    ctx.scratch["resets"] += 1
    ctx.scratch["pending"] = None


def select_pickup(ctx, value):
    """Use the real widget interaction, not hidden-input mutation or app internals."""
    browser = ctx.browser.browser
    if browser is None:
        raise Inconclusive("A test tab must be open before selecting a pickup")
    requested = json.dumps(value)  # Data is JSON encoded before becoming a JavaScript literal.
    selected = browser.evaluate(
        """(() => {
      const widget = document.querySelector('pickup-picker');
      const root = widget?.shadowRoot;
      const visible = e => e && !e.hidden && e.getClientRects().length > 0;
      if (!visible(widget) || !root) return {error: 'Pickup widget is unavailable'};
      const opener = widget.querySelector('button[aria-haspopup="listbox"]');
      const list = root.querySelector('[role="listbox"]');
      if (!visible(opener) || !list) return {error: 'Pickup controls are unavailable'};
      if (list.hidden) opener.click();
      const option = [...root.querySelectorAll('[role="option"]')]
        .find(e => e.dataset.value === VALUE);
      if (!visible(option)) return {error: 'Requested pickup is unavailable'};
      option.click();
      return {selected: VALUE};
    })()""".replace("VALUE", requested)
    )
    if not isinstance(selected, dict) or selected.get("selected") != value:
        raise Inconclusive(f"Custom pickup selection failed: {selected}")
    expected = "booking_pickup" if value == "Marina pier" else "booking_details"
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        observed = browser.evaluate("""({
          state: document.querySelector('#app')?.dataset.state,
          summary: document.querySelector('#pickup-summary')?.textContent
        })""")
        if observed.get("state") == expected and observed.get("summary") == f"Selected pickup: {value}":
            ctx.scratch["picker_selections"].append({"phase": ctx.phase, "value": value})
            return
        time.sleep(0.05)
    raise Inconclusive("The selected pickup did not appear in the independently rendered form summary")


def before_transition(ctx):
    if ctx.element_id in {"choose_marina_pickup", "choose_city_pickup"}:
        select_pickup(
            ctx, "Marina pier" if ctx.element_id == "choose_marina_pickup" else "City visitor centre"
        )
    watched = {"confirm_booking", "confirm_cancellation"}
    watched.update(
        name
        for name in ctx.model.edges
        if name.startswith(("booking_", "cancellation_")) and "_submit_" in name
    )
    if ctx.element_id in watched:
        ctx.scratch["pending"] = {
            "edge": ctx.element_id,
            "before": api(ctx),
            "data": dict(ctx.data),
            "phase": ctx.phase,
        }


def after_state(ctx):
    if not ctx.result or ctx.result.get("status") != "PASS":
        return
    pending = ctx.scratch.get("pending")
    if not pending:
        return
    state = (
        api(ctx)
        if ctx.element_id
        in {
            "booking_review",
            "booking_error",
            "booking_confirmed",
            "cancellation_review",
            "cancellation_error",
            "cancellation_done",
        }
        else None
    )
    if state is None:
        return
    before, edge = pending["before"], pending["edge"]
    checked = None
    if "_submit_" in edge and ctx.element_id in {
        "booking_review",
        "booking_error",
        "cancellation_review",
        "cancellation_error",
    }:
        ctx.check(
            ledger(state) == ledger(before), "Review or rejected input must not change inventory or payments"
        )
        checked = "review has no booking, inventory or refund side effects"
    elif edge == "confirm_booking" and ctx.element_id == "booking_confirmed":
        ctx.check(
            len(state["bookings"]) == len(before["bookings"]) + 1, "Confirmation creates exactly one booking"
        )
        created = state["bookings"][-1]
        data = pending["data"]
        expected = {name: str(value).strip() for name, value in data.items()}
        ctx.check(created["data"] == expected, "Persisted booking matches the supplied business values")
        ctx.check(created["status"] == "confirmed", "Persisted booking is confirmed")
        # Deliberately independent prices: do not borrow the application's calculation.
        unit = {"Ridge walk": 80, "River paddle": 120}[data["Adventure"]]
        gift = (Decimal(str(data["Conservation contribution"])) * 100).quantize(
            Decimal("1"),
            rounding=ROUND_HALF_UP,
        )
        ctx.check(
            created["total_cents"] == int(data["Party size"]) * unit * 100 + int(gift),
            "Persisted charge equals party price plus one conservation gift",
        )
        expected_remaining = dict(before["remaining"])
        expected_remaining[data["Adventure"]] -= int(data["Party size"])
        ctx.check(
            state["remaining"] == expected_remaining, "Confirmation reserves exactly the submitted places"
        )
        ctx.check(state["refunds"] == before["refunds"], "Confirmation must not create a refund")
        checked = "confirmation persists one booking and reserves its exact inventory"
    elif edge == "confirm_cancellation" and ctx.element_id == "cancellation_done":
        original = before["bookings"][-1]
        updated = state["bookings"][-1]
        ctx.check(len(state["bookings"]) == len(before["bookings"]), "Cancellation must not create a booking")
        ctx.check(
            updated["id"] == original["id"] and updated["status"] == "cancelled",
            "The existing booking must be cancelled",
        )
        refunds = [r for r in state["refunds"] if r["booking_id"] == original["id"]]
        ctx.check(len(refunds) == 1, "Cancellation issues exactly one refund for this booking")
        ctx.check(
            len(state["refunds"]) == len(before["refunds"]) + 1, "No unrelated or duplicate refund is issued"
        )
        ctx.check(
            refunds[0]["amount_cents"] == original["total_cents"], "Refund returns the full original charge"
        )
        ctx.check(
            refunds[0]["reason"] == str(pending["data"]["Cancellation reason"]).strip(),
            "Refund records the supplied cancellation reason",
        )
        expected_remaining = dict(before["remaining"])
        expected_remaining[original["data"]["Adventure"]] += int(original["data"]["Party size"])
        ctx.check(
            state["remaining"] == expected_remaining, "Cancellation releases exactly the reserved places"
        )
        checked = "cancellation records one full refund and releases its exact inventory"
    if checked:
        ctx.scratch["checkpoints"].append({"phase": ctx.phase, "state": ctx.element_id, "check": checked})
        ctx.scratch["pending"] = None


def after_run(ctx):
    report = {
        "resets": ctx.scratch.get("resets", 0),
        "checks": ctx.scratch.get("checkpoints", []),
        "custom_picker": ctx.scratch.get("picker_selections", []),
        "final_backend": api(ctx),
        "walks": ctx.scratch.get("walks", []),
        "cases": ctx.scratch.get("cases", []),
    }
    destination = Path(ctx.report["directory"]) / "trailhead-hook-audit.json"
    destination.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
