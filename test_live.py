"""Live end-to-end test for all inventory endpoints."""
import sys
sys.path.insert(0, '.')

from fastapi.testclient import TestClient
from unittest import mock
from uuid import uuid4

with mock.patch('backend.workers.worker.start'):
    from backend.main import app

client = TestClient(app, raise_server_exceptions=True)

from backend.db.connection import init_pool, execute
from backend.config import settings
init_pool(settings.database_url, min_conn=1, max_conn=3)

from backend.tenants.service import create_tenant
from backend.users import service as user_svc

t = create_tenant(f'test-live-{uuid4().hex[:6]}')
email = f'live-{uuid4().hex[:6]}@example.com'
u = user_svc.create_user(t['id'], email, 'TestPass123!', 'admin', 'Test Admin')
user_svc.mark_verified(t['id'], u['id'])

resp = client.post('/api/v1/auth/login', json={'email': email, 'password': 'TestPass123!'})
assert resp.status_code == 200
token = resp.json()['data']['access_token']
H = {'Authorization': f'Bearer {token}'}
results = []

def check(name, cond, detail=''):
    status = 'PASS' if cond else 'FAIL'
    results.append((status, name, detail))

# 1. Supplier CRUD
r = client.post('/api/v1/inventory/suppliers', headers=H, json={
    'name': 'Prov Test SA', 'lead_time_dias': 12,
    'lead_time_std': 2, 'payment_terms': '30 dias'
})
check('Create supplier', r.status_code == 201, f'status={r.status_code}')
sup_id = r.json()['data']['id'] if r.status_code == 201 else None

r = client.get('/api/v1/inventory/suppliers', headers=H)
check('List suppliers', r.status_code == 200 and len(r.json()['data']) == 1)

r = client.patch(f'/api/v1/inventory/suppliers/{sup_id}', headers=H, json={'lead_time_dias': 15})
check('Patch supplier', r.status_code == 200 and r.json()['data']['lead_time_dias'] == 15)

# 2. Stock CRUD
sku = f'SKU-{uuid4().hex[:6].upper()}'
r = client.put(f'/api/v1/inventory/stock/{sku}', headers=H, json={
    'stock_actual': 150, 'lead_time_dias': 12,
    'costo_unitario': 2500, 'moq': 10
})
check('Upsert stock', r.status_code == 200 and r.json()['data']['stock_actual'] == 150)

r = client.patch(f'/api/v1/inventory/stock/{sku}', headers=H, json={'stock_actual': 180})
check('Patch stock', r.status_code == 200 and r.json()['data']['stock_actual'] == 180)

r = client.get(f'/api/v1/inventory/stock/{sku}', headers=H)
check('Get stock', r.status_code == 200 and r.json()['data']['sku'] == sku)

# 3. SKU-Supplier link
if sup_id:
    r = client.put(f'/api/v1/inventory/stock/{sku}/suppliers/{sup_id}', headers=H,
                   json={'is_primary': True, 'unit_cost': 2500, 'moq': 10})
    check('Assign supplier to SKU', r.status_code == 200)

    r = client.get(f'/api/v1/inventory/stock/{sku}/suppliers', headers=H)
    check('Get SKU suppliers', r.status_code == 200 and len(r.json()['data']) == 1)

# 4. Bulk import
csv_b = b'sku,stock_actual,lead_time_dias\nSKU-B1,200,10\nSKU-B2,50,15\n'
r = client.post('/api/v1/inventory/bulk', headers=H,
                files={'file': ('s.csv', csv_b, 'text/csv')})
check('Bulk import', r.status_code == 200 and r.json()['data']['imported'] == 2)

# 5. Inventory status
sid = uuid4().hex
r = client.get(f'/api/v1/inventory/status?session_id={sid}', headers=H)
check('Inventory status 200', r.status_code == 200)
data = r.json()['data']
check('Status has items+summary', 'items' in data and 'summary' in data)
our = next((i for i in data['items'] if i['sku'] == sku), None)
check('SKU in status', our is not None)
check('Signal is SIN_DATOS', our and our['signal'] == 'SIN_DATOS')
check('demand_trend_pct in response', our and 'demand_trend_pct' in our)
check('stock_actual correct in status', our and our['stock_actual'] == 180)

# 6. Morning briefing
r = client.get(f'/api/v1/inventory/morning-briefing?session_id={sid}', headers=H)
check('Morning briefing 200', r.status_code == 200)
b = r.json()['data']
check('Briefing has all keys', all(k in b for k in [
    'date', 'risks', 'warnings', 'overstocked',
    'demand_changes', 'recommendations', 'kpis'
]))
check('KPIs complete', all(k in b['kpis'] for k in [
    'total_skus', 'pedir_ya', 'avg_accuracy', 'capital_in_overstock'
]))
check('total_skus >= 3', b['kpis']['total_skus'] >= 3)

