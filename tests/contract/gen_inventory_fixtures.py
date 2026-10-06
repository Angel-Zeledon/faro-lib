"""Write the differential fixture for the Rust inventory numerics.

    python tests/contract/gen_inventory_fixtures.py [--check]

The cases are generated from a fixed seed and the EXPECTED values come from
running the real Python implementation (`backend.inventory.service`,
`abc_xyz`, `reception_service`). The Rust side
(`backend-rs/src/inventory/calc.rs`, `cargo test differential_`) replays them
and demands the same f64 bits. Floats travel as the decimal string of their
IEEE-754 bits, because no JSON float parser is trusted to round-trip them.

`--check` regenerates in memory and fails if the committed file differs, which
is what turns "someone edited the Python formula" into a red test instead of a
silent divergence. It also depends on the Python version (`sum()` is
compensated from 3.12, libm differs per platform), so the file records both.
"""
from __future__ import annotations

import json
import math
import platform
import random
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.inventory import abc_xyz  # noqa: E402
from backend.inventory import reception_service as rec  # noqa: E402
from backend.inventory import service as svc  # noqa: E402

OUT = ROOT / "backend-rs" / "tests" / "fixtures" / "inventory_calc.json"


def F(x):
    """float -> decimal string of its IEEE bits (None stays null)."""
    if x is None:
        return None
    return str(struct.unpack("<Q", struct.pack("<d", float(x)))[0])


