# API pública de Faro

## En resumen

Faro no quiere ser el sistema donde vive tu inventario. Quiere ser la capa que
decide **qué comprar** encima del sistema que ya tienes. Esta API es esa costura:
tu ERP empuja lo que ya sabe y se lleva la decisión, sin que nadie abra la
aplicación.

Seis llamadas, en el orden en que ocurre el trabajo (la lista completa son
ocho — las otras dos están más abajo):

```
0. GET  /planning                      qué sesión estoy mirando  ← empieza aquí
1. POST /data-sources/{id}/file        el export de anoche
2. POST /sessions/{id}/train           reentrenar    (opcional: ver abajo)
3. GET  /inventory/status              el semáforo
4. GET  /inventory/morning-briefing    qué comprar y por qué
5. POST /inventory/log-po              la orden que se emitió
```

La 0 es la que conviene hacer primero. `log-po` y las dos de entrenamiento
**exigen** un `session_id`, y este es el único endpoint público que lo entrega.
En `status` y `morning-briefing` es opcional: si lo omites, usan la sesión activa
del tenant — o sea, lo mismo que devuelve `/planning`. Pedirlo explícito igual
tiene una ventaja: sabes contra qué corrida estás leyendo, en vez de que cambie
bajo tus pies a mitad de un ciclo.

## El envoltorio

**Toda** respuesta viene envuelta. Lo que te interesa está siempre en `data`:

```json
{
  "success": true,
  "data": { "…lo tuyo…" },
  "meta": { "timestamp": "2026-08-11T16:45:10.783947+00:00" }
}
```

Los errores traen `detail`, y **algunos** traen además `error_code` y
`error_params` en el nivel superior, sin `data`. Cuando venga `error_code`,
ramifica por él y nunca por el texto de `detail`: ese está escrito para personas
y se reescribe.

Conviene saber cuáles **no** lo traen, porque son justo los tres con los que te
vas a topar al integrar:

| Caso | Qué llega de verdad |
|---|---|
| `401` clave inválida o vencida | Solo `detail` en texto. Sin `error_code` |
| `429` pasaste el límite | Solo `detail` en texto. Sin `error_code`; usa la cabecera `Retry-After` |
| `403` clave de solo lectura escribiendo | `error_code = "role_not_permitted"` |
| `409` sesión no entrenable | `error_code = "session_not_trainable"` |

Para los dos primeros ramifica por el **status HTTP**, que sí es estable.

La quinta no es opcional aunque lo parezca: sin ella la orden no existe para
Faro, y es de ahí que sale el aprendizaje del plazo real de cada proveedor. Es
decir, es la llamada que hace que el próximo pronóstico sea mejor que este.

La segunda **sí** suele ser innecesaria. Si la sesión tiene una programación,
Faro reentrena solo después de que el archivo cambió: subir el export por la
noche y no hacer nada más es la integración más simple que funciona.

**Base URL:** `https://<tu-instancia>/api/v1`
**Autenticación:** `Authorization: Bearer sk_live_…`
**Límite:** 120 llamadas por minuto por llave, igual para todos. Al pasarse:
`429` con `Retry-After`.
**Incluida:** siempre. No hay plan que no la traiga.

## Dónde sacar tu clave

En la aplicación: **Automatización → pestaña "API Keys" → "Generar key"**.

Al crearla se eligen dos cosas:

- **Nombre.** Pon el del sistema que la va a usar ("ERP nocturno"), no el de la
  persona. La clave no pertenece a quien la crea: sigue funcionando cuando esa
  persona se va, y no gana permisos si la ascienden.
- **Qué puede hacer.** *Solo leer* alcanza para el semáforo y el briefing.
  Para subir el export o registrar órdenes hace falta **Leer y escribir** — que
  es lo que una integración de verdad necesita. Por defecto viene en solo
  lectura, así que hay que cambiarlo a propósito.

**La clave se muestra una sola vez.** De la clave en claro no queda copia en
ninguna parte: guardamos solo un hash y los últimos 4 caracteres, que son los que
te dejan reconocerla en la lista. Ni nosotros podemos volver a mostrártela, así
que se copia en ese momento. Si se pierde: se crea
otra y se revoca la anterior desde esa misma pantalla, donde además se ve cuándo
se usó cada una por última vez.

