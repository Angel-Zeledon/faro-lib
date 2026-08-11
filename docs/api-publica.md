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
- **La llave actúa como sí misma**, no como la persona que la creó. En el
  historial de actividad las acciones aparecen a nombre de la integración, y
  siguen funcionando cuando esa persona se va de la empresa.

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

Por producto: en qué estado está (`PEDIR_YA`, `PEDIR_PRONTO`, `OK`,
`SOBRESTOCK`), cuántos días de cobertura le quedan y cuánto conviene pedir.

Los productos sin stock registrado aparecen marcados como **sin datos**, no como
"sin riesgo". Es una distinción deliberada: no saber y estar bien no son lo
mismo, y una integración que los confunda va a comprar tarde.

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
- **No hay sandbox.** Se prueba contra el tenant real.
- **No hay versionado real todavía.** El prefijo `/api/v1` existe, pero la
  promesa de estabilidad la da esta lista, no el número. Si alguna vez hace falta
  romper algo, habrá `/v2` y aviso previo.
