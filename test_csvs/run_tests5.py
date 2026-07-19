"""
Full end-to-end test of the forecast wizard.
Covers: upload -> inspect -> columns (with outlier config) -> features (Fourier) -> models -> validation -> train -> SKU Intelligence.
20 files: 12 CSV + 4 JSON valid, 4 invalid (empty, no dates, all zeros, missing columns).
"""
import requests, time, json, io
from datetime import date, timedelta

BASE  = 'http://localhost:8001/api/v1'
EMAIL = 'demo@acmecorp.demo'
PW    = 'Test1234!'

r = requests.post(f'{BASE}/auth/login', json={'email': EMAIL, 'password': PW}, timeout=15)
assert r.status_code == 200, f"Login failed: {r.text[:200]}"
token = r.json()['data']['access_token']
H = {'Authorization': f'Bearer {token}'}


# ─────────────────────────────────────────────────────────────────────────────
# Generadores de datos
# ─────────────────────────────────────────────────────────────────────────────

def _d(n, start='2022-01-01'):
    d0 = date.fromisoformat(start)
    return [(d0 + timedelta(i)).isoformat() for i in range(n)]

def daily_single(n=120, noise=True):
    rows = ['date,sales']
    for i, d in enumerate(_d(n)):
        val = 100 + (i % 7) * 5 + (10 if noise and i % 13 == 0 else 0)
        rows.append(f'{d},{val}')
    return '\n'.join(rows)

def daily_multi(skus=3, n=90):
    rows = ['date,sku,units']
    bases = {'A': 50, 'B': 200, 'C': 15}
    for i, d in enumerate(_d(n)):
        for sku, base in list(bases.items())[:skus]:
            rows.append(f'{d},{sku},{base + i%7*3}')
    return '\n'.join(rows)

def weekly_multi(skus=4, n=52):
    rows = ['week,product,demand']
    bases = {'P1': 300, 'P2': 150, 'P3': 80, 'P4': 500}
    d0 = date(2022, 1, 3)
    for i in range(n):
        d = (d0 + timedelta(weeks=i)).isoformat()
        for sku, base in list(bases.items())[:skus]:
            rows.append(f'{d},{sku},{base + i%4*20}')
    return '\n'.join(rows)

def monthly_multi(n=36):
    rows = ['month,category,revenue']
    d0 = date(2021, 1, 1)
    for i in range(n):
        m = i % 12
        y = 2021 + i // 12
        d = f'{y}-{m+1:02d}-01'
        for cat, base in [('Electronics', 10000), ('Clothing', 5000), ('Food', 8000)]:
            rows.append(f'{d},{cat},{base + m*200 + i*50}')
    return '\n'.join(rows)

def with_exog(n=90):
    rows = ['fecha,sku,ventas,price,promo']
    for i, d in enumerate(_d(n)):
        sales = 100 + i%7*5 - (30 if i%20==0 else 0)
        price = 9.99 if i%30 < 15 else 12.99
        promo = 1 if i%20==0 else 0
        rows.append(f'{d},A,{ventas},{precio},{promo}')
    return '\n'.join(rows)

def with_outliers_clean(n=90, skus=3):
    rows = ['fecha,sku,ventas']
    for sku, base in [('X', 100), ('Y', 250), ('Z', 50)]:
        for i, d in enumerate(_d(n)):
            val = base + i%7*base//10
            if i in (15, 60): val = base * 20  # known outliers
            rows.append(f'{d},{sku},{val}')
    return '\n'.join(rows)

def intermittent(n=90):
    rows = ['date,sku,qty']
    for i, d in enumerate(_d(n)):
        rows.append(f'{d},SPARE_PART,{0 if i%5 != 0 else 3}')
    return '\n'.join(rows)

def lumpy(n=90):
    import random; random.seed(42)
    rows = ['date,sku,qty']
    for i, d in enumerate(_d(n)):
        val = random.choice([0,0,0,0,0,0,0,1,5,20])
        rows.append(f'{d},LUMPY_A,{val}')
    return '\n'.join(rows)

def high_sku_count(n_skus=15, n=60):
    rows = ['fecha,sku,ventas']
    for s in range(n_skus):
        sku = f'SKU_{s+1:03d}'
        for i, d in enumerate(_d(n)):
            rows.append(f'{d},{sku},{50 + s*10 + i%7*2}')
    return '\n'.join(rows)

def seasonal_strong(n=365):
    rows = ['date,sales']
    import math
    for i, d in enumerate(_d(n)):
        v = 200 + 100*math.sin(2*math.pi*i/7) + 50*math.sin(2*math.pi*i/30) + i//30
        rows.append(f'{d},{max(0,round(v))}')
    return '\n'.join(rows)

