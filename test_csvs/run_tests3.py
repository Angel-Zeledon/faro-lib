"""
Test enhanced profiler via the full API pipeline.
Verifies that data quality issues produce meaningful warnings (not just generic ones).
"""
import requests
from datetime import date, timedelta

BASE  = 'http://localhost:8001/api/v1'
EMAIL = 'demo@acmecorp.demo'
PW    = 'Test1234!'

r = requests.post(f'{BASE}/auth/login', json={'email': EMAIL, 'password': PW}, timeout=15)
token = r.json()['data']['access_token']
H = {'Authorization': f'Bearer {token}'}


def test_csv(name: str, content: str) -> dict:
    files = {'file': (f'{name}.csv', content.encode(), 'text/csv')}
    up = requests.post(f'{BASE}/datasets', headers=H, files=files)
    if up.status_code not in (200, 201):
        return {'name': name, 'stage': 'upload_fail', 'code': up.status_code, 'msg': up.text[:200]}
    ds_id = up.json()['data']['id']

    ss = requests.post(f'{BASE}/sessions', headers=H, json={'name': name})
    if ss.status_code not in (200, 201):
        return {'name': name, 'stage': 'session_fail', 'msg': ss.text[:200]}
    sid = ss.json()['data']['session_id']

    at = requests.post(f'{BASE}/sessions/{sid}/dataset', headers=H, json={'dataset_id': ds_id})
    if at.status_code != 200:
        return {'name': name, 'stage': 'attach_fail', 'msg': at.json().get('detail', at.text[:200])}

    ins = requests.get(f'{BASE}/sessions/{sid}/inspect', headers=H, timeout=30)
    if ins.status_code != 200:
        try:
            detail = ins.json().get('detail', ins.text[:300])
        except Exception:
            detail = ins.text[:300] or f'HTTP {ins.status_code}'
        return {'name': name, 'stage': 'inspect_fail', 'code': ins.status_code, 'msg': detail}

    d = ins.json()['data']
    profile  = d.get('profile', {})
    stats    = profile.get('stats', {})
    warnings = profile.get('warnings', [])
    dq       = profile.get('data_quality', {})
    return {
        'name':          name,
        'stage':         'ok',
        'rows':          stats.get('n_rows'),
        'warnings':      warnings,
        'issues':        dq.get('issues', []),
        'gap_fill_needed': dq.get('gap_fill_needed', False),
    }


d0 = date.fromisoformat('2023-01-01')
def daily_range(n=90, val=100):
    rows = ['fecha,sku,ventas']
    for i in range(n):
        rows.append(f'{(d0 + timedelta(i)).isoformat()},A,{val}')
    return '\n'.join(rows)


tests = {
    '01_good_clean_90days': daily_range(n=90, val=100),
    '03_missing_dates_30pct': '\n'.join(
        [r for i, r in enumerate(daily_range(n=90).split('\n')) if i == 0 or i % 3 != 0]
    ),
    '04_duplicates': daily_range(n=30) + '\n2023-01-05,A,999\n2023-01-10,A,999',
    '05_negative': 'fecha,sku,ventas\n' + '\n'.join(
        [f'2023-01-{str(i+1).zfill(2)},A,{-50 if i%7==0 else 80}' for i in range(28)]
    ),
    '06_all_zeros': daily_range(n=90, val=0),
    '09_short_3rows': 'fecha,sku,ventas\n2024-01-01,A,50\n2024-01-02,A,55\n2024-01-03,A,48',
    '14_intermittent': 'fecha,sku,ventas\n' + '\n'.join(
        [f'2023-0{i//30+1}-{str(i%30+1).zfill(2)},A,{0 if i%5!=0 else 5}' for i in range(90)]
    ),
    '17_empty_header': 'fecha,sku,ventas\n',
    '20_constant': daily_range(n=90, val=42),
}

results = []
for name, content in tests.items():
    result = test_csv(name, content)
    results.append(result)

print('\n' + '='*74)
print('TEST RESULTS — Enhanced Profiler via API')
print('='*74 + '\n')

CAUGHT = 0
MISSED = 0
for r in results:
    stage = r.get('stage', '?')
    warns = r.get('warnings', [])
    issues = r.get('issues', [])
    gap_needed = r.get('gap_fill_needed', False)
    is_good = r['name'].startswith('01_')

    if stage != 'ok':
        status = f'PIPELINE ERROR ({stage}): {r.get("msg","")[:70]}'
    elif is_good:
        status = f'GOOD ({len(warns)} warns, {len(issues)} issues)'
    elif warns or issues:
        status = 'CAUGHT'
        CAUGHT += 1
    else:
        status = 'MISSED (no warning generated)'
        MISSED += 1

    rows_str = str(r.get('rows', '?')).rjust(5)
    print(f'  {r["name"]:30s}  rows={rows_str}  {status}')

    # Show non-generic warnings
    generic = {'No group/SKU column detected', 'No date column detected',
               'No numeric target column detected'}
    meaningful_warns = [w for w in warns if not any(g in w for g in generic)]
    for w in meaningful_warns:
        print(f'    WARN: {w[:75]}')

    for iss in issues:
        sev = iss.get('severity', '?').upper()
        print(f'    [{sev:7s}] {iss["type"]}: {iss["message"][:65]}')

    if gap_needed:
        gap_iss = next((i for i in issues if i['type'] == 'temporal_gaps'), {})
        print(f'    => GAP-FILL prompt: {gap_iss.get("gap_count","?")} missing dates detected')

print(f'\n  Caught: {CAUGHT}  |  Missed: {MISSED}  |  Total bad tests: {len(tests)-1}')
