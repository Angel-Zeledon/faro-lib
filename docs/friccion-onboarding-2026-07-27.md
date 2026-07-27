# Puntos de fricción del onboarding — planes de mitigación

**Fecha:** 2026-07-27
**Alcance:** los 6 puntos de fricción detectados sobre el flujo real (auth → quick-start → data → hoy → inventory).
**Criterio:** ningún plan renombra rutas ni identificadores persistidos (ver CLAUDE.md, "fuera de alcance"). Todo cambio de copy pasa por i18n.

---

## Orden de ejecución recomendado

| # | Fricción | Esfuerzo | Impacto | Orden |
|---|----------|----------|---------|-------|
| 1 | El muro del stock / lead time / costo | Alto (por fases) | **Crítico** | 1º (fase 1) |
| 2 | Defaults invisibles | Medio | **Crítico** | 2º |
| 3 | Verificación de email bloquea el primer login | Bajo | Alto | 3º |
| 6 | La app envejece sola | Medio | Alto | 4º |
| 4 | No existe en celular | Alto | Medio | 5º |
| 5 | Vocabulario ML filtrado | Bajo | Medio | 6º |

**Por qué este orden:** la fase 1 de #1 y el paso 2 de #2 comparten la misma migración de procedencia de datos — hacerlos juntos evita migrar dos veces. #3 es barato y desbloquea al 100% de los afectados (que hoy no tienen salida). #4 y #5 son mejoras sobre un producto que ya funciona; los primeros cuatro son sobre un producto que no llega a funcionar.

---

## 1. El muro del stock, lead time y costo

### Diagnóstico

El hallazgo central: **el producto ya le pidió estos datos al usuario y los tira a la basura.**

`CANONICAL_FIELDS` (`ForecastingCore/forecasting_core/data/canonical.py:26-41`) incluye:

```python
{"name": "inventory",  "label": "Inventario",       "required": False, "dtype": "float"},
{"name": "lead_time",  "label": "Lead Time (días)", "required": False, "dtype": "int"},
{"name": "price",      "label": "Precio",           "required": False, "dtype": "float"},
{"name": "cost",       "label": "Costo",            "required": False, "dtype": "float"},
```

El wizard los ofrece en el paso de mapeo (`Frontend/src/app/quick-start/page.tsx:360-363`). El runner los carga en el dataframe y explícitamente los preserva (`backend/workers/runner.py:245-246`: *"Non-target columns (price, cost, stock) would be nonsense summed, so they keep their first value"*).

Y después **nunca llegan a `inventory_stock`**. Verificado: `grep unit_cost|sale_price` sobre `runner.py` → cero coincidencias.

Resultado: el usuario mapea su columna de inventario en el minuto 3, entrena, llega a `/hoy`, y la app le dice que no tiene datos de inventario (`Frontend/src/app/hoy/page.tsx:1022`) y que teclee `sku, current_stock, lead_time_days` por SKU (`inventory/page.tsx:1535, 1657`).

### Objetivo medible

% de tenants que llegan a `/hoy` con semáforo poblado **en la primera sesión, sin pasar por `/inventory`**. Hoy es ~0% salvo que el usuario suba un segundo archivo.

### Fase 1 — Cosecha (el 80% del valor, el 20% del trabajo)

Al terminar el entrenamiento, sembrar `inventory_stock` desde las columnas canónicas que el usuario **efectivamente mapeó**.

1. **Extractor en la frontera pandas.** Nuevo helper en `backend/dataframes/` (es la única capa que puede tocar pandas): `extract_sku_attributes(df, mapped_fields) -> list[dict]`.
   - Devuelve Python puro (dicts), nunca un DataFrame — respeta el boundary que enforcea `test_no_pandas_in_backend.py`.
   - Semántica: **último valor por SKU ordenado por fecha**, no promedio. El stock del archivo es un corte temporal; promediarlo no significa nada.
   - Mapeo: `inventory → current_stock`, `lead_time → lead_time_days`, `cost → unit_cost`, `price → sale_price`.

