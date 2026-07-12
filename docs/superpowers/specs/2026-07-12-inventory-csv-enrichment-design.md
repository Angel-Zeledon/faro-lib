# Diseño — Enriquecimiento de la plantilla/import CSV de inventario

**Fecha:** 2026-07-12
**Alcance:** puntos 4 y 5 de la solicitud (van juntos).
**Decisiones del usuario:** campos nuevos = `precio_venta`, `categoria`, `marca`,
`unidad_medida`, `codigo_barras`; alcance = **guardar + plantilla + editar** (sin
lógica nueva de margen/filtros); plantilla = **endpoint backend**.

## Problema

1. El import CSV (`POST /inventory/bulk`) y el modelo de stock solo reconocen un
   conjunto fijo de columnas. Falta `precio` (venta) y otros campos de catálogo.
2. **No hay plantilla descargable**: el usuario adivina las columnas.
3. **Bug pre-existente relevante:** `bulk_import` valida cada fila a través de
   `StockPatch`, que NO tiene los campos `proveedor` ni `notas`, así que esas
   columnas se descartan silenciosamente al importar por CSV. La tanda 5 pide
   "columna proveedor bien documentada" — para que la plantilla sirva, el
   import debe realmente persistir `proveedor`.

## Regla / objetivo

El import CSV y el formulario de edición aceptan y persisten los campos nuevos.
Existe una plantilla CSV canónica descargable con todos los encabezados y una
fila de ejemplo. `proveedor` y `notas` se importan de verdad.

## Campos nuevos (tabla `inventory_stock`)

| Columna         | Tipo   | Notas                                            |
|-----------------|--------|--------------------------------------------------|
| `precio_venta`  | FLOAT  | Precio de venta. Validación `>= 0`.              |
| `categoria`     | TEXT   | Categoría del producto.                          |
| `marca`         | TEXT   | Marca.                                           |
| `unidad_medida` | TEXT   | Unidad (caja, unidad, kg…).                      |
| `codigo_barras` | TEXT   | Código de barras / código del producto en el proveedor. |

Se añaden con el mismo patrón aditivo `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`
que ya usan `product_type` y `service_level` en `backend/db/migrations.py`.

## Cambios

### 1. Migración (backend/db/migrations.py)
Cinco entradas nuevas en la lista de migraciones (una por columna), todas
`ADD COLUMN IF NOT EXISTS`. `migrations.run_all()` las aplica al arrancar.

### 2. Persistencia (backend/inventory/service.py)
- `upsert_stock`: añadir las 5 columnas al set `allowed`.
- `_DATASET_STOCK_*`: `precio_venta` al set de floats; `categoria`, `marca`,
  `unidad_medida`, `codigo_barras` al set de strings — así también se siembran
  desde un dataset de entrenamiento que los traiga.
- `get_inventory_status`: incluir los 5 campos en cada item devuelto (para que
  aparezcan en UI y exports). Valores `None` cuando no hay dato.

### 3. Validación / import (backend/api/v1/inventory.py)
- `StockUpsert` y `StockPatch`: añadir los 5 campos opcionales
  (`precio_venta: Optional[float] = Field(default=None, ge=0)`; los cuatro de
  texto `Optional[str] = None`). **Además** añadir `proveedor: Optional[str]` y
  `notas: Optional[str]` a `StockPatch` para cerrar el bug de descarte en bulk.
- `bulk_import`: parsear las columnas nuevas (float para `precio_venta`, str
  para las de texto) además de las actuales, y documentar el set completo en el
  docstring.

### 4. Plantilla descargable (backend/api/v1/inventory.py + Frontend)
- Nuevo `GET /inventory/template.csv` (solo lectura, `get_current_user`) que
  devuelve un CSV con la fila de encabezados canónica y UNA fila de ejemplo
  realista. Orden de columnas:
  `sku, display_name, categoria, marca, unidad_medida, codigo_barras,
  stock_actual, stock_minimo, lead_time_dias, costo_unitario, precio_venta,
  moq, proveedor, notas`.
  `Content-Disposition: attachment; filename="plantilla_inventario.csv"`.
- Frontend `/inventory`: botón "Descargar plantilla" junto al botón CSV
  existente (`page.tsx:833`), que descarga desde ese endpoint. Cliente API en
  `Frontend/src/lib/api.ts`.

### 5. Formulario de edición (Frontend/src/app/inventory/page.tsx)
- El modal de edición (`editState`, ~línea 1268-1310) gana inputs para los 5
  campos nuevos. `precio_venta` numérico `>= 0`; los otros de texto.
- El tipo del item de inventario en `Frontend/src/lib/types.ts` gana los 5
  campos opcionales.

## Pruebas (TESTING_GUIDELINES.md)

- **Import (bulk):** subir un CSV con las columnas nuevas + `proveedor` →
  consulta directa a `inventory_stock` confirma que `precio_venta`, `categoria`,
  `marca`, `unidad_medida`, `codigo_barras` y `proveedor` quedaron guardados
  (no solo HTTP 200). Par de permisos: viewer 403 en `POST /bulk` + sin fila
  creada; analyst 201 + fila creada.
- **Regresión del bug:** un test que confirme que `proveedor`/`notas` ahora SÍ
  se importan por CSV (antes se descartaban).
- **Plantilla:** `GET /inventory/template.csv` → 200, `Content-Type` CSV, la
  fila de encabezados contiene exactamente las columnas esperadas (incluida
  `precio_venta`), y hay una fila de ejemplo parseable.
- **upsert directo:** `PATCH /inventory/stock/{sku}` con `precio_venta` y
  `categoria` → `GET` del SKU refleja los valores; par de permisos.

## Riesgos / notas

- Añadir `proveedor`/`notas` a `StockPatch` cambia el comportamiento del bulk
  import (ahora persiste esas columnas). Es el fix deseado, pero hay que
  verificar que `upsert_stock` ya las acepta (sí: están en `allowed`).
- No se introduce lógica de margen ni filtros por categoría/marca (fuera de
  alcance por decisión del usuario); los campos solo se guardan, editan y
  exportan. Un uso posterior (margen desde `precio_venta`) queda para otra tanda.
- Sin cambios de datos destructivos: solo columnas nuevas nullable.