Ninguna clave puede ser administrador. No existe forma de que una integración
borre el tenant, cree usuarios o toque la facturación.

---

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

```
Authorization: Bearer sk_live_xxxxxxxxxxxxxxxxxxxxxxxx
```

Tres cosas que conviene saber antes de integrar:

- **Una llave es `viewer` o `analyst`, nunca administrador.** No existe forma de
  que una llave borre el tenant, cree usuarios ni toque facturación. Para
  escribir (subir archivos, entrenar, registrar órdenes) hace falta `analyst`.
- **La llave actúa como sí misma**, no como la persona que la creó: internamente
  el actor es `api_key:<id>`, así que la integración sigue funcionando cuando esa
  persona se va de la empresa y no hereda permisos si la ascienden.

  **Y deja rastro.** Toda escritura de una llave queda registrada a su nombre —
  no al de la persona que la creó — con la ruta, el resultado y la hora. Un
  intento que llegó a ejecutarse y falló también queda, marcado como error.

  Con una excepción que conviene conocer: **lo rechazado en la puerta no deja
  fila.** Un `401` (clave mala), un `403` (clave de solo lectura intentando
  escribir) y un `429` no se registran, porque se cortan antes de llegar al
  endpoint. Es decir: si tu integración escribe con una clave de solo
  lectura, no vas a ver nada en el registro — vas a ver el `403` en tu lado.
  Empieza por ahí antes de sospechar del registro.

  Las lecturas tampoco se registran, porque a 120 llamadas por minuto
  enterrarían lo que importa.

## Límites

El techo es **por llave**, y es uno solo: **120 llamadas por minuto**.

Al pasarse: `429` con `Retry-After: 60`, y el mensaje nombra el techo. Los 120
están pensados para el trabajo real de una integración —un empuje nocturno y el
sondeo alrededor—, no para ser generosos: si hacen falta más, casi siempre hay
un bucle. Si tu operación necesita otro techo, se habla con nosotros: es un
número de infraestructura, no una función que se venda.

Si el limitador no puede escribir, **deja pasar**. La sincronización de un
cliente no se cae porque un contador esté caído.

## Los trabajos, en orden

### 0. Saber de qué sesión estamos hablando

```http
GET /api/v1/planning
```

```json
{
  "period": "daily",
  "horizon": 14,
  "available_periods": ["daily", "weekly"],
  "max_horizon": 90,
  "period_source": "auto",
  "active_session_id": "sess_718a890426d4"
}
```

`active_session_id` es el que va en las llamadas siguientes. **No lo guardes
fijo en tu configuración**: cambia cuando se entrena una corrida nueva, y es
justamente el que la aplicación está mostrando en pantalla. Pedirlo cada vez es
lo que mantiene a tu integración y a la persona que mira Faro viendo lo mismo.

`period` y `horizon` te dicen a qué granularidad y a cuántos períodos está
calculado lo que vas a leer después.

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
`status` pasa por `QUEUED` → `RUNNING` → `COMPLETED` o `FAILED`; sondéalo cada
pocos segundos hasta uno de los dos últimos.

```json
{
  "session_id": "sess_718a890426d4",
  "status": "COMPLETED",
  "job_id": "job_55989f9d2809",
  "job": { "created_by": "api_key:11c76a09-…", "started_at": "…" }
}
```

**Casi siempre no hace falta llamarlos.** Si la sesión tiene una programación
—Automatización → programar—, Faro reentrena solo después de que el archivo
cambió. Subir el export por la noche y dejar que la programación haga el resto
es la integración más simple que funciona, y no requiere ninguna de estas dos
llamadas.

### 3. Leer el semáforo

```http
GET /api/v1/inventory/status?session_id={id}
```

Devuelve **todos** los productos: no hay paginación. Con catálogos grandes
conviene filtrar en el servidor en vez de traer todo y descartar:

```http
GET /api/v1/inventory/status?signal=PEDIR_YA
GET /api/v1/inventory/status?supplier=Andina
```

