import pandas as pd
import numpy as np
from pathlib import Path

OUT = Path('test_csvs')
OUT.mkdir(exist_ok=True)
np.random.seed(42)

# ── 1. GOOD: daily, multi-SKU, clean ─────────────────────────────────────────
dates = pd.date_range('2023-01-01', periods=365, freq='D')
rows = []
for sku in ['SKU-A', 'SKU-B', 'SKU-C']:
    for d in dates:
        rows.append({'date': d.strftime('%Y-%m-%d'), 'sku': sku,
                     'sales': max(0, int(np.random.normal(100, 20)))})
pd.DataFrame(rows).to_csv(OUT / '01_good_daily_multi_sku.csv', index=False)
print('01 OK')

# ── 2. GOOD: weekly, single SKU ──────────────────────────────────────────────
dates_w = pd.date_range('2021-01-04', periods=156, freq='W-MON')
df2 = pd.DataFrame({'date': dates_w.strftime('%Y-%m-%d'),
                    'sales': np.random.randint(50, 500, 156)})
df2.to_csv(OUT / '02_good_weekly_single_sku.csv', index=False)
print('02 OK')

# ── 3. BAD: missing dates 30% random gaps ────────────────────────────────────
dates = pd.date_range('2023-01-01', periods=180, freq='D')
rows = []
for sku in ['SKU-A', 'SKU-B']:
    idxs = np.random.choice(len(dates), size=int(len(dates)*0.7), replace=False)
    for i in sorted(idxs):
        rows.append({'date': dates[i].strftime('%Y-%m-%d'), 'sku': sku,
                     'sales': int(np.random.randint(10, 200))})
pd.DataFrame(rows).to_csv(OUT / '03_bad_missing_dates.csv', index=False)
print('03 OK')

# ── 4. BAD: duplicate date+SKU rows ──────────────────────────────────────────
dates = pd.date_range('2023-01-01', periods=60, freq='D')
rows = [{'date': d.strftime('%Y-%m-%d'), 'sku': 'SKU-A',
         'sales': int(np.random.randint(10, 100))} for d in dates]
for i in [5, 10, 15, 20, 25]:
    rows.append({'date': dates[i].strftime('%Y-%m-%d'), 'sku': 'SKU-A', 'sales': 999})
pd.DataFrame(rows).to_csv(OUT / '04_bad_duplicates.csv', index=False)
print('04 OK')

# ── 5. BAD: negative target values ───────────────────────────────────────────
dates = pd.date_range('2023-01-01', periods=90, freq='D')
vals = np.random.randint(-50, 200, 90)
pd.DataFrame({'date': dates.strftime('%Y-%m-%d'), 'sku': 'SKU-A',
              'sales': vals}).to_csv(OUT / '05_bad_negative_target.csv', index=False)
print('05 OK')

# ── 6. BAD: all zeros ────────────────────────────────────────────────────────
dates = pd.date_range('2023-01-01', periods=90, freq='D')
pd.DataFrame({'date': dates.strftime('%Y-%m-%d'), 'sku': 'SKU-A',
              'sales': 0}).to_csv(OUT / '06_bad_all_zeros.csv', index=False)
print('06 OK')

# ── 7. BAD: extreme outliers ─────────────────────────────────────────────────
dates = pd.date_range('2023-01-01', periods=90, freq='D')
vals = np.random.randint(80, 120, 90).tolist()
vals[30] = 999999
vals[60] = 1000000
pd.DataFrame({'date': dates.strftime('%Y-%m-%d'), 'sku': 'SKU-A',
              'sales': vals}).to_csv(OUT / '07_bad_outliers.csv', index=False)
print('07 OK')

# ── 8. BAD: NaN in target ────────────────────────────────────────────────────
dates = pd.date_range('2023-01-01', periods=90, freq='D')
vals = list(map(float, np.random.randint(50, 150, 90)))
for i in [5, 20, 40, 55, 70]:
    vals[i] = float('nan')
pd.DataFrame({'date': dates.strftime('%Y-%m-%d'), 'sku': 'SKU-A',
              'sales': vals}).to_csv(OUT / '08_bad_null_target.csv', index=False)
print('08 OK')

# ── 9. BAD: very short history (12 rows) ─────────────────────────────────────
dates = pd.date_range('2024-01-01', periods=12, freq='D')
pd.DataFrame({'date': dates.strftime('%Y-%m-%d'), 'sku': 'SKU-A',
              'sales': np.random.randint(10, 100, 12)}).to_csv(OUT / '09_bad_short_history.csv', index=False)
print('09 OK')

# ── 10. BAD: mixed / ambiguous date formats ───────────────────────────────────
rows = [
    {'date': '01/15/2023', 'sku': 'SKU-A', 'sales': 100},
    {'date': '02/15/2023', 'sku': 'SKU-A', 'sales': 110},
    {'date': '2023-03-15', 'sku': 'SKU-A', 'sales': 120},
    {'date': 'March 15, 2023', 'sku': 'SKU-A', 'sales': 130},
    {'date': '15-04-2023', 'sku': 'SKU-A', 'sales': 140},
]
pd.DataFrame(rows).to_csv(OUT / '10_bad_mixed_date_format.csv', index=False)
print('10 OK')

