# Implementa PENDIENTES.md + endurecimiento por QA adversarial

Crear el PR en: https://github.com/Angel-Zeledon/faro-lib/pull/new/feat/pendientes-implementation

Rama `feat/pendientes-implementation` → `main` · 4 commits · backend **1594 passed, 19 skipped** · ForecastingCore **740 passed, 1 skipped** · `tsc --noEmit` limpio

> Los dos tests de `test_stress.py` (`test_login_responds_under_2s`, `test_concurrent_log_appends`) fallan de forma intermitente **bajo carga** y pasan 13/13 cuando el archivo corre solo. Ninguno toca código de esta rama: uno es una aserción de reloj de pared (2,77 s contra un techo de 2 s) y el otro cuenta 22 líneas de log en vez de 20 sobre `session_store.append_log`, que no se modificó aquí. Vale mirarlo aparte.

---

## Qué incluye

Los 11 puntos de `PENDIENTES.md`, más los defectos encontrados al someter la app a datos rotos desde la interfaz real, más una segunda pasada dedicada a los fallos que no se veían porque la app reportaba éxito.

### Las causas raíz no eran las esperadas

| Síntoma reportado | Causa real |
|---|---|
| "Precisión 95-97% pero el pronóstico no tiene sentido" | El KPI promediaba el WAPE de **todos** los modelos, incluidos los baselines naive que existen solo como referencia |
| "Historial plano y de pronto un salto enorme" | No era el modelo: el gráfico sacaba la granularidad del **histórico**, así que una sesión semanal dibujaba totales semanales (~164) junto a histórico diario (~30) en el mismo eje |
| "El cambio de teléfono no funciona" | Fuera de `ENVIRONMENT=production` el código **nunca se enviaba** (se devolvía en la respuesta) y el frontend compilado lo ocultaba |

### Funcionalidad nueva

- **Órdenes de compra**: creación manual sin pronóstico, selector de proveedor por línea persistido como `supplier_id`, envío al WhatsApp del comprador con enlace `wa.me` y copiado, teléfono obligatorio en el registro. El envío ahora reporta las líneas sin proveedor resoluble en vez de descartarlas en silencio.
- **Transferencias**: tabla `transfer_lanes` con lead time, costo por unidad y costo fijo, alimentando la heurística y el MILP; la decisión comprar-vs-trasladar se emite como `{reason_code, params}` y se muestra como **texto**, nunca como alerta.
- **What-if**: cablea el `ScenarioEngine` que estaba escrito sin usarse; escenarios persistidos con cuatro tipos de regla, reutilizando el semáforo existente en vez de reimplementarlo.
- **Eventos**: scope `family` entre categoría y SKU (resolución SKU > familia > categoría > evento).
- **Quick Start**: nombre, horizonte y granularidad se eligen ahí (el control del navbar desaparece); reuso de dataset y clonado de sesión con su mapeo de columnas.
- **Sesiones**: página de historial con renombrar y eliminar.
- **Predicciones**: solo línea/barras con gestos, pantalla completa, comparación multi-modelo, una sola banda de confianza; fuera el bloque "Predictability / puedes decidir con confianza".
- **Planes**: Starter a 1.000 SKUs, límites de tamaño de dataset y trabajos concurrentes efectivamente aplicados, copy de la landing alineado al catálogo, y `/settings` gateado tras `api_access` con aviso de "Próximamente" porque las API keys todavía no autentican nada.

### Defectos encontrados con datos rotos (18 CSV por la UI)

1. **`KeyError('model')` visible al usuario** como *"El entrenamiento falló: 'model'"*. Cuando fallan todos los modelos la tabla de métricas queda vacía y se agrupaba igual. Ahora es un desenlace válido con mensaje accionable.
2. **Exportaciones de Excel en español (`;`) se leían como una sola columna.** Ningún `read_csv` pasaba separador. Añadida detección de separador y `utf-8-sig`.
3. **Un catálogo que nunca vendió reportaba "100% de precisión"** (WAPE divide por la demanda total). Ya no se muestra número cuando no hay escala contra la cual medir.
4. **Todo error 422 mostraba el inglés de Pydantic.** `ApiError` ahora conserva los errores por campo y la UI reconstruye la frase desde `errors.validation.<tipo>` + `errors.field.<nombre>`.
5. **Exportaciones por transacción hacían comprar un tercio de lo necesario.** Un ERP exporta una línea por venta: 3 transacciones de 4+3+3 en un día se entrenaban como 3 días de ~3 unidades. Verificado: el pronóstico pasó de 3-4 a **10,0 exacto**. El perfilador ya lo detectaba; nadie actuaba.
6. **Cantidades infinitas** (`1e309`) pasaban los tres filtros. Cubierto en cliente, validador y runner.
7. **El relleno de huecos no tenía tope**: una fecha de 1900 junto a una de 2099 son ~73.000 filas por SKU.

### Fallos silenciosos (segunda pasada de QA)

Casos donde la app reportaba éxito mientras no pasaba nada. Ninguno lanzaba un error visible: por eso ninguno había aparecido antes.