`session_id` es opcional acá; sin él usa la sesión activa.

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
| `lead_time_source`, `unit_cost_source`, `moq_source` | De dónde salió cada supuesto. Son **cinco** valores, no tres |

De más tuyo a más nuestro:

| Valor | Qué significa |
|---|---|
| `user` | Lo escribiste en Faro |
| `file` | Vino en tu propio archivo |
| `supplier_rule` | Sale de una regla del proveedor que configuraste |
| `learned` | Lo aprendimos de tus recepciones. **Solo aparece en `lead_time_source`** |
| `default` | No teníamos el dato y pusimos un supuesto |

Contempla los cinco. Una integración escrita solo para `user`/`learned`/`default`
se cae justo en `file` y `supplier_rule`, que son los dos casos no-`default` más
frecuentes.

Los tres campos `*_source` son la parte que más conviene usar y la que más se
ignora: distinguen un número que diste de uno que nos inventamos. Una integración
que trate ambos igual va a confiar en supuestos nuestros como si fueran datos
suyos.

`explanation_code` es un código estable con sus parámetros aparte — ramifica por
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

## La integración completa, de una vez

Esto es el cron nocturno entero. No hay nada más que hacer.

```bash
#!/usr/bin/env bash
set -euo pipefail
API=https://tu-instancia/api/v1
KEY=$FARO_API_KEY          # de tu gestor de secretos, no del repositorio

# 0. La sesión que la app está mirando. Se pide cada vez, no se guarda fija.
SESSION=$(curl -sf "$API/planning" -H "Authorization: Bearer $KEY" \
          | jq -r '.data.active_session_id')

# 1. El export que tu ERP dejó anoche. Reemplaza en su sitio: mismo id, mismo
#    mapeo de columnas, sin asistente.
curl -sf -X POST "$API/data-sources/$SOURCE_ID/file" \
     -H "Authorization: Bearer $KEY" -F "file=@/exports/ventas.csv"

# 2. Si la sesión tiene una programación, saltate esto: Faro reentrena solo.
curl -sf -X POST "$API/sessions/$SESSION/train" \
     -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' -d '{}'
# Espera a QUEUED *y* a RUNNING. Recién posteado el estado es QUEUED, así que
# un bucle que solo mire RUNNING sale en la primera vuelta y el paso 3 lee el
# semáforo de la corrida ANTERIOR.
while :; do
  ST=$(curl -sf "$API/sessions/$SESSION/train/status" \
       -H "Authorization: Bearer $KEY" | jq -r '.data.status')
  case "$ST" in QUEUED|RUNNING) sleep 5 ;; *) break ;; esac
done

# 3. Qué comprar. Ojo con `*_source`: separa lo que diste tú de lo que
#    supusimos nosotros.
curl -sf "$API/inventory/status?session_id=$SESSION" \
     -H "Authorization: Bearer $KEY" \
  | jq '.data.items[] | select(.signal == "PEDIR_YA")
        | {sku, recommended_qty, lead_time_source, unit_cost_source}'

# 4. Y cuando emitas la orden, decíselo. Sin esto la orden no existe para Faro
#    y el plazo real de tus proveedores nunca se aprende.
curl -sf -X POST "$API/inventory/log-po?session_id=$SESSION" \
     -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
     -d '{"items":[{"sku":"ABC-1","recommended_qty":120,"final_qty":100,
                    "status":"modified","unit_cost":12.5}]}'
```

Dos cosas que este script hace a propósito y conviene copiar: pide el
`session_id` en cada corrida en vez de fijarlo, y manda `final_qty` distinto de
`recommended_qty` cuando el comprador ajusta — eso es lo que mide si Faro te
está sirviendo.

## Errores

| Código | Qué pasó |
|---|---|
| `401` | Llave desconocida, revocada o vencida. No se distingue cuál, a propósito. |
| `403` | La llave es `viewer` y la operación escribe. |
| `429` | Se pasó de 120 por minuto. Reintentar después de `Retry-After`. |
| `409` | La sesión no está en un estado que permita eso (entrenar una que ya corre). |

