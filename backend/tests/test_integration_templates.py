"""The importable n8n templates (integrations/n8n/): they stay valid, they only call
endpoints and read event fields that exist, they hold no secret, and the signature
step they ship is run under Node against real signatures. No database."""

import json
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from backend.scripts import export_public_api as exporter
from backend.webhooks import signing
from backend.webhooks.catalog import EVENT_TYPES

N8N_DIR = Path(__file__).resolve().parents[2] / "integrations" / "n8n"
TEMPLATES = sorted(N8N_DIR.glob("*.json"))

EVENT_FOR = {
    "order-approved-to-slack-and-sheets.json": "purchase_order.approved",
    "stockout-imminent-to-jira.json": "stockout.imminent",
}


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _walk(value):
    if isinstance(value, dict):
        for v in value.values():
            yield from _walk(v)
    elif isinstance(value, list):
        for v in value:
            yield from _walk(v)
    elif isinstance(value, str):
        yield value


def _endpoints():
    snap = json.loads(exporter.SNAPSHOT.read_text(encoding="utf-8"))
    return [(e["method"], e["path"]) for t in snap["tags"] for e in t["endpoints"]]


def test_the_templates_exist():
    assert len(TEMPLATES) == 3


@pytest.mark.parametrize("path", TEMPLATES, ids=lambda p: p.name)
class TestEveryTemplate:

    def test_the_graph_is_well_formed(self, path):
        wf = _load(path)
        names = [n["name"] for n in wf["nodes"]]
        ids = [n["id"] for n in wf["nodes"]]
        assert len(set(names)) == len(names) and len(set(ids)) == len(ids)
        assert wf["active"] is False                  # never switched on by an import
        for source, outputs in wf["connections"].items():
            assert source in names
            for branch in outputs["main"]:
                for link in branch:
                    assert link["node"] in names
        for n in wf["nodes"]:
            assert {"parameters", "id", "name", "type", "typeVersion", "position"} <= set(n)
            assert n["type"].startswith("n8n-nodes-base.")

    def test_it_holds_no_secret_and_no_credential_reference(self, path):
        text = path.read_text(encoding="utf-8")
        assert '"credentials"' not in text
        assert not re.search(r"sk_live_[A-Za-z0-9_-]{8,}", text)
        assert not re.search(r"(?i)bearer\s+[a-z0-9_-]{20,}", text)
        assert "whsec_" not in text

    def test_every_n8n_expression_is_balanced(self, path):
        for s in _walk(_load(path)["nodes"]):
            assert s.count("{{") == s.count("}}"), s

    def test_every_stockai_call_is_a_real_endpoint(self, path):
        known = _endpoints()
        for node in _load(path)["nodes"]:
            if node["type"] != "n8n-nodes-base.httpRequest":
                continue
            url = node["parameters"]["url"]
            assert url.startswith("https://app.stockai.es/api/v1/")
            route = "/" + url.split("/api/v1/", 1)[1]
            route = re.sub(r"REPLACE_WITH_\w+", "{id}", route)
            method = node["parameters"]["method"]
            assert any(m == method and re.fullmatch(re.sub(r"\{\w+\}", "[^/]+", p), route)
                       for m, p in known), f"{method} {route} is not on the public API"


@pytest.mark.parametrize("name,event", sorted(EVENT_FOR.items()))
def test_a_webhook_template_reads_only_fields_its_event_carries(name, event):
    wf = _load(N8N_DIR / name)
    allowed = set(EVENT_TYPES[event].data_keys)
    used = set()
    for s in _walk(wf["nodes"]):
        used |= set(re.findall(r"\$json\.data\.(\w+)", s))
    assert used, "the template never reads the event"
    assert used <= allowed, f"fields not in {event}: {sorted(used - allowed)}"
    hook = next(n for n in wf["nodes"] if n["type"] == "n8n-nodes-base.webhook")
    assert hook["parameters"]["options"]["rawBody"] is True       # the signature covers the raw bytes


def test_the_drive_template_uploads_the_field_the_endpoint_reads():
    wf = _load(N8N_DIR / "drive-file-to-dataset.json")
    node = next(n for n in wf["nodes"] if n["type"] == "n8n-nodes-base.httpRequest")
    assert node["parameters"]["bodyParameters"]["parameters"][0]["name"] == "file"
    assert node["onError"] == "continueErrorOutput"            # a refusal reaches the Slack node
    assert wf["connections"]["Replace the dataset"]["main"][1][0]["node"] == "Say it failed"


