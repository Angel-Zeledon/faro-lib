"""Retiring an endpoint: the headers it carries and the notice it must have given.
Builds its own tiny app, so it needs neither the database nor the real routes."""

from datetime import date

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api import deprecation as dep
from backend.middleware.deprecation import DeprecationMiddleware


def _entry(**over):
    base = dict(announced=date(2026, 1, 1), sunset=date(2026, 12, 31), successor="/api/v2/alerts")
    base.update(over)
    return dep.Deprecation(**base)


class TestHeaders:
    def test_a_route_that_is_not_deprecated_gets_nothing(self):
        assert dep.headers_for("GET", "/alerts", registry={}) == {}

    def test_a_deprecated_route_says_when_it_was_announced_when_it_goes_and_what_replaces_it(self):
        h = dep.headers_for("get", "/alerts", registry={("GET", "/alerts"): _entry()})
        assert h["Deprecation"] == "@1767225600"                  # 2026-01-01T00:00:00Z
        assert h["Sunset"] == "Thu, 31 Dec 2026 00:00:00 GMT"
        assert '</api/v2/alerts>; rel="successor-version"' in h["Link"]
        assert 'rel="deprecation"' in h["Link"]

    def test_without_a_successor_there_is_no_successor_link(self):
        h = dep.headers_for("GET", "/x", registry={("GET", "/x"): _entry(successor=None)})
        assert "successor-version" not in h["Link"] and 'rel="deprecation"' in h["Link"]

    def test_only_the_listed_method_is_marked(self):
        reg = {("GET", "/alerts"): _entry()}
        assert dep.headers_for("POST", "/alerts", registry=reg) == {}


class TestRegistryRules:
    def test_nothing_is_deprecated_today(self):
        # Adding an entry is a decision: this assertion is the place to make it.
        assert dep.DEPRECATIONS == {}

    def test_every_listed_route_gave_the_minimum_notice(self):
        short = {k: dep.notice_days(v) for k, v in dep.DEPRECATIONS.items()
                 if dep.notice_days(v) < dep.MIN_NOTICE_DAYS}
        assert short == {}, f"deprecations with less than {dep.MIN_NOTICE_DAYS} days of notice"

    def test_the_helper_measures_notice_in_days(self):
        assert dep.notice_days(_entry(announced=date(2026, 1, 1), sunset=date(2026, 7, 1))) == 181


class TestMiddleware:
    def _client(self, monkeypatch):
        monkeypatch.setattr(dep, "DEPRECATIONS", {("GET", "/old"): _entry()})
        app = FastAPI()
        app.add_middleware(DeprecationMiddleware)

        @app.get("/api/v1/old")
        def old():
            return {"ok": True}

        @app.get("/api/v1/new")
        def new():
            return {"ok": True}

        @app.get("/api/v1/things/{thing_id}")
        def thing(thing_id: str):
            return {"id": thing_id}

        return TestClient(app)

    def test_the_deprecated_route_carries_the_headers_and_still_answers(self, monkeypatch):
        r = self._client(monkeypatch).get("/api/v1/old")
        assert r.status_code == 200 and r.json() == {"ok": True}
        assert r.headers["Deprecation"].startswith("@")
        assert r.headers["Sunset"].endswith("GMT")

    def test_other_routes_are_untouched(self, monkeypatch):
        c = self._client(monkeypatch)
        for path in ("/api/v1/new", "/api/v1/things/abc"):
            r = c.get(path)
            assert r.status_code == 200
            assert "Deprecation" not in r.headers and "Sunset" not in r.headers

    def test_a_templated_route_is_matched_by_its_template_not_its_values(self, monkeypatch):
        monkeypatch.setattr(dep, "DEPRECATIONS", {("GET", "/things/{thing_id}"): _entry()})
        app = FastAPI()
        app.add_middleware(DeprecationMiddleware)

        @app.get("/api/v1/things/{thing_id}")
        def thing(thing_id: str):
            return {"id": thing_id}

        r = TestClient(app).get("/api/v1/things/42")
        assert r.status_code == 200 and "Sunset" in r.headers
