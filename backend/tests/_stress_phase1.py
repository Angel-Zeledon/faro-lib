"""
Phase 1 stress (v2): concurrent HTTP load against the running backend (port 8002)
on the local volume DB. Robust: every request is guarded; no unguarded awaits.

Characterizes:
  - Lightweight read (sessions list) + write (create) + auth burst under a concurrency ramp.
  - inventory/stock degradation curve at LOW concurrency (known bottleneck: 19MB/50k rows).
Emits one JSON line per stage + a final summary.

Run:  PYTHONPATH=.. python -m tests._stress_phase1 <email>
"""
import asyncio
import json
import sys
import time

import httpx

BASE = "http://localhost:8002/api/v1"
EMAIL = sys.argv[1] if len(sys.argv) > 1 else "u0-126197@stress-test.com"
PASSWORD = "StressPass123!"


async def timed(client, method, url, **kw):
    t0 = time.perf_counter()
    try:
        r = await client.request(method, url, **kw)
        return {"ms": (time.perf_counter() - t0) * 1000, "code": r.status_code,
                "len": len(r.content), "err": None}
    except Exception as e:
        return {"ms": (time.perf_counter() - t0) * 1000, "code": -1, "len": 0,
                "err": f"{type(e).__name__}"}


def summarize(name, results, wall_s):
    codes = {}
    for r in results:
        codes[str(r["code"])] = codes.get(str(r["code"]), 0) + 1
    lats = sorted(r["ms"] for r in results)
    def pct(p):
        return round(lats[min(len(lats) - 1, int(len(lats) * p))], 1) if lats else None
    return {
        "stage": name, "n": len(results),
        "rps": round(len(results) / wall_s, 1) if wall_s else None,
        "codes": codes,
        "p50_ms": pct(0.50), "p95_ms": pct(0.95), "p99_ms": pct(0.99),
        "max_ms": round(lats[-1], 1) if lats else None,
    }


async def stage(headers, name, method, path, n, concurrency, timeout=60.0, **kw):
    limits = httpx.Limits(max_connections=concurrency + 5)
    async with httpx.AsyncClient(headers=headers, timeout=timeout, limits=limits) as client:
        sem = asyncio.Semaphore(concurrency)
        results = []
        async def one():
            async with sem:
                results.append(await timed(client, method, f"{BASE}{path}", **kw))
        t0 = time.perf_counter()
        await asyncio.gather(*[one() for _ in range(n)])
        out = summarize(f"{name} c={concurrency}", results, time.perf_counter() - t0)
    print(json.dumps(out), flush=True)
    return out


async def main():
    report = {"stages": []}
    async with httpx.AsyncClient(timeout=30.0) as c:
        r = await timed(c, "POST", f"{BASE}/auth/login", json={"email": EMAIL, "password": PASSWORD})
        if r["code"] != 200:
            print(json.dumps({"fatal": "login failed", "probe": r})); return
        rr = await c.post(f"{BASE}/auth/login", json={"email": EMAIL, "password": PASSWORD})
        token = rr.json()["data"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 1) Lightweight read ramp — sessions list (2000 rows, paginated to 50)
    for conc in [10, 25, 50, 100, 200]:
        report["stages"].append(await stage(headers, "GET sessions(list,limit=50)",
                                             "GET", "/sessions?skip=0&limit=50", 400, conc, timeout=30))
    # 2) Write ramp — create session
    for conc in [25, 100]:
        report["stages"].append(await stage(headers, "POST sessions(create)",
                                             "POST", "/sessions", 300, conc, timeout=30,
                                             json={"name": "stress"}))
    # 3) Auth burst (rate limit should be OFF in testing mode)
    auth_res = await stage({}, "POST auth/login(burst)", "POST", "/auth/login", 150, 50,
                           timeout=30, json={"email": EMAIL, "password": PASSWORD})
    report["stages"].append(auth_res)
    report["auth_429s"] = auth_res["codes"].get("429", 0)

    # 4) inventory/stock degradation curve — LOW concurrency only (known bottleneck)
    for conc in [1, 2, 4, 8]:
        report["stages"].append(await stage(headers, "GET inventory/stock(50k,19MB)",
                                             "GET", "/inventory/stock", conc * 3, conc, timeout=120))

    # Health after load
    async with httpx.AsyncClient(headers=headers, timeout=15.0) as c:
        h = await timed(c, "POST", f"{BASE}/auth/login", json={"email": EMAIL, "password": PASSWORD})
    report["responsive_after_load"] = (h["code"] == 200)

    # Aggregate
    agg = {}
    for s in report["stages"]:
        for code, n in s["codes"].items():
            agg[code] = agg.get(code, 0) + n
    report["aggregate_codes"] = agg
    report["any_5xx_or_connerr"] = any((k.startswith("5") or k == "-1") and v > 0
                                       for k, v in agg.items())
    print("\n===SUMMARY===")
    print(json.dumps({k: report[k] for k in
                      ("aggregate_codes", "any_5xx_or_connerr", "auth_429s",
                       "responsive_after_load")}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
