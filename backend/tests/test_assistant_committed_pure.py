"""The assistant's committed-demand tool and context section, on a stub account
(no database). The wall that keeps every account read a GET lives in
test_assistant.py and now also covers `AccountData.committed_demand`."""
import json

from backend.assistant import context, tools


class _Stub:
    def __init__(self, ledger, missing=None):
        self.committed_demand = ledger
        self.missing = missing or {}


LEDGER = {
    "items": [
        {"sku": "A", "customer": "Big Corp", "delivery_date": "2027-01-10", "quantity": 100,
         "probability": 1.0, "at_risk": True, "shortfall": 40.0,
         "latest_safe_order_date": "2026-12-20", "order_date_passed": False},
        {"sku": "B", "customer": "Small", "delivery_date": "2027-02-01", "quantity": 10,
         "probability": 1.0, "at_risk": False, "shortfall": 0.0},
        {"sku": "C", "customer": "Small", "delivery_date": "2027-03-01", "quantity": 5,
         "probability": 1.0, "at_risk": None, "shortfall": None},
    ],
    "by_customer": [{"customer": "Big Corp", "open": 1, "at_risk": 1}],
}


def test_tool_is_registered_and_read_shaped():
    assert "list_committed_demand" in tools.BY_NAME
    assert tools.BY_NAME["list_committed_demand"].name.split("_")[0] in ("find", "get", "list")


def test_tool_filters_and_keeps_unknown_distinct_from_covered():
    out, ok = tools.run_tool(_Stub(LEDGER), "list_committed_demand",
                             json.dumps({"customer": "small"}))
    assert ok
    body = json.loads(out)
    assert body["total_matching"] == 2
    assert [i["at_risk"] for i in body["items"]] == [False, None]


def test_tool_at_risk_only():
    body = json.loads(tools.run_tool(_Stub(LEDGER), "list_committed_demand",
                                     json.dumps({"at_risk_only": True}))[0])
    assert [i["sku"] for i in body["items"]] == ["A"]


def test_tool_reports_a_failed_read_instead_of_an_empty_ledger():
    body = json.loads(tools.run_tool(_Stub({}, {"committed_demand": "boom"}),
                                     "list_committed_demand", "{}")[0])
    assert "error" in body and "items" not in body


def test_context_section_names_the_risk_and_the_unknown():
    s = context._committed(_Stub(LEDGER))
    text = "\n".join(s.lines)
    assert "3 open commitments, 1 at risk" in text and "1 with no verdict" in text
    assert "Big Corp" in text and "2026-12-20" in text
    assert s.priority > 50


def test_context_omits_an_empty_ledger_but_flags_a_failed_read():
    assert context._committed(_Stub({"items": []})) is None
    failed = context._committed(_Stub({}, {"committed_demand": "boom"}))
    assert failed is not None and "do not say there are none" in failed.lines[0]
