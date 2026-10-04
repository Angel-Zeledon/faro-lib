"""Capture what every API-key endpoint REALLY answers, into
`Frontend/public/api-response-examples.json` (served as a static file and
fetched by /desarrolladores and /api only when an endpoint is opened).

No route declares a response model, so OpenAPI cannot describe a response. This
script calls the running API, records each answer, and writes for every
endpoint:

    {"status": 200,                      # the status the call really returned
     "content_type": "application/json",
     "example": {...},                   # the body, trimmed and scrubbed
     "schema": {...}}                    # inferred from the WHOLE body

`schema` uses the same node tree as the request side (see
`backend/scripts/export_public_api.py::_node`): arrays, objects and maps stay
distinct, `required` means "present in every observed object", `nullable` means
"was null at least once". An empty list has no item shape to infer, so its
`items` is `{"type": "any"}` rather than a guess.

    # a backend running locally; with no credentials a throwaway trial tenant
    # is created (POST /trial), seeded with the demo data and trained, so the
    # lists are not empty.
    backend/.venv/Scripts/python.exe -m backend.scripts.capture_api_examples \
        --base http://127.0.0.1:8011

The output is deterministic where it can be: ids are renamed in order of first
appearance, timestamps are fixed, absolute paths, emails, phone numbers and
secrets are replaced. It still depends on the data in the account, so it is
refreshed by hand when a response shape changes — the test
`backend/tests/test_public_api_examples.py` compares the committed file with
the live routes and goes red when they disagree.

Reads are always called. Writes are called ONLY against a trial tenant this
script created itself, or an account passed with --email AND --writes (which
says, out loud, that its data is thrown away). They run in dependency order, so
the record is a real answer to a real call.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT = ROOT / "Frontend" / "src" / "data" / "public-api.json"
OUT = ROOT / "Frontend" / "public" / "api-response-examples.json"

_MAX_LIST = 1
_MAX_STR = 90
_MAX_DEPTH = 6
_MAX_KEYS = 40
FIXED_TIME = "2026-10-01T08:00:00+00:00"
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"\+\d[\d\s-]{7,}\d")
_PHONE_KEYS = re.compile(r"(phone|whatsapp|tel)", re.I)
_SECRET_KEYS = re.compile(r"(token|secret|password|api_key|key_hash|refresh|^key$)", re.I)
_ID = re.compile(r"^([a-z]{2,6})_[0-9a-f]{12}$")
_DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_PATH = re.compile(r"^([A-Za-z]:\\|/)[^\s]*[\\/][^\s]*$")


# ── scrubbing ────────────────────────────────────────────────────────────────

class Scrubber:
    """Replaces what differs between runs or must not be published, the same
    way every time: ids are renamed in order of first appearance."""

    def __init__(self) -> None:
        self.ids: dict[str, str] = {}

    def _id(self, value: str) -> str:
        if value not in self.ids:
            prefix = value.split("_", 1)[0]
            digest = hashlib.sha1(f"{prefix}:{len(self.ids)}".encode()).hexdigest()[:12]
            self.ids[value] = f"{prefix}_{digest}"
        return self.ids[value]

    def string(self, key: str, value: str) -> str:
        if _SECRET_KEYS.search(key):
            return "…"
        if key == "worker_id":
            return "worker-1"   # the machine's name
        if _PHONE_KEYS.search(key) and value:
            return "+50688887777"
        m = _ID.match(value)
        if m:
            return self._id(value)
        if _DATETIME.match(value):
            return FIXED_TIME
        if _PATH.match(value):
            return "/app/storage/" + value.replace("\\", "/").rsplit("/", 1)[-1]
        value = _EMAIL.sub("user@example.com", value)
        value = _PHONE.sub("+50688887777", value)
        value = re.sub(r"\b[a-z]{2,6}_[0-9a-f]{12}\b", lambda m: self._id(m.group(0)), value)
        return value if len(value) <= _MAX_STR else value[:_MAX_STR] + "…"

    def scrub(self, value, key: str = "", depth: int = 0):
        if isinstance(value, dict):
            if depth >= _MAX_DEPTH:
                return {}
            return {k: self.scrub(v, k, depth + 1) for k, v in list(value.items())[:_MAX_KEYS]}
        if isinstance(value, list):
            if depth >= _MAX_DEPTH:
                return []
            return [self.scrub(v, key, depth + 1) for v in value]
        if isinstance(value, str):
            return self.string(key, value)
        return value


def _richest(items: list) -> object:
    """The item of a list that shows the most: the most non-null fields."""
    def score(v):
        if isinstance(v, dict):
            return sum(1 for x in v.values() if x not in (None, "", [], {}))
        return 0
    return max(items[:25], key=score)


def _trim(value, depth: int = 0):
    if isinstance(value, dict):
        return {k: _trim(v, depth + 1) for k, v in list(value.items())[:_MAX_KEYS]}
    if isinstance(value, list):
        if not value:
            return []
        keep = [_richest(value)] if _MAX_LIST == 1 else value[:_MAX_LIST]
        return [_trim(v, depth + 1) for v in keep]
    return value


# ── schema inference ─────────────────────────────────────────────────────────

def infer(value) -> dict:
    """The schema node of one observed value."""
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, int):
        return {"type": "integer"}
    if isinstance(value, float):
        return {"type": "number"}
    if isinstance(value, str):
        node = {"type": "string"}
        if _DATETIME.match(value):
            node["format"] = "date-time"
        elif _DATE.match(value):
            node["format"] = "date"
        return node
    if isinstance(value, list):
        items: dict | None = None
        for v in value[:300]:
            n = infer(v)
            items = n if items is None else merge(items, n)
        return {"type": "array", "items": items or {"type": "any"}}
    if isinstance(value, dict):
        if not value:
            return {"type": "object", "free_form": True}
        fields = [{"name": k, "required": True, **infer(v)} for k, v in value.items()]
        node = {"type": "object", "fields": fields}
        # A dict keyed by data (SKUs, dates) is a map, not a record: many keys,
        # all with the same shape.
        if len(fields) >= 10:
            shapes = {json.dumps({k: v for k, v in f.items() if k not in ("name", "required")}, sort_keys=True) for f in fields}
            if len(shapes) == 1:
                one = {k: v for k, v in fields[0].items() if k not in ("name", "required")}
                return {"type": "object", "values": one}
        return node
    return {"type": "any"}


def merge(a: dict, b: dict) -> dict:
    """Combine two observations of the same position."""
    if a["type"] == "null" and b["type"] != "null":
        return {**b, "nullable": True}
    if b["type"] == "null":
        return {**a, "nullable": True} if a["type"] != "null" else a
    nullable = a.get("nullable") or b.get("nullable")
    if a["type"] == "any":
        out = dict(b)
    elif b["type"] == "any":
        out = dict(a)
    elif a["type"] == b["type"]:
        out = dict(a)
        if a["type"] == "array":
            out["items"] = merge(a["items"], b["items"])
        elif a["type"] == "object":
            if "fields" in a and "fields" in b:
                by_b = {f["name"]: f for f in b["fields"]}
                names = [f["name"] for f in a["fields"]] + [f["name"] for f in b["fields"] if f["name"] not in {x["name"] for x in a["fields"]}]
                by_a = {f["name"]: f for f in a["fields"]}
                fields = []
                for n in names:
                    if n in by_a and n in by_b:
                        m = merge(by_a[n], by_b[n])
                        m["name"] = n
                        m["required"] = by_a[n]["required"] and by_b[n]["required"]
                        fields.append(m)
                    else:
                        f = dict(by_a.get(n) or by_b[n])
                        f["required"] = False
                        fields.append(f)
                out["fields"] = fields
            elif "values" in a and "values" in b:
                out["values"] = merge(a["values"], b["values"])
            elif "free_form" in a and "fields" in b:
                out = dict(b)
            elif "fields" in a and "free_form" in b:
                out = dict(a)
        elif a["type"] == "string" and a.get("format") != b.get("format"):
            out.pop("format", None)
    elif {a["type"], b["type"]} == {"integer", "number"}:
        out = {"type": "number"}
    else:
        out = {"type": f"{a['type']} | {b['type']}", "any_of": [a, b]}
    if nullable:
        out["nullable"] = True
    return out


def forget_required(node: dict) -> dict:
    """In a RESPONSE `required` would read as an obligation of the caller. The
    key is kept (the renderer ignores it for responses) but a flag says so."""
    return node


# ── the run ──────────────────────────────────────────────────────────────────

_NOUN_IDS = {
    "suppliers": "supplier_id", "events": "event_id", "transfers": "transfer_id",
    "scenarios": "scenario_id", "webhooks": "webhook_id", "documents": "doc_id",
    "sessions": "session_id", "data-sources": "source_id", "price-breaks": "price_break_id",
    "po": "po_log_id", "datasets": "dataset_id", "jobs": "job_id",
}
_ID_KEYS = ("session_id", "supplier_id", "po_log_id", "dataset_id", "job_id", "scenario_id",
            "source_id", "event_id", "transfer_id", "webhook_id", "doc_id", "price_break_id")

# A write that moves a session back to MODELS_CONFIGURED (every `configure/*`)
# must not touch the trained session the reads are documented from, so those
# run on a scratch session. These need the trained one.
_NEEDS_TRAINED = ("/reconcile", "/reports/generate", "/drift", "/predict", "/overrides",
                  "/scenarios", "/analyst/", "/shap/", "/schedule")

# Bodies that must name this tenant's real data (the documented examples are
# illustrative: a user's file will not have the demo file's column names).
_CAPTURE_BODIES = {
    ("POST", "/sessions/{session_id}/configure/columns"): {"canonical_mapping": {"sku": "sku", "date": "fecha", "demand": "cantidad"}},
    ("POST", "/sessions/{session_id}/columns"): {"canonical_mapping": {"sku": "sku", "date": "fecha", "demand": "cantidad"}},
}

# Never called, even on a throwaway tenant: they would start long work or reach
# outside (a real database, a model provider, a mail transport).
_NEVER = {
    ("POST", "/sessions/{session_id}/train"),
    ("GET", "/mcp"),
    ("POST", "/data-sources/sql"),
    ("POST", "/data-sources/{source_id}/test-connection"),
    ("POST", "/data-sources/{source_id}/execute-query"),
    ("POST", "/data-sources/{source_id}/export-query"),
    ("POST", "/data-sources/{source_id}/materialize"),
    ("PATCH", "/data-sources/{source_id}/sql-config"),
    ("PATCH", "/data-sources/{source_id}/query"),
    ("DELETE", "/jobs/{job_id}"),
}


def _fill(template: str, known: dict) -> str | None:
    miss = []

    def sub(m):
        name = m.group(1).split(":")[0]
        if name in known:
            return str(known[name])
        miss.append(name)
        return "X"
    out = re.sub(r"\{([^}]+)\}", sub, template)
    return None if miss else out


def _subst(value, known: dict):
    """Replace `<name>` placeholders in a documented example with real ids."""
    if isinstance(value, str):
        m = re.fullmatch(r"<(\w+)>", value)
        return known.get(m.group(1), value) if m else value
    if isinstance(value, list):
        return [_subst(v, known) for v in value]
    if isinstance(value, dict):
        return {k: _subst(v, known) for k, v in value.items()}
    return value


def _harvest(ep: dict, data, known: dict, created: dict) -> None:
    """Remember ids from answers, to fill path parameters of later calls."""
    items = data if isinstance(data, list) else (
        data.get("items") if isinstance(data, dict) and isinstance(data.get("items"), list) else None)
    first = items[0] if items and isinstance(items[0], dict) else (data if isinstance(data, dict) else {})
    for k in _ID_KEYS:
        if isinstance(first, dict) and isinstance(first.get(k), str):
            known.setdefault(k, first[k])
    if ep["method"] == "POST" and isinstance(first, dict):
        noun = ep["path"].strip("/").split("/")[-1]
        param = _NOUN_IDS.get(noun)
        rid = first.get("id") or (first.get(param) if param else None)
        if param and isinstance(rid, str):
            created[param] = rid
            known.setdefault(param, rid)
    if ep["method"] == "GET" and isinstance(first, dict) and isinstance(first.get("id"), str):
        noun = ep["path"].strip("/").split("/")[-1]
        param = _NOUN_IDS.get(noun)
        if param:
            known.setdefault(param, first["id"])


def _record(scrub: Scrubber, resp: httpx.Response) -> dict | None:
    ctype = resp.headers.get("content-type", "").split(";")[0]
    if resp.status_code == 204 or not resp.content:
        return {"status": resp.status_code}
    if "json" not in ctype:
        return {"status": resp.status_code, "content_type": ctype, "binary": True}
    body = resp.json()
    return {
        "status": resp.status_code,
        "content_type": ctype,
        "example": scrub.scrub(_trim(body)),
        "schema": infer(body),
    }


def _csv_for(path: str, templates: dict) -> tuple[str, bytes, str]:
    if path.endswith("/documents"):
        return "notes.txt", b"Supplier terms: payment at 30 days, delivery in 7 days.\n", "text/plain"
    if path.endswith("/reconcile"):
        rows = ["sku,date,actual"] + [f"SKU-001,2026-10-{d:02d},{12 + d}" for d in range(1, 8)]
        return "actuals.csv", ("\n".join(rows) + "\n").encode(), "text/csv"
    if "suppliers/import" in path and "suppliers" in templates:
        return "suppliers.csv", templates["suppliers"], "text/csv"
    if "po/import" in path and "po" in templates:
        return "orders.csv", templates["po"], "text/csv"
    if "inventory/bulk" in path and "inventory" in templates:
        return "inventory.csv", templates["inventory"], "text/csv"
    rows = ["sku,date,quantity"]
    for sku in ("SKU-001", "SKU-002"):
        for d in range(1, 29):
            rows.append(f"{sku},2026-09-{d:02d},{10 + d % 5}")
    return "sales.csv", ("\n".join(rows) + "\n").encode(), "text/csv"


def _wait_completed(c: httpx.Client, prefix: str, session_id: str, minutes: float) -> bool:
    deadline = time.time() + minutes * 60
    while time.time() < deadline:
        s = c.get(f"{prefix}/sessions/{session_id}").json().get("data", {})
        if s.get("status") in ("COMPLETED", "FAILED"):
            return s.get("status") == "COMPLETED"
        time.sleep(8)
    return False


_DEMO_CSV = ROOT / "backend" / "resources" / "demo_ventas.csv"
_DEMO_STOCK = {
    "SKU-001": (120, 7, 2.5), "SKU-002": (900, 9, 1.2), "SKU-003": (40, 5, 3.1),
    "SKU-004": (7000, 15, 2.4), "SKU-005": (790, 10, 0.9), "SKU-006": (800, 8, 1.6),
}


def _seed_demo(c: httpx.Client, prefix: str, known: dict, wait_minutes: float) -> None:
    """Give the trial tenant something to read: the bundled sales history, a
    session trained on it with two fast models (the demo quick-start trains
    five, global_lgbm among them, which can take far longer than a capture
    should), and stock for a few SKUs. All through the public endpoints."""
    up = c.post(f"{prefix}/datasets", files={"file": ("demo_ventas.csv", _DEMO_CSV.read_bytes(), "text/csv")})
    up.raise_for_status()
    known["dataset_id"] = up.json()["data"]["id"]
    sid = c.post(f"{prefix}/sessions", json={"name": "Capture run"}).json()["data"]["id"]
    known["session_id"] = sid
    steps = [
        ("POST", f"/sessions/{sid}/dataset", {"dataset_id": known["dataset_id"]}),
        ("GET", f"/sessions/{sid}/inspect", None),
        ("POST", f"/sessions/{sid}/configure/columns",
         {"canonical_mapping": {"sku": "sku", "date": "fecha", "demand": "cantidad"}}),
        ("POST", f"/sessions/{sid}/configure/features", {"lags": [1, 7], "rolling": [7], "diffs": [1], "calendar": True}),
        ("POST", f"/sessions/{sid}/configure/models", {"mode": "selected", "selected_models": ["ets", "croston"]}),
        ("POST", f"/sessions/{sid}/configure/validation",
         {"train_ratio": 0.8, "walk_forward": True, "wfv_splits": 3, "min_history": 20, "seasonal_period": 7}),
        ("POST", f"/sessions/{sid}/train", {"user_granularity": "daily"}),
    ]
    for method, path, body in steps:
        r = c.request(method, prefix + path, json=body) if body is not None else c.request(method, prefix + path)
        r.raise_for_status()
    # A second session with the dataset attached, for the writes that would
    # send the trained one back to MODELS_CONFIGURED.
    scratch = c.post(f"{prefix}/sessions", json={"name": "Capture scratch"}).json()["data"]["id"]
    c.post(f"{prefix}/sessions/{scratch}/dataset", json={"dataset_id": known["dataset_id"]}).raise_for_status()
    c.get(f"{prefix}/sessions/{scratch}/inspect").raise_for_status()
    known["scratch_session_id"] = scratch
    for sku, (stock, lead, cost) in _DEMO_STOCK.items():
        c.put(f"{prefix}/inventory/stock/{sku}", json={
            "current_stock": stock, "lead_time_days": lead, "unit_cost": cost, "moq": 12,
            "display_name": sku, "supplier": "Distribuidora Andina"}).raise_for_status()
    print(f"demo session completed: {_wait_completed(c, prefix, sid, wait_minutes)}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", default="http://127.0.0.1:8011")
    ap.add_argument("--email", help="an existing account; reads only, no trial tenant is made")
    ap.add_argument("--password")
    ap.add_argument("--writes", action="store_true",
                    help="with --email: the account is DISPOSABLE; seed it and call the write endpoints too")
    ap.add_argument("--wait-minutes", type=float, default=10.0,
                    help="how long to wait for the training of a new trial tenant's session")
    ap.add_argument("--verbose", action="store_true", help="print every call that was refused")
    args = ap.parse_args(argv)

    snapshot = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    prefix = snapshot["base_path"]
    # Names that must be unique per tenant: a second run against the same
    # throwaway account must not collide with what the first one left behind.
    tag = format(int(time.time()) % 0xFFFF, "04x")
    second = f"secondary-{tag}"
    _CAPTURE_BODIES.update({
        ("POST", "/inventory/suppliers"): {"name": f"Distribuidora Andina {tag}", "email": "ventas@andina.example", "lead_time_days": 7},
        ("POST", "/inventory/warehouses"): {"name": second, "is_default": False},
        ("PUT", "/inventory/warehouses/lanes"): {"from_warehouse": "principal", "to_warehouse": second, "lead_time_days": 3, "cost_per_unit": 0.1, "fixed_cost": 5.0},
        ("POST", "/inventory/transfers"): {"from_warehouse": "principal", "to_warehouse": second, "items": [{"sku": "SKU-001", "qty": 10}], "notes": "Weekly rebalancing"},
    })
    lane_query = {"from_warehouse": "principal", "to_warehouse": second}
    endpoints = [e for t in snapshot["tags"] for e in t["endpoints"]]
    scrub = Scrubber()
    known: dict = {"sku": "SKU-001", "sku_id": "SKU-001", "parent_sku": "SKU-001", "child_sku": "SKU-002"}
    created: dict = {}
    examples: dict = {}
    templates: dict = {}
    writes = (not args.email) or args.writes

    with httpx.Client(base_url=args.base, timeout=120) as c:
        if args.email:
            creds = {"email": args.email, "password": args.password}
        else:
            r = c.post(f"{prefix}/trial", json={})
            r.raise_for_status()
            d = r.json()["data"]
            creds = {"email": d["email"], "password": d["password"]}
        r = c.post(f"{prefix}/auth/login", json=creds)
        r.raise_for_status()
        c.headers["Authorization"] = f"Bearer {r.json()['data']['access_token']}"

        if writes:
            _seed_demo(c, prefix, known, args.wait_minutes)

        for kind, path in (("suppliers", "/inventory/suppliers/import/template"),
                           ("po", "/inventory/po/import/template"),
                           ("inventory", "/inventory/template.csv")):
            t = c.get(prefix + path)
            if t.status_code == 200:
                templates[kind] = t.content

        def call(ep: dict) -> None:
            scope = dict(known)
            if ep["method"] != "GET" and "scratch_session_id" in known and not any(m in ep["path"] for m in _NEEDS_TRAINED):
                scope["session_id"] = known["scratch_session_id"]
            path = _fill(ep["path"], {**scope, "id": known.get("_id", "")})
            if path is None or (ep["method"], ep["path"]) in _NEVER:
                return
            params = {p["name"]: _subst(p["example"], known) for p in ep["parameters"]
                      if p["in"] == "query" and p["required"]}
            if ep["method"] == "DELETE" and ep["path"].endswith("/warehouses/lanes"):
                params = lane_query
            headers = {}
            kwargs: dict = {}
            b = ep["request_body"]
            if b and b["content_type"].startswith("multipart/"):
                name, blob, mime = _csv_for(ep["path"], templates)
                file_field = next((f["name"] for f in (b["schema"].get("fields") or []) if f["type"] == "file"), "file")
                form = {f["name"]: str(_subst(f.get("default", ""), known))
                        for f in (b["schema"].get("fields") or []) if f["type"] != "file" and f["required"]}
                kwargs = {"files": {file_field: (name, blob, mime)}, "data": form}
            elif b:
                kwargs = {"json": _subst(_CAPTURE_BODIES.get((ep["method"], ep["path"]), b["example"]), scope)}
            try:
                resp = c.request(ep["method"], prefix + path, params=params, headers=headers, **kwargs)
            except httpx.HTTPError as exc:
                print("  error", ep["id"], exc)
                return
            if not 200 <= resp.status_code < 300:
                if args.verbose:
                    print(f"  {resp.status_code} {ep['method']} {ep['path']}: {resp.text[:160]}")
                return
            rec = _record(scrub, resp)
            if rec is None:
                return
            old = examples.get(ep["id"])
            # A read repeated after the writes replaces the earlier answer only
            # when it shows more (an empty list is a poor example).
            if old is None or len(json.dumps(rec.get("example"))) > len(json.dumps(old.get("example"))):
                examples[ep["id"]] = rec
            try:
                body = resp.json() if resp.content and "json" in resp.headers.get("content-type", "") else None
            except ValueError:
                body = None
            if isinstance(body, dict):
                _harvest(ep, body.get("data"), known, created)

        gets = [e for e in endpoints if e["method"] == "GET"]
        for ep in sorted(gets, key=lambda e: "{" in e["path"]):
            call(ep)
        if writes:
            posts = [e for e in endpoints if e["method"] in ("POST", "PUT", "PATCH")]
            # Creating things first lets later calls find what they need; a
            # second pass picks up what the first one could not fill.
            for _ in range(2):
                for ep in sorted(posts, key=lambda e: (len(e["path"].split("/")), e["path"])):
                    if ep["id"] not in examples:
                        call(ep)
            # Reads again: they now see the data the writes made.
            for ep in sorted(gets, key=lambda e: "{" in e["path"]):
                call(ep)
            deletes = [e for e in endpoints if e["method"] == "DELETE"]
            for ep in deletes:
                # Only what this run created: never the demo session itself.
                param = re.findall(r"\{(\w+)\}", ep["path"])
                scoped = dict(known)
                for p in param:
                    if p in created:
                        scoped[p] = created[p]
                    elif p in ("session_id", "dataset_id", "source_id", "supplier_id", "po_log_id"):
                        scoped.pop(p, None)
                saved, known = known, scoped
                try:
                    call(ep)
                finally:
                    known = saved

    ordered = {ep["id"]: examples[ep["id"]] for ep in endpoints if ep["id"] in examples}
    OUT.write_text(json.dumps(ordered, separators=(",", ":"), ensure_ascii=False) + "\n",
                   encoding="utf-8", newline="\n")
    print(f"captured {len(ordered)} of {len(endpoints)} endpoints -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
