"""
Run each test CSV through the full pipeline:
  1. Upload as data source
  2. Create session + attach
  3. Inspect
  4. Report what the system catches
"""
import os, sys, json, requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv('backend/.env')

BASE  = 'http://localhost:8001/api/v1'
EMAIL = 'demo@acmecorp.demo'
PW    = 'Test1234!'

# ── Auth ──────────────────────────────────────────────────────────────────────
r = requests.post(f'{BASE}/auth/login', json={'email': EMAIL, 'password': PW})
print('Login status:', r.status_code, r.text[:100])
token = r.json()['data']['access_token']
HDRS  = {'Authorization': f'Bearer {token}'}

CSVs = sorted(Path('test_csvs').glob('[0-9][0-9]_*.csv'))
results = []

for csv_path in CSVs:
    label = csv_path.stem
    print(f'\n── {label} ──')
    try:
        # 1. Upload
        with open(csv_path, 'rb') as f:
            up = requests.post(f'{BASE}/data-sources/file',
                               headers=HDRS,
                               files={'file': (csv_path.name, f, 'text/csv')},
                               data={'name': label})
        if up.status_code != 200:
            print(f'  UPLOAD FAILED {up.status_code}: {up.text[:200]}')
            results.append({'csv': label, 'stage': 'upload', 'error': up.text[:200]})
            continue
        src_id = up.json()['data']['id']

        # 2. Create session + attach
        sess = requests.post(f'{BASE}/sessions', headers=HDRS, json={'name': label})
        if sess.status_code != 200:
            print(f'  SESSION FAILED: {sess.text[:200]}')
            results.append({'csv': label, 'stage': 'session', 'error': sess.text[:200]})
            continue
        sid = sess.json()['data']['session_id']

        att = requests.post(f'{BASE}/sessions/{sid}/dataset', headers=HDRS,
                            json={'data_source_id': src_id})
        if att.status_code != 200:
            print(f'  ATTACH FAILED: {att.text[:200]}')
            results.append({'csv': label, 'stage': 'attach', 'error': att.text[:200]})
            continue

        # 3. Inspect
        ins = requests.post(f'{BASE}/sessions/{sid}/inspect', headers=HDRS)
        if ins.status_code != 200:
            print(f'  INSPECT FAILED {ins.status_code}: {ins.text[:300]}')
            results.append({'csv': label, 'stage': 'inspect', 'status': ins.status_code,
                            'error': ins.text[:300]})
            continue

        data = ins.json()['data']
        profile = data.get('profile', {})
        stats   = profile.get('stats', {})
        warnings = profile.get('warnings', [])

        print(f'  rows={stats.get("n_rows")} skus={stats.get("n_skus")} '
              f'freq={profile.get("recommended", {}).get("freq")}')
        if warnings:
            for w in warnings:
                print(f'  WARN: {w}')
        else:
            print('  (no warnings detected)')

        results.append({
            'csv': label,
            'stage': 'ok',
            'rows': stats.get('n_rows'),
            'skus': stats.get('n_skus'),
            'freq': profile.get('recommended', {}).get('freq'),
            'warnings': warnings,
        })

    except Exception as e:
        print(f'  EXCEPTION: {e}')
        results.append({'csv': label, 'stage': 'exception', 'error': str(e)})

print('\n\n══════ SUMMARY ══════')
for r in results:
    caught = r.get('warnings', [])
    stage  = r.get('stage', '?')
    status = '✓ caught' if caught else ('✗ missed' if stage == 'ok' else f'✗ {stage}')
    print(f"  {r['csv']:45s}  {status}")
    for w in caught:
        print(f"    → {w}")
