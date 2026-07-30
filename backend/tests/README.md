# What counts as a test here

Written after a session where **1,944 tests were green while the landing page
could not be scrolled at all** — and had not been scrollable since the initial
commit. `body { overflow: hidden }` propagated to the viewport, the wheel did
nothing, and no test noticed, because no test ever asked whether a person could
use the screen.

Measured across the suite at that moment:

| | tests | share |
|---|---|---|
| Only look at the response, never verify state | 1,204 | 63% |
| **Can pass while the feature is broken** | **295** | **15%** |
| Read state back | 402 | 21% |

`test_endpoints_offline.py` was deleted outright. Its own docstring described
what it did: *"routing (404 vs 200)"*, *"response shape (expected fields
present)"*. Ninety-one tests proving FastAPI had mounted the routers. It could
not detect a wrong value, a write that never happened, or a screen nobody could
use.

## The one question

**Could this test fail if the feature broke?**

If you cannot describe the break that turns it red, it is not a test. Delete it.

Before trusting a new test, break the code on purpose and watch it go red. Three
of my own checks in that session returned false negatives — including a
`check_tours.py` that had become a silent no-op while still printing "tours OK",
caught only by deliberately breaking a tour.

## What a real test looks like, in order of value

**1. Invariant audits.** Walk the codebase or the route table and assert a rule
holds everywhere, so code written tomorrow is covered the moment it exists. These
have the best record here: `test_write_guard_audit.py` caught a brand-new
unguarded webhook route within minutes of it being written, and
`test_no_pandas_in_backend.py` has held an architectural boundary for months.
Prefer these over one test per instance.

**2. State assertions.** The response echoing your input proves nothing — it was
your input. Query the database and check the row.

```python
r = client.patch(f"/api/v1/data-sources/{sid}", headers=viewer_headers,
                 json={"name": "Renamed by a reader"})
assert r.status_code == 403
row = query_one("SELECT name FROM datasets WHERE id = %s", (sid,))
assert row["name"] == "Original name", "the rename went through anyway"
```

The second assertion is the test. The 403 alone would pass on an endpoint that
returned 403 *and* renamed the row.

**3. Permission pairs.** Denied **and the state unchanged**, then the same action
succeeding as an analyst. A 403 with no state check is one of the 295.

**4. Reality, not fixtures.** Compare against something computed independently.
The data-source tests query the customer's Postgres and MySQL directly and compare
row counts to what Faro returned — `(4380, 6, 484765)` both ways. A fixture that
asserts against itself asserts nothing.

**5. Browser tests for what only a browser can see.** Everything found by hand in
that session was invisible to this suite: a page that will not scroll, a panel
that collapses to nothing, a list that jumps 220px under the cursor, a tooltip
clipped by an ancestor's overflow, a contrast ratio of 1.21:1.

These now live in `Frontend/tests/smoke.mjs` — run `node tests/smoke.mjs` from
`Frontend/` with the app up. Every assertion in it corresponds to a bug that
shipped and survived a green run of this suite. It is cheap to extend: when a
defect is only visible in a browser, it belongs there and nowhere else.

- `document.documentElement.scrollHeight > innerHeight` and the wheel moves it
- the panel's height equals the viewport's, not its content's
- an element's `y` is identical before and after opening a menu
- the tooltip's rect is inside the viewport
- no horizontal overflow at 360 / 390 / 414px
- zero console errors

## Anti-patterns, all of them found in this suite

- `assert r.status_code == 200` and nothing else
- `assert "user" in body` — a key being present says nothing about its value
- `assert x == 1 or x == 2` — passes either way, so it tests nothing
- `xfail` on a real bug: it converts a defect into a green tick
- Asserting the response echo of what you just sent
- A test named for a behaviour that asserts only that the endpoint exists

## Existing standards that still apply

- Tests depending on quotas must `monkeypatch settings.testing_mode = False`
  themselves — the local `.env` runs with `TESTING_MODE=true`, and with it on
  `require_feature` returns the user untouched, so every plan gate reads as open.
  A plan test that skips this passes on a Starter tenant and proves nothing.
- conftest patches `backend.notifications.email._send` session-wide; transport
  tests must target `_transport_send`.
- Test email addresses use `@faro-e2e.io`, a domain with no MX record, so nothing
  can reach a real person.