def freeze(v):
    """Nested payload: every float leaf becomes the string "~<bits>"."""
    if isinstance(v, bool) or v is None or isinstance(v, (int, str)):
        return v
    if isinstance(v, float):
        return "~" + F(v)
    if isinstance(v, dict):
        return {k: freeze(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [freeze(x) for x in v]
    raise TypeError(type(v))


def rnd_float(r: random.Random, lo, hi, ints=0.15):
    """A float, sometimes a whole number, sometimes a tie-friendly dyadic."""
    k = r.random()
    if k < ints:
        return float(r.randint(int(lo), int(hi)))
    if k < ints + 0.1:
        return r.randint(int(lo * 8), int(hi * 8)) / 8.0
    return r.uniform(lo, hi)


def build() -> dict:
    r = random.Random(20261006)
    fx: dict = {
        "meta": {
            "python": platform.python_version(),
            "platform": sys.platform,
            "seed": 20261006,
        }
    }

    # ── z and the inverse normal CDF ────────────────────────────────────────
    cases = []
    fixed = [0.90, 0.95, 0.97, 0.99, 0.5, 0.999, 0.999999, 0.98, 0.9999, 0.0, -1.0, 1.0, 1.5,
             0.75, 0.85, 0.6, 0.55, 0.9000000001, 0.95 + 1e-12, float("nan"),
             0.02425, 1 - 0.02425, 0.025, 0.975]
    for sl in fixed + [r.uniform(0.0, 1.2) for _ in range(300)] + [r.uniform(0.5, 0.999) for _ in range(300)]:
        cases.append({"sl": F(sl), "z": F(svc._z_for(sl))})
    fx["z_for"] = cases

    cases = []
    ps = [svc._P_LOW, 1 - svc._P_LOW, 0.5, 1e-9, 1 - 1e-9, 0.02424999, 0.97575001, 1e-300, 0.999999]
    ps += [r.uniform(1e-6, 1 - 1e-6) for _ in range(600)]
    ps += [r.uniform(1e-12, svc._P_LOW) for _ in range(500)]
    ps += [1 - r.uniform(1e-12, svc._P_LOW) for _ in range(500)]
    ps += [10 ** r.uniform(-300, -1) for _ in range(300)]
    for p in ps:
        cases.append({"p": F(p), "out": F(svc._inverse_normal_cdf(p))})
    fx["inverse_normal_cdf"] = cases

    # ── period helpers ──────────────────────────────────────────────────────
    cases = []
    for _ in range(600):
        period = r.choice([None, "daily", "weekly", "monthly", "yearly", "", "Weekly"])
        lt = r.choice([float(r.randint(1, 400)), r.uniform(0.01, 400), 7.0, 14.0, 30.0, 0.5, 90.0,
                       r.randint(1, 60) * 7.0, r.randint(1, 20) * 30.0, 29.999999, 7.000001])
        c = {"period": period, "lt": F(lt), "dpp": svc._days_per_period(period),
             "unit": svc._coverage_unit(period)}
        if period is not None:
            c["ltp"] = F(svc._lead_time_in_periods(lt, period))
            c["steps"] = svc._steps_for_lead_time(lt, period)
        cases.append(c)
    fx["periods"] = cases

    # ── signal ──────────────────────────────────────────────────────────────
    cases = []
    for _ in range(3500):
        lt = rnd_float(r, 1, 120)
        rp = lt + abs(rnd_float(r, 0, 80))
        th = None
        if r.random() < 0.5:
            th = {"order_now_factor": r.choice([0.5, r.uniform(0.05, 0.95)]),
                  "overstock_factor": r.choice([3.0, r.uniform(1.5, 6.0)])}
        onf = (th or {"order_now_factor": 0.5})["order_now_factor"]
        osf = (th or {"overstock_factor": 3.0})["overstock_factor"]
        sob = max(lt * osf, rp * 2.0)
        pick = r.random()
        if pick < 0.12:
            cov = lt * onf
        elif pick < 0.24:
            cov = rp
        elif pick < 0.36:
            cov = sob
        elif pick < 0.40:
            cov = r.choice([9990.0, 9999.0, 9989.999999, 0.0, -3.0])
        elif pick < 0.55:
            cov = r.choice([lt * onf, rp, sob]) * (1 + r.choice([-1e-15, 1e-15, -1e-9, 1e-9]))
        else:
            cov = rnd_float(r, 0, max(10, sob * 1.3))
        sig = svc._calc_signal(cov, lt, rp, th)
        rec_qty = rnd_float(r, 0, 500)
        cases.append({"cov": F(cov), "lt": F(lt), "rp": F(rp),
                      "th": None if th is None else {k: F(v) for k, v in th.items()},
                      "out": sig, "rec": F(rec_qty),
                      "gated": F(svc._gate_recommended_by_signal(sig, rec_qty))})
    fx["calc_signal"] = cases

    # ── measured safety stock ───────────────────────────────────────────────
    def make_risk(sl_hint):
        k = r.random()
        if k < 0.06:
            return None
        if k < 0.10:
            return {}
        if k < 0.14:
            return {"cumulative_offsets": {}}
        if k < 0.17:
            return {"other": 1}
        hs = r.sample([1, 3, 7, 14, 21, 30, 45, 60, 90], r.randint(1, 5))
        offs = {}
        for h in hs:
            base = rnd_float(r, 1, 400)
            qs = r.choice([
                [0.5, 0.9, 0.95], [0.9, 0.95], [0.5, 0.9], [0.5], [0.95, 0.99], [0.9], [0.5, 0.8, 0.9, 0.95, 0.99],
            ])
            band = {}
            for q in qs:
                key = r.choice([str(q), str(q), f"{q:.2f}", f"{q:.3f}"])
                val = base * (1 + (q - 0.5) * r.uniform(0.2, 2.0))
                if r.random() < 0.04:
                    val = str(round(val, 3))
                band[key] = val
            if r.random() < 0.05:
                band = {}
            if sl_hint is not None and r.random() < 0.2:
                band[str(sl_hint)] = base * 1.7
            offs[str(h)] = band
        if r.random() < 0.06:
            offs["abc"] = {"0.9": 1.0}
        if r.random() < 0.04:
            offs["-1"] = {"0.9": 1.0}
        return {"cumulative_offsets": offs}

    def sl_pick():
        return r.choice([0.9, 0.95, 0.97, 0.99, 0.98, 0.5, 0.999, 0.9 + 1e-7, 0.95 - 1e-8,
                         r.uniform(0.5, 0.999), r.uniform(0.5, 0.999), 0.75, 0.6])

    cases = []
    for _ in range(1500):
        sl = sl_pick()
        risk = make_risk(sl)
        lt = r.choice([rnd_float(r, 0.3, 120), float(r.choice([1, 3, 7, 14, 21, 30, 45, 60, 90, 120, 200]))])
        cases.append({"risk": freeze(risk), "risk_none": risk is None, "lt": F(lt), "sl": F(sl),
                      "out": F(svc._measured_safety_stock(risk, lt, sl))})
    fx["measured"] = cases

    # ── safety stock and recommended quantity ───────────────────────────────
    cases = []
    for _ in range(3000):
        sl = sl_pick()
        risk = make_risk(sl) if r.random() < 0.4 else None
        lt = r.choice([rnd_float(r, 0.3, 120), float(r.choice([1, 7, 14, 15, 30, 60]))])
        review = r.choice([0.0, 0.0, rnd_float(r, 0, 30), -2.0])
        pi = lt + max(0.0, review)
        avg_daily = r.choice([0.0, rnd_float(r, 0, 500), rnd_float(r, 0, 5), 1e-9])
        avg_std = r.choice([0.0, rnd_float(r, 0, 200), rnd_float(r, 0, 3)])
        lt_std = r.choice([0.0, 0.0, rnd_float(r, 0, 20), -1.0])
        current = r.choice([0.0, rnd_float(r, 0, 5000), rnd_float(r, 0, 50)])
        incoming = r.choice([0.0, 0.0, rnd_float(r, 0, 2000), -5.0])
        moq = r.choice([None, 0.0, 1.0, -3.0, rnd_float(r, 0, 800), 500.0, rnd_float(r, 1, 50)])
        scale = r.choice([1.0, 1.0, r.uniform(0.1, 1.0), 0.5, 1 / 3])
        cases.append({
            "risk": freeze(risk), "risk_none": risk is None, "current": F(current),
            "avg_daily": F(avg_daily), "avg_std": F(avg_std), "lt": F(lt), "pi": F(pi),
            "moq": F(moq), "sl": F(sl), "risk_scale": F(scale), "incoming": F(incoming),
            "lt_std": F(lt_std), "review": F(review),
            "ss": F(svc._safety_stock(avg_std, pi, sl, risk, scale, avg_daily=avg_daily, lead_time_std=lt_std)),
            "rec": F(svc._calc_recommended(current, avg_daily, avg_std, lt, moq, sl, risk, scale,
                                           incoming, lt_std, review)),
        })
    fx["recommended"] = cases

    # ── forecast points and averages ────────────────────────────────────────
    def make_point():
        k = r.random()
        v = r.choice([None, 0, 0.0, rnd_float(r, 0, 300), rnd_float(r, 0, 5)])
        p = {"value": v}
        if k < 0.45:
            p["q90"] = (v or 0.0) + rnd_float(r, -5, 120)
        elif k < 0.75:
            p["upper"] = (v or 0.0) + rnd_float(r, -5, 120)
        elif k < 0.80:
            p["q90"] = None
            p["upper"] = (v or 0.0) + rnd_float(r, 0, 20)
        return p

    cases = []
    for _ in range(800):
        p = make_point()
        cases.append({"point": freeze(p), "out": F(svc._point_sigma(p))})
    fx["point_sigma"] = cases

    cases = []
    for _ in range(250):
        names = r.sample(["lightgbm", "xgboost", "prophet", "ets", "ensemble", "croston"], r.randint(1, 4))
        models = {}
        for n in names:
            pts = [make_point() for _ in range(r.choice([0, 1, 5, 14, 30, 60]))]
            models[n] = {"forecast": pts}
        preferred = r.choice([None, "", names[0], "nope", names[-1]])
        steps = r.choice([0, 1, 3, 7, 14, 30, 45])
        d, s = svc._avg_daily_forecast(models, steps, preferred)
        cases.append({"models": freeze(models), "steps": steps, "preferred": preferred,
                      "avg": F(d), "std": F(s)})
    fx["avg_forecast"] = cases

    # ── lead-time cascade ───────────────────────────────────────────────────
    cases = []
    suppliers = ["Acme", "  acme ", "BETA SA", "gamma", "Ñandú", "delta", "x"]
    for _ in range(1200):
        learned = {s.strip().lower(): rnd_float(r, -2, 40) for s in r.sample(suppliers, r.randint(0, 5))}
        for s in list(learned):
            if r.random() < 0.25:
                learned[s] = r.choice([0.5, 1.5, 2.5, 12.5, 7.5, 0.49999, 0.0, 14.05, 14.15, 9.95])
        configured_std = {s.strip().lower(): rnd_float(r, -1, 15) for s in r.sample(suppliers, r.randint(0, 5))}
        supplier = r.choice([None, "", *suppliers])
        configured = r.randint(1, 60)
        source = r.choice(["user", "file", "supplier_rule", "default"])
        days, out_source, raw = svc.resolve_lead_time(configured, supplier, learned, source)
        cases.append({
            "supplier": supplier, "learned": {k: F(v) for k, v in learned.items()},
            "configured_std": {k: F(v) for k, v in configured_std.items()},
            "configured": configured, "source": source, "days": days, "out_source": out_source,
            "raw": F(raw),
            "lt_std": F(svc._resolve_lead_time_std(supplier, learned, configured_std)),
            "review": F(svc._resolve_review_period_days(supplier, configured_std)),
        })
    fx["lead_time"] = cases

    # ── margin and fill rate ────────────────────────────────────────────────
    cases = []
    for _ in range(2000):
        price = r.choice([None, rnd_float(r, 0, 500), 10.125, 0.005, 2.675])
        cost = r.choice([None, rnd_float(r, 0, 500), 0.0, 1.005])
        cases.append({"price": F(price), "cost": F(cost), "out": F(svc.calc_unit_margin(price, cost))})
    fx["margin"] = cases

    cases = []
    for _ in range(2000):
        total = r.choice([0.0, -4.0, float(r.choice([1, 2, 4, 8, 16, 64, 100, 1000])), rnd_float(r, 1, 1000)])
        received = r.choice([0.0, total, total * 1.2, float(r.randint(0, 64)), rnd_float(r, 0, 1100), 1.0])
        cases.append({"received": F(received), "total": F(total), "out": F(rec._fill_rate(received, total))})
    fx["fill_rate"] = cases

    # ── ABC / XYZ ───────────────────────────────────────────────────────────
    cases = []
    for cv in [None, 0.0, 0.4999999, 0.5, 0.5000001, 0.9999999, 1.0, 1.0000001, 5.0, -1.0] + \
              [r.uniform(0, 2) for _ in range(400)]:
        cases.append({"cv": F(cv), "out": abc_xyz.classify_xyz(cv)})
    fx["xyz"] = cases

    cases = []
    for _ in range(1000):
        n = r.choice([0, 1, 2, 3, 5, 10, 25])
        k = r.random()
        vals = []
        for i in range(n):
            if k < 0.15:
                vals.append(r.choice([0.0, 0.0, -2.0]))
            elif k < 0.30:
                vals.append(r.choice([1.0, 2.0, 5.0]))          # many equal values (stable sort)
            elif k < 0.45 and i == 0:
                vals.append(r.uniform(1e5, 1e6))                 # one dominant SKU
            elif k < 0.55:
                vals.append(r.uniform(0, 1e-9))
            else:
                vals.append(rnd_float(r, 0, 1000) * r.choice([1, 1, 1, 10, 0.01]))
        dup = k > 0.9  # a repeated SKU: the dict keeps the first position and the last class
        scored = [(f"S{i // 2 if dup else i}", v) for i, v in enumerate(vals)]
        out = abc_xyz.classify_abc(scored)
        cases.append({"scored": [[s, F(v)] for s, v in scored], "out": [[s, c] for s, c in out.items()]})
    fx["abc"] = cases

    cases = []
    for _ in range(600):
        rows = []
        for _ in range(r.choice([0, 1, 2, 4, 9, 30])):
            rows.append({
                "abc": r.choice(["A", "A", "B", "B", "C", "C", None, "D"]),
                "value": r.choice([0.0, -1.0, rnd_float(r, 0, 1000), rnd_float(r, 0, 3)]),
                "service_level": r.choice([None, 0.95, 0.98, 0.9, r.uniform(0.5, 0.999), 0.98 + 1e-10]),
                "owned": r.random() < 0.4,
            })
        out = abc_xyz.class_summary(rows)
        cases.append({
            "rows": [{"abc": x["abc"], "value": F(x["value"]), "service_level": F(x["service_level"]),
                      "owned": x["owned"]} for x in rows],
            "out": [{"abc": o["abc"], "skus": o["skus"], "value_share": F(o["value_share"]),
                     "current_service_level": F(o["current_service_level"]),
                     "suggested_service_level": F(o["suggested_service_level"]),
                     "would_change": o["would_change"], "owned": o["owned"]} for o in out],
        })
    fx["class_summary"] = cases
    return fx


def main() -> int:
    fx = build()
    text = json.dumps(fx, separators=(",", ":"), ensure_ascii=False) + "\n"
    if "--check" in sys.argv:
        if not OUT.exists() or OUT.read_text(encoding="utf-8") != text:
            print("inventory_calc.json is stale: regenerate with tests/contract/gen_inventory_fixtures.py")
            return 1
        print("inventory_calc.json is current")
        return 0
    OUT.write_text(text, encoding="utf-8", newline="\n")
    total = sum(len(v) for k, v in fx.items() if k != "meta")
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KiB, {total} cases)")
    for k, v in fx.items():
        if k != "meta":
            print(f"  {k:20s} {len(v)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
