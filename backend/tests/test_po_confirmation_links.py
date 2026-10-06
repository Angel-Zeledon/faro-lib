"""Supplier confirmation link, end to end (needs the local test Postgres).

The feature puts a public, unauthenticated page in front of a purchase order, so
most of these tests are about what a stranger holding (or guessing) a link can and
cannot do: see prices, touch another order, tell a dead link from an invented one.
The rest check what the buyer gets: statuses, an accept-change action that is the
ONLY thing allowed to move an order's expected arrival, and history that is kept.
"""
import io
import json
import zipfile
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest

from backend.db.connection import execute, query, query_one
from backend.inventory import po_confirmation_core as core
from backend.inventory import reception_service as rec_svc

PRICE_A = 4321.5
PRICE_B = 8765.25
PORTAL = "/api/v1/supplier-portal"


# ── Setup helpers ────────────────────────────────────────────────────────────

def _supplier(tid, *, email="ventas@proveedor.test", whatsapp=None):
    from backend.inventory import supplier_service as sup_svc
    name = f"Proveedor {uuid4().hex[:6]}"
    data = {"name": name}
    if email:
        data["email"] = email
    if whatsapp:
        data["whatsapp"] = whatsapp
    sup_svc.create_supplier(tid, data)
    return name


def _po(tid, supplier, *, generated_days_ago=0, prices=(PRICE_A, PRICE_B)):
    po = query_one(
        "INSERT INTO inventory_po_log (tenant_id, session_id, sku_count, total_units, "
        "generated_at) VALUES (%s, %s, 2, 140, %s) RETURNING id",
        (tid, "sess-" + uuid4().hex[:8], datetime.now(timezone.utc) - timedelta(days=generated_days_ago)),
    )
    lines = {}
    for tag, qty, price in (("a", 100, prices[0]), ("b", 40, prices[1])):
        sku = f"CF-{tag}-{uuid4().hex[:6]}"
        row = query_one(
            "INSERT INTO inventory_po_items (po_log_id, tenant_id, sku, display_name, supplier, "
            "recommended_qty, final_qty, unit_cost, status, warehouse) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'approved', 'principal') RETURNING id",
            (po["id"], tid, sku, f"Producto {tag}", supplier, qty, qty, price),
        )
        lines[tag] = {"id": row["id"], "sku": sku, "qty": float(qty)}
    return po["id"], lines


@pytest.fixture
def mail(monkeypatch):
    """Captures what the send flow hands to the e-mail and WhatsApp channels."""
    from backend.notifications import email as email_mod, whatsapp as wa_mod
    sent = {"email": [], "wa": []}
    monkeypatch.setattr(email_mod, "send_po_to_supplier_email",
                        lambda **kw: sent["email"].append(kw) or True)
    monkeypatch.setattr(wa_mod, "send_whatsapp",
                        lambda *a, **kw: sent["wa"].append((a, kw)) or True)
    return sent


