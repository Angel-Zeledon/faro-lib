# Implementa PENDIENTES.md + endurecimiento por QA adversarial

Crear el PR en: https://github.com/Angel-Zeledon/faro-lib/pull/new/feat/pendientes-implementation

Rama `feat/pendientes-implementation` → `main` · 3 commits · suite **1509 passed, 19 skipped**

---

## Qué incluye

Los 11 puntos de `PENDIENTES.md`, más los defectos encontrados al someter la app a datos rotos desde la interfaz real.

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

---

## Verificación

Recorrido en navegador como usuario real: registro, Quick Start completo, panel de compras, orden manual, envío por WhatsApp, predicciones, historial, configuración, escenarios, rutas de traslado, y los 18 datasets rotos.

**Pendiente de verificar en vivo** (cubierto solo por tests): costo fijo del MILP y tope del relleno de huecos.

## Notas para el revisor

- `backend/.env` tiene credenciales Twilio reales; el conftest ahora parchea `whatsapp._send` a nivel de sesión para que los tests no envíen mensajes.
- Un commit único por fase: los cambios están entrelazados a nivel de archivo (`inventory.py` toca órdenes manuales, proveedor, rutas y familia), así que separarlos habría producido commits que no compilan por sí solos.
- El badge de sesión activa del TopBar es **código muerto** (`setActiveSessionId` no se llama en ningún lado). Se dejó como está: dónde mostrar la sesión activa es una decisión de diseño.
