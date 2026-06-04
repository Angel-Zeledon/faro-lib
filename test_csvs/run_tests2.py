"""
Test pipeline via backend API, same flow as the frontend.
"""
import os, sys, requests
from pathlib import Path
from dotenv import load_dotenv
load_dotenv('backend/.env')

BASE  = 'http://localhost:8001/api/v1'
EMAIL = 'demo@acmecorp.demo'
PW    = 'Test1234!'

r = requests.post(f'{BASE}/auth/login', json={'email': EMAIL, 'password': PW}, timeout=5)
token = r.json()['data']['access_token']
H = {'Authorization': f'Bearer {token}'}


def test_csv(name: str, content: str) -> dict:
    # 1. Upload → /datasets
    files = {'file': (f'{name}.csv', content.encode(), 'text/csv')}
    up = requests.post(f'{BASE}/datasets', headers=H, files=files)
    if up.status_code not in (200, 201):
        return {'name': name, 'stage': 'upload_fail', 'code': up.status_code,
                'msg': up.json().get('detail', up.text[:200])}
    ds_id = up.json()['data']['id']

    # 2. Create session
    ss = requests.post(f'{BASE}/sessions', headers=H, json={'name': name})
    if ss.status_code not in (200, 201):
        return {'name': name, 'stage': 'session_fail', 'msg': ss.text[:200]}
    sid = ss.json()['data']['session_id']

    # 3. Attach
    at = requests.post(f'{BASE}/sessions/{sid}/dataset', headers=H, json={'dataset_id': ds_id})
    if at.status_code != 200:
        return {'name': name, 'stage': 'attach_fail', 'msg': at.json().get('detail', at.text[:200])}

    # 4. Inspect (GET)
    ins = requests.get(f'{BASE}/sessions/{sid}/inspect', headers=H)
    if ins.status_code != 200:
        return {'name': name, 'stage': 'inspect_fail', 'code': ins.status_code,
                'msg': ins.json().get('detail', ins.text[:300])}

    d = ins.json()['data']
    profile  = d.get('profile', {})
    stats    = profile.get('stats', {})
    warnings = profile.get('warnings', [])
    return {
        'name':     name,
        'stage':    'ok',
        'rows':     stats.get('n_rows'),
        'skus':     stats.get('n_skus'),
        'freq':     profile.get('recommended', {}).get('freq'),
        'warnings': warnings,
    }


# ── Build test cases inline ────────────────────────────────────────────────────
def daily_range(start='2023-01-01', n=90, sku='A', val=100):
    from datetime import date, timedelta
    d0 = date.fromisoformat(start)
    rows = ['fecha,sku,ventas']
    for i in range(n):
        rows.append(f'{(d0 + timedelta(i)).isoformat()},{sku},{val}')
    return '\n'.join(rows)

tests = {
    '01_good_clean_90days':      daily_range(n=90, val=100),
    '03_missing_dates_30pct': (
        # 90-day range but with 30% rows removed
        lambda: (lambda rows: '\n'.join([rows[0]] + [r for i,r in enumerate(rows[1:]) if i % 3 != 2]))(daily_range(n=90).split('\n'))
    )(),
    '04_duplicates': (
        lambda: (lambda s: s + '\n2023-01-05,A,999\n2023-01-10,A,999\n2023-01-15,A,999')(daily_range(n=30))
    )(),
    '05_negative': (
        lambda: '\n'.join(
            ['fecha,sku,ventas'] +
            [f'2023-01-{str(i+1).zfill(2)},A,{-50 if i%7==0 else 80}' for i in range(28)]
        )
    )(),
    '06_all_zeros':             daily_range(n=90, val=0),
    '07_outliers': (
        lambda: '\n'.join(
            ['fecha,sku,ventas'] +
            [f'2023-0{i//30+1}-{str(i%30+1).zfill(2)},A,{999999 if i==30 else 100}' for i in range(90)]
        )
    )(),
    '08_null_target': (
        lambda: '\n'.join(
            ['fecha,sku,ventas'] +
            [f'2023-0{i//30+1}-{str(i%30+1).zfill(2)},A,{("" if i in [5,15,25,35] else 80)}' for i in range(60)]
        )
    )(),
    '09_short_6rows':           'fecha,sku,ventas\n2024-01-01,A,50\n2024-01-02,A,55\n2024-01-03,A,48\n2024-01-04,A,52\n2024-01-05,A,60\n2024-01-06,A,45',
    '10_mixed_date_format':     'fecha,sku,ventas\n01/15/2023,A,100\n2023-03-15,A,120\nMarch 15 2023,A,130',
    '11_text_in_numeric':       'fecha,sku,ventas\n2023-01-01,A,100\n2023-01-02,A,N/A\n2023-01-03,A,#VALUE!\n2023-01-04,A,80',
    '14_intermittent_80pct': (
        lambda: '\n'.join(
            ['fecha,sku,ventas'] +
            [f'2023-0{i//30+1}-{str(i%30+1).zfill(2)},A,{0 if i%5!=0 else 5}' for i in range(90)]
        )
    )(),
    '15_unequal_history':       (
        'fecha,sku,ventas\n' +
        '\n'.join([f'2021-01-{str(i+1).zfill(2)},OLD,100' for i in range(28)]) + '\n' +
        '2024-06-01,NEW,50\n2024-06-02,NEW,60\n'
    ),
    '17_empty_header_only':     'fecha,sku,ventas\n',
    '18_single_row':            'fecha,sku,ventas\n2024-01-01,A,100',
    '19_semicolons':            'fecha;sku;ventas\n2023-01-01;A;100\n2023-01-02;A;110',
    '20_constant':              daily_range(n=90, val=42),
}

results = []
for name, content in tests.items():
    result = test_csv(name, content)
    results.append(result)

print('\n═══════════════════════════════════════════════════════════════════')
print('TEST RESULTS — What the system catches vs misses')
print('═══════════════════════════════════════════════════════════════════\n')

CAUGHT = 0
MISSED = 0
for r in results:
    stage = r.get('stage', '?')
    warns = r.get('warnings', [])
    is_good = r['name'].startswith('01_')

    if stage != 'ok':
        status = f'✗ PIPELINE ERROR ({stage}): {r.get("msg","")[:80]}'
    elif is_good:
        status = '✓ GOOD (no warnings expected)'
    elif warns:
        status = '✓ CAUGHT'
        CAUGHT += 1
    else:
        status = '✗ MISSED (no warning generated)'
        MISSED += 1

    print(f'  {r["name"]:35s}  rows={str(r.get("rows","?")).rjust(5)}  {status}')
    for w in warns:
        print(f'    → {w}')

print(f'\n  Caught: {CAUGHT}  |  Missed: {MISSED}  |  Total bad tests: {len(tests)-1}')
