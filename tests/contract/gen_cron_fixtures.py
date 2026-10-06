"""Fixtures for the two differential tests of the Rust schedule port.

    backend/.venv/Scripts/python.exe tests/contract/gen_cron_fixtures.py OUT_DIR

writes OUT_DIR/cron_fixtures.jsonl and OUT_DIR/tz_transitions.json, then:

    CRON_FIXTURES=OUT_DIR/cron_fixtures.jsonl TZ_TRANSITIONS=OUT_DIR/tz_transitions.json \
        cargo test --manifest-path backend-rs/Cargo.toml -- --ignored

* cron_fixtures.jsonl: one line per (expression, zone, now) with what
  croniter 6.2.2 + zoneinfo answer, through exactly the calls
  backend/api/v1/schedule.py makes (validator, then `get_next`).
  `result` is a UTC ISO instant, "ERR:<croniter message>", "BADDATE" or
  "COUNT" (refused by the five-field check).
* tz_transitions.json: every UTC offset change zoneinfo reports for the
  supported tenant zones, 2025-2075, as [zone, utc, offset_after_seconds].

Pure computation: no database, no backend. Needs the backend venv (croniter).
"""
import json
import os
import random
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from croniter import croniter

ZONES = ["America/Costa_Rica", "America/Bogota", "America/Mexico_City", "America/Santiago",
         "Europe/Madrid", "America/New_York", "UTC", "America/Argentina/Buenos_Aires"]
ALL_ZONES = ZONES + ["America/Lima", "America/Guayaquil", "America/Guatemala", "America/Panama",
                     "America/Santo_Domingo"]

ATOMS = {
    0: ["*", "0", "5", "30", "59", "*/15", "*/0", "0-30/10", "45-15", "10/5", "60", "-1", "a", "1,2,3",
        "0-59", "5-5", "*/7", "r", "h", "h/5", "1-70", "?", "07", "30,0"],
    1: ["*", "0", "6", "23", "*/6", "9-17", "22-2", "24", "8,20", "0-23", "6/4", "x", "1-3/0", "0-0"],
    2: ["*", "1", "15", "31", "l", "L", "15w", "w1", "31w", "32w", "0", "1-31", "?", "1,15", "28-3",
        "29", "10/10", "l-5", "5-l", "1-l", "2w,3", "0-5"],
    3: ["*", "1", "2", "12", "jan", "JAN-MAR", "feb", "nov-feb", "*/3", "13", "0", "1-12", "dec-jan/2", "0-5"],
    4: ["*", "0", "1", "7", "mon", "MON-FRI", "sat,sun", "1-5", "5-2", "0-7", "8", "mon#2", "1#1", "5#5",
        "1#6", "l5", "l0", "?", "fri#3", "1,2#3", "sun-sat", "mon-wed#2", "*/2", "6-0"],
}
FIXED = ["0 6 * * 1", "0 0 * * *", "0 * * * *", "0 8 * * 0", "0 6 * * 1-5", "0 0 1 * *",
         "0 6 31 2 *", "0 6 L * *", "5-1 * * * *", "0 6 * *", "0 2 * * *", "30 2 * * *", "30 1 * * *",
         "0 0 29 2 *", "0 3 * * 0", "*/30 1-3 * * *", "0 0 15w * *", "0 0 * * l5", "0 0 13 * 5",
         "0 0 1 1 1", "0 6 ? * MON", "@daily", "0 6 * * 1 2"]


def cron_fixtures(path: str) -> int:
    rnd = random.Random(7)
    exprs = set(FIXED)
    for _ in range(900):
        exprs.add(" ".join(rnd.choice(ATOMS[i]) for i in range(5)))
    nows = [datetime(2026, 10, 5, 12, 0, 0, 123456, tzinfo=timezone.utc)]
    # Around DST transitions of the next two years, plus random instants.
    for t in ["2026-11-01T05:30:00", "2026-11-01T06:10:00", "2027-03-14T06:45:00", "2027-03-14T07:05:00",
              "2026-10-25T00:30:00", "2027-03-28T00:59:00", "2027-04-04T02:30:00", "2027-09-05T03:30:00",
              "2027-04-04T03:30:00", "2026-12-31T23:59:30", "2028-02-28T23:00:00"]:
        nows.append(datetime.fromisoformat(t).replace(tzinfo=timezone.utc))
    for _ in range(10):
        nows.append(datetime(2026, 10, 5, tzinfo=timezone.utc) + timedelta(seconds=rnd.randint(0, 3 * 365 * 86400)))

    out = []
    for e in sorted(exprs):
        v = e.strip()
        if len(v.split()) != 5:
            out.append({"expr": e, "zone": "UTC", "now": nows[0].isoformat(), "result": "COUNT"})
            continue
        if " r" in " " + v.lower().replace(",", " "):
            continue  # a random field cannot be compared
        try:
            croniter(v)
        except Exception as exc:  # noqa: BLE001 - the validator catches everything too
            out.append({"expr": e, "zone": "UTC", "now": nows[0].isoformat(), "result": f"ERR:{exc}"})
            continue
        for now in rnd.sample(nows, 4) + [nows[0]]:
            zone = rnd.choice(ZONES)
            tz = ZoneInfo(zone)
            try:
                nxt = croniter(v, now.astimezone(tz)).get_next(datetime)
                if nxt.tzinfo is None:
                    nxt = nxt.replace(tzinfo=tz)
                res = nxt.astimezone(timezone.utc).isoformat()
            except Exception as exc:  # noqa: BLE001
                res = "BADDATE" if type(exc).__name__ == "CroniterBadDateError" else f"RAISE:{exc}"
            out.append({"expr": e, "zone": zone, "now": now.isoformat(), "result": res})
    with open(path, "w", encoding="utf-8") as fh:
        for o in out:
            fh.write(json.dumps(o) + "\n")
    return len(out)


def tz_transitions(path: str) -> int:
    out = []
    for z in ALL_ZONES:
        tz = ZoneInfo(z)
        t = datetime(2025, 1, 1, tzinfo=timezone.utc)
        prev = t.astimezone(tz).utcoffset()
        out.append([z, t.strftime("%Y-%m-%d %H:%M:%S"), int(prev.total_seconds())])
        while t < datetime(2076, 1, 1, tzinfo=timezone.utc):
            t += timedelta(minutes=30)
            o = t.astimezone(tz).utcoffset()
            if o != prev:
                out.append([z, t.strftime("%Y-%m-%d %H:%M:%S"), int(o.total_seconds())])
                prev = o
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(out, fh)
    return len(out)


if __name__ == "__main__":
    out_dir = sys.argv[1] if len(sys.argv) > 1 else "."
    print(cron_fixtures(os.path.join(out_dir, "cron_fixtures.jsonl")), "cron fixtures")
    print(tz_transitions(os.path.join(out_dir, "tz_transitions.json")), "zone transitions")
