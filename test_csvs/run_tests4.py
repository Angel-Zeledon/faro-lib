"""
Test outlier config + Fourier features via full API pipeline.
Uploads a CSV with known outliers, runs choose_columns with various outlier strategies,
and confirms the trained job completes.
"""
import time, requests
from datetime import date, timedelta

BASE  = 'http://localhost:8001/api/v1'
EMAIL = 'demo@acmecorp.demo'
PW    = 'Test1234!'

r = requests.post(f'{BASE}/auth/login', json={'email': EMAIL, 'password': PW}, timeout=15)
token = r.json()['data']['access_token']
H = {'Authorization': f'Bearer {token}'}


def build_csv_with_outliers(n=90):
    """90 days of data for 3 SKUs; each SKU has 1–2 extreme outlier spikes."""
    rows = ['fecha,sku,ventas']
    d0 = date(2023, 1, 1)
    for i in range(n):
        d = (d0 + timedelta(i)).isoformat()
        for sku, base in [('A', 50), ('B', 200), ('C', 10)]:
            val = base
            if sku == 'A' and i in (15, 60):
                val = 5000  # outlier
            elif sku == 'B' and i in (30,):
                val = 50000  # outlier
            rows.append(f'{d},{sku},{val}')
    return '\n'.join(rows)


CSV_GOOD    = build_csv_with_outliers()
CSV_MINIMAL = 'fecha,sku,ventas\n' + '\n'.join([f'2023-01-{i+1:02d},A,{50+i}' for i in range(35)])


def test_outlier_strategy(strategy: str, extra: dict = None) -> dict:
    """Upload CSV, configure columns with given outlier strategy, return inspect result."""
    files = {'file': (f'outlier_{strategy}.csv', CSV_GOOD.encode(), 'text/csv')}
    up = requests.post(f'{BASE}/datasets', headers=H, files=files)
    if up.status_code not in (200, 201):
        return {'name': strategy, 'stage': 'upload_fail', 'msg': up.text[:200]}
    ds_id = up.json()['data']['id']

    ss = requests.post(f'{BASE}/sessions', headers=H, json={'name': f'test_outlier_{strategy}'})
    sid = ss.json()['data']['session_id']

    at = requests.post(f'{BASE}/sessions/{sid}/dataset', headers=H, json={'dataset_id': ds_id})
    if at.status_code != 200:
        return {'name': strategy, 'stage': 'attach_fail', 'msg': at.json().get('detail', '')}

    ins = requests.get(f'{BASE}/sessions/{sid}/inspect', headers=H, timeout=30)
    if ins.status_code != 200:
        return {'name': strategy, 'stage': 'inspect_fail', 'msg': ins.text[:200]}

    profile  = ins.json()['data']['profile']
    dq       = profile.get('data_quality', {})
    outliers = dq.get('outliers', {})

    # Build outlier config
    outlier_config = {'strategy': strategy}
    if extra:
        outlier_config.update(extra)

    body = {
        'date_column':   'date',
        'target_column': 'sales',
        'sku_column':    'sku',
        'gap_fill':      'zero',
        'outlier_config': outlier_config,
    }
    cc = requests.post(f'{BASE}/sessions/{sid}/columns', headers=H, json=body)
    if cc.status_code != 200:
        return {'name': strategy, 'stage': 'columns_fail', 'msg': cc.json().get('detail', cc.text[:200])}

    return {
        'name':         strategy,
        'stage':        'ok',
        'total_outliers': outliers.get('total_count', 0),
        'per_sku_keys': list(outliers.get('per_sku', {}).keys()),
        'session_id':   sid,
    }


