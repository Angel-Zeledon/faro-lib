"""Fill the demo tenant's THIN screens, so a landing screenshot shows a product.

`seed_demo.py` builds the operational core — SKUs, sales history, warehouses,
suppliers, purchase orders, transfers. Seven screens stay empty after it, and an
empty screen is the one thing a landing gallery must never show: it sells
nothing and reads as an unfinished product.

    /usuarios              a team, not one admin talking to himself
    /mensajes              a real 1-to-1 exchange between two of them
    /asistente             a conversation with the AI analyst
    /escenarios            a saved what-if
    /automatizacion        an API key and a scheduled retrain
    /configurar-inventario a supplier rule
    /historial             activity — which the writes above produce on their own

Everything goes through the REAL HTTP API with the demo admin's token, so every
guard, validation and provenance stamp runs exactly as it would for a customer.
Nothing here writes to a table directly.

Idempotent: each block checks what already exists and skips. Safe to re-run
before a demo or a screenshot session.

    backend/.venv/Scripts/python.exe -m backend.scripts.seed_demo_screens
"""

from __future__ import annotations

import logging
import sys

import httpx

log = logging.getLogger("seed_demo_screens")

BASE = "http://127.0.0.1:8011/api/v1"
ADMIN = {"email": "demo@faro.app", "password": "demo1234"}

TEAM = [
    {"email": "compras@faro.app", "full_name": "Marcela Ruiz", "role": "analyst"},
    {"email": "bodega@faro.app", "full_name": "Diego Fallas", "role": "viewer"},
]

# A real exchange between the buyer and the analyst, about SKUs this tenant
# actually has. Generic filler ("hola", "test") would look like filler.
THREAD = [
    "Marcela, la Harina de Maíz #34 quedó en 1 día de cobertura. ¿La subimos a la orden de Insumos Express?",
    "Sí, ya la agregué. Pedí 78 unidades, que es lo que sugiere el semáforo con el plazo de 4 días.",
    "Perfecto. El Aceite de Oliva 1L también está en rojo, pero ahí hay excedente en Norte — mejor traslado que compra.",
]

ASSISTANT_QUESTION = (
    "¿Cuáles son mis tres productos con mayor riesgo de quiebre esta semana y "
    "cuánto capital tengo inmovilizado en sobrestock?"
)

# The landing shows the app in the visitor's language, screenshots included, so
# the demo tenant needs a thread and a chat in English too. They go to a
# DIFFERENT recipient and a differently-titled chat rather than appending to the
# Spanish ones: a thread that switches language halfway is not what either
# screenshot should show.
THREAD_EN = [
    "Marcela, Harina de Maiz #34 is down to 1 day of coverage. Do we add it to the Insumos Express order?",
    "Yes, I already added it. I ordered 78 units, which is what the signal suggests against the 4-day lead time.",
    "Good. Aceite de Oliva 1L is red too, but Norte is holding surplus — a transfer beats a purchase there.",
]

ASSISTANT_QUESTION_EN = (
    "Which three products are most at risk of stocking out this week, and how "
    "much capital do I have tied up in overstock?"
)

ASSISTANT_TITLE = {
    "es": "Riesgo de quiebre y capital inmovilizado",
    "en": "Stockout risk and tied-up capital",
}


def _client() -> httpx.Client:
    c = httpx.Client(base_url=BASE, timeout=120.0)
    r = c.post("/auth/login", json=ADMIN)
    r.raise_for_status()
    c.headers["Authorization"] = f"Bearer {r.json()['data']['access_token']}"
    return c


def _data(resp: httpx.Response):
    return resp.json().get("data")


def _items(resp: httpx.Response) -> list:
    """The rows, whatever this endpoint wraps them in.

    The API is not uniform: `/users` and `/sessions` answer
    `{"data": {"items": [...]}}` while `/api-keys` and `/analyst/chats` answer
    `{"data": [...]}`. Guessing wrong costs a KeyError, not a wrong result, so
    this reads both rather than each caller remembering which is which.
    """
    d = _data(resp)
    if isinstance(d, dict):
        return d.get("items") or []
    return d or []


def seed_team(c: httpx.Client) -> list[dict]:
    # `GET /users` answers {"data": {"items": [...]}}, not a bare list.
    existing = {u["email"]: u for u in _items(c.get("/users"))}
    made = []
    for member in TEAM:
        if member["email"] in existing:
            made.append(existing[member["email"]])
            log.info("team: %s already there", member["email"])
            continue
        r = c.post("/users", json=member)
        if r.status_code in (200, 201):
            made.append(_data(r))
            log.info("team: created %s (%s)", member["email"], member["role"])
        else:
            log.warning("team: %s -> %s %s", member["email"], r.status_code, r.text[:160])
    return made