2. **Solo cosechar lo realmente mapeado.** Este es el detalle que puede arruinar la fase entera: `apply_canonical_defaults` (`canonical.py:59`) rellena `inventory` con `0` y `lead_time` con `7` cuando el usuario NO mapeó esas columnas. Sembrar esos valores sería **peor que no sembrar** — pondría stock 0 en todo el catálogo y dispararía PEDIR_YA falsos en masa.
   - Leer `columns_cfg` de `session_configs` y cosechar únicamente los campos con un `source_column` no nulo.

3. **Punto de escritura.** Nuevo `backend/inventory/seed_service.py`, invocado al completar el job. No meter esto dentro de `runner.py`: el runner ya es el módulo más cargado y esto es lógica de negocio de inventario, no de entrenamiento.

4. **No pisar al usuario.** `bulk_upsert` (`backend/inventory/service.py:311`) necesita un modo `only_fill_missing=True`: si el usuario ya editó un SKU a mano, el archivo no lo sobrescribe. Un re-entrenamiento mensual no puede borrar la configuración manual acumulada.

**Tests obligatorios (mandato de testing):**
- Sesión con `inventory` mapeado → consulta directa a `inventory_stock` confirma stock sembrado por SKU con el valor de la **última fecha**.
- Sesión SIN `inventory` mapeado → `inventory_stock` queda vacío (NO sembrado con ceros). Este es el test que evita el desastre.
- SKU con `current_stock` editado a mano → re-entrenamiento no lo modifica.
- Par de permisos sobre cualquier endpoint nuevo (viewer 403 + estado sin cambios / analyst 200).

### Fase 2 — Configurar por proveedor y categoría, no por SKU

Un distribuidor no tiene 2.000 lead times. Tiene 12 proveedores.