# 7. PO export CSV
r = client.get(f'/api/v1/inventory/status/export-po?session_id={sid}', headers=H)
check('PO export CSV', r.status_code == 200 and 'text/csv' in r.headers.get('content-type', ''))

# 8. PDF report
r = client.get(f'/api/v1/inventory/report/pdf?session_id={sid}', headers=H)
check('PDF report', (
    r.status_code == 200 and
    'application/pdf' in r.headers.get('content-type', '') and
    len(r.content) > 500
))

# 9. Events
r = client.post('/api/v1/inventory/events', headers=H, json={
    'name': 'Black Friday', 'start_date': '2026-11-27',
    'end_date': '2026-11-30', 'multiplier': 2.5
})
check('Create event', r.status_code == 201)

r = client.get('/api/v1/inventory/events', headers=H)
check('List events', r.status_code == 200 and len(r.json()['data']) >= 1)

r = client.get('/api/v1/inventory/events/upcoming?days=180', headers=H)
check('Upcoming events', r.status_code == 200)

# 10. ROI
r = client.get('/api/v1/inventory/roi', headers=H)
check('ROI summary', r.status_code == 200 and 'total_pos_generated' in r.json()['data'])

r = client.get('/api/v1/inventory/po-history', headers=H)
check('PO history', r.status_code == 200)

# 11. Delete flows
if sup_id:
    r = client.delete(f'/api/v1/inventory/suppliers/{sup_id}', headers=H)
    check('Delete supplier (soft)', r.status_code == 204)
    r = client.get('/api/v1/inventory/suppliers', headers=H)
    check('Supplier gone from list', r.status_code == 200 and len(r.json()['data']) == 0)

r = client.delete(f'/api/v1/inventory/stock/{sku}', headers=H)
check('Delete stock', r.status_code == 204)
r = client.get(f'/api/v1/inventory/stock/{sku}', headers=H)
check('Stock 404 after delete', r.status_code == 404)

# 12. Service unit tests (pure Python, no HTTP)
from backend.inventory.service import (
    _calc_signal, _calc_recommended, generate_recommendations
)
check('Signal PEDIR_YA boundary', _calc_signal(3, 15) == 'PEDIR_YA')
check('Signal PEDIR_PRONTO boundary', _calc_signal(14, 15) == 'PEDIR_PRONTO')
check('Signal OK boundary', _calc_signal(25, 15) == 'OK')
check('Signal SOBRESTOCK boundary', _calc_signal(60, 15) == 'SOBRESTOCK')
check('Recommended MOQ multiple', _calc_recommended(0, 50, 5, 14, 100) % 100 == 0)
check('Recommended zero when overstock', _calc_recommended(10_000, 1, 0.1, 14, 1) == 0)
check('Recommended positive when understocked', _calc_recommended(0, 50, 5, 14, 1) > 0)

recs = generate_recommendations([{
    'sku': 'A', 'signal': 'PEDIR_YA',
    'dias_cobertura': 2, 'lead_time_dias': 10,
    'cantidad_recomendada': 400, 'proveedor': 'Prov',
    'abc': 'A', 'demand_trend_pct': None,
    'valor_inventario': None, 'display_name': 'Producto A',
}])
check('Recommendations generated for PEDIR_YA', len(recs) >= 1)
check('Rec type is STOCKOUT_RISK', recs[0]['rec_type'] == 'STOCKOUT_RISK')
check('Rec text mentions SKU name', 'Producto A' in recs[0]['text'])
check('Rec has action field', 'action' in recs[0] and len(recs[0]['action']) > 0)

recs_demand = generate_recommendations([{
    'sku': 'B', 'signal': 'OK',
    'dias_cobertura': 30, 'lead_time_dias': 10,
    'cantidad_recomendada': 0, 'proveedor': None,
    'abc': 'B', 'demand_trend_pct': 35.0,
    'valor_inventario': 50000, 'display_name': 'Producto B',
}])
check('DEMAND_UP recommendation generated', any(r['rec_type'] == 'DEMAND_UP' for r in recs_demand))

# Cleanup
execute('DELETE FROM tenants WHERE id=%s', (t['id'],))

# Print results
passed = sum(1 for s, _, _ in results if s == 'PASS')
failed = sum(1 for s, _, _ in results if s == 'FAIL')
print()
for status, name, detail in results:
    d = f' ({detail})' if detail else ''
    print(f'  {status:4}  {name}{d}')
print()
print(f'Results: {passed} passed, {failed} failed out of {len(results)} tests')
if failed:
    sys.exit(1)
