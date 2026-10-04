"""Fill the screens that shipped after `seed_demo_screens.py` was written.

That script covers the seven screens `seed_demo.py` leaves thin. Six more
arrived since, and every one of them renders an empty state on the demo tenant
— which is the one thing a manual screenshot must never show, because an empty
screen documents nothing and reads as an unfinished product.

    /inventario · Plata parada          stock that has not moved, ranked by money
    /inventario · Costos al alza        a supplier's own price history
    /inventario · Margen que se achica  cost up, price flat
    /inventario · Qué costó ignorar     what we recommended and what followed
    per-SKU    · Por qué cambió         today's number against the last one
    /inventario · an event in effect    Semana Santa moving the recommendation
    /proveedores · an order cadence     so the reorder point states its interval

Same rule as its sibling: **through the real HTTP API wherever the API can
express it**, so every guard, validation and provenance stamp runs exactly as
it would for a customer.

THE ONE EXCEPTION, stated rather than hidden: the recommendation log records
one row per tenant per SKU per DAY, written as a side effect of reading the
status. "What did it cost me to ignore you" and "why did it change" are both
differences BETWEEN days, so they need history that no amount of calling the
API today can produce — an HTTP call can only ever write today's row. Those
rows are therefore backdated through `recommendation_log.record_recommendations`
with an explicit `as_of`, which is the function's own supported argument and
not a raw INSERT. Everything else goes over the wire.

Idempotent: each block checks what exists and skips. Safe to re-run before a
screenshot session.

    backend/.venv/Scripts/python.exe -m backend.scripts.seed_demo_today
"""
from __future__ import annotations

import logging
import sys
from datetime import date, timedelta

import httpx

BASE = "http://127.0.0.1:8011/api/v1"
ADMIN = {"email": "demo@faro.app", "password": "demo1234"}

log = logging.getLogger("seed_today")

# How many days of recommendation history to lay down. Seven gives "why did it
# change" a previous day to difference against and "what did it cost me" a
# window with more than one flagged day in it.
HISTORY_DAYS = 7


def _client() -> httpx.Client:
    c = httpx.Client(base_url=BASE, timeout=60.0)
    r = c.post("/auth/login", json=ADMIN)
    r.raise_for_status()
    c.headers["Authorization"] = f"Bearer {r.json()['data']['access_token']}"
    return c


def _data(resp: httpx.Response):
    resp.raise_for_status()
    body = resp.json()
    return body.get("data", body)


def seed_order_cadence(c: httpx.Client) -> None:
    """Give the suppliers a weekly cadence, so the reorder point explains its
    protection interval instead of silently covering only the lead time."""
    suppliers = _data(c.get("/inventory/suppliers")) or []
    suppliers = suppliers if isinstance(suppliers, list) else suppliers.get("items", [])
    touched = 0
    for s in suppliers:
        if s.get("review_period_days"):
            continue
        # Weekly for most, fortnightly for the slow importer — a catalogue where
        # every supplier carries the same number looks generated.
        cadence = 14 if int(s.get("lead_time_days") or 0) >= 18 else 7
        c.patch(f"/inventory/suppliers/{s['id']}", json={"review_period_days": cadence})
        touched += 1
    log.info("order cadence: %d supplier(s) set", touched)


def seed_event(c: httpx.Client) -> None:
    """A declared event that actually overlaps the decision window, so the
    'Ver por qué' panel has an event line to show."""
    existing = _data(c.get("/inventory/events")) or []
    existing = existing if isinstance(existing, list) else existing.get("items", [])
    if any(e.get("name") == "Semana Santa" for e in existing):
        log.info("event: already declared")
        return
    today = date.today()
    _data(c.post("/inventory/events", json={
        "name": "Semana Santa",
        "start_date": str(today + timedelta(days=3)),
        "end_date": str(today + timedelta(days=10)),
        "multiplier": 1.8,
    }))
    log.info("event: Semana Santa x1.8 declared over the next window")


def seed_recommendation_history(session_id: str) -> None:
    """Backdate the recommendation log — see the module docstring for why this
    one block cannot go over HTTP.

    Each day is recorded from the status as it is computed TODAY, with the
    stock walked backwards so the history reads like a shelf draining rather
    than the same row copied seven times. It is demo data and it is shaped to
    look like what a real week looks like, not to be a forecast of anything.
    """
    from backend.inventory import recommendation_log, service as inv_svc
    from backend.scripts.seed_demo import DEMO_TENANT_ID as tenant_id

    today = date.today()
    if recommendation_log.already_recorded(tenant_id, today - timedelta(days=1)):
        log.info("recommendation history: already laid down")
        return

    items = inv_svc.get_inventory_status(tenant_id, session_id)
    if not items:
        log.warning("recommendation history: the demo session has no status rows")
        return

    for age in range(HISTORY_DAYS, 0, -1):
        day = today - timedelta(days=age)
        # Older days held more stock: the shelf has been draining all week.
        # `recommended_qty` moves the other way, which is what makes "why did
        # it change" have something to explain.
        drift = 1.0 + 0.06 * age
        past = []
        for it in items:
            row = dict(it)
            if row.get("current_stock") is not None:
                row["current_stock"] = round(float(row["current_stock"]) * drift, 2)
            if row.get("recommended_qty"):
                row["recommended_qty"] = round(float(row["recommended_qty"]) / drift, 2)
            past.append(row)
        recommendation_log.record_recommendations(
            tenant_id, session_id, past, as_of=day,
        )
    log.info("recommendation history: %d day(s) laid down", HISTORY_DAYS)


def _active_session(c: httpx.Client) -> str | None:
    """The session every inventory screen reads, or the newest completed one.

    `planning_service.resolve_active_session` is the authority the screens use;
    it is called directly rather than over HTTP because it has no endpoint of
    its own, and guessing from the session list would risk seeding history
    against a session the screens do not show.
    """
    from backend.scripts.seed_demo import DEMO_TENANT_ID
    from backend.sessions import planning_service

    active = planning_service.resolve_active_session(DEMO_TENANT_ID)
    if active:
        return active
    sessions = _data(c.get("/sessions")) or []
    sessions = sessions if isinstance(sessions, list) else sessions.get("items", [])
    done = [s for s in sessions if s.get("status") == "COMPLETED"]
    return done[0]["id"] if done else None


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        c = _client()
    except Exception as exc:
        sys.exit(f"Cannot reach the API at {BASE} — is the backend running? ({exc})")

    seed_order_cadence(c)
    seed_event(c)

    session_id = _active_session(c)
    if not session_id:
        log.warning("no completed session — recommendation history skipped")
    else:
        seed_recommendation_history(session_id)
    log.info("done")


if __name__ == "__main__":
    main()