def seed_messages(c: httpx.Client, team: list[dict], lang: str = "es") -> None:
    # Read the tenant's users rather than only what this run created: on a
    # re-run `team` is whatever was skipped, and the analyst is already there.
    # The English thread goes to the OTHER teammate so the two languages never
    # end up interleaved in one conversation.
    wanted_role = "analyst" if lang == "es" else "viewer"
    analyst = next((u for u in _items(c.get("/users")) if u.get("role") == wanted_role), None)
    if not analyst:
        log.warning("messages: no %s to talk to", wanted_role)
        return
    # An invited user is `pending_confirmation` until they follow the setup
    # link, and the messages endpoint refuses an inactive recipient — correctly:
    # a message to somebody who never accepted has nowhere to land. For a demo
    # tenant the acceptance is the part nobody is going to perform, so it is
    # done here, through the same service the link would call.
    if analyst.get("status") != "active":
        from backend.users import service as user_svc
        from backend.db.connection import init_pool, pool_is_initialized
        from backend.config import settings
        if not pool_is_initialized():
            init_pool(settings.database_url)
        for u in _items(c.get("/users")):
            if u.get("status") != "active":
                user_svc.mark_verified(u["tenant_id"], u["id"])
                log.info("team: activated %s", u["email"])

    threads = _items(c.get("/messages/conversations"))
    if any(t.get("user_id") == analyst["id"] or t.get("id") == analyst["id"] for t in threads):
        log.info("messages: a thread with %s already exists", analyst["email"])
        return
    # Sent from the admin's side only. The replies would need the analyst's
    # session, and this account is created by an invite whose password only the
    # invitee ever sees — there is no "send as" in this API and there should not
    # be, because impersonation is not a feature. A one-sided thread still shows
    # the screen doing its job.
    thread = THREAD if lang == "es" else THREAD_EN
    for text in thread:
        r = c.post("/messages", json={"recipient_id": analyst["id"], "body": text})
        if r.status_code not in (200, 201):
            log.warning("messages: %s %s", r.status_code, r.text[:140])
            return
    log.info("messages: %s thread seeded (%d messages)", lang, len(thread))


def seed_assistant(c: httpx.Client, lang: str = "es") -> None:
    title = ASSISTANT_TITLE[lang]
    if any(ch.get("title") == title for ch in _items(c.get("/analyst/chats"))):
        log.info("assistant: the %s conversation already exists", lang)
        return
    sessions = _items(c.get("/sessions"))
    sid = sessions[0]["id"] if sessions else None
    r = c.post("/analyst/chats", json={"title": title, "session_id": sid})
    if r.status_code not in (200, 201):
        log.warning("assistant: could not create the chat: %s", r.text[:160])
        return
    chat_id = _data(r)["id"]
    # A real question against real data — this calls DeepSeek, which is the
    # point: the screenshot has to show the product answering, not a stub.
    question = ASSISTANT_QUESTION if lang == "es" else ASSISTANT_QUESTION_EN
    a = c.post(f"/analyst/chats/{chat_id}/messages", json={"question": question})
    log.info("assistant: asked in %s (%s)", lang, a.status_code)


def seed_scenario(c: httpx.Client) -> None:
    sessions = _items(c.get("/sessions"))
    if not sessions:
        log.warning("scenario: no session to attach to")
        return
    sid = sessions[0]["id"]
    if _items(c.get(f"/sessions/{sid}/scenarios")):
        log.info("scenario: one already saved")
        return
    r = c.post(f"/sessions/{sid}/scenarios", json={
        "name": "Temporada alta: +40% de demanda",
        "rules": [{"type": "demand_multiplier", "multiplier": 1.4}],
    })
    log.info("scenario: %s", r.status_code)


def seed_api_key(c: httpx.Client) -> None:
    if _items(c.get("/api-keys")):
        log.info("api key: one already there")
        return
    r = c.post("/api-keys", json={"name": "ERP nocturno", "role": "analyst"})
    log.info("api key: %s", r.status_code)


def seed_schedule(c: httpx.Client) -> None:
    sessions = _items(c.get("/sessions"))
    if not sessions:
        return
    sid = sessions[0]["id"]
    # Five cron fields: 06:00 every Monday, in the tenant's timezone.
    r = c.post(f"/sessions/{sid}/schedule",
               json={"cron_expr": "0 6 * * 1", "enabled": True})
    log.info("schedule: %s %s", r.status_code, "" if r.status_code < 400 else r.text[:140])


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    lang = "es"
    if "--lang" in sys.argv:
        lang = sys.argv[sys.argv.index("--lang") + 1]
    if lang not in ("es", "en"):
        sys.exit(f"--lang takes 'es' or 'en', not {lang!r}")

    try:
        c = _client()
    except Exception as exc:
        sys.exit(f"Cannot reach the API at {BASE} — is the backend running? ({exc})")

    team = seed_team(c)
    seed_messages(c, team, lang)
    seed_scenario(c)
    seed_api_key(c)
    seed_schedule(c)
    seed_assistant(c, lang)   # last: it makes a real LLM call and is the slowest
    log.info("done (%s)", lang)


if __name__ == "__main__":
    main()
