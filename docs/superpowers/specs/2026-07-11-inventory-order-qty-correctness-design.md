# Diseño — Corrección de cantidades de compra en el semáforo de inventario

**Fecha:** 2026-07-11
**Alcance:** puntos 1, 2 y 3 de la solicitud (correctness de cantidades y edición manual).
**Fuera de alcance (tandas siguientes):** plantilla CSV enriquecida (4), import con proveedor documentado (5), limpieza de "Avanzado" y opciones sin uso (6, 7).

## Problema

Tres comportamientos incorrectos alrededor de la cantidad sugerida de compra:

1. **Sugiere pedir con stock suficiente.** `_calc_recommended`
   (`backend/inventory/service.py`) devuelve `demanda_lead_time + safety_stock −
   stock_actual` sin importar la señal del semáforo. Un SKU en `OK` con alta
   variabilidad puede arrojar una cantidad > 0 solo por el safety stock, aunque
   el semáforo diga que hay stock suficiente.

2. **Se puede aprobar/exportar una compra de 0 unidades.** Causa raíz: un SKU
   `PEDIR_PRONTO` puede tener `cantidad_recomendada = 0` cuando el stock ya cubre
   el lead time + colchón (`max(0, …)` da 0). Ese ítem entra al carrito de `/hoy`
   con `qty = 0` y hoy es aprobable y exportable como línea de 0 unidades
   (`Frontend/src/app/hoy/page.tsx`: `approveItem`, `downloadCSV`,
   `logPOGeneration`). El backend (`POLineItem.cantidad_final` con `ge=0`)
   también acepta 0.

3. **La cantidad no es editable en todos lados.** En `/hoy` ya existe un input
   editable; en `/inventory` (vistas tabla y proveedor) la columna "Pedir" es
   solo lectura.

## Regla de negocio

Solo se recomienda pedir cuando el semáforo lo pide. La cantidad sugerida de
compra es distinta de 0 **únicamente** cuando la señal es `PEDIR_YA` o
`PEDIR_PRONTO`. Ninguna orden de compra puede contener una línea de 0 (o menos)
unidades.

## Cambios

### 1. Cantidad recomendada atada al semáforo (backend)

Archivo: `backend/inventory/service.py`, función `get_inventory_status`.

Tras calcular `signal` y `recomendado`, si `signal ∉ {PEDIR_YA, PEDIR_PRONTO}`:
- `recomendado = 0.0`
- `calc_explanation` pasa a un objeto que comunica "stock suficiente, no se
  recomienda pedir" en lugar de mostrar la resta (para que el tooltip del
  frontend no muestre un cálculo que contradice el 0).

Se corrige en el backend porque `get_inventory_status` es la única fuente de
verdad: `/inventory`, `/hoy`, el PDF (`generate_inventory_pdf`), las alertas
diarias y el export consumen todos este mismo valor.

**Criterio de aceptación:** para todo item con `signal` en `{OK, SOBRESTOCK,
SIN_DATOS}`, `cantidad_recomendada == 0`. Para `PEDIR_YA`/`PEDIR_PRONTO` el
cálculo actual se conserva.

### 2. Nunca aprobar/exportar 0 unidades

**Frontend — `Frontend/src/app/hoy/page.tsx`:**
- Una línea del carrito con `qty ≤ 0` no es aprobable: en vez del botón
  "Aprobar" se muestra un estado no accionable ("Stock suficiente por ahora").
- `approved`, la descarga CSV (`downloadCSV`) y `logPOGeneration` **excluyen toda
  línea con `qty ≤ 0`**.

**Backend (defensa en profundidad) —
`backend/inventory/roi_service.py` `log_po_generation`:**
- Las líneas `approved`/`modified` con `cantidad_final ≤ 0` se ignoran (no se
  registran como pedidas) para que ninguna vía —incluida la API directa— cree
  una compra de 0.

**Criterio de aceptación:** ningún PO log ni CSV exportado contiene una línea con
cantidad ≤ 0, incluso si el cliente envía una.

### 3. Edición manual de la cantidad en `/inventory`

Archivo: `Frontend/src/app/inventory/page.tsx`.

- La columna "Pedir" en las vistas **tabla** y **proveedor** pasa a ser un input
  editable (mismo patrón que `ActionCard` en `/hoy`: botón que abre input
  numérico, valida > 0, revierte al valor original si es inválido).
- Estado local por SKU con la cantidad editada; el valor recomendado original se
  conserva como referencia inmutable.
- El export de PO desde `/inventory` usa las cantidades editadas: envía el
  carrito real vía `logPOGeneration` (líneas con qty > 0), en lugar de que el
  servidor re-derive las cantidades.
- `/hoy` ya tiene la edición; solo se ajusta para respetar la regla de qty > 0
  de la Sección 2.

**Criterio de aceptación:** el usuario puede cambiar la cantidad en `/inventory`
antes de exportar, y el CSV/PO resultante refleja la cantidad editada.

## Pruebas (siguiendo TESTING_GUIDELINES.md)

- **Backend, punto 1:** sesión con un SKU forzado a `OK`/`SOBRESTOCK` de alta
  variabilidad → `get_inventory_status` devuelve `cantidad_recomendada == 0`;
  un SKU `PEDIR_YA` mantiene cantidad > 0. Asserts sobre el valor calculado, no
  solo sobre 200.
- **Backend, punto 2:** `log_po_generation` con una línea `approved` de
  `cantidad_final = 0` → consulta directa a `inventory_po_items` confirma que la
  línea no quedó registrada como pedida; par de permisos (viewer 403 / analyst
  ok) en el endpoint `POST /log-po`.
- **Frontend:** verificación manual con el smoke test (`/quick-start` →
  "Probar con datos de ejemplo" → `/inventory` y `/hoy`) de que (a) los `OK`
  muestran "Pedir —", (b) no se puede aprobar una línea de 0, (c) la cantidad es
  editable en `/inventory` y el export la respeta.

## Riesgos

- Cambiar `cantidad_recomendada` a 0 para `OK` puede afectar tests existentes que
  asuman un valor > 0 en esos casos; se revisan y ajustan
  (`backend/tests/test_inventory.py` y afines).
- El export de `/inventory` pasa de re-derivar en el servidor a enviar el
  carrito del cliente; hay que conservar el fallback server-side existente para
  el CSV legacy que no manda líneas.