Los errores traen `error_code` estable además del mensaje. Conviene ramificar
por el código, no por el texto: el texto está pensado para personas y se reescribe.

## Lo que todavía no hay

Honestidad por delante, para que nadie diseñe contra algo que no existe:

- **Los webhooks disparan una sola vez, sin reintento.** Si tu endpoint estaba
  caído en ese momento, el evento se perdió: para lo que no se puede perder,
  sondea.

- **No hay sandbox.** Se prueba contra el tenant real.
- **No hay versionado real todavía.** El prefijo `/api/v1` existe, pero la
  promesa de estabilidad la da esta lista, no el número. Si alguna vez hace falta
  romper algo, habrá `/v2` y aviso previo.

## Correrla en infraestructura separada

No hace falta otro proyecto ni otro código: es **la misma imagen con otra
configuración**.

```bash
PUBLIC_API_ONLY=true      # solo las 8 rutas públicas + /health
WORKER_ENABLED=false      # no reclama trabajos de entrenamiento
SCHEDULER_ENABLED=false   # no corre crons — debe haber exactamente UNA
                          # instancia con esto en true, o los correos diarios
                          # salen dos veces
```

Arrancada así, la instancia lo dice en su propio log:

```
PUBLIC_API_ONLY: serving 13 of 269 routes (8 public endpoints + health)
Worker components: none (API-only instance)
```

**Qué compra.** La promesa deja de ser una lista que alguien tiene que respetar y
pasa a ser un muro: en ese host las rutas internas responden **404, no 403** —
no existen. Un integrador no llega a un endpoint interno ni adivinando, y un
endpoint pensado para una pantalla no puede recibir tráfico de máquina por
accidente. Además la integración del cliente deja de competir por CPU con la
aplicación, y un despliegue de la UI no reinicia su conexión.

**Qué NO compra, y conviene decirlo antes de que alguien lo asuma:**

- **No aísla la base de datos.** Las dos instancias comparten el mismo Postgres.
  Si la base se cae, se cae la integración del cliente y la aplicación juntas.
  Partir eso es una decisión mucho más grande y probablemente equivocada para
  este producto.
- **Las migraciones corren en cada arranque**, en toda instancia. Con dos
  servicios levantando a la vez hay una carrera. Hoy son idempotentes
  (`IF NOT EXISTS`), así que en la práctica aguanta, pero lo correcto es que una
  sola instancia las corra y las demás esperen. **Sin resolver.**
- **No cambia la autenticación ni los permisos.** Es menor alcance, no un
  segundo modelo de seguridad: lo que era alcanzable ahí lo sigue siendo con las
  mismas credenciales y los mismos guardas.

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

**La pantalla también se caminó** el mismo día, y encontró lo que hacía falta
para que todo esto sirviera: la pestaña de API Keys estaba **apagada**
(`ENABLED['api-keys'] = false`), así que no había forma de obtener una clave
desde el producto — la API existía para nadie. Además avisaba "Próximamente, las
claves aún no permiten autenticarse", que era cierto cuando se escribió y falso
ahora.

Y una tercera, la que de verdad importaba: la pantalla **no mandaba el rol**, así
que toda clave creada desde ahí salía `viewer` en silencio. Un cliente habría
generado su clave, la habría puesto en su ERP y habría recibido 403 al primer
intento de subir el export, sin nada que le dijera por qué. Ahora se elige, y se
verificó: clave "Leer y escribir" creada desde la pantalla → `201` al registrar
una orden, y `role = analyst` en la base.

**El límite de tasa, en vivo.** Se levantó una instancia con `TESTING_MODE=false`
—porque en desarrollo el limitador se salta— y se gastó el cupo con una clave
real: **429 en la llamada 121**, con `Retry-After: 60`. Exactamente el diseño
(120 pasan, la siguiente no).

**El modo `PUBLIC_API_ONLY`, en vivo.** Instancia levantada con la bandera: las
rutas públicas responden 200 y las internas —`/auth/login`, `/users`, `/tenant`,
`/api-keys`, `/messages`— responden **404**. No están montadas.
