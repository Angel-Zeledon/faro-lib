"""Quick test of the enhanced DataProfiler."""
import sys
sys.path.insert(0, 'ForecastingCore')

from forecasting_core.data.profiler import DataProfiler
import pandas as pd
import io
from datetime import date, timedelta

d0 = date.fromisoformat('2023-01-01')

def daily_range(n=90, val=100, sku='A'):
    rows = ['fecha,sku,ventas']
    for i in range(n):
        rows.append(f'{(d0 + timedelta(i)).isoformat()},{sku},{val}')
    return '\n'.join(rows)

tests = {
    '01_good_90days':    daily_range(n=90, val=100),
    '03_missing_30pct':  '\n'.join(
        [r for i, r in enumerate(daily_range(n=90).split('\n')) if i == 0 or i % 3 != 0]
    ),
    '04_duplicates':     daily_range(n=30) + '\n2023-01-05,A,999\n2023-01-10,A,999',
    '05_negative':       'fecha,sku,ventas\n' + '\n'.join(
        [f'2023-01-{str(i+1).zfill(2)},A,{-50 if i%7==0 else 80}' for i in range(28)]
    ),
    '06_all_zeros':      daily_range(n=90, val=0),
    '08_null_target':    'fecha,sku,ventas\n' + '\n'.join(
        [f'2023-01-{str(i+1).zfill(2)},A,{80 if i not in [5,15,25] else ""}' for i in range(60)]
    ),
    '09_short':          'fecha,sku,ventas\n2024-01-01,A,50\n2024-01-02,A,55\n2024-01-03,A,48',
    '14_intermittent':   'fecha,sku,ventas\n' + '\n'.join(
        [f'2023-0{i//30+1}-{str(i%30+1).zfill(2)},A,{0 if i%5!=0 else 5}' for i in range(90)]
    ),
    '20_constant':       daily_range(n=90, val=42),
    '17_empty':          'fecha,sku,ventas\n',
    '18_single_row':     'fecha,sku,ventas\n2024-01-01,A,100',
}

print('TEST RESULTS — Enhanced DataProfiler')
print('=' * 72)

caught = 0
missed = 0
for name, content in tests.items():
    df = pd.read_csv(io.StringIO(content))
    profiler = DataProfiler()
    result = profiler.profile(df)
    warns = result.get('warnings', [])
    dq = result.get('data_quality', {})
    issues = dq.get('issues', [])
    gap_needed = dq.get('gap_fill_needed', False)

    is_good = name.startswith('01_')
    if is_good:
        non_col_warns = [w for w in warns if 'column' not in w.lower() and 'SKU' not in w]
        status = f'GOOD ({len(warns)} col-detect warns)'
    else:
        if warns or issues:
            status = 'CAUGHT'
            caught += 1
        else:
            status = '✗ MISSED'
            missed += 1

    print(f'{name:25s}  rows={len(df):3d}  {status}')
    for w in warns:
        print(f'   WARN: {w[:85]}')
    for iss in issues:
        print(f'   [{iss["severity"].upper():7s}] {iss["type"]}: {iss["message"]}')
    if gap_needed:
        gap_iss = next((i for i in issues if i['type'] == 'temporal_gaps'), None)
        if gap_iss:
            print(f'   => GAP-FILL PROMPT will show: {gap_iss["gap_count"]} missing dates')
    print()

print(f'Caught: {caught}  |  Missed: {missed}  |  Total bad tests: {len(tests)-1}')
