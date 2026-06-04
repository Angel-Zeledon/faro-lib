#!/usr/bin/env python3
"""
Generates scripts/sample_sales.csv — a synthetic multi-SKU time-series dataset.
No dependencies beyond the Python standard library.

Usage:
    python scripts/gen_sample_data.py [--skus 5] [--days 90] [--out sample_sales.csv]

The CSV has columns: date, sku, sales
It can be uploaded directly via the frontend Data Upload step or via curl.
"""

import argparse
import csv
import io
import math
import random
from datetime import date, timedelta
from pathlib import Path


def generate(n_skus: int = 5, n_days: int = 90, seed: int = 42) -> bytes:
    rng = random.Random(seed)
    start = date(2024, 1, 1)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["date", "sku", "sales"])
    for i in range(n_skus):
        sku = f"SKU_{i + 1:03d}"
        base = rng.uniform(30, 150)
        trend = rng.uniform(-0.01, 0.12)
        for d in range(n_days):
            dt = start + timedelta(days=d)
            season = base * 0.15 * math.sin(2 * math.pi * d / 7)
            noise = rng.gauss(0, base * 0.08)
            val = max(0.0, base + trend * d + season + noise)
            w.writerow([dt.isoformat(), sku, round(val, 2)])
    return buf.getvalue().encode("utf-8")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--skus", type=int, default=5, help="Number of SKUs (default: 5)")
    p.add_argument("--days", type=int, default=90, help="Days of history per SKU (default: 90)")
    p.add_argument("--out", default=None, help="Output path (default: scripts/sample_sales.csv)")
    args = p.parse_args()

    out = Path(args.out) if args.out else Path(__file__).parent / "sample_sales.csv"
    data = generate(n_skus=args.skus, n_days=args.days)
    out.write_bytes(data)

    rows = args.skus * args.days
    print(f"Generated: {out}")
    print(f"  {args.skus} SKUs × {args.days} days = {rows} rows")
    print(f"  Columns : date, sku, sales")
    print(f"  Size    : {len(data):,} bytes")
    print()
    print("Upload this file via:")
    print("  - Frontend wizard -> Data step")
    print(f"  - curl -X POST http://localhost:8001/api/v1/datasets \\")
    print(f"         -H 'Authorization: Bearer <token>' \\")
    print(f"         -F 'file=@{out}'")


if __name__ == "__main__":
    main()