# ── The signature step, run for real ─────────────────────────────────────────

NODE = shutil.which("node")

HARNESS = r"""
const fs = require('fs');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const wf = JSON.parse(fs.readFileSync(input.template, 'utf8'));
const code = wf.nodes.find(n => n.type === 'n8n-nodes-base.code').parameters.jsCode;
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
const run = new AsyncFunction('$env', '$input', '$getWorkflowStaticData', 'require', code);
const store = input.store || {};
(async () => {
  try {
    const out = await run.call(
      { helpers: { getBinaryDataBuffer: async () => Buffer.from(input.body, 'utf8') } },
      { STOCKAI_WEBHOOK_SECRET: input.secret },
      { first: () => ({ json: { headers: input.headers } }) },
      () => store,
      require,
    );
    console.log(JSON.stringify({ ok: true, out, store }));
  } catch (e) {
    console.log(JSON.stringify({ ok: false, error: e.message }));
  }
})();
"""


@pytest.fixture()
def verify(tmp_path):
    if NODE is None:
        pytest.skip("node is not installed")
    harness = tmp_path / "harness.js"
    harness.write_text(HARNESS, encoding="utf-8")

    def run(template: str, *, body: str, headers: dict, secret: str = "whsec-test", store=None):
        payload = json.dumps({"template": str(N8N_DIR / template), "body": body,
                              "headers": headers, "secret": secret, "store": store or {}})
        done = subprocess.run([NODE, str(harness)], input=payload, text=True,
                              capture_output=True, timeout=30)
        assert done.returncode == 0, done.stderr
        return json.loads(done.stdout)

    return run


def _delivery(secret="whsec-test", age=0, tamper=False):
    body = json.dumps({"id": "evt_" + "a" * 32, "type": "purchase_order.approved",
                       "data": {"po_number": "OC-000001", "approved_amount": 500}})
    ts = int(time.time()) - age
    header = signing.signature_header(secret, ts, body.encode())
    if tamper:
        body = body.replace("500", "900")
    return body, {"x-stockai-signature": header}


@pytest.mark.parametrize("name", sorted(EVENT_FOR))
class TestTheSignatureStep:

    def test_a_genuine_delivery_passes_and_carries_the_event(self, verify, name):
        body, headers = _delivery()
        result = verify(name, body=body, headers=headers)
        assert result["ok"] is True and result["out"][0]["json"]["data"]["po_number"] == "OC-000001"

    def test_a_changed_body_is_rejected(self, verify, name):
        body, headers = _delivery(tamper=True)
        result = verify(name, body=body, headers=headers)
        assert result["ok"] is False and "signature" in result["error"]

    def test_the_wrong_secret_is_rejected(self, verify, name):
        body, headers = _delivery(secret="someone-else")
        assert verify(name, body=body, headers=headers)["ok"] is False

    def test_an_old_delivery_is_rejected_as_a_replay(self, verify, name):
        body, headers = _delivery(age=3600)
        result = verify(name, body=body, headers=headers)
        assert result["ok"] is False and "too old" in result["error"]

    def test_a_missing_header_is_rejected(self, verify, name):
        body, _ = _delivery()
        assert verify(name, body=body, headers={})["ok"] is False

    def test_the_same_event_is_handled_once(self, verify, name):
        body, headers = _delivery()
        first = verify(name, body=body, headers=headers)
        again = verify(name, body=body, headers=headers, store=first["store"])
        assert first["out"] and again["ok"] is True and again["out"] == []

    def test_no_secret_configured_fails_loudly(self, verify, name):
        body, headers = _delivery()
        result = verify(name, body=body, headers=headers, secret="")
        assert result["ok"] is False and "STOCKAI_WEBHOOK_SECRET" in result["error"]


def test_both_webhook_templates_ship_the_same_verification_code():
    codes = {n["parameters"]["jsCode"]
             for f in EVENT_FOR for n in _load(N8N_DIR / f)["nodes"]
             if n["type"] == "n8n-nodes-base.code"}
    assert len(codes) == 1
