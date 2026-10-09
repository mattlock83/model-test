"""Synthetic backend for the Trailhead example, not part of the test oracle."""

import copy
import re
from decimal import ROUND_HALF_UP, Decimal
from threading import RLock

PRICES = {"Ridge walk": 80, "River paddle": 120}
CAPACITY = 1000


def money(value):
    return int((Decimal(str(value)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def clean(data):
    if not isinstance(data, dict) or not all(isinstance(v, (str, int, float)) for v in data.values()):
        raise ValueError("Supply a business-data object")
    return {key: str(value).strip() for key, value in data.items()}


def booking_data(data):
    values = clean(data)
    name, email = values.get("Traveller name", ""), values.get("Contact email", "")
    places, trip = values.get("Party size", ""), values.get("Adventure", "")
    contribution = values.get("Conservation contribution", "")
    if not 2 <= len(name) <= 60:
        raise ValueError("Traveller name must contain 2–60 characters")
    if not re.fullmatch(r"[^\s@]+@[^\s@.]+(?:\.[^\s@.]+)+", email):
        raise ValueError("A valid contact email is required")
    if not re.fullmatch(r"[+-]?\d+", places) or not 1 <= int(places) <= 6:
        raise ValueError("Party size must be a whole number from 1 through 6")
    if trip not in PRICES:
        raise ValueError("Choose Ridge walk or River paddle")
    if not re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)", contribution):
        raise ValueError("Contribution must be a decimal number from 0 through 50")
    if not 0 <= Decimal(contribution) <= 50:
        raise ValueError("Contribution must be from 0 through 50")
    total = int(places) * PRICES[trip] * 100 + money(contribution)
    return values, total


class TrailheadStore:
    """An isolated ledger per demo server; cents keep refunds deterministic."""

    def __init__(self):
        self.lock = RLock()
        self.reset()

    def reset(self, defect=""):
        if defect not in {"", "inventory", "refund"}:
            raise ValueError("Unknown demo defect")
        with self.lock:
            self.defect = defect
            self.bookings, self.refunds = [], []
            self.remaining = dict.fromkeys(PRICES, CAPACITY)
            self.profile = {
                "Display name": "Alex Morgan",
                "Member email": "alex@example.test",
                "Updates preference": "Email",
                "Access notes": "",
            }
            return self.snapshot()

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(
                {
                    "bookings": self.bookings,
                    "refunds": self.refunds,
                    "remaining": self.remaining,
                    "capacity": CAPACITY,
                    "prices": PRICES,
                    "profile": self.profile,
                }
            )

    def confirm(self, data, token, pickup="City visitor centre"):
        values, total = booking_data(data)
        if not isinstance(token, str) or not 1 <= len(token) <= 100:
            raise ValueError("A confirmation token is required")
        if pickup not in {"City visitor centre", "Marina pier"}:
            raise ValueError("Choose City visitor centre or Marina pier")
        with self.lock:
            existing = next((item for item in self.bookings if item["token"] == token), None)
            if existing:
                return copy.deepcopy(existing)
            trip, places = values["Adventure"], int(values["Party size"])
            if self.remaining[trip] < places:
                raise ValueError("Not enough remaining places")
            booking = {
                "id": f"TH-{len(self.bookings) + 1:04d}",
                "token": token,
                "data": values,
                "total_cents": total,
                "status": "confirmed",
                "pickup": pickup,
            }
            self.bookings.append(booking)
            if self.defect != "inventory":
                self.remaining[trip] -= places
            return copy.deepcopy(booking)

    def cancel(self, identifier, data):
        values = clean(data)
        reason = values.get("Cancellation reason", "")
        if not 5 <= len(reason) <= 120:
            raise ValueError("Cancellation reason must contain 5–120 characters")
        with self.lock:
            booking = next((item for item in self.bookings if item["id"] == identifier), None)
            if not booking:
                raise ValueError("Booking does not exist")
            existing = next((item for item in self.refunds if item["booking_id"] == identifier), None)
            if existing:
                return copy.deepcopy(existing)
            booking["status"] = "cancelled"
            trip, places = booking["data"]["Adventure"], int(booking["data"]["Party size"])
            self.remaining[trip] += places
            refund = {
                "booking_id": identifier,
                "amount_cents": booking["total_cents"],
                "reason": reason,
            }
            self.refunds.append(refund)
            if self.defect == "refund":
                self.refunds.append(copy.deepcopy(refund))
            return copy.deepcopy(refund)

    def save_profile(self, data):
        values = clean(data)
        if not 2 <= len(values.get("Display name", "")) <= 40:
            raise ValueError("Display name must contain 2–40 characters")
        if not re.fullmatch(r"[^\s@]+@[^\s@.]+(?:\.[^\s@.]+)+", values.get("Member email", "")):
            raise ValueError("A valid member email is required")
        if values.get("Updates preference") not in {"Email", "SMS", "None"}:
            raise ValueError("Choose Email, SMS or None")
        if len(values.get("Access notes", "")) > 80:
            raise ValueError("Access notes must contain at most 80 characters")
        with self.lock:
            self.profile = values
            return copy.deepcopy(values)