def _send(client, headers, po_id, mail, *, confirm=True):
    body = {"request_confirmation": True} if confirm else None
    resp = client.post(f"/api/v1/inventory/po/{po_id}/send", headers=headers, json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _token(mail, index=-1):
    url = mail["email"][index].get("confirm_url")
    assert url, "the e-mail carried no confirmation link"
    return url.rsplit("/", 1)[1]


@pytest.fixture
def order(client, auth_headers, test_tenant, mail):
    """An order with two lines, sent with a confirmation link."""
    tid = test_tenant["id"]
    supplier = _supplier(tid)
    po_id, lines = _po(tid, supplier)
    data = _send(client, auth_headers, po_id, mail)
    return {"tid": tid, "supplier": supplier, "po_id": po_id, "lines": lines,
            "token": _token(mail), "send": data}


def _requested(order):
    row = query_one("SELECT requested_date FROM po_confirmation_requests WHERE po_log_id = %s",
                    (order["po_id"],))
    return row["requested_date"]


def _answer(order, **overrides):
    """A valid 'confirm everything as requested' answer, with per-line overrides."""
    req = _requested(order).isoformat()
    out = []
    for tag, line in order["lines"].items():
        row = {"line_id": line["id"], "decision": "confirm",
               "confirmed_qty": line["qty"], "promised_date": req}
        row.update(overrides.get(tag, {}))
        out.append(row)
    return out


def _post(client, token, lines, **kw):
    return client.post(f"{PORTAL}/{token}/confirm", json={"lines": lines}, **kw)


def _events(tid, action, po_id):
    return query("SELECT context, user_id FROM activity_logs WHERE tenant_id = %s AND action = %s "
                 "AND resource = %s", (tid, action, po_id))


# ── Creating the link ────────────────────────────────────────────────────────

class TestIssuingTheLink:
    def test_without_the_flag_nothing_changes(self, client, auth_headers, test_tenant, mail):
        tid = test_tenant["id"]
        po_id, _ = _po(tid, _supplier(tid))
        data = _send(client, auth_headers, po_id, mail, confirm=False)
        assert "confirmation_links" not in data
        assert mail["email"][0].get("confirm_url") is None
        assert query_one("SELECT COUNT(*) AS n FROM po_confirmation_requests WHERE po_log_id = %s",
                         (po_id,))["n"] == 0

    def test_only_the_hash_is_stored(self, order):
        row = query_one("SELECT * FROM po_confirmation_requests WHERE po_log_id = %s",
                        (order["po_id"],))
        assert row["token_hash"] == core.hash_token(order["token"])
        assert order["token"] not in json.dumps(dict(row), default=str)
        assert len(order["token"]) >= 43
        assert row["tenant_id"] == order["tid"] and row["supplier"] == order["supplier"]
        assert order["send"]["confirmation_links"] == [
            {"supplier": order["supplier"], "email": True, "whatsapp": False}]
        # 21 days, or 7 after the expected arrival, whichever is later.
        assert row["expires_at"] >= datetime.now(timezone.utc) + timedelta(days=20)

    def test_resend_rotates_the_token_and_keeps_one_row(self, client, auth_headers, order, mail):
        old = order["token"]
        _send(client, auth_headers, order["po_id"], mail)
        new = _token(mail)
        assert new != old
        assert query_one("SELECT COUNT(*) AS n FROM po_confirmation_requests WHERE po_log_id = %s",
                         (order["po_id"],))["n"] == 1
        assert client.get(f"{PORTAL}/{old}").status_code == 404
        assert client.get(f"{PORTAL}/{new}").status_code == 200

    def test_resend_does_not_unlock_or_drop_answers(self, client, auth_headers, order, mail):
        assert _post(client, order["token"], _answer(order)).status_code == 200
        _send(client, auth_headers, order["po_id"], mail)
        view = client.get(f"{PORTAL}/{_token(mail)}").json()["data"]
        assert view["locked"] is True
        assert query_one("SELECT COUNT(*) AS n FROM po_line_confirmations "
                         "WHERE po_log_id = %s", (order["po_id"],))["n"] == 2

    def test_whatsapp_text_carries_the_link_only_on_a_plan_with_the_bot(
        self, client, auth_headers, test_tenant, mail, monkeypatch,
    ):
        import backend.entitlements.service as ent
        tid = test_tenant["id"]
        supplier = _supplier(tid, whatsapp="+15551234567")

        monkeypatch.setattr(ent, "tenant_has_feature", lambda *_a, **_k: False)
        po_id, _ = _po(tid, supplier)
        _send(client, auth_headers, po_id, mail)
        assert mail["email"][-1]["confirm_url"]                       # e-mail: always
        assert "/proveedor/" not in mail["wa"][-1][0][1]             # WhatsApp text: no

        monkeypatch.setattr(ent, "tenant_has_feature", lambda *_a, **_k: True)
        po2, _ = _po(tid, supplier)
        data = _send(client, auth_headers, po2, mail)
        assert "/proveedor/" in mail["wa"][-1][0][1]
        assert data["confirmation_links"][0]["whatsapp"] is True

    def test_a_supplier_reachable_only_by_unentitled_whatsapp_gets_no_link(
        self, client, auth_headers, test_tenant, mail, monkeypatch,
    ):
        import backend.entitlements.service as ent
        monkeypatch.setattr(ent, "tenant_has_feature", lambda *_a, **_k: False)
        tid = test_tenant["id"]
        supplier = _supplier(tid, email=None, whatsapp="+15551234567")
        po_id, _ = _po(tid, supplier)
        data = _send(client, auth_headers, po_id, mail)
        assert data["confirmation_links"] == []
        assert query_one("SELECT COUNT(*) AS n FROM po_confirmation_requests WHERE po_log_id = %s",
                         (po_id,))["n"] == 0


# ── What a stranger can see ──────────────────────────────────────────────────

class TestThePublicPageShowsNoMoreThanItShould:
    def test_lines_are_visible_and_prices_are_not(self, client, order, test_tenant):
        # Stock, a second supplier's order and a second order of the same supplier
        # all exist; none of them may reach the page.
        execute("INSERT INTO inventory_stock (tenant_id, sku, current_stock, warehouse) "
                "VALUES (%s, %s, 987654, 'principal')", (order["tid"], order["lines"]["a"]["sku"]))
        other_po, other_lines = _po(order["tid"], order["supplier"])
        resp = client.get(f"{PORTAL}/{order['token']}")
        assert resp.status_code == 200
        data = resp.json()["data"]
        text = resp.text
        assert {l["sku"] for l in data["lines"]} == {v["sku"] for v in order["lines"].values()}
        assert {l["quantity"] for l in data["lines"]} == {100.0, 40.0}
        for forbidden in (str(PRICE_A), str(PRICE_B), "4321", "8765", "987654",
                          "unit_cost", "cost", "price", "total_value"):
            assert forbidden not in text, forbidden
        for v in other_lines.values():
            assert v["sku"] not in text
        assert other_po not in text
        assert set(data) == {"reference", "buyer", "supplier", "language", "requested_date",
                             "expires_at", "locked", "submitted_at", "lines"}
        assert set(data["lines"][0]) == {"line_id", "sku", "name", "quantity", "unit",
                                         "requested_date", "response"}

    def test_page_is_not_cacheable_or_indexable(self, client, order):
        resp = client.get(f"{PORTAL}/{order['token']}")
        assert resp.headers["cache-control"] == "no-store"
        assert resp.headers["referrer-policy"] == "no-referrer"
        assert "noindex" in resp.headers["x-robots-tag"]

    def test_only_this_suppliers_lines_are_shown(self, client, auth_headers, test_tenant, mail):
        tid = test_tenant["id"]
        s1, s2 = _supplier(tid), _supplier(tid)
        po_id, lines = _po(tid, s1)
        # A third line on the same order belongs to another supplier.
        foreign_sku = f"CF-x-{uuid4().hex[:6]}"
        execute("INSERT INTO inventory_po_items (po_log_id, tenant_id, sku, supplier, "
                "recommended_qty, final_qty, unit_cost, status, warehouse) "
                "VALUES (%s, %s, %s, %s, 5, 5, 1, 'approved', 'principal')",
                (po_id, tid, foreign_sku, s2))
        _send(client, auth_headers, po_id, mail)
        # The send loop visits suppliers in name order, which is random here, so
        # the first e-mail is NOT necessarily s1's: pick each link by the
        # supplier the message was addressed to.
        links = {e["supplier_name"]: e["confirm_url"].rsplit("/", 1)[1] for e in mail["email"]}
        assert set(links) == {s1, s2}

        mine = client.get(f"{PORTAL}/{links[s1]}")
        assert mine.status_code == 200
        data = mine.json()["data"]
        assert data["supplier"] == s1
        assert {l["sku"] for l in data["lines"]} == {v["sku"] for v in lines.values()}
        assert foreign_sku not in mine.text and s2 not in mine.text

        # And the other supplier's link shows only the other supplier's line.
        theirs = client.get(f"{PORTAL}/{links[s2]}").json()["data"]
        assert theirs["supplier"] == s2
        assert [l["sku"] for l in theirs["lines"]] == [foreign_sku]
        assert not ({v["sku"] for v in lines.values()} & {l["sku"] for l in theirs["lines"]})


# ── Bad links are indistinguishable ──────────────────────────────────────────

class TestEveryBadLinkAnswersTheSame:
    def _fingerprint(self, resp):
        body = resp.json()
        body.pop("meta", None)
        return resp.status_code, body

    def test_unknown_malformed_expired_revoked_and_cancelled_are_one_answer(
        self, client, auth_headers, order,
    ):
        tid, po_id = order["tid"], order["po_id"]
        request_id = query_one("SELECT id FROM po_confirmation_requests WHERE po_log_id = %s",
                               (po_id,))["id"]

        tokens = {
            "unknown": core.new_token(),
            "malformed": "short",
            "wrong_case": order["token"].swapcase(),
            "empty_ish": "a" * 43,
        }
        prints = {name: self._fingerprint(client.get(f"{PORTAL}/{t}")) for name, t in tokens.items()}

        # expired
        execute("UPDATE po_confirmation_requests SET expires_at = NOW() - INTERVAL '1 second' "
                "WHERE id = %s", (request_id,))
        prints["expired"] = self._fingerprint(client.get(f"{PORTAL}/{order['token']}"))
        execute("UPDATE po_confirmation_requests SET expires_at = NOW() + INTERVAL '10 days' "
                "WHERE id = %s", (request_id,))
        assert client.get(f"{PORTAL}/{order['token']}").status_code == 200

        # revoked (through the buyer's own action)
        r = client.post(f"/api/v1/inventory/po/{po_id}/confirmation-links/{request_id}/revoke",
                        headers=auth_headers)
        assert r.status_code == 200 and r.json()["data"]["changed"] is True
        prints["revoked"] = self._fingerprint(client.get(f"{PORTAL}/{order['token']}"))
        execute("UPDATE po_confirmation_requests SET revoked_at = NULL WHERE id = %s", (request_id,))

        # order cancelled
        assert client.post(f"/api/v1/inventory/po/{po_id}/cancel", headers=auth_headers).status_code == 200
        prints["cancelled"] = self._fingerprint(client.get(f"{PORTAL}/{order['token']}"))

        distinct = {json.dumps(p, sort_keys=True) for p in prints.values()}
        assert len(distinct) == 1, prints
        status, body = next(iter(prints.values()))
        assert status == 404 and body["error_code"] == "supplier_portal_not_found"

    def test_posting_to_a_bad_link_is_the_same_404_and_writes_nothing(self, client, order):
        for token in (core.new_token(), "short", order["token"].swapcase()):
            resp = _post(client, token, _answer(order))
            assert resp.status_code == 404
            assert resp.json()["error_code"] == "supplier_portal_not_found"
        assert query_one("SELECT COUNT(*) AS n FROM po_line_confirmations WHERE po_log_id = %s",
                         (order["po_id"],))["n"] == 0

    def test_a_revoked_link_keeps_the_answers_already_given(self, client, auth_headers, order):
        assert _post(client, order["token"], _answer(order)).status_code == 200
        request_id = query_one("SELECT id FROM po_confirmation_requests WHERE po_log_id = %s",
                               (order["po_id"],))["id"]
        client.post(f"/api/v1/inventory/po/{order['po_id']}/confirmation-links/{request_id}/revoke",
                    headers=auth_headers)
        assert client.get(f"{PORTAL}/{order['token']}").status_code == 404
        assert query_one("SELECT COUNT(*) AS n FROM po_line_confirmations WHERE po_log_id = %s",
                         (order["po_id"],))["n"] == 2


# ── Answering ────────────────────────────────────────────────────────────────

class TestAnswering:
    def test_full_flow_confirm_change_note_and_lock(self, client, auth_headers, order):
        later = (_requested(order) + timedelta(days=9)).isoformat()
        note = "<img src=x onerror=alert(1)> two trucks"
        resp = _post(client, order["token"], _answer(
            order, b={"confirmed_qty": 30, "promised_date": later, "note": note}),
            headers={"User-Agent": "pytest-agent/1.0", "X-Forwarded-For": "203.0.113.7"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["counts"] == {"confirmed": 1, "changed": 1, "declined": 0}

        rows = {r["po_item_id"]: r for r in query(
            "SELECT * FROM po_line_confirmations WHERE po_log_id = %s", (order["po_id"],))}
        a, b = rows[order["lines"]["a"]["id"]], rows[order["lines"]["b"]["id"]]
        assert (a["status"], a["revision"], a["confirmed_qty"]) == ("confirmed", 1, 100.0)
        assert (b["status"], b["confirmed_qty"], b["promised_date"].isoformat()) == ("changed", 30.0, later)
        assert b["note"] == note                       # stored as typed, never rewritten
        assert b["user_agent"] == "pytest-agent/1.0"
        assert b["ip_hash"] and "203.0.113.7" not in b["ip_hash"]
        assert a["tenant_id"] == order["tid"]

        # locked: a second answer is refused, nothing is added
        again = _post(client, order["token"], _answer(order))
        assert again.status_code == 409 and again.json()["error_code"] == "supplier_portal_locked"
        assert query_one("SELECT COUNT(*) AS n FROM po_line_confirmations WHERE po_log_id = %s",
                         (order["po_id"],))["n"] == 2
        view = client.get(f"{PORTAL}/{order['token']}").json()["data"]
        assert view["locked"] is True
        assert {l["response"]["status"] for l in view["lines"]} == {"confirmed", "changed"}

        # the buyer is told: a change needs a decision, so it is a warning event
        ev = _events(order["tid"], "purchase.supplier_changes_proposed", order["po_id"])
        assert len(ev) == 1 and ev[0]["context"]["severity"] == "warning"
        assert ev[0]["context"]["reason"] == "supplier_proposed_changes"
        assert ev[0]["context"]["changed"] == 1 and ev[0]["context"]["confirmed"] == 1

    def test_all_confirmed_is_an_info_event(self, client, order):
        assert _post(client, order["token"], _answer(order)).status_code == 200
        assert len(_events(order["tid"], "purchase.supplier_confirmed", order["po_id"])) == 1
        assert _events(order["tid"], "purchase.supplier_changes_proposed", order["po_id"]) == []

    def test_declined_line(self, client, order):
        resp = _post(client, order["token"], _answer(
            order, b={"decision": "decline", "note": "out of stock"}))
        assert resp.status_code == 200
        row = query_one("SELECT status, confirmed_qty, promised_date FROM po_line_confirmations "
                        "WHERE po_item_id = %s", (order["lines"]["b"]["id"],))
        assert (row["status"], row["confirmed_qty"], row["promised_date"]) == ("declined", None, None)

    def test_a_status_sent_by_the_client_is_ignored(self, client, order):
        later = (_requested(order) + timedelta(days=4)).isoformat()
        lines = _answer(order, a={"promised_date": later, "status": "confirmed"})
        assert _post(client, order["token"], lines).status_code == 200
        assert query_one("SELECT status FROM po_line_confirmations WHERE po_item_id = %s",
                         (order["lines"]["a"]["id"],))["status"] == "changed"

    def test_cannot_answer_lines_of_another_order(self, client, order):
        other_po, other_lines = _po(order["tid"], order["supplier"])
        stolen = _answer(order)
        stolen[1] = {**stolen[1], "line_id": other_lines["a"]["id"]}
        resp = _post(client, order["token"], stolen)
        assert resp.status_code == 422
        assert resp.json()["error_params"]["reason"] == "line_unknown"
        assert query_one("SELECT COUNT(*) AS n FROM po_line_confirmations WHERE po_log_id IN (%s, %s)",
                         (order["po_id"], other_po))["n"] == 0
        assert query_one("SELECT submitted_at FROM po_confirmation_requests WHERE po_log_id = %s",
                         (order["po_id"],))["submitted_at"] is None

    @pytest.mark.parametrize("mutate,reason", [
        (lambda l: l[:1], "lines_incomplete"),
        (lambda l: [{**l[0], "confirmed_qty": -1}, l[1]], "quantity_invalid"),
        (lambda l: [{**l[0], "promised_date": "2999-01-01"}, l[1]], "date_out_of_range"),
        (lambda l: [{**l[0], "note": "x" * 501}, l[1]], "note_too_long"),
    ])
    def test_invalid_answers_are_refused_and_write_nothing(self, client, order, mutate, reason):
        resp = _post(client, order["token"], mutate(_answer(order)))
        assert resp.status_code == 422
        assert resp.json()["error_params"]["reason"] == reason
        assert query_one("SELECT COUNT(*) AS n FROM po_line_confirmations WHERE po_log_id = %s",
                         (order["po_id"],))["n"] == 0
        # and the supplier can still answer correctly afterwards
        assert _post(client, order["token"], _answer(order)).status_code == 200

    def test_oversized_and_unparseable_bodies(self, client, order):
        big = client.post(f"{PORTAL}/{order['token']}/confirm",
                          content=b'{"lines": "' + b"x" * (core.MAX_BODY_BYTES + 10) + b'"}',
                          headers={"Content-Type": "application/json"})
        assert big.status_code == 413 and big.json()["error_code"] == "supplier_portal_body_too_large"
        junk = client.post(f"{PORTAL}/{order['token']}/confirm", content=b"{not json",
                           headers={"Content-Type": "application/json"})
        assert junk.status_code == 422
        assert junk.json()["error_params"]["reason"] == "body_invalid"
        assert query_one("SELECT COUNT(*) AS n FROM po_line_confirmations WHERE po_log_id = %s",
                         (order["po_id"],))["n"] == 0

    def test_two_simultaneous_submissions_record_one(self, client, order):
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(4) as pool:
            codes = list(pool.map(
                lambda _: _post(client, order["token"], _answer(order)).status_code, range(4)))
        assert sorted(codes).count(200) == 1
        assert query_one("SELECT COUNT(*) AS n FROM po_line_confirmations WHERE po_log_id = %s",
                         (order["po_id"],))["n"] == 2


# ── The buyer's side ─────────────────────────────────────────────────────────

def _changed_order(client, order):
    later = (date.today() + timedelta(days=30)).isoformat()
    assert _post(client, order["token"], _answer(
        order, b={"confirmed_qty": 25, "promised_date": later})).status_code == 200
    return later


class TestTheBuyersView:
    def test_statuses_dates_and_summary(self, client, auth_headers, order):
        later = _changed_order(client, order)
        rows = client.get(f"/api/v1/inventory/po/{order['po_id']}/confirmations",
                          headers=auth_headers).json()["data"]
        assert len(rows) == 1
        req = rows[0]
        assert req["status"] == "changed" and req["locked"] is True and req["state"] == "active"
        assert req["pending_acceptance"] == 1
        by_sku = {l["sku"]: l["response"] for l in req["lines"]}
        assert by_sku[order["lines"]["a"]["sku"]]["status"] == "confirmed"
        b = by_sku[order["lines"]["b"]["sku"]]
        assert (b["status"], b["promised_date"], b["confirmed_qty"], b["acceptable"]) == (
            "changed", later, 25.0, True)
        summary = client.get("/api/v1/inventory/po-confirmations", headers=auth_headers).json()["data"]
        mine = [s for s in summary if s["po_log_id"] == order["po_id"]]
        assert mine == [{"po_log_id": order["po_id"], "status": "changed",
                         "pending_acceptance": 1, "suppliers": 1}]

    def test_an_order_nobody_answered_is_pending(self, client, auth_headers, order):
        rows = client.get(f"/api/v1/inventory/po/{order['po_id']}/confirmations",
                          headers=auth_headers).json()["data"]
        assert rows[0]["status"] == "pending" and rows[0]["locked"] is False
        assert all(l["response"] is None for l in rows[0]["lines"])

    def test_reopen_allows_a_new_revision_and_keeps_history(self, client, auth_headers, order):
        _changed_order(client, order)
        request_id = query_one("SELECT id FROM po_confirmation_requests WHERE po_log_id = %s",
                               (order["po_id"],))["id"]
        url = f"/api/v1/inventory/po/{order['po_id']}/confirmation-links/{request_id}/reopen"
        assert client.post(url, headers=auth_headers).json()["data"]["changed"] is True
        assert client.post(url, headers=auth_headers).json()["data"]["changed"] is False   # idempotent
        assert _post(client, order["token"], _answer(order)).status_code == 200
        revs = query("SELECT revision, status FROM po_line_confirmations WHERE po_item_id = %s "
                     "ORDER BY revision", (order["lines"]["b"]["id"],))
        assert [(r["revision"], r["status"]) for r in revs] == [(1, "changed"), (2, "confirmed")]
        # locked again after the new answer
        assert _post(client, order["token"], _answer(order)).status_code == 409

    def test_tenant_isolation(self, client, order, make_tenant_user_headers):
        _changed_order(client, order)
        conf_id = query_one("SELECT id FROM po_line_confirmations WHERE po_item_id = %s",
                            (order["lines"]["b"]["id"],))["id"]
        request_id = query_one("SELECT id FROM po_confirmation_requests WHERE po_log_id = %s",
                               (order["po_id"],))["id"]
        other = make_tenant_user_headers(role="admin")
        assert client.get(f"/api/v1/inventory/po/{order['po_id']}/confirmations",
                          headers=other).status_code == 404
        assert client.post(f"/api/v1/inventory/po/{order['po_id']}/confirmations/{conf_id}/accept",
                           headers=other).status_code == 404
        assert client.post(f"/api/v1/inventory/po/{order['po_id']}/confirmation-links/{request_id}/revoke",
                           headers=other).status_code == 404
        assert client.get("/api/v1/inventory/po-confirmations", headers=other).json()["data"] == []
        assert query_one("SELECT COUNT(*) AS n FROM po_confirmation_acceptances "
                         "WHERE po_log_id = %s", (order["po_id"],))["n"] == 0
        assert query_one("SELECT revoked_at FROM po_confirmation_requests WHERE id = %s",
                         (request_id,))["revoked_at"] is None


# ── Accepting a change: the only thing that moves the expected arrival ───────

class TestAcceptChange:
    def _overdue(self, tid, po_id):
        return [r for r in rec_svc.get_overdue_receptions(tid) if r["po_log_id"] == po_id]

    def test_promised_date_replaces_the_model_date_only_after_acceptance(
        self, client, auth_headers, test_tenant, mail,
    ):
        tid = test_tenant["id"]
        supplier = _supplier(tid)
        po_id, lines = _po(tid, supplier, generated_days_ago=60)
        _send(client, auth_headers, po_id, mail)
        order = {"tid": tid, "supplier": supplier, "po_id": po_id, "lines": lines,
                 "token": _token(mail)}

        before = self._overdue(tid, po_id)
        assert len(before) == 1 and before[0]["expected_arrival_source"] == "model"

        promised = (date.today() + timedelta(days=30)).isoformat()
        assert _post(client, order["token"], _answer(
            order, a={"promised_date": promised}, b={"promised_date": promised})).status_code == 200

        # Proposed, not accepted: purchasing still runs on the model's date.
        assert self._overdue(tid, po_id)[0]["expected_arrival_source"] == "model"
        assert self._overdue(tid, po_id)[0]["expected_arrival"] == before[0]["expected_arrival"]

        confs = query("SELECT id, po_item_id FROM po_line_confirmations WHERE po_log_id = %s", (po_id,))
        first = client.post(f"/api/v1/inventory/po/{po_id}/confirmations/{confs[0]['id']}/accept",
                            headers=auth_headers)
        assert first.status_code == 200 and first.json()["data"]["changed"] is True
        # One accepted line, one not: the order is still overdue (the unaccepted
        # line keeps the model's date).
        assert len(self._overdue(tid, po_id)) == 1
        second = client.post(f"/api/v1/inventory/po/{po_id}/confirmations/{confs[1]['id']}/accept",
                             headers=auth_headers)
        assert second.status_code == 200
        # Every line accepted: the order is expected on the promised date, so it
        # is no longer overdue.
        assert self._overdue(tid, po_id) == []

        acc = query("SELECT accepted_by FROM po_confirmation_acceptances WHERE po_log_id = %s", (po_id,))
        assert len(acc) == 2 and all(a["accepted_by"] for a in acc)
        assert len(_events(tid, "purchase.supplier_change_accepted", po_id)) == 2

    def test_accepting_twice_is_idempotent(self, client, auth_headers, order):
        _changed_order(client, order)
        conf_id = query_one("SELECT id FROM po_line_confirmations WHERE po_item_id = %s",
                            (order["lines"]["b"]["id"],))["id"]
        url = f"/api/v1/inventory/po/{order['po_id']}/confirmations/{conf_id}/accept"
        assert client.post(url, headers=auth_headers).json()["data"]["changed"] is True
        assert client.post(url, headers=auth_headers).json()["data"]["changed"] is False
        assert query_one("SELECT COUNT(*) AS n FROM po_confirmation_acceptances "
                         "WHERE po_log_id = %s", (order["po_id"],))["n"] == 1
        assert len(_events(order["tid"], "purchase.supplier_change_accepted", order["po_id"])) == 1

    def test_only_a_proposed_change_can_be_accepted(self, client, auth_headers, order):
        assert _post(client, order["token"], _answer(order)).status_code == 200
        conf_id = query_one("SELECT id FROM po_line_confirmations WHERE po_item_id = %s",
                            (order["lines"]["a"]["id"],))["id"]
        resp = client.post(f"/api/v1/inventory/po/{order['po_id']}/confirmations/{conf_id}/accept",
                           headers=auth_headers)
        assert resp.status_code == 409
        assert resp.json()["error_code"] == "confirmation_not_acceptable"

    def test_a_superseded_answer_cannot_be_accepted(self, client, auth_headers, order):
        _changed_order(client, order)
        old_id = query_one("SELECT id FROM po_line_confirmations WHERE po_item_id = %s",
                           (order["lines"]["b"]["id"],))["id"]
        request_id = query_one("SELECT id FROM po_confirmation_requests WHERE po_log_id = %s",
                               (order["po_id"],))["id"]
        client.post(f"/api/v1/inventory/po/{order['po_id']}/confirmation-links/{request_id}/reopen",
                    headers=auth_headers)
        later = (date.today() + timedelta(days=40)).isoformat()
        assert _post(client, order["token"], _answer(order, b={"promised_date": later})).status_code == 200
        resp = client.post(f"/api/v1/inventory/po/{order['po_id']}/confirmations/{old_id}/accept",
                           headers=auth_headers)
        assert resp.status_code == 409
        assert query_one("SELECT COUNT(*) AS n FROM po_confirmation_acceptances "
                         "WHERE po_log_id = %s", (order["po_id"],))["n"] == 0

    def test_scorecard_gets_a_promised_vs_real_column(self, client, auth_headers, order):
        # Yesterday (UTC) is the oldest date a supplier may still promise; the
        # goods are received today, one day after the accepted promise.
        promised = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
        resp = _post(client, order["token"], _answer(order, a={"promised_date": promised},
                                                     b={"promised_date": promised}))
        assert resp.status_code == 200, resp.text
        for c in query("SELECT id FROM po_line_confirmations WHERE po_log_id = %s", (order["po_id"],)):
            client.post(f"/api/v1/inventory/po/{order['po_id']}/confirmations/{c['id']}/accept",
                        headers=auth_headers)
        rec_svc.receive_po(order["tid"], order["po_id"], "tester")
        from backend.inventory import po_confirmation_service as svc
        got = svc.promise_stats(order["tid"])[order["supplier"].lower()]
        assert got["n"] == 2 and got["kept"] == 0 and got["avg_slip_days"] == 1.0
        rows = svc.attach_promise_stats(order["tid"], [{"supplier": order["supplier"]},
                                                       {"supplier": "Nobody"}])
        assert rows[0]["promise_kept_rate"] == 0.0 and rows[0]["promises_measured"] == 2
        assert rows[1]["promise_kept_rate"] is None and rows[1]["promises_measured"] == 0


# ── Permissions ──────────────────────────────────────────────────────────────

class TestPermissionPairs:
    def test_viewer_cannot_accept_reopen_or_revoke_and_nothing_changes(
        self, client, viewer_headers, analyst_headers, order,
    ):
        _changed_order(client, order)
        conf_id = query_one("SELECT id FROM po_line_confirmations WHERE po_item_id = %s",
                            (order["lines"]["b"]["id"],))["id"]
        request_id = query_one("SELECT id FROM po_confirmation_requests WHERE po_log_id = %s",
                               (order["po_id"],))["id"]
        base = f"/api/v1/inventory/po/{order['po_id']}"
        for url in (f"{base}/confirmations/{conf_id}/accept",
                    f"{base}/confirmation-links/{request_id}/reopen",
                    f"{base}/confirmation-links/{request_id}/revoke"):
            r = client.post(url, headers=viewer_headers)
            assert r.status_code == 403, url
        assert query_one("SELECT COUNT(*) AS n FROM po_confirmation_acceptances "
                         "WHERE po_log_id = %s", (order["po_id"],))["n"] == 0
        row = query_one("SELECT revoked_at, reopened_at FROM po_confirmation_requests WHERE id = %s",
                        (request_id,))
        assert row["revoked_at"] is None and row["reopened_at"] is None
        # the analyst can
        assert client.post(f"{base}/confirmations/{conf_id}/accept", headers=analyst_headers).status_code == 200
        assert query_one("SELECT COUNT(*) AS n FROM po_confirmation_acceptances "
                         "WHERE po_log_id = %s", (order["po_id"],))["n"] == 1

    def test_viewer_can_read_the_answers(self, client, viewer_headers, order):
        _changed_order(client, order)
        assert client.get(f"/api/v1/inventory/po/{order['po_id']}/confirmations",
                          headers=viewer_headers).status_code == 200

    def test_an_api_key_cannot_reach_the_confirmation_routes(self, app):
        from backend.api.public_surface import exposure
        routes = [r for r in app.routes
                  if "confirmation" in getattr(r, "path", "") or "supplier-portal" in getattr(r, "path", "")]
        assert len(routes) >= 7
        assert not any(exposure(r).exposed for r in routes)


# ── Abuse limits ─────────────────────────────────────────────────────────────

class TestRateLimits:
    def test_per_address_limit_answers_429_for_valid_and_invalid_links_alike(
        self, client, order, monkeypatch,
    ):
        from backend.api.v1 import supplier_portal as portal
        monkeypatch.setattr("backend.config.settings.testing_mode", False)
        monkeypatch.setattr(portal, "_READ_PER_ADDRESS", (3, 600))
        monkeypatch.setattr(portal, "_READ_PER_LINK", (1000, 600))
        headers = {"X-Forwarded-For": f"198.51.100.{uuid4().int % 250}"}
        codes = [client.get(f"{PORTAL}/{order['token']}", headers=headers).status_code for _ in range(5)]
        assert codes[:3] == [200, 200, 200] and codes[3:] == [429, 429]
        # the same address is limited on a bad link too, with the same status
        assert client.get(f"{PORTAL}/{core.new_token()}", headers=headers).status_code == 429

    def test_per_link_limit_holds_across_addresses(self, client, order, monkeypatch):
        from backend.api.v1 import supplier_portal as portal
        monkeypatch.setattr("backend.config.settings.testing_mode", False)
        monkeypatch.setattr(portal, "_READ_PER_ADDRESS", (1000, 600))
        monkeypatch.setattr(portal, "_READ_PER_LINK", (2, 600))
        base = uuid4().int % 200
        codes = [client.get(f"{PORTAL}/{order['token']}",
                            headers={"X-Forwarded-For": f"192.0.2.{base + i}"}).status_code
                 for i in range(4)]
        assert codes == [200, 200, 429, 429]

    def test_write_limit(self, client, order, monkeypatch):
        from backend.api.v1 import supplier_portal as portal
        monkeypatch.setattr("backend.config.settings.testing_mode", False)
        monkeypatch.setattr(portal, "_WRITE_PER_LINK", (2, 600))
        base = uuid4().int % 200
        codes = [_post(client, order["token"], [], headers={"X-Forwarded-For": f"192.0.2.{base + i}"}).status_code
                 for i in range(3)]
        assert codes[:2] == [422, 422] and codes[2] == 429


# ── Export and erasure ───────────────────────────────────────────────────────

class TestExportAndErase:
    def test_export_lists_the_tables_and_never_the_token_hash(self, client, order):
        from backend.tenants import data_export
        _changed_order(client, order)
        zf = zipfile.ZipFile(io.BytesIO(data_export.build_export_zip(order["tid"])))
        requests = json.loads(zf.read("po_confirmation_requests.json"))
        assert len(requests) == 1 and "token_hash" not in requests[0]
        assert core.hash_token(order["token"]) not in zf.read("po_confirmation_requests.json").decode()
        assert len(json.loads(zf.read("po_line_confirmations.json"))) == 2

    def test_erasing_the_tenant_removes_every_row(self, client, order):
        from backend.tenants import data_export
        _changed_order(client, order)
        conf_id = query_one("SELECT id FROM po_line_confirmations WHERE po_item_id = %s",
                            (order["lines"]["b"]["id"],))["id"]
        execute("INSERT INTO po_confirmation_acceptances (confirmation_id, tenant_id, po_log_id, accepted_by) "
                "VALUES (%s, %s, %s, 'u')", (conf_id, order["tid"], order["po_id"]))
        order_ids = order["tid"]
        data_export.delete_tenant(order_ids)
        for table in ("po_confirmation_requests", "po_line_confirmations",
                      "po_confirmation_acceptances"):
            assert query_one(f"SELECT COUNT(*) AS n FROM {table} WHERE tenant_id = %s",
                             (order_ids,))["n"] == 0, table
        order_list = data_export._DELETE_ORDER
        assert (order_list.index("po_confirmation_acceptances")
                < order_list.index("po_line_confirmations")
                < order_list.index("po_confirmation_requests")
                < order_list.index("inventory_po_items"))
