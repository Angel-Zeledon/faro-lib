"""
Generate a parametric CSV (N SKUs x D days) in-memory for Phase 2 volume tests.
Importable helper:  csv_bytes_for(n_skus, n_days) -> bytes
"""
import csv
import io
import math
import random
from datetime import date, timedelta

random.seed(11)


def csv_bytes_for(n_skus: int, n_days: int = 365, start=date(2023, 1, 1)) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["date", "sku", "qty_sold", "current_stock", "lead_time_days"])
    for i in range(n_skus):
        sku = f"SKU-{i:05d}"
        base = random.randint(5, 60)
        trend = random.uniform(-0.02, 0.05)
        amp = random.uniform(0, 1.5)
        stock = base * 30
        for d in range(n_days):
            cur = start + timedelta(days=d)
            doy = cur.timetuple().tm_yday
            season = 1 + amp * max(0, math.sin((doy - 80) / 365 * 2 * math.pi))
            wk = 1.3 if cur.weekday() >= 5 else 1.0
            val = max(0, (base + trend * d) * season * wk + random.gauss(0, base * 0.15))
            qty = round(val)
            stock = max(0, stock - qty)
            if d % 14 == 0 and d > 0:
                stock += base * 20
            w.writerow([cur.isoformat(), sku, qty, round(stock), 5 + (i % 10)])
    return buf.getvalue().encode("utf-8")


if __name__ == "__main__":
    import sys
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 50
    d = int(sys.argv[2]) if len(sys.argv) > 2 else 365
    b = csv_bytes_for(n, d)
    print(f"{n} SKUs x {d} days = {len(b)} bytes, ~{b.count(chr(10))} rows")