# ── 11. BAD: text values in numeric column ───────────────────────────────────
dates = pd.date_range('2023-01-01', periods=30, freq='D')
vals = [str(np.random.randint(10, 100)) for _ in range(30)]
vals[5] = 'N/A'
vals[10] = '#VALUE!'
vals[15] = 'null'
vals[20] = '-'
pd.DataFrame({'date': dates.strftime('%Y-%m-%d'), 'sku': 'SKU-A',
              'sales': vals}).to_csv(OUT / '11_bad_text_in_numeric.csv', index=False)
print('11 OK')

# ── 12. BAD: mixed frequency (daily vs weekly per SKU) ───────────────────────
rows = []
for d in pd.date_range('2023-01-01', periods=180, freq='D'):
    rows.append({'date': d.strftime('%Y-%m-%d'), 'sku': 'SKU-DAILY', 'sales': 50})
for d in pd.date_range('2023-01-02', periods=26, freq='W-MON'):
    rows.append({'date': d.strftime('%Y-%m-%d'), 'sku': 'SKU-WEEKLY', 'sales': 350})
pd.DataFrame(rows).to_csv(OUT / '12_bad_mixed_frequency.csv', index=False)
print('12 OK')

# ── 13. BAD: future dates mixed in historical ─────────────────────────────────
dates_past = pd.date_range('2024-01-01', periods=60, freq='D')
dates_fut  = pd.date_range('2027-01-01', periods=10, freq='D')
all_dates  = list(dates_past) + list(dates_fut)
vals = np.random.randint(50, 150, len(all_dates))
pd.DataFrame({'date': [d.strftime('%Y-%m-%d') for d in all_dates],
              'sku': 'SKU-A', 'sales': vals}).to_csv(OUT / '13_bad_future_dates.csv', index=False)
print('13 OK')

# ── 14. BAD: highly intermittent (80% zeros) ─────────────────────────────────
dates = pd.date_range('2023-01-01', periods=180, freq='D')
vals = np.where(np.random.rand(180) > 0.8, np.random.randint(1, 10, 180), 0)
pd.DataFrame({'date': dates.strftime('%Y-%m-%d'), 'sku': 'SKU-A',
              'sales': vals}).to_csv(OUT / '14_bad_intermittent.csv', index=False)
print('14 OK')

# ── 15. BAD: SKUs with very different history lengths ────────────────────────
rows = []
for d in pd.date_range('2021-01-01', periods=730, freq='D'):
    rows.append({'date': d.strftime('%Y-%m-%d'), 'sku': 'SKU-OLD', 'sales': 100})
for d in pd.date_range('2024-06-01', periods=14, freq='D'):
    rows.append({'date': d.strftime('%Y-%m-%d'), 'sku': 'SKU-NEW', 'sales': 50})
pd.DataFrame(rows).to_csv(OUT / '15_bad_unequal_history.csv', index=False)
print('15 OK')

# ── 16. BAD: non-numeric target (all text) ───────────────────────────────────
dates = pd.date_range('2023-01-01', periods=30, freq='D')
pd.DataFrame({'date': dates.strftime('%Y-%m-%d'), 'sku': 'SKU-A',
              'sales': ['cien', 'doscientos', 'trescientos'] * 10
              }).to_csv(OUT / '16_bad_all_text_target.csv', index=False)
print('16 OK')

# ── 17. BAD: header only, no data ────────────────────────────────────────────
pd.DataFrame(columns=['date', 'sku', 'sales']).to_csv(OUT / '17_bad_empty.csv', index=False)
print('17 OK')

# ── 18. BAD: single row ──────────────────────────────────────────────────────
pd.DataFrame([{'date': '2024-01-01', 'sku': 'SKU-A', 'sales': 100}]
             ).to_csv(OUT / '18_bad_single_row.csv', index=False)
print('18 OK')

# ── 19. BAD: semicolon separator ─────────────────────────────────────────────
dates = pd.date_range('2023-01-01', periods=30, freq='D')
pd.DataFrame({'date': dates.strftime('%Y-%m-%d'), 'sku': 'SKU-A',
              'sales': 100}).to_csv(OUT / '19_bad_semicolon_sep.csv', index=False, sep=';')
print('19 OK')

# ── 20. BAD: constant target (zero variance) ─────────────────────────────────
dates = pd.date_range('2023-01-01', periods=90, freq='D')
pd.DataFrame({'date': dates.strftime('%Y-%m-%d'), 'sku': 'SKU-A',
              'sales': 42}).to_csv(OUT / '20_bad_constant_target.csv', index=False)
print('20 OK')

print('\nAll 20 CSVs created in', OUT.resolve())