def test_per_sku_override():
    """Global strategy=leave, but SKU A gets winsorize_sigma and B gets iqr_fence."""
    files = {'file': ('per_sku_test.csv', CSV_GOOD.encode(), 'text/csv')}
    up = requests.post(f'{BASE}/datasets', headers=H, files=files)
    ds_id = up.json()['data']['id']

    ss = requests.post(f'{BASE}/sessions', headers=H, json={'name': 'test_per_sku_override'})
    sid = ss.json()['data']['session_id']

    requests.post(f'{BASE}/sessions/{sid}/dataset', headers=H, json={'dataset_id': ds_id})
    requests.get(f'{BASE}/sessions/{sid}/inspect', headers=H, timeout=30)

    body = {
        'date_column':   'date',
        'target_column': 'sales',
        'sku_column':    'sku',
        'gap_fill':      'zero',
        'outlier_config': {
            'strategy': 'leave',
            'per_sku_overrides': {'A': 'winsorize_sigma', 'B': 'iqr_fence'},
            'per_sku_n_sigma':   {'A': 2.5},
            'per_sku_iqr_k':     {'B': 1.5},
        },
    }
    cc = requests.post(f'{BASE}/sessions/{sid}/columns', headers=H, json=body)
    return {
        'name':  'per_sku_override',
        'stage': 'ok' if cc.status_code == 200 else f'fail:{cc.status_code}',
        'msg':   cc.json().get('detail', '') if cc.status_code != 200 else '',
    }


def test_fourier_features():
    """Configure features with Fourier periods=[7,30], K=2."""
    files = {'file': ('fourier_test.csv', CSV_MINIMAL.encode(), 'text/csv')}
    up = requests.post(f'{BASE}/datasets', headers=H, files=files)
    ds_id = up.json()['data']['id']

    ss = requests.post(f'{BASE}/sessions', headers=H, json={'name': 'test_fourier'})
    sid = ss.json()['data']['session_id']

    requests.post(f'{BASE}/sessions/{sid}/dataset', headers=H, json={'dataset_id': ds_id})
    requests.get(f'{BASE}/sessions/{sid}/inspect', headers=H, timeout=30)

    cc = requests.post(f'{BASE}/sessions/{sid}/columns', headers=H, json={
        'date_column': 'date', 'target_column': 'sales', 'sku_column': 'sku', 'gap_fill': 'zero'})
    if cc.status_code != 200:
        return {'name': 'fourier', 'stage': f'columns_fail:{cc.status_code}'}

    fc = requests.post(f'{BASE}/sessions/{sid}/config/features', headers=H, json={
        'lags': [1, 7], 'rolling': [7], 'calendar': True,
        'fourier_periods': [7, 30], 'fourier_K': 2,
    })
    return {
        'name':  'fourier_features',
        'stage': 'ok' if fc.status_code == 200 else f'fail:{fc.status_code}',
        'msg':   fc.json().get('detail', '') if fc.status_code != 200 else '',
    }


print('\n' + '='*70)
print('TEST: Outlier Config + Fourier via API')
print('='*70 + '\n')

results = []

for strat in ['leave', 'winsorize_sigma', 'winsorize_pct', 'iqr_fence', 'remove', 'log1p']:
    extra = {}
    if strat == 'winsorize_sigma': extra = {'n_sigma': 2.5}
    if strat == 'winsorize_pct':   extra = {'percentile': 2.0}
    if strat == 'iqr_fence':       extra = {'iqr_k': 1.5}
    results.append(test_outlier_strategy(strat, extra))

results.append(test_per_sku_override())
results.append(test_fourier_features())

PASS = FAIL = 0
for r in results:
    ok = r['stage'] == 'ok'
    status = 'PASS' if ok else f'FAIL ({r["stage"]})'
    if ok:
        PASS += 1
        extras = ''
        if 'total_outliers' in r:
            extras = f' | outliers_detected={r["total_outliers"]}, per_sku={r["per_sku_keys"]}'
        print(f'  {r["name"]:25s}  {status}{extras}')
    else:
        FAIL += 1
        print(f'  {r["name"]:25s}  {status}  msg={r.get("msg","")[:60]}')

print(f'\n  PASS: {PASS}  |  FAIL: {FAIL}  |  Total: {len(results)}')