1. Nueva tabla `stock_defaults`: `(tenant_id, scope_type, scope_value, lead_time_days, service_level, moq, holding_cost_pct)` con `scope_type ∈ ('supplier','category','global')`.
2. Resolución en cascada: **SKU > proveedor > categoría > global > default del sistema**, devolviendo el nivel que ganó (alimenta directamente el plan #2).
3. UI en `/inventory/suppliers`: "lead time de este proveedor: 12 días" → aplica a sus 340 SKUs de un tirón, con preview del conteo afectado antes de confirmar.

Reusa el patrón de precedencia que ya existe para los multiplicadores de eventos (commit `4472272`), incluida la lección de dejar la precedencia explícita en el copy.

### Fase 3 — Pareto: pedir solo lo que mueve la aguja

El problema no es que falten 2.000 filas; es que pedimos las 2.000 con la misma urgencia.

1. `GET /inventory/setup-gaps` → SKUs sin configurar, **ordenados por impacto** (demanda proyectada × precio), con el % de gasto acumulado.
2. UI: *"Con 40 de tus 2.000 SKUs cubres el 82% de tu compra mensual. Empieza por estos."* Barra de progreso ponderada **por dinero, no por conteo de filas**.
3. Cambia la meta de "2.000 filas" (inalcanzable, se abandona) a "82% del gasto cubierto" (alcanzable en una sentada).

### Fase 4 — Importador de stock a la altura del de ventas

Hoy `bulk_import` (`backend/api/v1/inventory.py:176-186`) exige headers exactos. El importador de ventas es incomparablemente mejor: detecta separador `,`/`;`, decimal con coma, fechas `dd/mm/yyyy`, y aliases en español (`Frontend/src/lib/csvCheck.ts`).

- Reusar `validateSalesCsv` + el wizard de mapeo canónico para el archivo de stock.
- Aceptar `.xlsx` (hoy solo CSV en este endpoint; ventas ya acepta Excel).
- El export de stock de cualquier ERP LatAm no trae los headers que pedimos. Asumir lo contrario es la razón por la que este importador no se usa.

### Fase 5 — Destrabar el aprendizaje de lead time

`resolve_lead_time` (`backend/inventory/service.py:590-609`) ya aprende de las entregas reales del proveedor. Pero solo se activa si el SKU tiene `supplier` **y** ya hubo recepciones. Un tenant nuevo nunca cumple ninguna de las dos → siempre cae en `"configured"`, que es el default que nadie configuró.

- Sembrar `supplier` desde el archivo cuando venga (fase 1 lo habilita si añadimos `supplier` a los canónicos, o vía el importador de stock de la fase 4).
- Mostrar el estado del aprendizaje en la UI: *"Todavía no tengo entregas tuyas de este proveedor. Cuando recibas 2 órdenes, ajusto el lead time solo."* Convierte un default silencioso en una promesa visible.

---

## 2. Defaults invisibles que deciden plata

### Diagnóstico

Hay **tres defaults de lead time distintos en el mismo producto**:

| Lugar | Valor |
|---|---|
| `canonical.py:16` (`OPTIONAL_DEFAULTS`) | 7 |
| `runner.py:156` (business config) | 7 |
| `migrations.py:364` (`inventory_stock` DB) | **15** |
| `quick-start/page.tsx:789` (business config del wizard) | **15** |
| `inventory/page.tsx:923, 1222, 1673` (`?? 15`) | **15** |

Y el bug de raíz: `lead_time_days INT NOT NULL DEFAULT 15`. Con esa columna es **físicamente imposible** distinguir "el usuario configuró 15 días" de "nadie tocó esto nunca".

Peor: `build_explanation` (`backend/inventory/service.py:642-646`) le dice al usuario, textualmente, *"tu proveedor tarda 15 días en entregar (lead time configurado)"* — cuando nadie lo configuró. **Estamos afirmando algo falso en la explicación que usamos para ganarnos su confianza.**

### Plan

1. **Unificar el default en una sola constante** del backend, importada por todos los consumidores. Decidir 7 vs 15 es decisión de negocio; para distribuidor LatAm con importación, 15 es más realista. Lo inaceptable es que convivan tres.

2. **Migración de procedencia** (comparte trabajo con la fase 1 de #1):
   - `lead_time_days` → nullable, o columna hermana `lead_time_set_by TEXT NULL` ∈ `('user','file','supplier_rule','learned','default')`.
   - Mismo tratamiento para `service_level`, `unit_cost`, `moq`.
   - Sin esto, todo lo demás de este plan es imposible.

3. **Extender `lead_time_source`.** El campo ya existe y ya viaja a la UI con `'learned'|'configured'` (`service.py:797, 893`), y recepciones usa `'declared'|'observed'` (`reception_service.py:533`). Unificar el vocabulario a los 5 valores reales y propagarlo al resto de campos.

4. **UI honesta.** Badge gris punteado "estimado" junto a cada valor derivado, en `/inventory` y en el detalle de `/hoy`. El usuario debe poder distinguir de un vistazo *dato mío* de *supuesto de Faro*.

5. **Corregir la explicación.** `build_explanation` debe decir *"asumimos 15 días porque no has configurado este proveedor"* cuando la fuente es `default`. Aprovechar el cambio para sacar el español hardcodeado del backend (viola CLAUDE.md: debe ser código de error + params, renderizado por i18n en el front).

6. **Banner de confianza en `/hoy`:** *"Estas recomendaciones usan 3 supuestos nuestros — revísalos"* → link a los gaps ordenados por impacto (fase 3 de #1).

**Tests:**
- SKU nunca tocado → API reporta `lead_time_source = 'default'`.
- SKU editado por el usuario con el mismo valor que el default (15) → reporta `'user'`, no `'default'`. Este es el test que prueba que la migración sirvió de algo.
- SKU con regla de proveedor → `'supplier_rule'`; con recepciones → `'learned'`.

---

## 3. La verificación de email bloquea el primer login

### Diagnóstico

`backend/api/v1/auth.py:227` devuelve 403 `email_not_verified`. Y **no existe endpoint de reenvío**: el único envío está en el signup (`auth.py:175`). Si el correo cae en spam —cosa habitual con dominios `@hotmail`/`@yahoo`, muy comunes en PyME LatAm— el usuario queda **permanentemente fuera, sin ninguna salida de autoservicio**, habiendo visto cero del producto.

`auth.py:190` ya contempla que el envío falle ("Account created but verification email could not be sent"), pero eso deja al usuario en un callejón sin salida.

### Plan

1. **Reenvío (urgente, ~1h).** `POST /auth/resend-verification`, con rate limit y respuesta genérica anti-enumeración (mismo criterio que ya se usa en `forgot-password`, `auth.py:297`). En la pantalla de login, al recibir 403 `email_not_verified`, mostrar el botón "Reenviar correo" en vez de solo el mensaje de error.

2. **Dejar entrar sin verificar, en modo limitado.** El cambio de fondo: sustituir el 403 por un token con claim `email_verified: false` que permite explorar, subir datos y ver el demo, y bloquea solo lo que tiene consecuencias hacia afuera: invitar usuarios, integraciones, envío de notificaciones. Se pide verificar cuando importa, no antes de haber visto valor.

3. **Deliverability.** Verificar dominio en Resend (SPF/DKIM/DMARC), remitente propio, y registrar bounces. Sin esto, los pasos 1 y 2 son parches sobre un canal que no llega.

4. **Fallback honesto.** Si `email_sent == False` en el signup, mostrar el link de verificación en pantalla en vez de mandar al usuario a revisar un correo que sabemos que no salió.

**Tests:** reenvío respeta rate limit; usuario no verificado puede leer pero recibe 403 al invitar usuarios (par de permisos + estado sin cambios en DB).

---

## 4. La app no existe en celular

### Diagnóstico

El único `@media` de todo el frontend está en `globals.css:384` y solo esconde la decoración del login. Cero lógica responsive en componentes. `tailwindcss` está en `package.json:29` pero el código usa estilos inline (`style={{...}}`) — **los breakpoints de Tailwind no aplican a estilos inline**, así que la dependencia no ayuda aquí.

`/hoy` (1.688 líneas), `/inventory` (2.184), `/skus` (2.153) son tablas densas con anchos fijos.

### Plan — no hacer la app responsive, hacer *una* vista móvil

Reescribir 17.000 líneas de estilos inline es inviable y contradice la prioridad de congelar features y estabilizar. La estrategia es quirúrgica:

1. **Hook `useIsNarrow()`** (`contexts/`, `matchMedia('(max-width: 768px)')`). Debe montar en `useEffect` con fallback a desktop: tocar `window` durante el render rompe el SSR de Next.

2. **`/hoy` móvil.** Si narrow → renderizar `<HoyMobile/>`: lista de tarjetas (SKU, semáforo, cantidad sugerida, botón "agregar al carrito") en lugar de la tabla. **Componente nuevo, no toca el desktop** — riesgo de regresión casi nulo.

3. **Sidebar → drawer** con overlay en narrow. Ya existe el estado `collapsed` en `SidebarContext`; se extiende, no se reescribe.

4. **`/pedidos` en móvil**, lectura + marcar recepción. Es el segundo caso de uso real en celular: recibir mercadería en bodega.

5. **Meta viewport + guard de `overflow-x`**, y un aviso honesto en el resto de páginas: "esta vista está pensada para computadora". Mejor decirlo que dejar que el usuario pelee con una tabla rota.

**Orden de valor:** `/hoy` móvil solo ya cubre el ~90% del uso en celular. Si hay que parar después del paso 2, el plan sigue valiendo la pena.

---

## 5. Se filtra el vocabulario ML

### Diagnóstico

`/quick-start`, `/data` y `/sessions` son tres puertas distintas para el mismo concepto mental del usuario: *"subí mis ventas"*. El sidebar tiene 14 ítems en 5 grupos, de los cuales 4 salen con candado en plan bajo (`Sidebar.tsx:33-51`) — el nav le enseña al usuario más de lo que no tiene que de lo que sí.

`DataFreshness.tsx:11` ya resolvió esto bien en su ámbito ("*instead of ML jargon (forecast session)*"). Falta extender ese criterio.

### Plan — solo copy, cero cambios estructurales

1. **Renombrar en i18n únicamente** (las rutas se quedan; CLAUDE.md las declara fuera de alcance porque los usuarios las han compartido):
   - "Sesiones" → "Historial de actualizaciones"
   - "Quick start" → "Actualizar mis ventas"
   - "Data" → "Mis archivos"
2. **Colapsar `/quick-start` + `/data`** en un solo ítem de nav con dos pestañas. Las dos rutas siguen existiendo; solo deja de haber dos entradas para lo mismo.
3. **Ocultar los candados** en lugar de mostrarlos permanentemente. El upsell aparece cuando el usuario intenta algo que lo necesita, no como decoración fija del sidebar.
4. **Barrido de i18n** por "modelo", "entrenamiento", "pronóstico", "sesión", "dataset" en copy de usuario → lenguaje de negocio.

**Restricción dura:** solo se tocan los valores `es`/`en` de `translations.ts`. Ningún identificador, ruta, campo de API ni columna de DB.

---

## 6. La app envejece sola

### Diagnóstico

`DataFreshness.tsx` avisa a los 14 días — pero es **pasivo**: solo se ve si el usuario abre la app, y el momento en que hay que avisarle es precisamente cuando dejó de abrirla.

Existe scheduler cron (`backend/workers/worker.py:129`), pero re-entrenar sobre datos viejos solo produce pronósticos viejos con fecha nueva. Y las integraciones ERP —lo único que evita el envejecimiento— están gated a enterprise (`integraciones/page.tsx:4-5`).

### Plan

1. **Separar frescura de stock de frescura de ventas.** El stock envejece en días, no en semanas. Añadir el chequeo sobre `inventory_stock.updated_at` y mostrarlo en `/hoy`: *"tu stock tiene 23 días"*. Hoy el semáforo muestra verde con total seguridad sobre un stock de hace un mes.

2. **Recordatorio activo.** La infraestructura ya existe: `notifications/email.py`, `whatsapp.py` y el loop diario de las 8:00 UTC en `worker.py`. Añadir el aviso: *"Tus ventas son de hace 34 días — sube el archivo de este mes"*, con un link directo a `/quick-start` con la sesión preseleccionada.

3. **Degradar con honestidad.** Superado el umbral, el semáforo pasa a estado "desactualizado" en vez de seguir mostrando verde. Un semáforo que miente con confianza es peor que uno que se declara ciego.

4. **Re-subida trivial.** "Actualizar" sobre una sesión existente debe reusar el mapeo de columnas anterior: un botón, un archivo, cero configuración. Hoy se rehace el wizard completo cada mes, y ese costo recurrente es lo que produce el abandono del mes 2.

5. **Revisar el gate de integraciones (decisión de pricing, no técnica).** La integración ERP es exactamente el mecanismo que impide que el producto envejezca. Reservarla al plan alto condena a los planes bajos al churn estructural. Vale la pena evaluar una versión limitada (solo lectura de stock, sync diario) en planes medios.

---

## Notas sueltas encontradas en el camino

- `Frontend/src/lib/csvCheck.ts:220, 232, 269...` tiene mensajes de error en español hardcodeado, fuera de i18n. Un usuario en modo inglés recibe errores en español. Viola CLAUDE.md.
- `backend/inventory/service.py:642-668` (`build_explanation`) genera copy de usuario en español desde el backend. Viola la regla de "el backend devuelve inglés o código estructurado; el frontend renderiza el español". Se corrige naturalmente al ejecutar el plan #2.
- `ForecastingCore/forecasting_core/data/canonical.py:27-40` tiene los `label` en español dentro del motor ML. Es metadata que llega a la UI, así que aplica la misma regla.
