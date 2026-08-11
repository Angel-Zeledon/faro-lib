# API pública de Faro

**Para quién es:** el sistema que ya usa el cliente —su ERP, su POS, su script de
exportación— para que los datos entren y las decisiones salgan sin que nadie
abra la aplicación.

**Lo que promete:** los endpoints de esta página no cambian de ruta, método ni
forma de respuesta sin aviso previo. La lista vive en
`backend/api/public_surface.py` y `backend/tests/test_public_api_surface.py`
falla si alguno deja de existir, así que este documento no puede describir una
API que ya no corre.

**Lo que NO promete:** cualquier otro endpoint del servicio. Son alcanzables con
una llave, y cambian cuando cambia una pantalla — varios cambiaron esta misma
semana. Construir sobre ellos es construir sobre algo que nadie se comprometió a
mantener.

## Autenticación

Se crea una llave desde la aplicación (Automatización → llaves de API). Se
muestra **una sola vez**: nada la guarda, ni este servicio ni la base. Quien la
pierde crea otra.

```
Authorization: Bearer sk_live_xxxxxxxxxxxxxxxxxxxxxxxx
```

Tres cosas que conviene saber antes de integrar:

- **Una llave es `viewer` o `analyst`, nunca administrador.** No existe forma de
  que una llave borre el tenant, cree usuarios ni toque facturación. Para
  escribir (subir archivos, entrenar, registrar órdenes) hace falta `analyst`.
- **El plan se verifica en cada llamada**, no solo al crear la llave. Si el
  tenant baja de plan, la llave deja de funcionar ese mismo día. La API está
  incluida desde **Professional**.
- **La llave actúa como sí misma**, no como la persona que la creó: internamente
  el actor es `api_key:<id>`, así que la integración sigue funcionando cuando esa
  persona se va de la empresa y no hereda permisos si la ascienden.

  **Ojo:** eso *no* significa que hoy quede rastro visible. Comprobado el
  2026-08-11 llamando la API de punta a punta: subir el archivo, entrenar y
  registrar la orden **no escriben nada** en el historial de actividad —
  `activity_logs` solo recoge alertas salientes y creación de sesiones. Si
  necesitás auditar lo que hace una integración, hoy no lo tenés.

## Límites

**120 llamadas por minuto por llave.** Al pasarse, la respuesta es `429` con
cabecera `Retry-After: 60`. Está pensado para el trabajo real de una integración
—un empuje nocturno y el sondeo alrededor— no para ser generoso: si hacen falta
más de 120 por minuto, casi siempre hay un bucle.

Si el limitador no puede escribir, **deja pasar**. La sincronización de un
cliente no se cae porque un contador esté caído.

## Los cinco trabajos

### 1. Meter los datos

```http
POST /api/v1/data-sources/{source_id}/file
Content-Type: multipart/form-data
```

Reemplaza el archivo **en su sitio**: la fuente conserva su id y su mapeo de
columnas, así que el siguiente entrenamiento no necesita volver a mapear nada.
La escritura es atómica —se escribe un temporal, se verifica el tamaño y recién
ahí se cambia—, de modo que un disco lleno no deja media exportación.

Acepta `.csv`, `.xlsx`, `.xls`, `.parquet` y `.json`. Lee separadores `;`,
números con coma decimal, fechas `dd/mm/yyyy` y archivos en `cp1252` con acentos.

Para saber qué `source_id` reemplazar:

```http
GET /api/v1/data-sources
```

### 2. Convertirlos en decisiones

```http
POST /api/v1/sessions/{session_id}/train
GET  /api/v1/sessions/{session_id}/train/status
```

El entrenamiento es asíncrono: el `POST` encola y el `GET` informa el estado.

**Casi siempre no hace falta llamarlos.** Si la sesión tiene una programación
—Automatización → programar—, Faro reentrena solo después de que el archivo
cambió. Subir el export por la noche y dejar que la programación haga el resto
es la integración más simple que funciona, y no requiere ninguna de estas dos
llamadas.

### 3. Leer el semáforo

```http
GET /api/v1/inventory/status?session_id={id}
```

Por producto. Los campos que una integración necesita, con sus nombres reales
(verificados contra una respuesta viva, no contra la memoria de nadie):