def with_gaps(n=90, gap_pct=0.15):
    import random; random.seed(7)
    rows = ['fecha,sku,ventas']
    dates = _d(n)
    for i, d in enumerate(dates):
        if random.random() > gap_pct:
            rows.append(f'{d},A,{100 + i%10}')
    return '\n'.join(rows)

def json_good_records():
    records = []
    for i, d in enumerate(_d(60)):
        records.append({'date': d, 'sku': 'J1', 'sales': 80 + i%5*4})
        records.append({'date': d, 'sku': 'J2', 'sales': 200 + i%7*10})
    return json.dumps(records, indent=2).encode()

def json_good_table():
    data = {
        'columns': ['date', 'product', 'qty'],
        'data': [[_d(45)[i], f'P{i%3+1}', 100 + i*2] for i in range(45)]
    }
    return json.dumps(data).encode()

def json_nested():
    records = [{'ts': _d(50)[i], 'item': 'BOLT', 'cnt': 10 + i%8} for i in range(50)]
    return json.dumps({'results': records, 'meta': {'source': 'erp'}}).encode()

def json_array_flat():
    return json.dumps([
        {'date': _d(40)[i], 'sku': 'WIDGET', 'demand': 30 + i*3} for i in range(40)
    ]).encode()


# ─────────────────────────────────────────────────────────────────────────────
# Definition of the 20 test files
# ─────────────────────────────────────────────────────────────────────────────

TESTS = [
    # ── GOOD CSVs ──────────────────────────────────────────────────────────
    {'name': '01_daily_single_sku',    'ext': 'csv', 'data': daily_single(),          'expect': 'good'},
    {'name': '02_daily_3_skus',        'ext': 'csv', 'data': daily_multi(3),          'expect': 'good'},
    {'name': '03_weekly_4_skus',       'ext': 'csv', 'data': weekly_multi(),          'expect': 'good'},
    {'name': '04_monthly_3_cats',      'ext': 'csv', 'data': monthly_multi(),         'expect': 'good'},
    {'name': '05_with_exog_vars',      'ext': 'csv', 'data': with_exog(),             'expect': 'good'},
    {'name': '06_outliers_raw',        'ext': 'csv', 'data': with_outliers_clean(),   'expect': 'good'},
    {'name': '07_intermittent',        'ext': 'csv', 'data': intermittent(),          'expect': 'warn'},
    {'name': '08_lumpy_demand',        'ext': 'csv', 'data': lumpy(),                 'expect': 'warn'},
    {'name': '09_15_skus_60days',      'ext': 'csv', 'data': high_sku_count(),        'expect': 'good'},
    {'name': '10_seasonal_365days',    'ext': 'csv', 'data': seasonal_strong(),       'expect': 'good'},
    {'name': '11_with_gaps_15pct',     'ext': 'csv', 'data': with_gaps(),             'expect': 'warn'},
    {'name': '12_daily_7_skus',        'ext': 'csv', 'data': daily_multi(7),          'expect': 'good'},
    # ── GOOD JSONs ─────────────────────────────────────────────────────────
    {'name': '13_json_records',        'ext': 'json', 'data': json_good_records(),    'expect': 'good'},
    {'name': '14_json_table',          'ext': 'json', 'data': json_good_table(),      'expect': 'good'},
    {'name': '15_json_nested',         'ext': 'json', 'data': json_nested(),          'expect': 'good'},
    {'name': '16_json_array_flat',     'ext': 'json', 'data': json_array_flat(),      'expect': 'good'},
    # ── BAD files ──────────────────────────────────────────────────────────
    {'name': '17_bad_empty',           'ext': 'csv', 'data': 'fecha,sku,ventas\n',       'expect': 'bad'},
    {'name': '18_bad_no_numeric',      'ext': 'csv', 'data': 'date,sku,notes\n2024-01-01,A,foo\n2024-01-02,A,bar\n', 'expect': 'bad'},
    {'name': '19_bad_all_zeros',       'ext': 'csv', 'data': daily_single(60).replace(',100',',0').replace(',1',',0'), 'expect': 'bad'},
    {'name': '20_bad_short_2rows',     'ext': 'csv', 'data': 'fecha,sku,ventas\n2024-01-01,A,10\n2024-01-02,A,12', 'expect': 'bad'},
]


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def upload_and_inspect(test: dict) -> dict:
    name, ext = test['name'], test['ext']
    raw = test['data']
    content = raw.encode() if isinstance(raw, str) else raw
    mime = 'application/json' if ext == 'json' else 'text/csv'

    up = requests.post(f'{BASE}/datasets', headers=H,
                       files={'file': (f'{name}.{ext}', content, mime)})
    if up.status_code not in (200, 201):
        return {'stage': 'upload_fail', 'msg': up.text[:150]}
    ds_id = up.json()['data']['id']

    ss = requests.post(f'{BASE}/sessions', headers=H, json={'name': name})
    if ss.status_code not in (200, 201):
        return {'stage': 'session_fail', 'msg': ss.json().get('detail', ss.text[:100])}
    sid = ss.json()['data']['session_id']

    at = requests.post(f'{BASE}/sessions/{sid}/dataset', headers=H, json={'dataset_id': ds_id})
    if at.status_code != 200:
        return {'stage': 'attach_fail', 'sid': sid, 'msg': at.json().get('detail', '')}

    ins = requests.get(f'{BASE}/sessions/{sid}/inspect', headers=H, timeout=60)
    if ins.status_code != 200:
        try: detail = ins.json().get('detail', ins.text[:200])
        except: detail = ins.text[:200]
        return {'stage': 'inspect_fail', 'sid': sid, 'code': ins.status_code, 'msg': detail}

    d = ins.json()['data']
    return {
        'stage':    'ok',
        'sid':      sid,
        'ds_id':    ds_id,
        'profile':  d.get('profile', {}),
        'col_opts': d.get('column_options', {}),
    }