1. **Las validaciones no llegaban al usuario.** Corren en modo WARNING y nunca abortan un entrenamiento, pero sus hallazgos solo iban al log del servidor. `TARGET_FEATURE_LEAKAGE` es el caso que lo vuelve grave: produce una precisión casi perfecta sobre un pronóstico inservible, y no existía ningún canal para decir que ese 97% era falso. Ahora viajan con los resultados y se muestran en `/skus` (`RunWarningsPanel`).
2. **Un reporte que fallaba no dejaba rastro.** La generación corre como background task; si fallaba —incluida la salida temprana cuando la sesión no tiene resultado de entrenamiento— no se escribía nada, y la descarga posterior respondía `404 "No pdf report found. Generate one first"`: le pedía al usuario repetir justo lo que acababa de fallar. Cada intento escribe ahora una fila en `report_runs` (running → completed/failed) y la descarga informa qué pasó de verdad, como `error_code` + params en inglés.
3. **Los loops diarios del worker se caían a fin de mes.** El límite del siguiente disparo se calculaba con `next_run.replace(day=next_run.day + 1)`, que lanza *"day is out of range for month"* el día 31, el 28/29 de febrero y el último día de cada mes de 30. El `except Exception: time.sleep(3600)` de abajo se tragaba la excepción sin loguearla, así que esos días no salía la alerta de quiebre ni la de lead time de proveedor, no corría el sync de integraciones de las 6:00 UTC, y nada decía por qué.
4. **Envíos que no salían se reportaban como enviados.** Sin transporte de email configurado, `_transport_send` retornaba en silencio y todos los `try: _send() ... return True` daban por mandada una invitación, un enlace de verificación o una orden de compra que nadie recibió. Ahora el nivel de transporte **lanza** en todo desenlace que no sea "entregado a un proveedor vivo" (`EmailNotConfigured`), y los `send_*` públicos devuelven `bool` sin lanzar, para que un servidor de correo caído no rompa un request ni un loop de alertas. Se distingue `not_configured` (falta configurar credenciales) de `transport_error` (el proveedor nos rechazó), que los arregla gente distinta.
5. **El digest diario de quiebres mentía en el número.** Contaba las filas que había listado (10 en email, 5 en WhatsApp) en vez de los SKUs realmente en riesgo (47), y nunca decía cuántos quedaban ocultos.
6. **Una granularidad mezclada en el archivo no llegaba al usuario.** El perfilador siempre la detectó y la API siempre la llevó en `inspection.granularity`, pero nadie la leía. Importa porque cada SKU entrena sobre **un** eje temporal: un producto reportado mensualmente se modela como serie diaria, así que su cantidad a pedir sale unas 30 veces por debajo de lo que necesita. Se muestra ahora en Quick Start (`DataIssuesPanel`).
7. **Un reentrenamiento programado que fallaba se veía igual que uno sano.** El error solo iba al log, así que un schedule roto hacía semanas era indistinguible de uno correcto. `scheduled_jobs` gana `last_error` / `last_error_at` (mismo contrato que `integration_connections.last_error`) y `GET /schedule` los expone. Además un disparo fallido dejaba `next_run` en el pasado, así que el loop lo reintentaba en cada vuelta; ahora avanza igual que en el camino feliz.
8. **El costo fijo del MILP se apagaba solo en catálogos grandes.** El big-M que liga los envíos a su binario estaba sumado sobre **todo** el catálogo, así que lo dominaba el SKU más grande del tenant y la fila de linking quedaba satisfecha con un `ship` de (unidades movidas / M). Cuando ese cociente cae por debajo de la tolerancia de integralidad del solver (~1e-6), HiGHS acepta el binario como "0" y el envío viaja gratis. Verificado contra el solver real: con un SKU de 5e7 unidades en el mismo problema, un envío de 2 unidades en una ruta que cobra 1000 salía costando **0,00004**. El big-M pasa a ser por SKU, que además aprieta la relajación lineal.

---

## Verificación

Recorrido en navegador como usuario real: registro, Quick Start completo, panel de compras, orden manual, envío por WhatsApp, predicciones, historial, configuración, escenarios, rutas de traslado, y los 18 datasets rotos.

**Pendiente de verificar en vivo** (cubierto solo por tests): costo fijo del MILP y tope del relleno de huecos.

## Notas para el revisor

- `backend/.env` tiene credenciales Twilio reales; el conftest ahora parchea `whatsapp._send` a nivel de sesión para que los tests no envíen mensajes.
- Un commit único por fase: los cambios están entrelazados a nivel de archivo (`inventory.py` toca órdenes manuales, proveedor, rutas y familia), así que separarlos habría producido commits que no compilan por sí solos.
- El badge de sesión activa del TopBar era **código muerto**: leía un `ActiveSessionContext` cuyo setter no se llamaba en ningún lado, así que `activeSessionId` quedaba en `null` para siempre y el badge nunca se dibujaba. Ese contexto se **borró** en vez de cablearse: `PlanningContext` ya trae `active_session_id` del resolutor del servidor (`planning_service.resolve_active_session`), que es la sesión de la que cada pantalla saca sus números. Un segundo almacén de "sesión activa" en el cliente habría que empujarlo desde cada página y podría desincronizarse del resolutor; leer la respuesta del resolutor no puede. El badge se oculta en `/skus`, que tiene su propio selector.