| Campo | Qué es |
|---|---|
| `signal` | `PEDIR_YA`, `PEDIR_PRONTO`, `OK`, `SOBRESTOCK` o `SIN_DATOS` |
| `recommended_qty` | Cuánto pedir. **No** `order_qty` ni `suggested_qty` |
| `coverage_days` | Días de stock que quedan al ritmo pronosticado |
| `current_stock` | Lo que hay hoy |
| `reorder_point` | El nivel donde conviene pedir |
| `has_stock`, `has_forecast` | Si falta alguno, lo de arriba puede venir vacío |
| `explanation_code` + `explanation_params` | El porqué, **estructurado** |
| `lead_time_source`, `unit_cost_source`, `moq_source` | De dónde salió cada supuesto: `user` (lo cargaste vos), `learned` (lo aprendimos) o `default` (lo inventamos nosotros) |

Los tres campos `*_source` son la parte que más conviene usar y la que más se
ignora: distinguen un número que diste de uno que nos inventamos. Una integración
que trate ambos igual va a confiar en supuestos nuestros como si fueran datos
suyos.

`explanation_code` es un código estable con sus parámetros aparte — ramificá por
ahí, nunca por el texto.

Los productos sin stock registrado aparecen como `SIN_DATOS`, no como "sin
riesgo". Es una distinción deliberada: no saber y estar bien no son lo mismo, y
una integración que los confunda va a comprar tarde.

### 4. Leer qué comprar

```http
GET /api/v1/inventory/morning-briefing?session_id={id}
```

Lo mismo ordenado como el trabajo de un día, con el motivo de cada
recomendación, el proveedor sugerido y el valor estimado.

### 5. Cerrar el ciclo

```http
POST /api/v1/inventory/log-po?session_id={id}
```

Registra que la orden se emitió. **Sin esta llamada la orden no existe para
Faro**: la recepción se controla contra ella, y el aprendizaje del plazo real de
cada proveedor se calcula a partir de ella. Es decir, es lo que hace que el
próximo pronóstico sea mejor que este.

Se envía lo que el comprador decidió por línea, incluidas las rechazadas —esa es
la señal de adopción—:

```json
{
  "items": [
    { "sku": "ABC-1", "recommended_qty": 120, "final_qty": 100,
      "status": "modified", "unit_cost": 12.5, "supplier": "Andina" },
    { "sku": "XYZ-9", "recommended_qty": 40, "final_qty": 0, "status": "rejected" }
  ]
}
```

## Errores

| Código | Qué pasó |
|---|---|
| `401` | Llave desconocida, revocada o vencida. No se distingue cuál, a propósito. |
| `403` | El plan no incluye la API, o la llave es `viewer` y la operación escribe. |
| `429` | Se pasó de 120 por minuto. Reintentar después de `Retry-After`. |
| `409` | La sesión no está en un estado que permita eso (entrenar una que ya corre). |

Los errores traen `error_code` estable además del mensaje. Conviene ramificar
por el código, no por el texto: el texto está pensado para personas y se reescribe.

## Lo que todavía no hay

Honestidad por delante, para que nadie diseñe contra algo que no existe:

- **No hay webhooks en Professional.** Hoy son Enterprise, así que en
  Professional la integración tiene que sondear. Está bajo revisión.
- **No hay auditoría de lo que hace una llave.** Ver la nota en Autenticación.
- **No hay sandbox.** Se prueba contra el tenant real.
- **No hay versionado real todavía.** El prefijo `/api/v1` existe, pero la
  promesa de estabilidad la da esta lista, no el número. Si alguna vez hace falta
  romper algo, habrá `/v2` y aviso previo.

## Cómo se verificó esto

No está escrito de memoria. El 2026-08-11 se recorrieron los cinco trabajos con
una llave real contra el servidor, en este orden: crear la llave → listar
fuentes → reemplazar el archivo por un export nuevo de 360 filas (la fuente
conservó su id) → reentrenar y esperar `COMPLETED` → leer el semáforo → leer el
briefing → registrar la orden, que quedó en la base como OC-000005 con 250
unidades y `modified_count = 1`.

También los caminos infelices: una llave `viewer` lee (200) y no escribe (403 en
subir archivo y en registrar orden), y una llave inventada da 401.

Lo que **no** se ejercitó contra el servidor: el 429 del límite de tasa —está
cubierto por tests, incluida su compuerta de mutación, pero gastar 120 llamadas
por minuto contra el entorno de desarrollo no aportaba nada.