def configure_and_train(sid: str, col_opts: dict, profile: dict, outlier_strategy='iqr_fence') -> dict:
    """Configure all steps and kick off training. Returns job_id or error."""
    tc = col_opts.get('target_candidates', [])
    dc = col_opts.get('date_candidates', [])
    gc = col_opts.get('group_candidates', [])
    if not tc or not dc:
        return {'stage': 'no_cols'}

    outliers = profile.get('data_quality', {}).get('outliers', {})
    per_sku_overrides = {}
    for sku, info in outliers.get('per_sku', {}).items():
        if info.get('count', 0) > 0:
            per_sku_overrides[sku] = outlier_strategy

    cc = requests.post(f'{BASE}/sessions/{sid}/columns', headers=H, json={
        'date_column':   dc[0],
        'target_column': tc[0],
        'sku_column':    gc[0] if gc else None,
        'gap_fill':      'zero',
        'outlier_config': {
            'strategy': 'leave',
            'iqr_k': 1.5,
            'per_sku_overrides': per_sku_overrides,
        },
    })
    if cc.status_code != 200:
        return {'stage': 'columns_fail', 'msg': cc.json().get('detail', '')}

    fc = requests.post(f'{BASE}/sessions/{sid}/config/features', headers=H, json={
        'lags': [1, 7, 14], 'rolling': [7, 14], 'calendar': True,
        'fourier_periods': [7], 'fourier_K': 2,
    })
    if fc.status_code != 200:
        return {'stage': 'features_fail', 'msg': fc.json().get('detail', '')}

    mc = requests.post(f'{BASE}/sessions/{sid}/config/models', headers=H, json={
        'mode': 'selected',
        'selected_models': ['lightgbm', 'ets'],
        'auto_select_best': True,
        'selection_metric': 'wape',
    })
    if mc.status_code != 200:
        return {'stage': 'models_fail', 'msg': mc.json().get('detail', '')}

    vc = requests.post(f'{BASE}/sessions/{sid}/config/training', headers=H, json={
        'train_ratio': 0.8, 'walk_forward': True, 'wfv_splits': 2,
        'min_history': 15, 'horizon': 7,
    })
    if vc.status_code != 200:
        return {'stage': 'validation_fail', 'msg': vc.json().get('detail', '')}

    tr = requests.post(f'{BASE}/sessions/{sid}/train', headers=H)
    if tr.status_code not in (200, 201, 202):
        return {'stage': 'train_start_fail', 'msg': tr.json().get('detail', tr.text[:100])}

    job_id = tr.json()['data'].get('job_id') or tr.json()['data'].get('id')
    return {'stage': 'training', 'job_id': job_id}


def wait_for_job(job_id: str, timeout=300) -> str:
    """Poll job until terminal state. Returns final status."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = requests.get(f'{BASE}/jobs/{job_id}', headers=H)
        if r.status_code != 200:
            return 'poll_error'
        status = r.json()['data'].get('status', '')
        if status in ('COMPLETED', 'FAILED', 'CANCELLED'):
            return status
        time.sleep(5)
    return 'TIMEOUT'


def get_sku_intelligence(sid: str) -> dict:
    # First get metrics to know which SKUs exist
    metrics_r = requests.get(f'{BASE}/sessions/{sid}/metrics', headers=H)
    if metrics_r.status_code != 200:
        return {'ok': False, 'msg': 'metrics unavailable', 'code': metrics_r.status_code}

    metrics_data = metrics_r.json()['data']
    rows = metrics_data.get('rows', [])
    skus = list({row['sku'] for row in rows if row.get('sku')}) or ['__all__']

    # Test SKU intelligence for first SKU
    sku = skus[0]
    r = requests.get(f'{BASE}/sessions/{sid}/sku-intelligence/{sku}', headers=H)
    if r.status_code == 200:
        d = r.json()['data']
        return {
            'ok': True,
            'n_skus': len(skus),
            'sku_tested': sku,
            'has_forecast': len(d.get('forecast', [])) > 0,
            'has_historical': len(d.get('historical', [])) > 0,
        }
    return {'ok': False, 'sku': sku, 'code': r.status_code, 'msg': r.text[:100]}


# ─────────────────────────────────────────────────────────────────────────────
# Run
# ─────────────────────────────────────────────────────────────────────────────

print('\n' + '='*80)
print('TEST END-TO-END — 20 archivos (CSV + JSON) — Wizard completo')
print('='*80 + '\n')

UPLOAD_OK = INSPECT_OK = TRAIN_OK = TRAIN_SKIP = 0
UPLOAD_FAIL = INSPECT_FAIL = TRAIN_FAIL = 0

train_candidates = []   # sessions that completed all config steps

for test in TESTS:
    name = test['name']
    expect = test['expect']

    r = upload_and_inspect(test)
    stage = r.get('stage')

    if stage != 'ok':
        status = f'PIPELINE ERROR ({stage}): {r.get("msg","")[:60]}'
        if expect == 'bad':
            status = f'[expected] {status}'
            UPLOAD_OK += 1
        else:
            UPLOAD_FAIL += 1
        print(f'  {name:35s}  {status}')
        continue

    UPLOAD_OK += 1
    profile  = r['profile']
    col_opts = r['col_opts']
    sid      = r['sid']
    stats    = profile.get('stats', {})
    dq       = profile.get('data_quality', {})
    outliers = dq.get('outliers', {})
    warns    = profile.get('warnings', [])
    issues   = dq.get('issues', [])

    out_str = f' | outliers={outliers.get("total_count",0)}' if outliers.get('total_count',0) > 0 else ''
    warn_str = f' | {len(warns)} warns' if warns else ''
    issue_str = f' | {len(issues)} issues' if issues else ''
    INSPECT_OK += 1
    print(f'  {name:35s}  rows={stats.get("n_rows","?"):>5}  SKUs={stats.get("n_skus","1"):>3}{out_str}{warn_str}{issue_str}')

    # For good/warn files: do full wizard
    if expect in ('good', 'warn'):
        cfg = configure_and_train(sid, col_opts, profile)
        cfg_stage = cfg.get('stage', '')
        if cfg_stage == 'training':
            train_candidates.append({'name': name, 'sid': sid, 'job_id': cfg['job_id']})
            print(f'    => Training queued (job={cfg["job_id"][:16]})')
            TRAIN_OK += 1
        elif cfg_stage == 'no_cols':
            print(f'    => Skipped (no usable columns detected)')
            TRAIN_SKIP += 1
        else:
            print(f'    => Config failed at [{cfg_stage}]: {cfg.get("msg","")[:60]}')
            TRAIN_FAIL += 1

print(f'\n{"─"*80}')
print(f'  Inspect:  {INSPECT_OK} OK | {UPLOAD_FAIL} failed')
print(f'  Training: {TRAIN_OK} queued | {TRAIN_SKIP} skipped | {TRAIN_FAIL} failed')

# ── Wait for jobs ──────────────────────────────────────────────────────────
if train_candidates:
    print(f'\n  Waiting for {len(train_candidates)} training jobs...\n')
    for item in train_candidates:
        final = wait_for_job(item['job_id'], timeout=240)
        sym = '✓' if final == 'COMPLETED' else 'X' if final == 'FAILED' else '?'
        print(f'  {sym} {item["name"]:35s}  status={final}')

        if final == 'COMPLETED':
            sku_r = get_sku_intelligence(item['sid'])
            if sku_r.get('ok'):
                print(f'    SKU Intelligence: {sku_r.get("n_skus","?")} SKUs available')
            else:
                print(f'    SKU Intelligence: not available (code={sku_r.get("code")})')

COMPLETED_JOBS = sum(1 for item in train_candidates if wait_for_job(item['job_id'], timeout=1) == 'COMPLETED')

print(f'\n{"="*80}')
print(f'  DONE — {len(TESTS)} files tested')
print(f'  Inspect OK: {INSPECT_OK}/{len(TESTS)} | Training queued: {TRAIN_OK} | Skipped: {TRAIN_SKIP}')
print(f'{"="*80}\n')
