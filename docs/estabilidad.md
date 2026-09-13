# Estabilidad — qué falta para que la app no tenga bugs

**Creado:** 2026-08-11
**Regla que lo gobierna:** CLAUDE.md, "Priority: stability over scope". Nada de
lo que está aquí es una feature. Si arreglar algo de esta lista **necesita** una
capacidad nueva —un endpoint, un campo, una pantalla, un interruptor— se avisa y
se pregunta antes de construirla.

Este documento reemplaza a siete documentos de planes, auditorías y propuestas
que quedaron obsoletos (ver "Lo que se borró" al final). Es el único backlog
vigente. La tabla viva sigue siendo `inventario-pantallas.md`; esto es el orden
en que se ataca.

---

## Lo primero: qué NO puede significar "sin bugs"

**El frontend no tiene un solo test.** `npx tsc --noEmit` verifica tipos, no
comportamiento, y no existe suite end-to-end. El 2026-08-06 la suite estaba
verde con 27 defectos vivos en la aplicación.

Así que "sin ningún bug" no es un estado que se pueda certificar. Lo alcanzable,
y lo que persigue esta lista, es:

> **Cada camino que un usuario puede tomar, ejercido al menos una vez por una
> persona en un navegador, y arreglado lo que salga.**

Y el dato que ordena la prioridad no es una intuición: **cada vez que se caminó
una pantalla en serio, aparecieron defectos.** `/pronosticos` dio 4, `/compras`
1, `/archivos` 1, `/usuarios` 1, y `/api` dio 6 con solo mirarla de cerca. No
queda ninguna razón para suponer que las acciones nunca ejercidas estén sanas.

---

## 1. El bug que encabezaba esta lista — **[ARREGLADO bd38436]**

### `ConfirmDialog` perdía la promesa de la primera confirmación — **[ARREGLADO bd38436]**

> Verificado en el código el 2026-08-23: un segundo `confirm()` ahora hace
> `settle(false)` sobre el pendiente en vez de sobrescribir el resolver, hay
> manejador de `Escape` en fase de captura, y el foco queda atrapado dentro del
> panel. Los tres defectos que lo hacían alcanzable están cerrados.

`Frontend/src/components/ui/ConfirmDialog.tsx:43` guarda **un solo** `resolver`
en un `useRef`. Si se abre una segunda confirmación antes de cerrar la primera,
la segunda **sobrescribe** al resolver de la primera: esa promesa no se resuelve
nunca. La acción original queda colgada para siempre — sin mensaje, sin spinner,
sin error. Falla del lado seguro (no escribe de más), pero falla **invisible**,
que es la peor forma de fallar.

Dos defectos del mismo componente lo hacen alcanzable en vez de teórico:

- **No cierra con `Escape`.** No hay manejador de teclado.
- **No atrapa el foco**, y la página de atrás no queda inerte. Con teclado se
  sale del "modal" tabulando y se llega a los botones de abajo — que es
  exactamente cómo se dispara la segunda confirmación.

Lo usan **6 pantallas**, incluidas las de borrar y la de generar orden de compra.
Encontrado revisando `/api`, pero no es de `/api`.

**Esto es lo primero que se arregla.**

---

## 1.bis Seis defectos que encontró la suite de caos (2026-08-22)

> **Estado al cierre del 2026-08-22: los seis arreglados.**
> Cada uno verificado por el test que lo encontró. (a) techos de plan, (b) rate
> limiter, (c) NUL en la URL, (d) SKU en blanco, (e) `sniff_separator`, y (f) el
> truncado silencioso de pandas — ahora el archivo con NUL se **rechaza** en
> `dataframes/io.py` (formatos binarios exentos: un .xlsx es un ZIP y está lleno
> de NULs), y el importador de stock, que sí tiene reporte por fila, rechaza la
> fila individual con `inventory_import_row_has_nul`.
>
> **El candado `limit_guard` ya cubre todas las rutas**: stock, alta de usuarios,
> invitación, sesiones, API keys, bodegas, importación masiva, transferencias,
> recepción de OC y sync de integraciones. Las que ya tenían transacción propia
> usan `take_tenant_lock(tenant_id, conn)` dentro de ella — una sola conexión, y
> el candado se libera con el mismo commit que hace visibles las filas.



Se agregaron tres archivos de tests hostiles —volumen masivo, datos corruptos y
concurrencia real— que hasta ahora no existían:
`backend/tests/test_chaos_ingestion.py`, `test_chaos_evil_path.py` y
`test_chaos_concurrency.py`. Encontraron esto **en la primera corrida**. Los
tests quedaron **en rojo a propósito**: ninguno usa `xfail`, porque la regla de
la casa prohíbe esconder un bug real detrás de una marca.

Ordenados por lo que cuestan si pasan en producción:

### a) Los techos de plan se saltaban con dos clics simultáneos — **[ARREGLADO 2026-08-22]**

`enforce_limit` es un `SELECT COUNT(*)` seguido de un `INSERT`, sin nada atómico
entre medio. Medido:

| Límite | Concurrencia | Techo | Filas que quedaron |
|---|---|---|---|
| `max_skus` | 12 peticiones | 5 | **10** |
| `max_users` | 8 peticiones | 2 | **9** |

Esto no es una molestia de rendimiento: en el plan gratis **es la frontera
comercial completa del producto**. Un importador masivo en una pestaña y una
carga manual en otra bastan. Arreglarlo necesita una decisión de diseño (índice
único parcial, `INSERT … SELECT` con la condición adentro, o un advisory lock por
tenant), por eso se pregunta antes de tocarlo.
*Test:* `test_a_plan_ceiling_cannot_be_walked_through_by_clicking_twice`.

### b) El rate limiter de API keys admitía 3× su techo bajo paralelismo — **[ARREGLADO 2026-08-22]**

Mismo patrón (leer el contador, insertar después): 20 llamadas simultáneas contra
un techo de 5 dejaron pasar **16**. Una credencial de máquina —la única que corre
desatendida, en un cron, con reintentos— puede multiplicar su cuota abriendo
sockets. Afecta también al techo por minuto, que es más viejo que los tiers.
*Test:* `test_the_rate_limiter_counts_exactly_under_parallel_hammering`.

### c) Un NUL en la URL se reportaba como falla del servidor — **[ARREGLADO 2026-08-22]**

`GET /api/v1/sessions/%00x/results` → el byte viaja intacto hasta psycopg2, que
lo rechaza, y el handler de excepciones lo convierte en **500 `internal_error`**.
El sobre existe (la corrección de su día funcionó), pero el veredicto es el
equivocado: al usuario se le dice que el servidor se rompió por una URL que
malformó él, y despierta a quien esté de guardia. Debe ser 400/404, validado
donde se lee el id.
*Test:* `test_a_nul_byte_in_a_path_is_the_callers_mistake_not_the_servers`.

### d) Un SKU de solo espacios creaba una fila invisible — **[ARREGLADO 2026-08-22]**

`PUT /api/v1/inventory/stock/%20%20%20` responde 200 y deja una fila cuyo SKU es
`"   "`. En pantalla no se ve nada: una fila de inventario que nadie puede
encontrar ni borrar desde la UI.
*Test:* `test_an_empty_sku_cannot_create_a_nameless_row`.

### e) `sniff_separator` reventaba con `IndexError` ante un archivo de solo BOM — **[ARREGLADO 2026-08-22]**

`backend/dataframes/io.py:32`. Excel escribe `﻿` en un export vacío;
`sample.strip()` no lo considera espacio, `lstrip("﻿")` deja la cadena
vacía y `splitlines()[0]` explota. Subir un export vacío = 500 "error
inesperado". Es una línea de guarda.
*Test:* `test_separator_sniffing_survives_files_designed_to_fool_it`.

### f) pandas truncaba la celda en el NUL, sin avisar — **[ARREGLADO 2026-08-22]**

`SKU-\0-1` entra a la base como `SKU-`. No hay error, no hay warning: el
archivo dice una cosa y el inventario guarda otra. Un comprador termina pidiendo
contra un SKU que su proveedor no conoce. Hay que detectarlo al leer y rechazar
la fila, no dejarla pasar cambiada.
*Test:* `test_a_nul_byte_is_carried_or_refused_but_never_silently_dropped`.

### Y una medición, que no es un bug pero se parece

El preview de un `.xlsx` de 200.000 filas tardó **139,8 s** y ~700 MB para
devolver 20 filas; el mismo dato en CSV tarda menos de un segundo. La rama de
Excel de `dataset_preview` lee la hoja entera antes de cortar. Corre en el hilo
de la petición: una sola subida deja un worker clavado más de dos minutos.
*Test:* `test_an_excel_bomb_does_not_take_the_process_with_it` (con el umbral
puesto como guardia de regresión, no como bendición).

### Lo que ya se arregló, porque lo introdujo el mismo cambio

La misma carrera de (a) estaba en `POST /entitlements/upgrade-request`, escrito
ese mismo día: 6 clics simultáneos dejaban 4 solicitudes abiertas. Se cerró con
un índice único parcial sobre `(tenant_id) WHERE status = 'new'` y un
`ON CONFLICT … DO UPDATE`, que es exactamente la forma que (a) necesita.

---

## 1.ter Hallazgo del recorrido en navegador (2026-08-22) — ARREGLADO

**El resumen ejecutivo del panel de compras reportaba euros en un tenant en
colones.** El KPI de la misma pantalla dice `₡196K` y el párrafo generado justo
debajo dice «El inventario total asciende a 195.755,6 €». La moneda del tenant
es CRC (`/mi-cuenta` lo confirma: «Así se ve: ₡1 250 000»).

Es el narrador de IA: recibe las cifras pero no la moneda del tenant, o no la
respeta en el prompt. Dos símbolos distintos para el mismo número, a diez píxeles
uno del otro — el usuario no sabe cuál creer, y el que está mal es el que viene
en prosa, que es el que se lee primero.

Visto en el recorrido con el tenant demo sembrado; no era un defecto de los
datos sintéticos.

**Causa:** los montos entraban al prompt como números pelados
(`"total_inventory_value": 195755.6`), sin moneda. El modelo no fue descuidado —
no tenía con qué: tuvo que elegir un símbolo y eligió uno. `key_points` y el
fallback por reglas ya pasaban por `money()`; solo la rama del LLM no.

**Arreglo:** los montos van pre-formateados al prompt, con instrucción explícita
de copiarlos tal cual. Mismo cambio en `generate_inventory_insight`. Se agregó
`_as_money` / `_has_money` porque el fallback y los key points leen los MISMOS
dicts y ahora reciben texto donde antes había números — un `> 0` sobre eso
reventaba (lo cazó el propio test).

**Verificado:** en el navegador, 0 euros y 20 colones en la pantalla; el párrafo
dice `₡195,756`, idéntico al KPI de arriba. Test de regresión:
`test_the_prompt_itself_carries_the_currency_not_a_bare_number`, que captura el
prompt real y falla si vuelve a llegar un número sin moneda (comprobado que
falla sin el arreglo).

---

## 1.quater Los tres últimos del reporte de agentes — **[ARREGLADOS 2026-08-23]**

Los tres que los agentes reportaron sin arreglar, por caer fuera de sus archivos.

**`/cash-calendar/fit` descartaba `result.status`.** El optimizador degrada a un
atajo voraz cuando el solver no alcanza, y lo dice — pero este endpoint tiraba
esa señal, así que una respuesta de caja construida sobre el atajo llegaba con la
misma confianza que una construida sobre el óptimo. El número no estaba mal; el
plan que describe era otro, y nada en el cable lo decía. Ahora manda
`plan_status`, y **solo** en el camino que resuelve: quien mandó su propio carrito
está siendo respondido sobre su carrito, y no hay plan que calificar.

**El aviso del plan aproximado no se pintaba cuando el plan salía vacío.** La
sección entera se condicionaba a que hubiera líneas, así que un atajo voraz sin
resultados dibujaba **nada** — y el comprador leía ese silencio como «no hay que
comprar». La verdad era «el optimizador se rindió y no sabemos», que es otra
frase y la cara de equivocar. La condición ahora incluye `status === 'fallback'`,
y ese caso tiene copy propio: el aviso normal habla de «esta lista» sobre una
lista que no existe, lo que se lee como tranquilidad en vez de advertencia.

**`/proveedores` mostraba el inglés crudo en su banner.** Imprimía `e.message`,
que para un `AppError` es el texto de respaldo que el backend manda a clientes
sin catálogo. Esta pantalla tiene catálogo. El toast global ya renderizaba
`errors.<code>` en el idioma del usuario, así que el mismo fallo se leía en
español en la esquina y en inglés en el panel — y el panel es el que está pegado
al formulario que tienes delante.

---

## 1.quinquies Lo que salió al capturar la app en inglés — **[ARREGLADOS 2026-08-23]**

Retomar las dieciocho pantallas con la interfaz en inglés destapó cinco defectos
que en español eran invisibles, porque en español el valor equivocado coincide
con el correcto.

**Las fechas de `/pedidos` salían en español.** `POHistory.tsx` fijaba el locale
en `'es'`, así que un usuario leyendo una pantalla en inglés veía `22 ago 2026`.
Ahora sigue el idioma de la interfaz.

**Veintitrés cifras se formateaban con separadores en español.** `1.234` en una
pantalla en inglés no es un número mal alineado: son las mismas cifras leídas
como otra cantidad. Hilar `lang` por diez componentes era más ruido que el fallo,
así que el locale vive en `lib/numberLocale.ts` y `LanguageProvider` lo mantiene
al día — puesto **durante** el render, no en un efecto, porque los hijos formatean
en su primer pintado.

**Dos fechas más seguían el idioma del navegador**, no el de la app
(`/pronosticos` y `/historial` pasaban `undefined` como locale). Coincidían por
accidente mientras el navegador estuviera en español.

**Las quince etiquetas del mapeo de columnas de `/ventas` estaban en español
duro.** Es la única pantalla donde equivocar una columna cuesta un entrenamiento
entero, y era la que se quedaba sin traducir. Veinte llaves nuevas, es/en.

**El asistente imprimía los `###` del markdown.** Su renderizador entendía
negritas y viñetas pero no encabezados, así que la respuesta del modelo llegaba
con las almohadillas a la vista. Se veía en la propia captura que la landing
mostraba a los visitantes.

**De paso:** `docs/estabilidad.md` —este archivo— contenía un NUL literal dentro
de un ejemplo, lo que hacía que ripgrep clasificara el único backlog vivo como
binario y lo saltara en toda búsqueda. El ejemplo ahora escribe `\0`.

---

## 1.sexies Los tres cortos que quedaban del nivel 4 — **[ARREGLADOS 2026-08-23]**

**Dos proveedores primarios del mismo SKU, y quién ganaba era el orden en que
Postgres devolviera las filas.** `sku_suppliers.is_primary` es `DEFAULT TRUE` y
nada en el esquema impide dos, así que enlazar un segundo proveedor sin nombrar
la bandera lo hacía primario también. Desde ahí, "quién surte este SKU" no tenía
respuesta: `get_primary_suppliers_map` construía un dict sobre un `SELECT` sin
`ORDER BY` (ganaba la última fila) y `get_sku_suppliers` ordenaba por nombre, así
que la misma petición podía nombrar un proveedor en la lista y construir la
recomendación para otro. Dos mitades: la **escritura** ahora desmarca a los demás
en la misma transacción, y la **lectura** quedó ordenada —gana el primario más
antiguo— para que las filas que ya violan el invariante resuelvan igual en todas
partes, sin migración de datos. Se borró `get_primary_supplier`, que tenía **cero
llamadas** y era un `LIMIT 1` sin orden: el día que alguien lo cableara habría
contestado distinto que el mapa, en la misma petición.

**Dos porcentajes de bodegaje en el mismo producto.** `/inventario` costeaba el
stock inmovilizado a un **25% anual escrito a mano** mientras el panel de escalas
de precio y el optimizador MILP costeaban la **misma** bodega al `holding_cost_pct`
del tenant (20% por defecto). El comprador leía "tenerlo te cuesta X al mes" en
una pantalla y recibía consejo de compra construido sobre otro costo del dinero
en la otra. Ahora `/inventory/dead-stock` resuelve la tasa igual que
`/price-breaks/evaluate` y **la devuelve**, porque el pie que la narra tiene que
nombrar el número que se usó: la cifra salió de la frase y pasó a ser un
parámetro de i18n.

**Lo que NO se arregló, y por qué**: `sku_suppliers.lead_time_days / moq /
unit_cost` siguen sin llegar a ninguna ruta de planificación. Con una corrección
al hallazgo original: **ninguna pantalla los muestra tampoco** — el endpoint
existe, el cliente existe en `api.ts` y `types.ts`, y ningún componente lo llama;
todo entró en el commit inicial y la mitad de interfaz nunca se hizo. Meterlos a
la cascada es **un nivel de precedencia nuevo** que cambia el semáforo de
cualquier tenant que tenga esas filas: es decisión del dueño, no un arreglo.

---

## 1.septies `/inventario` tenía 26 controles antes de la primera fila — **[SIMPLIFICADA 2026-08-23]**

Medido sobre la app corriendo, sin contar la barra lateral: **26 controles y 7
colores** antes de que el comprador llegara a un SKU. Cinco de los botones eran
exports, dos eran enlaces a pantallas que ya están en el menú lateral, y tres
líneas de alerta de colores repetían palabra por palabra las tarjetas KPI que
tenían justo debajo. El dueño lo dijo mejor: no entendía su propia pantalla.

Qué se hizo, sin quitar **ninguna** función:

- Los cinco exports viven en un menú **Descargar**; importar CSV, refrescar y
  registrar salida en un **⋯**. La navegación duplicada se borró.
- Los tres botones de configuración de bodegas salieron de la fila de pestañas
  —donde se hacían pasar por bodegas— y quedaron tras un engranaje.
- Las tres líneas de alerta se fundieron con su tarjeta: la frase pasó a ser el
  subtítulo del número que describe.
- Se extrajo `components/ui/MenuButton.tsx` en vez de escribir un tercer
  desplegable a mano — `/pronosticos` tiene el suyo copiado y puede adoptarlo.

Resultado: **de 26 a 16 controles**, y de 14 acciones sueltas a 4.

**Un defecto introducido y cazado en el navegador, en la misma pasada:** al
quitar las tres líneas, el guardia del mensaje "Todo el inventario está bien
cubierto" era `!lines.length` — cierto **siempre** desde que las líneas se fueron.
La pantalla escribió en verde que todo estaba cubierto sobre cuatro productos en
PEDIR_YA. Ahora la condición es el conteo de señales, no el efecto colateral de
un hermano. Es exactamente el tipo de afirmación sin respaldo que este documento
persigue, y lo produje yo: sin caminar la pantalla habría llegado a producción
con la suite en verde.

---

## 1.octies El bot de WhatsApp — hallazgos del diseño del asistente (2026-08-23, SIN ARREGLAR)

Salieron al diseñar `docs/asistente-acciones.md`. **No se tocó nada**: los tres
dependen de una decisión del dueño que está abierta.

**a) Dos acciones vivas que nadie puede deshacer.** `WRITE_TOOLS`
(`backend/whatsapp/tools.py:237`) expone `approve_po` y `register_reception`.
La segunda llama a `receive_po`: suma unidades al stock real y escribe
`supplier_lead_time_obs`, que mueve el plazo aprendido y el scorecard del
proveedor. **No existe des-recibir.** `approve_po` sella `sent_at`, que ancla el
calendario de caja, y tampoco se limpia. Twilio está configurado en `backend/.env`,
así que esto está vivo **en cuanto se despliegue** (en localhost Twilio no
alcanza el webhook). Contradice la regla que el dueño puso como fundamental para
el asistente: toda acción del LLM es reversible. Cerrarlo es una línea —sacarlas
de `WRITE_TOOLS` hasta que existan los inversos— o esperar al trabajo de undo.
**Decisión del dueño, planteada el 2026-08-23.**

**b) El enrutador falla en silencio.** `_route` (`agent.py:87` y `:91`) devuelve
`{"tool": None}` cuando la respuesta del modelo no parsea, **sin escribir un solo
log**. El usuario no se queda sin respuesta —cae al camino "sin herramienta" y
recibe el texto de ayuda— pero pidió registrar una recepción y recibió un menú, y
en los logs no queda rastro. Nadie que opere el bot puede medir con qué frecuencia
pasa. Es la firma exacta de la skill `silent-failures`.

**c) La regex que extrae el JSON es greedy.** `_JSON_RE = re.compile(r"\{.*\}",
re.DOTALL)` (`agent.py:73`) toma desde la primera llave hasta la última. Si el
modelo emite prosa con llaves antes del objeto, o dos objetos, el trozo capturado
no parsea y cae en (b).

**Lo que el diseño confirmó y conviene no olvidar:** el mecanismo de elegir
función ya existe y está probado — el modelo nunca nombra un endpoint, nombra una
llave de un diccionario que escribimos nosotros, y esa es la lista blanca. Lo que
falta para cumplir las tres reglas del dueño es preview calculado por el backend,
caducidad de la propuesta y undo.

---

## 1.nonies `PATCH /inventory/stock/{sku}` fabrica stock fantasma en `principal` (2026-08-23, SIN ARREGLAR)

Salió del recorrido de la API para el diseño del asistente. **Leído en código, no
reproducido en navegador** — pero el camino no tiene ambigüedad.

`patch_stock` (`backend/api/v1/inventory.py:187`) hace dos cosas que no hablan
entre sí:

1. Comprueba que el SKU existe con `svc.get_stock(tenant_id, sku)` — **sin filtro
   de bodega** (`service.py:268`, el parámetro `warehouse` es opcional y no se
   pasa). Encuentra la fila esté donde esté.
2. Escribe con `svc.upsert_stock(tenant_id, sku, data)`, y `data` sale de
   `StockPatch`, **que no tiene campo `warehouse`** (`inventory.py:84-96`). Así
   que entra en `upsert_stock` sin bodega y cae en el default:
   `if "warehouse" not in data: data = {**data, "warehouse": "principal"}`
   (`service.py:96-97`).

Un SKU que solo vive en `Norte`: el PATCH pasa el 404 mirando la fila de Norte, y
**crea una fila nueva en `principal`** con el valor parcheado. La de Norte queda
intacta. El SKU pasa a tener existencias en dos bodegas, y la vista consolidada
las suma.

**Por qué esto es nivel 1 y no cosmético:** el stock fantasma infla la cobertura.
Un producto que debía salir en PEDIR_YA puede leerse OK porque la mitad de sus
unidades no existen. Es la misma familia que el hallazgo 1.2 —inventarse
existencias y decidir compras sobre ellas— pero al revés: en vez de un cero
inventado que compra de más, es un positivo inventado que **deja de comprar**.

El propio código ya sabía de este agujero por otro motivo: el comentario de
`service.py:135` dice que este endpoint "404-checks get_stock() without a
warehouse filter, so it never knew the target (sku, warehouse) pair was new" —
escrito al arreglar el salto del techo de bodegas, sin cerrar la puerta que lo
causa.

**Arreglarlo es una decisión pequeña pero es una decisión:** o el PATCH escribe
en la bodega de la fila que encontró (comportamiento que nadie declaró y que
cambia lo que hoy hace el endpoint), o `StockPatch` gana un campo `warehouse`
obligatorio (rompe a cualquier cliente actual de la API pública). No se toca sin
que el dueño elija.

---

## 2. La columna "qué falta" de `inventario-pantallas.md` es el backlog

Las 26 pantallas tienen **alguna** caminata. **Ninguna está caminada entera.**
Esa columna es la parte honesta de la tabla y es el trabajo.

Lo más grande sin ejercer, agrupado por dónde vive el riesgo:

| Pantalla | Sin caminar |
|---|---|
| `/pronosticos` | Las 3 exportaciones (Excel por SKU, "Todos los SKUs", PDF), pantalla completa, análisis estadístico detallado |
| `/inventario` | Registrar salida, inmovilizado, exportar PDF, vista Proveedor, eventos y temporadas, importar CSV de stock |
| `/archivos` | Reemplazar archivo, conectar fuente SQL, buscador, correr un Análisis completo |
| `/compras` | Envío a proveedores, bodega destino, edición de cantidades, deshacer aprobación, gate de "Crear transferencia" |
| `/mi-cuenta` | Todo menos zona horaria: moneda, WhatsApp, cambio de contraseña, tema/idioma, granularidad, registros |
| `/pedidos` | Llegada completa, nueva orden manual, enviar pedido, WhatsApp, recibir de más |
| `/ventas` | Reusar archivo subido, repetir carga anterior, datos de ejemplo, cancelar a media corrida |

---

## 3. Tres pantallas que un muro de plan tapaba — el muro ya no existe

**Ya no hay planes** (2026-08-16, decisión del dueño: un solo plan, sin Stripe,
el precio se habla con nosotros). Con eso desaparecieron los ~40 muros de
`require_feature`, y estas tres dejaron de estar bloqueadas:

- **`/escenarios`** — antes solo se vio el muro. **Caminada el 2026-08-16**:
  cambio de demanda y atraso de proveedor, ambos ejercidos y contrastados contra
  el plan actual (ver el bloque del 2026-08-16 al final).
- **`/integraciones`** — el muro se fue, pero el flujo real sigue **sin caminar**:
  conectar, probar credenciales, sincronizar, ver el error cuando fallan. Sigue
  fuera del menú a propósito, y ahora la razón es esa y no una comercial.
- **`/proveedores`** — verificada solo por API. La pantalla nunca se abrió; alta
  y edición de proveedor sin tocar.

---

## 4. Lo reciente, todavía sin ver en un navegador — **[PARCIALMENTE CERRADO 2026-08-23]**

- ~~**El rediseño de `/api`**~~ — **caminado el 2026-08-23**: la banda de
  cabecera, la barra de clave, el raíl de endpoints y las secciones a dos
  columnas se ven correctos con el tenant demo sembrado.
- ~~**Una escritura hasta el final**~~ — **ejecutada el 2026-08-23**: se creó una
  API key real desde la pantalla («ERP nocturno»), se verificó en BD que quedó
  **una** fila (el candado `limit_guard` que se puso ese día no la duplicó), y se
  revocó desde la misma pantalla.
- **El arreglo de `log-po`** (commit `7866c64`): sigue sin verse.
- Siguen sin verse: la **subida de archivo** por `/api`, `train`, y la **vista
  angosta**.

**Defecto encontrado al caminarla, y arreglado:** tras revocar una clave, el
panel seguía mostrando «Key generada — cópiala ahora» con su botón *Copiar*. El
usuario copiaba una credencial recién muerta, y un 401 en la integración se lee
como "está roto", no como "la revoqué". `handleRevoke` ahora limpia el banner.
Verificado en pantalla: aparece al crear, desaparece al revocar.

**Segundo defecto, este introducido el mismo día:** la página prometía «120
llamadas por minuto por clave, **para todos**» — falso desde que el plan gratis
sumó un techo de 500 llamadas/día. Corregido en es y en.

---

## 5. Hallazgos de la revisión de `/api` que se decidió no arreglar

Reales, verificados, y conscientemente fuera del arreglo de ese día. Quedan aquí
para que la decisión sea visible y revisable, no para que se olviden:

| # | Qué es | Por qué se dejó |
|---|---|---|
| a | Un parámetro de ruta usado dos veces en la misma ruta solo se sustituiría la primera vez (`String.replace` con aguja de texto) | Latente: ninguna ruta actual lo hace |
| b | `values` se indexa por nombre pelado, así que un parámetro de ruta y uno de query con el **mismo nombre** compartirían celda de estado | Latente: ningún endpoint actual colisiona |
| c | Un `/` dentro de un parámetro se codifica a `%2F` y no sobrevive los dos saltos (rewrite + uvicorn) | Los ids son UUID; el diagnóstico sería confuso, no el resultado |
| d | Una página futura en `Frontend/src/app/api/<algo>/` **taparía** el endpoint del backend con ese nombre | No hay ninguna hoy; merece un comentario de advertencia en el archivo |
| e | El texto crudo de la excepción se muestra junto a copy traducida | Es una consola de desarrollo; el detalle técnico ahí es útil |

---

# Barrido adversarial del 2026-08-11 — seis agentes

Seis revisiones de solo lectura, en paralelo, sobre lo que ya existe: aprendizaje
de lead time, tratos raros de proveedor, el optimizador, fidelidad de `/impacto`,
consistencia del mismo número entre pantallas, y copy que afirma más de lo que el
dato aguanta.

**Cómo leer esto.** Cada agente marcó CONFIRMADO (rastreado de punta a punta) o
SOSPECHA. Los marcados **[verificado]** los leí yo directamente en el código
además del agente. Un hallazgo fue **refutado** al verificarlo y está anotado
abajo con lo que el agente pasó por alto — importa tanto como los reales.

**Estado al 2026-08-12.** El nivel 1 y el nivel 2 están cerrados salvo una cosa,
nombrada abajo. Cada hallazgo lleva su marca `[ARREGLADO]` y con qué lo cubre,
porque el hallazgo escrito es lo que explica por qué el arreglo es ese y no otro.

- `3e0bde6` cerró **1.1**, **1.4**, **1.5**, **1.6**, **2.7**, **3.2**, **3.5** y
  la mitad de panel de **1.2**. Su mensaje de commit sólo mencionaba siete cosas;
  1.4 y 2.7 iban dentro sin nombrarse.
- El trabajo del 2026-08-12 cerró **1.3**, **1.7**, **1.8**, **2.1**, **2.2**,
  **2.3**, **2.4**, **2.5**, **2.6** y **2.8**, más un hallazgo nuevo que apareció
  escribiendo el test de 1.3 (ver **1.9**).

**Lo único que queda del nivel 1:** la mitad de importación de **1.2**, que
necesita la columna `current_stock_set_by` — capacidad nueva, no arreglo.

Del nivel 3 y 4 no se tocó nada. Varios son un guardia de una línea con precedente
en el propio repo; **3.6** necesita capacidad nueva (un campo de MOQ en la entrada
del optimizador) y por eso se pregunta antes.

## Nivel 1 — el usuario pierde plata actuando sobre esto

### 1.1 La alerta de las 8:00 UTC lee la sesión activa al grano equivocado [verificado] — **[ARREGLADO 3e0bde6]**

> Los dos programadores y el snapshot mensual de sobrestock pasan `period`
> (`service.py:3453`, `:3472`, `:3495`, `:3600`), y la cobertura del correo y del
> WhatsApp se rinde en la unidad del inquilino, singular incluido — así que "4
> semanas" ya no se imprime como "4 días".


`service.py:3391` resuelve la sesión activa —la misma que muestran las
pantallas— y `:3405-3410` la calcula llamando a `_compute_inventory_status` **sin
`period`**, que por firma cae en `"daily"` (`:1257`). Igual en `:3423-3428`
(traslados del aviso de WhatsApp) y `:3547` (el snapshot que alimenta "capital
liberado" en `/impacto`).

Inquilino en `weekly`, SKU con 40 de stock, lead time 14 días, pronóstico de 10
unidades/semana. Pantalla: 4 semanas de cobertura contra 2 de lead time → **OK**,
nada que pedir. El correo de las 8:00 lee la misma sesión como diaria: 4 "días"
contra 14 → **PEDIR_YA**, pedir ~100 unidades. El comprador abre `/inventario` y
ve verde.

Agravante: el correo y el WhatsApp fijan la unidad "días" pase lo que pase
(`email.py:368`, `whatsapp.py:163`), así que ni arreglando el bucle quedaría bien
etiquetado. La suite cubre `/hoy` y la narrativa para esto; el bucle de alertas
nunca se cubrió.

### 1.2 Faro inventa `current_stock = 0` y luego marca en rojo mercadería en bodega [verificado] — **[ARREGLADO A MEDIAS 3e0bde6]**

> **El panel de huecos ya no lo hace:** dejó de mandar `current_stock: 0` junto
> con el costo y pide el conteo.
>
> **La importación sigue viva, y no se arregla sin decisión del dueño.** La
> columna es `current_stock FLOAT NOT NULL DEFAULT 0` (`migrations.py:369`) y
> `POST /inventory/bulk` solo exige `sku` (`inventory.py:499`): una lista de
> precios sin columna de stock crea filas nuevas con un cero que nada distingue
> de uno contado. Taparlo de verdad es la columna `current_stock_set_by` que este
> mismo hallazgo nombra — **capacidad nueva, se pregunta antes**.


`SetupGapsPanel.tsx:85` — `upsertInventoryStock(sku, { current_stock: 0, ...body })`.
Si el usuario solo llenó el costo, el cero persiste. No existe columna
`current_stock_set_by`, así que nada distingue un cero inventado de uno contado:
`coverage_days = 0` → **PEDIR_YA**.

Treinta filas costeadas de producto del que hay pallets llenos → treinta órdenes
de emergencia por inventario que ya está en la estantería. Lo mismo al importar
una lista de precios sin columna de stock, y el asistente reporta puras buenas
noticias. El camino de sincronización de dataset **sí** tiene el guardia
(`service.py:465-474`); el panel de huecos y `/inventory/bulk` lo esquivan.

### 1.3 El lead time nunca se reporta como faltante, y la pantalla promete que es obligatorio — **[ARREGLADO 2026-08-12]**

> Se reporta cuando la cascada caería en el 15 inventado (`resolve_field`
> devolviendo `SOURCE_DEFAULT`), y **no** cuando una regla de proveedor o
> categoría ya lo resuelve — pedirle al usuario un dato que ya nos dio entrena a
> ignorar la columna. Sigue fuera de `BLOCKING_FIELDS`: falta de lead time hace
> el plan equivocado, no imposible.
>
> La copy dice ahora lo que pasa de verdad: sin stock o sin costo el producto no
> aparece en el semáforo; sin días de entrega **sí** aparece, calculado sobre 15
> supuestos.
>
> **Limitación fijada con test:** `items` sólo lleva los `is_gap`, así que un SKU
> al que **sólo** le falta el lead time sigue sin aparecer en esa pantalla.
> Hacerlo visible es marcarlo bloqueante (falso: el SKU sí aparece en el
> semáforo, y movería `covered_pct` y la barra) o una segunda lista en la
> respuesta y una sección nueva — capacidad nueva, decisión del dueño.


La copy (`stockSetup.ts:18`): *"necesita tres datos más: cuánto tienes, cuánto te
cuesta y cuántos días tarda en llegar. Mientras falten, ese producto no aparece
en el semáforo."*

`setup_gaps_service.py:118-137` solo comprueba `unit_cost`, `sale_price` y
`supplier`; `BLOCKING_FIELDS = ("stock", "cost")`. El SKU **sí** aparece en el
semáforo, planificado sobre 15 días inventados. Un importador con 45 días de
tránsito reordena 30 días tarde en sus mejores vendedores, ciclo tras ciclo. La
columna `lead_time_set_by` que permitiría avisarlo existe y no se lee.

### 1.4 La primera migaja de un envío parcial fija el lead time del proveedor [verificado] — **[ARREGLADO 3e0bde6]**

> La observación se escribe sólo cuando la OC llega a `received`, fechada por el
> evento que la completó (`reception_service.py:361`). El plazo que importa para
> planificar es cuándo el comprador puede **contar** con la orden, o sea cuando
> aterriza la última unidad. Una OC que nunca se completa no produce observación
> — que es la respuesta honesta: todavía no sabemos cuánto tardó.


`reception_service.py:330` calcula `lead_days` desde **ese** evento, y el candado
`already_observed` (`:337-343`) hace que las entregas siguientes de esa OC no se
midan nunca.

OC de 5.000 unidades el 1 de agosto; llegan 20 de muestra el 3; el resto el 10 de
septiembre. Faro aprende **2 días**. A la tercera OC así, el valor aprendido pisa
al declarado para todos los SKU de ese proveedor, el punto de reorden se
desploma, y el scorecard muestra "real 2d vs declarado 30d" con 100% a tiempo.

Ningún código revisa la observación cuando la OC se completa.

### 1.5 Registrar "no llegó nada" deja la OC muerta para siempre [verificado] — **[ARREGLADO 3e0bde6]**

> `not_received` entró en `RECEIVABLE_STATES` (`reception_service.py:47`): es una
> orden atrasada, no una cancelada. Sigue recibible, sigue en recepciones
> atrasadas, sigue en stock en tránsito y en el conteo de OC abiertas — así que
> el semáforo ya no vuelve a pedir las unidades que un comprador acaba de
> reportar como no llegadas.


`reception_service.py:312` — si ninguna línea recibe cantidad, el estado pasa a
`not_received`, que no está en `RECEIVABLE_STATES` (`:39`), así que el guardia de
`:111-118` devuelve 409 para siempre. La OC sale además de las recepciones
atrasadas, de `isAwaitingReception` y de `get_incoming_qty`.

Cuando la mercadería llegue no hay forma de registrarla: ni stock, ni observación
de lead time. "Todavía no llegó" y "no va a llegar nunca" son el mismo estado
terminal. Se alcanza desde la interfaz poniendo ceros en las cantidades.

### 1.6 El horizonte se construye en días; la demanda y el lead time que van dentro, en períodos [verificado] — **[ARREGLADO 3e0bde6]**

> El optimizador habla **una** unidad: días de calendario en la frontera (que es
> lo que pide el endpoint), cubos del período activo adentro, con el lead time y
> el costo de bodegaje convertidos igual. La respuesta ya no reporta un conteo de
> cubos bajo una clave llamada `horizon_days`, y el camino de calce de caja
> cotiza el horizonte que le pidieron en vez de 30 días fijos al grano
> equivocado. Con eso el inquilino mensual sale del atajo voraz permanente.


`inventory.py:2600` — `horizon_days = plan.horizon * _days_per_period(period)`.
La curva que llena esos cubos es **por período** y el lead time se convierte al
revés: `ceil(raw_lead / days_per_period)` (`optimizer_service.py:227`). El
comentario de `:223-227` afirma que el endpoint expresa el horizonte en cubos del
período; el endpoint **multiplica**. Código y comentario se contradicen.

Plan mensual, horizonte 4, proveedor de 30 días: 120 cubos, de los que solo 0-3
tienen demanda; el modelo cree que el proveedor entrega en **1 cubo**; el costo
de almacenamiento queda subestimado ~30× (se cobra `/365` por cubo sobre cubos
que son meses); y las variables se inflan 30×, así que **con 6 SKU** se cruza el
techo de 5.000 y todo inquilino mensual cae permanentemente en el atajo voraz,
que no sabe hacer traslados.

`api.ts:1378-1384` ya documenta el síntoma; el arreglo que se aplicó fue dejar de
pasar 30, no corregir la unidad.

### 1.7 El MOQ se aplica como múltiplo, no como mínimo — y sin guardia de sobrestock — **[ARREGLADO 2026-08-12]**

> **Decisión del dueño (2026-08-12): `moq` es un MÍNIMO**, que es lo que dice el
> nombre del campo. `max(ceil(raw), moq)`, con guardia `raw > 0` para que un SKU
> bien surtido no reciba un pedido mínimo salido de la nada — el `ceil` viejo
> daba 0 para un `raw` de 0 y eso tenía que seguir igual. El redondeo a unidades
> enteras queda explícito en vez de ser efecto colateral de la aritmética del
> MOQ. Necesitar 520 con mínimo 500 pide 520, no 1000.
>
> **Lo que NO se hizo, y por qué:** el guardia de sobrestock. Cuando el mínimo
> del proveedor por sí solo ya crea meses de cobertura, recortar por debajo del
> mínimo produce una cantidad que el proveedor no va a despachar. El precedente
> del repo (`price_break_service`) no recorta: **rechaza la oportunidad y
> devuelve el `reason_code`** para que la UI lo explique. Hacer lo mismo aquí es
> un campo nuevo en la fila del semáforo — capacidad nueva, se pregunta.


`service.py:948-949` — `raw = ceil(raw/moq) * moq`. Necesitas 520 con MOQ 500 →
recomienda **1000**, 92% de sobrepaso, con botón de convertir en OC.

Sin guardia de cobertura: SKU que vende 2/día con MOQ 1000 de contenedor →
recomienda **500 días de stock**, y al refrescar sale SOBRESTOCK.
`price_break_service.py:263-265` implementa exactamente esa comprobación y
rechaza un salto de 140 días por `would_overstock`.

No existe concepto de tamaño de empaque (`pack_size`) en ninguna parte.

### 1.8 "La recomendación siempre es múltiplo de este número" es falso en todos los caminos que llegan a una OC — **[ARREGLADO 2026-08-12]**

> Con 1.7 la afirmación quedó doblemente falsa, así que la copy dice lo que el
> producto hace: nunca recomendamos menos que ese mínimo, por encima pedimos lo
> que hace falta sin redondear de más, y **si editas la cantidad a mano
> respetamos lo que escribes**. Esa última frase es la que cierra el hallazgo:
> los seis caminos que persistían `final_qty` sin re-aplicar el redondeo ya no
> contradicen ninguna promesa, porque ya no hay una promesa de "siempre".


`translations.ts:2985` y `:2996` afirman "siempre". El redondeo se aplica en un
solo punto (`service.py:948`) y nunca se re-aplica. Persisten `final_qty` sin él:
la edición de cantidad en la tabla, el optimizador ("Convertir en OC"), el salto
de precio (fija la cantidad al `min_qty` del escalón), el carrito manual,
`POST /log-po` y `bom_service`. El camino de traslado usa **floor**, no ceil.

MOQ 12 con escalón a 100 → el carrito queda en 100; el proveedor factura 108.

### 1.9 Crear una fila de stock sellaba los defaults como elegidos por el usuario — **[ARREGLADO 2026-08-12]**

No salió del barrido: apareció escribiendo el test de 1.3, cuando el SKU recién
creado insistía en tener `lead_time_set_by = 'user'` sin que nadie hubiera
escrito un lead time.

`StockUpsert` tiene tres campos no-Optional (`min_stock`, `lead_time_days`,
`moq`), así que Pydantic los materializa a 0 / 15 / 1 y `model_dump` los entrega
como si el usuario los hubiera tecleado. El endpoint filtraba eso **sólo para
filas existentes** —"una fila nueva tiene que empezar en algún lado"— y con eso
`upsert_stock` estampaba `<campo>_set_by = 'user'` sobre una suposición.

El valor no cambiaba (la columna es `NOT NULL DEFAULT 15`); el sello sí, y el
sello es lo que se lee. `resolve_field` deja ganar a la fila del SKU sobre una
regla **sólo** si su procedencia dice que alguien la puso: un inquilino que
configuraba "Acme entrega en 45 días" como regla de proveedor la veía descartada
en silencio en cada SKU creado por la pantalla normal, y compraba sobre 15. Y
volvía indistinguibles "el usuario eligió 15" y "nadie tocó esto", que es
exactamente el bug que las columnas de procedencia existen para matar
(`defaults.py`, `SOURCE_DEFAULT`).

El arreglo es aplicar el mismo filtro a las filas nuevas: se escribe lo que el
llamador mandó, el default del esquema llena el resto, y `<campo>_set_by` queda
NULL — que es como se deletrea "esto lo supusimos nosotros".

## Nivel 2 — números que no significan lo que dice su etiqueta

### 2.1 La tasa de adopción de `/impacto` usa un denominador que su copy contradice [verificado] — **[ARREGLADO 2026-08-12, copy]**

> **Decisión del dueño: se arregla la copy, no el número.** Contar las
> recomendaciones ignoradas exigiría persistir lo que se **mostró**, que el
> producto no guarda en ninguna parte — capacidad nueva, y grande.
>
> La cifra ahora se presenta como lo que es: "de las recomendaciones que
> decidiste", con la aclaración de que las que dejaste pasar sin tocar no están
> en ninguno de los dos lados. El pie que la llamaba "la métrica más honesta de
> valor" ahora dice cómo leerla: *cuando decides, qué tanto sigues a Faro* — no
> *qué parte de todo lo que te sugirió seguiste*. Cambiado en pantalla y en el
> correo mensual (`locale.py`), que repetía la misma afirmación.


La copy: *"Seguiste N de M recomendaciones que **Faro te puso en frente**"*
(`translations.ts:2222`). `M` es `total_suggested`, que solo cuenta líneas que
llegaron a `log_po_generation`; `/compras` filtra `status !== 'pending'` antes de
registrar, con el comentario "el comprador nunca actuó sobre ellas".

Faro recomienda 20, apruebas 3, rechazas 1, ignoras 16 → **"75%, seguiste 3 de
4"**. La proporción real es 15%. Cinco veces inflada, siempre hacia el lado
halagador. El docstring del backend lo describe bien; la pantalla afirma algo más
fuerte. Y el pie llama a esta cifra "la métrica más honesta de valor".

### 2.2 Dos botones de `/inventario` fabrican 100% de adopción desde una descarga [verificado] — **[ARREGLADO 2026-08-12]**

> **"Exportar editado"** filtraba las líneas en cero **antes** de construir las
> decisiones, así que `rejected` era inalcanzable por construcción. Una línea
> que el comprador pone en cero es un rechazo y ahora se registra como tal: es
> lo único que puede mover la adopción de ese 100% verde.
>
> **"Exportar OC"** llama al camino heredado sin cuerpo, y el servidor
> re-derivaba todo marcándolo `approved`. La orden se sigue registrando entera
> —el comprador se llevó el archivo y va a actuar sobre él— pero los cuatro
> contadores de decisión quedan en 0 y la fila se marca `source='export'`,
> exactamente como `create_manual_po` ya hacía con las órdenes escritas a mano.
> Una descarga es evidencia de que se llevaron la lista, no de que estuvieran de
> acuerdo con cada línea, y esa diferencia es el significado entero de la
> métrica.
>
> **Sigue vivo:** sin deduplicación. Pulsar "Exportar" tres veces escribe tres
> OC. Ya no triplica la adopción, pero sí las órdenes y las unidades del mes.


"Exportar OC" llama a `logPOGeneration(sessionId, **undefined**, …)`, que cae en
el camino heredado del backend (`inventory.py:1233-1238`): re-deriva todos los
PEDIR_YA/PEDIR_PRONTO y `_normalize_decisions` los marca `approved` por defecto.
`exportEditedPO` solo puede emitir `approved` o `modified` — `rejected` es
estructuralmente imposible.

Un inquilino que trabaje desde `/inventario` ve **100% de adopción, en verde,
para siempre**, y cada SKU urgente contado como riesgo atendido. Sin
deduplicación: pulsar "Exportar" tres veces escribe tres OC y triplica las
cifras del mes.

Es el mismo agujero que se tapó en la consola de `/api` el 2026-08-11. La
pantalla del producto lleva usándolo desde antes.

### 2.3 "Capital liberado de sobrestock" atribuye a Faro una resta que nadie atribuyó — **[ARREGLADO 2026-08-12]**

> **La atribución, por copy.** La columna pasa a llamarse "Baja del sobrestock",
> el titular a "bajó tu inventario detenido este mes", y la nota nombra las
> otras causas: vender, registrar merma, borrar productos, reentrenar. El bloque
> de procedencia agrega que "registrado" no es lo mismo que "atribuido a Faro".
> Igual en el correo mensual.
>
> **Los dos `None`, por código.** `_capital_freed_during` devuelve ahora
> `(valor, estado)` con `measured` / `not_measured` / `grew`. Antes respondía
> `None` a dos preguntas distintas —"nunca tomamos una de las mediciones" y
> "tomamos las dos y tu sobrestock **creció**"— y la UI imprimía *"necesitamos
> dos mediciones"* para ambas: al inquilino cuyo stock muerto acababa de crecer
> se le decía que faltaban datos, y la columna quedaba estructuralmente incapaz
> de dar una mala noticia.


`roi_service.py:347-363` — `snapshot(M) - snapshot(M+1)` del valor en SOBRESTOCK,
de una sesión distinta cada mes. Nada liga la diferencia a ninguna acción: se
mueve al vender, al registrar merma, al **borrar SKU**, al editar costos y sobre
todo al reentrenar.

Borrar 200 SKU descontinuados con ₡8M de stock muerto titula **"₡8.000.000
liberaste de inventario detenido"**. Justo debajo, `recap.provenance_body`
asegura "No estimamos ahorros… solo mostramos lo que quedó registrado", lo que
hace que se lea como auditado.

Y `:363` devuelve `None` tanto cuando falta un snapshot como cuando el sobrestock
**creció**, y la UI pinta ambos como *"Necesitamos dos mediciones mensuales
seguidas"*. La columna solo puede mostrar ganancias.

### 2.4 "Riesgos de quiebre atendidos" cuenta líneas y afirma puntualidad que nadie mide — **[ARREGLADO 2026-08-12, copy]**

> El titular pasa a "líneas urgentes que pediste", y el detalle dice las tres
> cosas que el número no es: cuenta **líneas**, no productos distintos (los
> mismos 30 urgentes cada mes durante un año suman 360); no mide si llegaron a
> tiempo; e incluye órdenes que no han llegado. Antes el detalle decía
> literalmente "que sí pediste **a tiempo**", sobre un dato que nadie mide.


`roi_service.py:246` — `SUM(skus_order_now)` sobre todas las OC. 30 SKU urgentes
pedidos mensualmente durante un año se leen como **360**. Nada comprueba "a
tiempo". La tarjeta del recap lleva el matiz correcto; el titular, que es la
superficie más grande, afirma lo contrario.

### 2.5 Las órdenes que nunca llegaron cuentan como gestionadas — **[ARREGLADO 2026-08-12, copy]**

> **Decisión del dueño: no se cambia el número.** La política declarada del
> módulo es contar **la acción tomada, no el resultado**, y generar la orden es
> la acción. Filtrar por `reception_status` convertiría estas cifras en una
> medición de entregas, que es otra métrica.
>
> Lo que se arregla es que la pantalla lo diga: "líneas urgentes que pediste"
> aclara que incluye órdenes que no han llegado, y "compras gestionadas" dice
> "hayan llegado o no". El scorecard sigue excluyéndolas, y ahora esa diferencia
> es visible en vez de ser una contradicción silenciosa entre dos pantallas.


Ninguna consulta de `/impacto` filtra `reception_status`. Una OC de ₡12M nunca
entregada sigue como 40 riesgos atendidos y ₡12M gestionados. El scorecard, una
pantalla más allá, **sí** excluye las no recibidas.

### 2.6 "Compras gestionadas" tira en silencio las líneas sin costo unitario — **[ARREGLADO 2026-08-12]**

> `managed_purchase_value_complete` sale de contar líneas ordenadas contra
> líneas con costo en `inventory_po_items` — al leer, no en una columna nueva,
> así que no hay migración ni forma de que se desincronice del valor que
> califica. Cuando la cobertura es parcial la pantalla muestra `≥ ₡2,1M` y
> explica que faltan costos, en vez de imprimir un total que no lo es.


`roi_service.py:95-101`. El caso "ninguna línea tiene costo" se resuelve
honestamente. El caso **parcial** no: 40 líneas con 6 costeadas reportan esas 6
como el total. ₡30M pueden leerse como ₡2,1M, con pinta de exacto.

### 2.7 "% a tiempo" del scorecard puntúa contra un declarado que nadie declaró — **[ARREGLADO 3e0bde6]**

> El `LEFT JOIN` sólo acepta el declarado cuando `lead_time_set_by` está puesto
> (`reception_service.py:476`), así que un proveedor importado por CSV sale como
> "no declarado" en vez de ser calificado contra una promesa que nunca hizo — y
> `on_time_rate` y `deviation_days` se van con él. Además entró
> `MIN_RATE_OBSERVATIONS`: los dos porcentajes de la fila ya no hablan por
> debajo del piso de muestra, y `lead_time_unusable` cubre el caso de "todas las
> entregas el mismo día", que imprimía "0d" junto a "100%".


`reception_service.py:382-384` lee `s.lead_time_days` crudo, que es
`INT NOT NULL DEFAULT 15`. El guardia está en el **mismo archivo, 140 líneas más
abajo** (`:520-528`), con el comentario que explica por qué se puso.

Un proveedor importado por CSV que entrega en 12 días: "DECLARADO 15d, 100% a
tiempo". Uno que prometió 20 sale a 0% en rojo. Además `/proveedores` blanquea el
default: el formulario prellena 15 y `supplier_service.py:29-30` lo sella como
`SOURCE_USER` — pasar de largo por el campo y guardar lo convierte en una
decisión deliberada para siempre.

Sin N mínimo, además: un proveedor con retiros de mostrador (`lead_time = 0`)
imprime **"No concluyente"** y **"100%"** en la misma fila.

### 2.8 "Valor comprado" muestra un ₡0 confiado donde `/impacto` diría "no disponible" — **[ARREGLADO 2026-08-12]**

> Se quitó el `COALESCE(..., 0)`: sin una sola línea costeada la celda es `null`
> y la pantalla pinta el mismo guion que ya usa para todo lo no medible, con la
> explicación de que no es un cero. Con cobertura parcial muestra `≥`. Misma
> regla que `/impacto` aplica al mismo dato, que era la mitad del hallazgo:
> mismos datos, dos políticas, una pantalla de distancia.


`reception_service.py:398` — `SUM(final_qty * unit_cost)`; un costo NULL anula el
producto y SQL lo descarta. Un proveedor al que le compraste ₡40M sin costos
registrados lee **₡0** en verde. `roi_service.py:538-540` aplica la regla
opuesta para la misma cantidad. Mismos datos, dos políticas, una pantalla de
distancia.

## Nivel 3 — el mismo número, dos autoridades

### 3.1 "Exportar OC" de `/inventario` descarga cantidades distintas de las de la tabla — **[ARREGLADO 2026-08-12]**

> Eran **diez** llamadas a `get_inventory_status` sin período fuera de tests, no
> una. Todas resuelven ahora el grano del inquilino con el mismo patrón que
> `GET /status` ya usaba: CSV, `dashboard-summary`, stock muerto, carrito de
> respaldo de escalones, disparo de prueba de alertas, el camino heredado de
> `log-po`, el simulador de eventos, `production-requirements` y el PDF.
>
> Al PDF y a `explode_requirements` hubo que **agregarles el parámetro**: no
> existía, así que eran siempre diarios. El PDF importa doble porque es la única
> copia de estos números que sale de la app, y la lee gente que no puede
> contrastarla contra ninguna pantalla. `simulate_event_impact` y
> `get_demand_spikes` también lo aceptan ahora, con default `"daily"` para que
> ningún llamador existente cambie de comportamiento.
>
> Fijado con tres tests de punta a punta contra los endpoints —no contra el
> cálculo—, porque el defecto nunca estuvo en la cuenta sino en lo que el
> endpoint le pasaba.


La tabla es consciente del período (`inventory.py:701`); el export
(`:2511`) llama a `get_inventory_status` **sin período** y re-deriva la lista en
el servidor. Mismo inquilino semanal del 1.1: la pantalla no ofrece nada que
pedir y el CSV trae 100 unidades, más una OC en `/pedidos` que el comprador nunca
vio.

Misma recomputación sin período, menor alcance: el PDF (`service.py:2536`, que
**no tiene** parámetro `period`), dead-stock, el carrito de respaldo de escalones
de precio, el disparo de prueba de alertas, `dashboard-summary`,
`production-requirements` y `simulate_event_impact`.

### 3.2 La misma fila de `/inventario` muestra dos "demanda LT" distintas [verificado] — **[ARREGLADO 3e0bde6]**

> La columna publica `_demand_lt` (`service.py:1575`), el mismo valor que usa el
> punto de reorden y el desglose de "cómo se calcula". Una sola cuenta, un solo
> número, y el CSV hereda el correcto.


`service.py:1432` — `avg_daily * lt_periods`, que alimenta el punto de reorden.
`service.py:1563` — `avg_daily * lead_time`, en días de calendario. **Las dos en
el mismo diccionario.** La columna "Demanda LT" pinta la segunda; expandir la
fila muestra la primera. Semanal, 10/semana, 14 días: la columna dice **140**, el
desglose dice **20**. Factor 7 en semanal, 30 en mensual. El CSV usa la de la
columna, así que contradice al desglose.

### 3.3 El bot de WhatsApp responde sobre otra sesión, a otro grano y desde otro modelo — **[FUERA DE ALCANCE]**

> El dueño excluyó el bot de WhatsApp del trabajo de estabilidad (2026-08-12),
> junto con lo de LLM y Stripe. El hallazgo queda escrito y sin tocar: sigue
> siendo el **único** punto de todo el producto que se salta
> `resolve_active_session`.


`whatsapp/tools.py:62,106` usa `get_latest_completed_session` —un
`ORDER BY updated_at DESC LIMIT 1`— en vez de `resolve_active_session`. Es el
**único** punto de todo el producto que se salta el resolvedor. `:65` no pasa
período. Y `:114` hace `next(iter(models.values()))`: **el primer modelo del
diccionario**, que es exactamente el bug de `/pronosticos` ya arreglado en
`forecasts.py:512` y nunca portado aquí.

### 3.4 `/pronosticos` corona al campeón con una regla distinta de las tres del servidor — **[ARREGLADO 2026-08-12]**

> `championRank` era `r.cost_horizon ?? r.cost ?? r.wape` evaluado **por fila**,
> así que comparaba el `cost_horizon` de un modelo contra el `cost` de otro —dos
> cantidades en escalas distintas, y `test_horizon_comparability` dice que la
> segunda es sistemáticamente menor. Ahora `makeChampionRank` elige **una**
> métrica para todo el conjunto y compara dentro de ella, que es lo que hace
> `service._champion_metric`. Cambiado en los cinco usos: tarjeta de SKU, tira
> de estadísticas, tabla de métricas, PDF y la precisión junto al gráfico.
>
> Queda una diferencia de alcance, anotada en el código a propósito: el servidor
> elige la métrica sobre las filas de **toda la sesión** y el navegador sobre las
> que tiene en mano (las de un SKU en casi todas estas superficies). Sólo
> divergen en una sesión donde unos SKU traen `cost_horizon` y otros no.


Servidor y motor eligen **una** columna métrica para todo el conjunto y descartan
las filas sin ella. El navegador (`pronosticos/page.tsx:71-72`) hace
`r.cost_horizon ?? r.cost ?? r.wape` **por fila**, así que compara el
`cost_horizon` de un modelo contra el `cost` de otro — y `test_horizon_comparability`
afirma que el segundo es sistemáticamente menor.

El modelo excluido gana la comparación del navegador: la tira de estadísticas
anuncia "Mejor modelo: XGBoost" con su WAPE, mientras la curva dibujada y la
orden de compra salen de Prophet. Mecanismo CONFIRMADO; frecuencia SOSPECHADA
(depende de que `cost_horizon` sea None en filas ML, lo que `trainer.py:438-485`
produce por varios caminos reales).

### 3.5 El estado agregado y el por bodega resuelven el proveedor distinto — **[ARREGLADO 3e0bde6]**

> `service.py:1725` ahora cae al primario configurado igual que la vista
> agregada, así que las cuatro resoluciones que colgaban de esa palabra —lead
> time aprendido, `lead_time_days`, `moq`, `service_level`— dan lo mismo en las
> dos pestañas.


`service.py:1355-1357` — `stock.supplier or primary.supplier_name`.
`service.py:1696` — `stock.get("supplier")`, **sin el respaldo del primario**.
Esa palabra propaga a cuatro resoluciones: lead time aprendido, y las reglas de
`lead_time_days`, `moq` y `service_level`.

Un SKU con proveedor en blanco en la fila y "Acme" como primario: la pestaña
"Todas" da **PEDIR_YA** con Acme en la fila; la pestaña de la bodega da
**PEDIR_PRONTO** con proveedor vacío. Un SKU, una bodega, dos señales en dos
pestañas de la misma página.

### 3.6 El optimizador ignora todas las entradas de proveedor que usa el semáforo — **[ARREGLADO 2026-08-22]**

> Hay **un** resolvedor: `optimizer_service.resolve_planning_inputs` llama a los
> del semáforo —`stock_defaults_service.resolve_field` para la cascada
> (fila que alguien fijó > regla de proveedor > categoría > global > default) y
> `service.resolve_lead_time` para que las recepciones reales ganen— sobre la
> **misma** fila representativa por SKU (`_aggregate_stock_rows_by_sku`, anclada
> en la bodega por defecto) y el mismo respaldo de proveedor primario. El
> endpoint lo resuelve una vez por request y se lo pasa al build y al serialize,
> así que el plan se **resuelve** y se **reporta** con los mismos números.
>
> El MOQ no cabe en el MILP (el modelo no tiene variable de mínimo), así que se
> aplica como **piso** al serializar, exactamente como `_calc_recommended`: piso
> de UNA orden al proveedor, no uno por bodega —aplicado por línea multiplicaría
> el mínimo por la cantidad de bodegas entre las que el solver reparta— y nunca
> sobre un 0, porque "no hay nada que pedir" tiene que seguir significando eso.
>
> `test_optimizer_agrees_with_the_semaforo.py` lo fija: sin el arreglo el MILP
> resolvía sobre 15 días mientras `/hoy` mostraba 20 aprendidos, y ofrecía 50
> unidades contra un mínimo de proveedor de 500.

`optimizer_service.py:220-230` lee `inventory_stock.lead_time_days` crudo —sin
regla, sin procedencia, sin lead time aprendido— y el MOQ no se pasa: no existe
campo de MOQ en `OptimizationInput`. `/hoy` planifica sobre 45 días *"aprendido
de tus recepciones"* y MOQ 500; `/planning` resuelve sobre 15 y 137 unidades.
Ambas pantallas ofrecen convertir en OC.

### 3.7 "Cuál es la bodega por defecto" tiene dos respuestas — **[ARREGLADO 2026-08-12]**

> Ahora hay **un** resolvedor, `warehouse_service.get_default_warehouse_name`:
> bandera `is_default` anclada primero, `name_precedence_key` después.
> `get_demand_shares` lo llama y `_aggregate_stock_rows_by_sku` lo recibe como
> argumento —una consulta por request, no una por fila, que era la objeción
> legítima del comentario viejo.
>
> El comentario que afirmaba que la pregunta "se responde igual en todas partes"
> era falso y ahora es cierto. Lo que costaba: un inquilino cuya primera bodega
> fue "Zona Sur" tenía el 100% de la demanda ahí mientras la fila agregada tomaba
> costo, lead time, MOQ y proveedor —y con ellos el titular de valor en bodega—
> de "principal". Una fila describiendo dos edificios distintos.


`warehouse_service.py:145-148` mira la bandera `is_default` y luego el nombre;
`service.py:1222` solo el nombre, porque solo tiene filas de stock a mano. El
docstring de la clave afirma que ambas responden igual "en todas partes". No.

Si la primera bodega es "Bodega Sur" y "principal" se creó después, el 100% de la
demanda va a Bodega Sur mientras la fila agregada toma costo, lead time, MOQ y
proveedor de principal — y con ellos el "valor en bodega" del titular.

### 3.8 El optimizador repartía la demanda por dónde ya estaba el stock — **[ARREGLADO 3e0bde6, tests el 2026-08-12]**

No salió del barrido de los seis agentes; apareció arreglando 1.6, en el mismo
archivo. `optimizer_service` dividía la demanda de un SKU como
`stock0[(sku,w)] / stock_total`, lo que hace **auto-derrotante** la mitad de
traslados del modelo: la necesidad de una bodega quedaba definida como
proporcional a lo que ya tenía. Una sucursal con 0 unidades de un SKU que sí
vende recibía 0 de demanda y no podía ser destino de un traslado nunca; el
depósito central que lo tenía todo se llevaba el 100% de la demanda y le decían
que comprara más. Con stock 0 en todas partes el denominador era 0 y se inventaba
un reparto **parejo** — que no es un default neutro: mete un SKU en bodegas que
nunca lo han tenido.

Ahora lee lo mismo que el semáforo por bodega, en el mismo orden de preferencia:
pronósticos por tienda si existen, si no `warehouses.demand_share` de `/bodegas`,
renormalizado sobre las bodegas que sí tienen filas de stock —una parte asignada
a una bodega que el modelo no puede surtir se tragaría demanda en silencio— y sin
nada configurado, todo a la bodega por defecto, que es una afirmación que el
inquilino puede ver y cambiar. Cinco tests en `test_optimizer_service.py` lo
fijan, incluido que el total repartido sigue siendo la demanda entera.

## Nivel 4 — afirmaciones sin respaldo

### 4.1 "Serie limpia — sin advertencias" es estructuralmente incapaz de decir otra cosa [verificado] — **[ARREGLADO 2026-08-12]**

> La regla de "cada cuánto reporta esta serie" estaba escrita **dos veces**: el
> profiler la infería de los datos (y por eso el pie del gráfico sí decía "7
> huecos"), y `DataQualityChecker` recibía `date_freq: None` del runner y
> respondía 0 huecos para toda sesión jamás entrenada. Ahora hay una:
> `quality.infer_freq_days` —mediana del salto entre fechas, no `pd.infer_freq`,
> que devuelve None ante cualquier irregularidad, que es casi todo archivo real—
> y el profiler la usa también. Un `freq` configurado sigue ganando: quien
> conoce el calendario sabe más que una inferencia.
>
> Con eso la advertencia puede dispararse, el puntaje puede perder sus 20 puntos
> y "Baja" deja de ser inalcanzable.
>
> La palabra **"interpolado"** salió del pie del gráfico: el default de
> `gap_fill` es `leave` —no se rellena nada—, así que afirmaba un tratamiento
> que en la mayoría de las sesiones nunca ocurrió. Decir que fueron
> **detectados** es cierto con cualquier estrategia; decir qué se hizo con ellos
> exige la estrategia en el payload, que no viaja.


`runner.py:135` fija `"date_freq": None` (solo se lee, nunca se asigna) y
`quality.py:249` devuelve 0 si no hay frecuencia. Para **toda** sesión entrenada
`missing_dates = 0`, la advertencia nunca se dispara y el puntaje nunca pierde
sus 20 puntos: el piso queda en 0,65 contra un umbral de "Baja" de 0,45, así que
**"Baja" es inalcanzable**. El pie del gráfico del mismo SKU puede decir "7
huecos detectados" a una pestaña de distancia.

También incondicional en ese pie: la palabra "interpolado". El valor por defecto
es `gap_fill = "leave"` — no se rellena nada.

### 4.2 `/escenarios`: "Atraso de proveedor" no hace nada, y reporta eso como resultado — **[ARREGLADO 2026-08-12]**

> Faltaban **dos** cosas, no una:
>
> 1. **Que el resultado se creyera.** El escenario escribía el número y nunca la
>    procedencia, y `resolve_field` sólo honra el valor de la fila cuando
>    `lead_time_set_by` dice que alguien lo puso. Ahora se estampa `SOURCE_USER`
>    sobre las copias en memoria —que nunca tocan la BD—, que es exactamente lo
>    que un "qué pasaría si" afirma: el usuario está declarando ese plazo.
> 2. **Que se sumara sobre la base correcta.** `row["lead_time_days"]` es la
>    columna cruda, `NOT NULL DEFAULT 15`. Un SKU cuyo plazo real venía de una
>    regla de proveedor de 45 días quedaba en 15+21=36 — **más corto que su
>    realidad sin atraso**. La base ahora es el valor resuelto por la misma
>    cascada que usa el semáforo.
>
> Nota: el arreglo de **1.9** agranda este hallazgo, porque deja más filas con
> `set_by` en NULL. Los dos van juntos.


`scenarios/service.py:304-319` escribe el lead time en la fila pero nunca pone
`lead_time_set_by`, y `stock_defaults_service.py:314-318` solo honra el valor de
la fila si esa columna está puesta. Para todo SKU cuyo lead time no se configuró
a mano —la mayoría— gana la regla de proveedor o el default de 15.

Simulas "mi proveedor se atrasa 21 días" antes de temporada alta, la pantalla
responde *"Este escenario no cambia ninguna decisión de compra"*, no compras por
adelantado, y quiebras.

### 4.3 `/pedidos` en móvil afirma "todos" sobre una ventana de 50 — **[ARREGLADO 2026-08-12]**

> La frase se acota a lo que la pantalla puede ver ("entre tus pedidos
> recientes") y remite a la de computadora para lo más viejo.
>
> Y `not_received` entró en `OPEN_RECEPTION_STATUSES`, que es el espejo de
> `RECEIVABLE_STATES`. Esto era **una inconsistencia introducida por el arreglo
> de 1.5**: el backend ya lo trataba como recibible y el frontend seguía
> mandándolo a "Ya registrados", así que la pantalla afirmaba la *llegada* de
> mercadería que ella misma acababa de registrar como no llegada.


`'mobile.pedidos_awaiting_none'` dice *"ya registraste la llegada de **todos** tus
pedidos"*. La consulta es `ORDER BY generated_at DESC LIMIT 50`: la orden 51 en
`pending` es invisible, y un distribuidor que genera una OC diaria pasa de 50 en
menos de dos meses. Además `not_received` cae en "cerradas", así que la pantalla
afirma la *llegada* de mercadería que demostrablemente no llegó.

### 4.4 `/inventario`: "Todo el inventario está bien cubierto" ignora `SIN_DATOS` — **[ARREGLADO A MEDIAS 2026-08-12]**

> **La frase, arreglada.** `summary.sin_datos` viajaba en el payload y no se
> leía. Ahora la frase se parte: "N bien cubiertos, y M sin señal todavía — les
> falta el conteo de stock", en ámbar en vez de verde. Y el caso que no
> mostraba **nada** —sin accionables, sin OK, con SKU sin juzgar— ahora dice lo
> que pasa, porque un panel vacío se lee como "no news is good news".
>
> **El `9999` sigue vivo, y necesita tu decisión.** `coverage_days = 9999` cuando
> la demanda media es 0 produce SOBRESTOCK, y luego la cobertura se anula para
> mostrar: insignia azul, cobertura "—", leyenda "considera pausar el pedido".
> No lo toqué porque cambia el veredicto del semáforo para toda una clase de
> SKU, y `_calc_signal` es la única autoridad de la señal — la pieza que este
> mismo documento lista como sólida. Hay dos lecturas defendibles (un pronóstico
> de cero demanda **es** stock muerto / "no sabemos" no es "tienes de sobra") y
> es decisión de producto, no un arreglo.


La frase se empuja cuando no hay líneas de acción y hay algún OK. `summary.sin_datos`
viaja en el payload y no se lee. Cinco SKU en OK y 500 sin registro de stock
producen una frase verde afirmando que *todo* el inventario está cubierto.

Adyacente: `service.py:1414` pone `coverage_days = 9999` cuando la demanda media
es 0, lo que da **SOBRESTOCK**, y luego la cobertura se anula para mostrar. La
fila queda con insignia azul de sobrestock, cobertura "—" y la leyenda "considera
pausar el pedido". "No sabemos" pintado como "tienes de sobra".

### 4.5 La landing llevaba quince cifras sin fuente, y dos afirmaciones falsas — **[ARREGLADO 2026-08-23]**

> Las quince cifras se fueron; el panel de industrias pasó de «Impacto típico en
> {industria}» con porcentajes verdes a «Lo que Faro hace en {industria}» con
> frases comprobables contra el código. El encabezado era parte de la mentira:
> prometía un resultado medido, y eso hacía leer los números como mediciones.
>
> La afirmación de los «6 meses» aparecía en **dos** lugares, no en uno. El
> umbral real son 20 períodos (`config.py:107`, `gate.py:59`), y el motor
> **descarta** esas series (`quality.py:216`) en vez de clasificarlas: la
> categoría «alta incertidumbre» no existe en el repo — comprobado con grep.
>
> La de cifrado se reemplazó por lo que el código sí hace: consultas por tenant,
> roles, credenciales de integración cifradas con Fernet (`crypto.py`) y borrado
> real (`data_export.py:213`). Los datasets son archivos planos, y ahora lo dice.
>
> De paso, la contradicción adyacente: «Escala desde 50 hasta 50,000 SKUs»
> contra «5K+ SKUs por instancia» en el mismo archivo, un orden de magnitud de
> diferencia. Queda el 5K+, que es el que tiene algo detrás.
>
> Verificado en el navegador: cero porcentajes en pantalla, ninguna de las tres
> afirmaciones presente, `tsc` limpio.

Bajo *"Impacto típico en {industria}"* (`page.tsx:277-321`): "reducción de
quiebres 20-35%", "compras de emergencia −30-50%", "merma −25-40%", y doce más.
Es el mismo defecto que la tira del hero que **sí** se limpió a propósito, con el
razonamiento escrito 400 líneas más abajo en el mismo archivo.

Dos más, ambas confirmadas:
- *"Los productos con menos de 6 meses de datos se clasifican como 'alta
  incertidumbre'"* — el motor los **descarta**; el umbral son 20 períodos, no 6
  meses; y esa clasificación no existe en el repo.
- *"La transmisión y almacenamiento están cifrados"* — lo único cifrado son las
  credenciales de integraciones. Los datasets y artefactos son archivos planos
  bajo `storage/`.

### 4.6 El traslado afirma un plazo y una comparación de precio que no hizo — **[ARREGLADO 2026-08-12]**

> **La comparación que no ocurrió** tiene ahora su propio código:
> `transfer_faster_price_unknown`. Sin costo unitario en ninguna parte el test
> de precio no corre, y devolver `transfer_faster_and_cheaper` hacía que la
> pantalla dijera "cuesta menos que comprar" sobre una comparación que nunca
> pasó. El traslado se sigue aceptando —llegar antes es un argumento real por sí
> solo— pero bajo una frase que sólo afirma eso.
>
> **El carril inventado** ahora se declara: `lane_is_default` viaja en `params` y
> la UI agrega la nota de que se calculó con 1 día y costo cero. Ese default es
> justo el que gana cualquier comparación traslado-vs-compra, así que
> distinguirlo de una medición no es un detalle.


`transfer_lane_service.py:14-18` resuelve un par sin configurar a **1 día y costo
cero**, y lo dice: *"deliberadamente optimista"*. El carril resuelto lleva
`is_default: True` y `_evaluate_transfer_lane` nunca lo mete en `params`, así que
la UI no puede distinguir un carril medido del inventado. Peor: cuando no hay
costo unitario el test de precio se salta entero, `saving` queda en null — y el
código igual devuelve `reason_code = "transfer_faster_and_cheaper"`, así que la
frase sigue afirmando *"cuesta menos que comprar"*. Retiene la cifra y mantiene
la afirmación.

### 4.7 Más, cortas

- ~~**`50% anticipo` se lee como 50 días de crédito.**~~ **[ARREGLADO 2026-08-23]** — el tallo se amplió a `anticip|adelant` (= 0 días de crédito), los planes de cuotas (`2x30`, `30/60/90`) devuelven `None` y caen en `unknown_terms` como promete el módulo, y los porcentajes se recortan antes del extractor de números. La misma corrección se aplicó al backfill SQL, porque un test fija la paridad SQL/Python — y ese test pasaba contra el código viejo: SQL y Python estaban **consistentemente equivocados**, que es por qué la paridad sola nunca lo detectó. Original: `cash_service.py:34-37`
  busca `anticipad`, no `anticipo`. Cae al extractor de números → 50. Reporta
  `terms_known: True`, contradiciendo la promesa del módulo de que lo ilegible
  queda en None. Misma familia: `2x30` → 2, `30/60/90` → 30.
- ~~**Dos escalas de precio de proveedores distintos se fusionan.**~~ **[ARREGLADO 2026-08-23]** — los escalones se agrupan por `(sku, supplier_id)`; se cotiza la escalera del proveedor del SKU, y si el SKU no nombra proveedor gana la mejor escalera **acreditada a quien la ofreció**. Original:
  `price_break_service.py:298-312` agrupa solo por SKU y nombra al dueño del
  escalón más bajo. El panel puede decir "Andina: sube a 500 y ahorras ~1400"
  sobre un precio que Andina nunca ofreció.
- **No se puede reactivar un proveedor.** **[ARREGLADO COMPLETO 2026-08-23]** — ahora responde 409 con dos códigos distintos (`supplier_name_taken` y `supplier_name_taken_by_deactivated`), porque el siguiente movimiento del usuario es distinto en cada caso; también cubre la carrera de dos inserciones simultáneas. La ruta **sí se construyó** (`POST /inventory/suppliers/{id}/reactivate`, analyst+, idempotente, con par de permisos en test): sin ella la baja era una puerta de un solo sentido y el 409 nombraba una fila que el usuario no podía tocar — un callejón que creaba el propio mensaje de error. **No lleva re-chequeo de nombre**, a propósito: `UNIQUE (tenant_id, name)` no excluye inactivos, así que la colisión que ese chequeo evitaría no puede existir en la base. Una guarda que no puede dispararse se lee como protección y no protege nada. Hay un test que fija ese invariante para el día en que el índice se vuelva parcial. Original: La baja es lógica, no hay ruta de
  reactivación en ninguna parte, y el índice único no excluye inactivos: volver a
  darlo de alta con el mismo nombre da un **500 genérico**.
- ~~**Dar de baja un proveedor saca del control de caja plata que aún debes.**~~ **[LA MITAD DE CAJA, ARREGLADA 2026-08-23]** — cuentas por pagar ahora carga **todos** los proveedores (un activo gana una colisión de nombre); `supplier_service` mantiene su filtro, porque una OC no debe auto-enviarse a un proveedor dado de baja. ~~**La mitad del lead time**~~ **[DECIDIDA Y ARREGLADA 2026-08-23]** — la regla es: dar de baja = **dejar de actuar** hacia el proveedor (no auto-enviar OCs, no ofrecerlo para trabajo nuevo), **no** olvidar lo que sabemos de él. Bajo esa regla el raro era `build_rule_index`, que filtraba activos y hacía caer los SKU al default de 15 días — peor dato que el que el usuario escribió, aplicado sin decir nada. Ya no filtra: las tres fuentes ahora coinciden en no reaccionar. `supplier_service` mantiene su filtro, que es la mitad correcta de la misma regla. Descripción original: tres fuentes reaccionan en tres direcciones distintas a una baja — la tarjeta desaparece (`stock_defaults_service.build_rule_index` filtra activos → los SKU vuelven a 15 días), la regla `stock_defaults` con el mismo nombre **sigue aplicando** (se indexa por texto libre, nunca se une a `suppliers`), y el lead time **aprendido** de recepciones también sigue aplicando. Cerrarlo es una decisión, no un edit local: o la baja corta las tres o no corta ninguna, y cambia las entradas del semáforo para todos los SKU de ese proveedor. Original:
  `cash_service.py:132-138` solo carga activos, así que una OC enviada e impagada
  pasa a `unknown_terms` y sale de `committed_total`. Y sus SKU vuelven a 15 días
  mientras una regla de `stock_defaults` con el mismo nombre sigue aplicando: las
  dos mitades de "el lead time de este proveedor" reaccionan en direcciones
  opuestas.
- **`unit_cost = 0` vuelve un SKU invisible para el optimizador, sin marcarlo.**
  — **[ARREGLADO 2026-08-22]** Los coeficientes ya se calculaban con
  `_usable_unit_cost` (un 0 es un blanco que resultó ser número, no un precio),
  así que el SKU entra al plan con el costo asumido. Lo que seguía mintiendo era
  la bandera: `assumed_unit_cost` comprobaba `is None`, de modo que esas líneas
  se reportaban como precio real sobre un `total_cost` calculado con 1.0. Ahora
  comprueba lo mismo que la matemática, y el SKU o entra marcado o no entra.
- **Dos compradores pueden recibir dos planes distintos del optimizador.** —
  **[ARREGLADO 2026-08-22]** El cupo de solves concurrentes era 2 y el motor
  ejecuta **todos** los solves en un solo hilo dedicado (el arreglo del deadlock
  de HiGHS, que no se toca): el segundo admitido no resolvía en paralelo, hacía
  **cola**, y su espera —`time_limit + gracia`, contada desde el submit— se
  gastaba mientras el primero seguía resolviendo. Al vencer, `optimize()` lo
  trata como cualquier otro caso sin resolver y devuelve el plan voraz. El cupo
  ahora es **1**: el segundo comprador recibe un 503 honesto que el navegador
  reintenta, en vez de un plan silenciosamente distinto. No se pierde nada — ese
  segundo cupo nunca compró concurrencia, solo una espera que terminaba en
  degradación.
- ~~**El aviso de "esto es el atajo" está dentro del bloque que solo se pinta si
  hay órdenes o traslados.**~~ **[ARREGLADO 2026-08-23]** — es el mismo defecto
  que la sección 1.quater: la condición ahora incluye `status === 'fallback'` y
  ese caso tiene copy propio. Original: un atajo que no encuentra nada no
  mostraba nada, así que "no hay nada que hacer", "degradamos a una regla más
  simple" y "el problema era demasiado grande" se veían idénticos: un espacio en
  blanco.
- **`sku_suppliers.lead_time_days / moq / unit_cost` no llegan a ninguna ruta de
  planificación** — solo se muestran. `GET /inventory/stock/{sku}/suppliers`
  responde 60 días; el semáforo planifica sobre 15.
- **Dos proveedores pueden ser primarios del mismo SKU** y cuál gana es
  indefinido (dict sin `ORDER BY`, `LIMIT 1` sin orden). Nada compara nunca lead
  time, costo o MOQ entre dos proveedores: "de cuál conviene comprar" no se
  responde en ninguna parte.
- **El costo de bodegaje del 20% nunca se muestra** aunque viaja en el payload, y
  `/ventas` lo escribe siempre en `business_cfg`, así que parece configurado sin
  que nadie lo preguntara. La vista de stock muerto usa **25%** fijo.

## Un hallazgo refutado, y por qué importa

El agente de proveedores reportó como grave que teclear un conteo de stock
sobrescribe el lead time y el MOQ con los valores por defecto del modelo,
sellándolos como elegidos por el usuario. **Es falso para filas existentes.**

Leyó `data = body.model_dump(exclude_none=True)` en `inventory.py:145` y se
detuvo. En `:162` hay `data = {k: v for k, v in data.items() if k in
body.model_fields_set}`, y encima un comentario que describe ese bug exacto como
**ya arreglado**, con la medición que lo encontró ("un mínimo de proveedor de 100
se volvió 1 y la recomendación bajó de 100 a 81").

Lo que sí queda vivo es el caso de **fila nueva**, donde los defaults se aplican
y se sellan como `user` — que es el hallazgo 1.2 por otra puerta.

Queda anotado porque el modo de fallo se repite: leer hasta la primera línea que
confirma la sospecha y parar. Todo lo marcado **[verificado]** en este documento
se leyó entero antes de escribirlo.

## Lo que se probó y está sólido

Vale tanto como la lista de defectos, porque dice dónde **no** hay que buscar:

- **La señal del semáforo tiene una sola autoridad.** `_calc_signal` es el único
  sitio donde se derivan los cuatro valores; agregado, por bodega, briefing,
  correo, PDF, CSV y la API pública leen la cadena que produce. El frontend nunca
  la re-deriva. Las divergencias de este documento están en las **entradas**,
  nunca en una segunda tabla de umbrales.
- **La fórmula de cantidad a pedir existe una sola vez**, y el punto de reorden,
  la recomendación y el desglose llaman al mismo `_safety_stock`.
- **La elección de campeón coincide entre motor, backend y gráfico**, con
  `CHAMPION_METRIC_ORDER` fijado por un test de paridad. Solo el navegador
  discrepa (3.4).
- **La sesión activa pasa por `resolve_active_session`** en todos los endpoints
  HTTP y en ambos programadores. El bot de WhatsApp es el único bypass.
- **Los ciclos de traslado son estructuralmente imposibles**, el redondeo nunca
  inventa unidades, y la regresión de las 3692,67 unidades está cerrada con test
  que la guarda.
- **Los SKU sin stock en archivo se excluyen en vez de adivinarse**, y viajan en
  la respuesta para poder mostrarse: el único sitio donde "sin datos" y "sin
  sugerencias" se distinguen bien.
- **La cascada de valores por defecto es correcta y está bien argumentada**: una
  regla que deja un campo NULL no dice nada, el alcance viaja a la UI, y se niega
  a inventar un `unit_cost`.
- **El MOQ ≤ 0 no puede llegar a la matemática** por cuatro guardias
  independientes.
- **La economía de los escalones de precio es genuinamente buena** — costo de
  bodegaje contra el descuento, tope de cobertura, piso de materialidad, y las
  oportunidades rechazadas vuelven con su razón. Su único defecto es no filtrar
  por proveedor.
- **Las alertas de desviación de proveedor** usan IQR robusto, una cola, sigma
  con piso y n≥6, con la alternativa descartada documentada.
- **`compute_session_accuracy` es el defecto de "Mejor WAPE" bien reparado**:
  reporta el WAPE del modelo del que salieron los números, excluye baselines, y
  devuelve `None` en vez de un 100% triunfal sobre un catálogo que no vendió.
- **La fila de KPI de `/hoy` es la superficie mejor comportada del producto** —
  "—" en vez de un número cuando no hay conteo o no hay costo, el aviso de datos
  viejos, y la frase que dice que las cifras no significan "no hay riesgo" sino
  "no sabemos".
- **La pestaña de patrón de ventas de `/pronosticos`** cierra cada leyenda con
  "No es una predicción", tiene mínimos de observaciones declarados y se niega
  en datos semanales. El panel más honesto de la app.
- **Las mecánicas comprobables de la landing sí se sostienen**: "a la tercera
  recepción" = 3 exacto, la tabla de límites de plan coincide con el código, y la
  tabla de umbrales del semáforo coincide con `_calc_signal`.

## Orden de trabajo

1. ~~Arreglar `ConfirmDialog` — los tres defectos.~~ **Hecho** (`bd38436`): el
   resolver pendiente se resuelve antes de que la segunda confirmación lo pise,
   cierra con `Escape` y atrapa el foco.
2. ~~Los seis del barrido que no necesitaban capacidad nueva.~~ **Hecho**
   (`3e0bde6`): 1.1, 1.5, 1.6, 3.2, 3.5, la mitad de panel de 1.2, y el 3.8 que
   apareció adentro.
3. ~~Los niveles 1 y 2 completos.~~ **Hecho** (2026-08-12): 1.3, 1.7, 1.8, 2.1,
   2.2, 2.3, 2.4, 2.5, 2.6, 2.8, más el 1.9 que apareció adentro. Dos
   decisiones del dueño quedaron escritas en sus hallazgos: `moq` es un
   **mínimo**, y `/impacto` se corrige **por copy**, no cambiando cifras que ya
   se enviaron por correo.
4. ~~El nivel 3, salvo lo excluido.~~ **Hecho** (2026-08-12): 3.1, 3.4 y 3.7.
   3.2, 3.5 y 3.8 ya venían de `3e0bde6`; **3.3 está fuera de alcance** por
   decisión del dueño (bot de WhatsApp) y **3.6 necesita capacidad nueva**.
5. El nivel 4 — afirmaciones sin respaldo. **En curso.**
6. Correr la suite completa **una sola vez y sin nada más encima** (`python
   scripts/run_tests.py`). La corrida del 2026-08-12 tardó 3 horas en vez de 36
   minutos porque se le dejó pytest suelto y un `tsc` en paralelo: eso es carga,
   no defectos, y arruina los tests sensibles a tiempo.
7. Caminar `/api`: el rediseño, una escritura de punta a punta, subida y `train`.
8. Bajar por la tabla de la sección 2, pantalla por pantalla, arreglando lo que
   aparezca y anotando la caminata en `inventario-pantallas.md`.
9. Con luz verde: subir el plan del tenant de prueba y caminar las tres de la
   sección 3.

**Sigue abierto, y nada de esto es un descuido:**

| Qué | Por qué no se hizo |
|---|---|
| La mitad de importación de **1.2** | Necesita la columna `current_stock_set_by`. Capacidad nueva. |
| El guardia de sobrestock de **1.7** | Recortar por debajo del mínimo del proveedor da una cantidad que no se puede pedir; marcarla como hace `price_break_service` es un campo nuevo en la fila. |
| La lista de "sólo le falta el lead time" de **1.3** | Segunda lista en la respuesta y sección nueva en la pantalla. |
| La deduplicación de "Exportar OC" de **2.2** | Tres clics siguen escribiendo tres OC. Necesita decidir qué es un duplicado. |
| El `9999` de **4.4** | `coverage_days = 9999` con demanda media 0 da SOBRESTOCK. Cambia el veredicto del semáforo para toda una clase de SKU: decisión de producto. |
| **3.3** (bot de WhatsApp) | Fuera de alcance por decisión del dueño. |
| Cuatro cortas de **4.7** | `sku_suppliers.*` no llega a ninguna ruta de planificación; dos proveedores pueden ser primarios del mismo SKU sin desempate; el 20% de bodegaje no se muestra y stock muerto usa 25% fijo. |

*(El 3.6 y los niveles 3 y 4 salieron de esta tabla el 2026-08-23: sus
encabezados los marcan arreglados, y listarlos aquí hacía parecer que quedaba
más trabajo del que queda.)*

---

## Dos cosas que son decisión del dueño, no arreglos

- **Subir el plan del tenant de prueba**, para caminar escenarios,
  integraciones, proveedores y los muros desde el otro lado.
- **Tests end-to-end del frontend.** Es la única forma de que "sin bugs" se
  sostenga en el tiempo en vez de repetirse a mano en cada cambio. Pero es
  **capacidad nueva**, no un arreglo, así que no se empieza sin que se pida.

---

# 2026-08-16 — un solo plan, y la simulación de eventos caminada

## Los planes se fueron (decisión del dueño)

Faro vendía tres escalones —starter / professional / enterprise— con un set de
funciones cada uno y una suscripción de Stripe detrás. **Ahora hay un producto
solo: todo incluido, sin topes de productos, usuarios, bodegas ni sesiones, y el
precio se habla con nosotros.** Lo que se hizo:

- **Backend.** `entitlements/plans.py` pasa de un catálogo de tres a un `PLAN`
  con los dos únicos techos que quedan, y son de infraestructura, no de venta:
  8 trabajos concurrentes y 2 GB por archivo. `require_feature` y `has_feature`
  **se borraron** en vez de quedarse contestando que sí a todo: una autorización
  que no puede decir que no se lee como un guardia y no guarda nada. Con eso
  cayeron ~40 muros de ruta, el recorte de ABC-XYZ en `/dead-stock` y los dos
  guardias de WhatsApp del correo diario y del recordatorio de frescura.
- **Stripe, borrado entero**: `backend/billing/`, su router, el webhook, sus
  ajustes de configuración y su test. La migración nueva **suelta** la tabla
  `stripe_events` y las cuatro columnas que solo existían para eso, incluida
  `tenants.plan` — una columna muerta que nadie lee es la que alguien vuelve a
  leer por error dos años después. `trial_ends_at` y `quota` se quedan: la
  primera se sigue aplicando, la segunda es cómo se le ensancha el límite a **un**
  cliente sin un deploy, y ahora es el único mecanismo que lo hace.
- **`GET /entitlements`** ya no reporta `plan`, `features` ni `feature_plans`.
  Devuelve el estado de la prueba, los límites y `read_only`, que es lo único
  que todavía decide algo. Un mapa de funciones que contesta `true` a todo solo
  invita al navegador a seguir preguntando.
- **Frontend.** Fuera `/planes`, el panel de facturación de `/mi-cuenta`,
  `FeatureGate` y el filtrado por función del sidebar y de la paleta de
  comandos. El aviso de prueba vencida ya no manda a comparar planes: pide que
  nos escriban.
- **Copy.** La landing pierde la tabla de tres columnas, el "¿qué plan me toca?"
  y las menciones de escalón en el FAQ; la sección de precio dice lo que es
  cierto —un plan, todo adentro, el precio se arma sobre la operación— y ofrece
  escribirnos. La guía de usuario (`docs/help/index.html`) pierde sus 15
  insignias de "Profesional" y su tabla de límites. `docs/api-publica.md` deja de
  decir "incluida desde Professional" y de prometer un techo por escalón.
- **Tests.** Los que existían para probar el muro fueron reescritos como lo
  contrario: que la ruta **responda** con `testing_mode` apagado, que era el
  interruptor que encendía el muro. Los de límites siguen vivos con un `quota`
  explícito, porque lo que probaban era el **bypass** —importar por CSV se
  saltaba `max_locations`— y ese agujero sigue mereciendo un guardia aunque el
  número por defecto ya no exista.

## La simulación de eventos, caminada en navegador

Con el muro fuera, `/escenarios` y el panel "Eventos y temporadas" de
`/inventario` se ejercieron a mano el 2026-08-16 contra el tenant de la
ferretería. Lo que funciona, y lo que no.

**Funciona, verificado contra la aritmética:**

- **Cambio de demanda** ×1,4: 38,8 → 54,4 de demanda diaria, 131 → 240 unidades.
- **Atraso de proveedor** +7 días: plazo 9 → 16, señal **Pedir pronto → Pedir
  YA**, 131 → 422 unidades. Esto es el hallazgo **4.2** de este documento visto
  desde el navegador: antes respondía "este escenario no cambia ninguna decisión".
- **Simulación de un evento** ×2,0 a 103 días: 38,8 × 4 días × 2 = 310,6, pedir
  311, "pide antes del 18 de noviembre" (inicio − 9 días de plazo), ₡3 888.
- **Multiplicador por SKU** ×3,0: 465,9 unidades, y la fila explica de dónde sale
  el multiplicador ("por SKU" contra "del evento").

**Tres defectos, los tres arreglados el mismo día:**

### E1. El titular pedía una orden con fecha vencida

Evento a 3 días con un proveedor de 9: el titular decía **"Pide antes del 10 de
agosto"** — seis días en el pasado — mientras la fila del mismo producto decía
"¡hoy mismo!" y la línea roja de abajo decía que ya era tarde. Tres afirmaciones
sobre un producto, una de ellas imposible, y la imposible en la frase más grande
de la pantalla.

`summary.order_before` es el `order_by` **más temprano** entre los productos en
riesgo, haya pasado o no. Ahora la frase toma el más temprano que **todavía no
pasó**; los que ya no llegan los cubre la línea roja, que es lo honesto que se
puede decir de ellos, y si ninguno llega a tiempo la frase desaparece.

### E2. Cambiar el multiplicador de un producto no volvía a simular

Poner SPIKE-01 en ×3,0 guardaba el override y dejaba la tabla mostrando ×2,0 y
311 unidades. El usuario cambia el número del que sale toda la pantalla y la
pantalla sigue contestando lo de antes, sin decir que está vieja.
`MultiplierExplainer` llamaba a `onEdited`, que recargaba **la lista de eventos**
de la pantalla de atrás y nunca la simulación. Ahora vuelve a simular, y a
propósito **no** borra el resultado anterior mientras tanto: el editor donde el
usuario está escribiendo solo se pinta cuando hay resultado, y vaciarlo se lo
quitaría de debajo de las manos.

### E3. El titular y el pie describían un cálculo que no ocurrió

Con el override aplicado, arriba decía "+100% de demanda" y el pie "× 4 días ×
2.0" mientras la única fila corría a ×3,0. Los dos números salían de
`ev.multiplier` —el del evento— y no de lo que se aplicó. Ahora salen de
`multipliers_applied`, que el backend ya mandaba: si todos los productos
comparten multiplicador, la frase lo nombra; si no, deja de afirmar un único
"+X%" y dice que cada producto lleva el suyo (cinco llaves nuevas de copy, es/en).

**Lo que no se pudo ver en pantalla:** el caso de multiplicadores mezclados se
verificó por API (200 SKUs, uno con override: `multipliers_applied` devuelve dos
entradas) y por código, no en el navegador — la sesión de esa prueba es semanal y
el tenant está en día, así que la pantalla no la toma sin cambiar el período.

**Lo que sigue sin caminar del simulador:** la pestaña "Calendario LatAm"
(sembrar el catálogo de temporadas), editar y borrar un evento, el multiplicador
por familia y por categoría, y el simulador en un tenant semanal o mensual.

---

## 6. Tecnología — tres huecos de infraestructura (2026-09-01)

Salieron de revisar qué tecnología falta para que la app sea mejor "en
general", no de caminar una pantalla. Verificado en el código, no adivinado:
no hay Sentry/structlog/OpenTelemetry en `backend/` (grep sin resultados), no
hay Redis/Celery (`workers/worker.py` usa `ThreadPoolExecutor` + la tabla
`jobs`), y la búsqueda de RAG **ya** corre sobre Pinecone + Voyage AI
(`backend/ai/rag_service.py`) — eso no falta, ya está.

### a) El login por formulario y la subida de CSV no tenían script — **[HECHO 2026-09-01]**

Corrección a lo que decía esta entrada: **no es cierto que Playwright
estuviera sin usar.** `Frontend/tests/smoke.mjs` existe desde el 2026-07-29 y
ya cubre regresiones de layout/render en 8 pantallas — pero inicia sesión
inyectando el token por `fetch('/api/auth/login')`, así que nunca ejercita el
**formulario** de `/login`, y no toca `/ventas` en absoluto. Ninguno de los
dos está en `run_tests.py`; se corren a mano, como ya hacía `smoke.mjs`.

Nuevo: `Frontend/tests/critical_flows.mjs`, mismo estilo (`playwright` crudo,
sin `@playwright/test` — esa dependencia no está instalada y no hacía falta
agregarla). Cubre lo que `smoke.mjs` no cubría:

- El formulario de `/login`: contraseña incorrecta se queda en la pantalla y
  muestra el error; la correcta entra y guarda un token real.
- Alta nueva (`/signup`, dominio `@faro-e2e.io`, no toca el tenant demo) →
  verificación de correo → login → subir `scripts/sample_sales.csv` en
  `/ventas` → llega al paso de mapeo de columnas, sin errores de consola.

**Un defecto de infraestructura de prueba, no del producto, encontrado
escribiéndolo:** `next dev` compila cada ruta la primera vez y adjunta los
manejadores de React recién después de hidratar. Un clic en el botón antes de
eso cae al **envío nativo del HTML**, un GET de página completa a
`/login?email=...&password=...` — la contraseña queda en la URL y en el
historial del navegador, y el estado de React se pierde. `submitFormSafely()`
lo detecta por la firma (`?` pegado a la misma ruta) y reintenta una vez tras
recargar. No se ve en producción (el build no tiene esta ventana de
hidratación en frío), pero confirma que **NO se debe interactuar con un
formulario antes de que termine de cargar** — vale para cualquier script que
toque estas pantallas.

**Sigue sin walkear:** generar una orden de compra desde el semáforo. Pedía
una sesión ya entrenada (datos reales, minutos de cómputo) para tener algo
sobre lo que generar la OC, y quedó fuera del alcance de esta pasada — se
puede retomar con una sesión sembrada por `seed_demo.py` en vez de entrenar
una desde cero.

Verificado corriendo `node tests/critical_flows.mjs` contra la app real
(backend :8011, frontend :5000, `faro_db` en :5544): 8/8 repetido.

**Pulido 2026-09-01, tras encontrar el choque con `demo@faro.app`:** el
bloque de login ya no reusa la cuenta demo — usa la misma cuenta fresca de
`@faro-e2e.io` que el resto de la corrida, así que dos corridas seguidas no
se pisan con el límite de 5 intentos/5min de `POST /auth/login`
(`auth.py:285`). Verificado dos veces seguidas sin pausa: 7/7 en ambas.

**Limpieza agregada:** `backend/scripts/cleanup_e2e_tenants.py` borra todo
tenant cuyo usuario sea `@faro-e2e.io` (`ON DELETE CASCADE` se lleva el
resto). El script de e2e la corre sola al final de cada corrida, best-effort.
Se encontraron y borraron **261 tenants** acumulados desde el 2026-07-27 —
no eran de esta sesión, eran meses de pruebas anteriores sin limpiar.

### b) No había registro de métricas de entrenamiento — **[HECHO 2026-09-01, tabla propia]**

Decisión del dueño: tabla propia en Postgres, no MLflow — cero infraestructura
nueva. `engine.get_metrics()` (`ForecastingCore/forecasting_core/engine.py:848`)
**ya** calcula `by_model`: MAE/RMSE/WAPE/bias/MAPE/SMAPE promedio por modelo,
en cada corrida — solo vivía en `session_results.training_result`, un JSONB
que la corrida siguiente **sobrescribe**. No había dónde comparar "el
LightGBM de esta sesión contra el de hace dos semanas".

Se agregó:
- Migración `create_training_run_metrics` (`backend/db/migrations.py`): tabla
  `training_run_metrics` (`tenant_id, session_id, model` + las seis métricas +
  `trained_at`), `UNIQUE (session_id, model)` — reentrenar la misma sesión
  sobrescribe su fila, igual que ya hace `session_results`, en vez de
  acumular duplicados.
- `backend/training/metrics_history.py` — `record_training_metrics()` hace el
  upsert; `list_metrics_for_tenant()` queda listo para cuando se decida
  mostrarlo en pantalla (eso **no** se hizo — es capacidad de UI nueva, fuera
  de esta pasada).
- `runner.py`, justo después de `engine.get_metrics()`: la escritura está en
  `try/except` **no fatal** — un fallo ahí jamás puede tumbar un
  entrenamiento, mismo patrón que el resto de escrituras no críticas de ese
  archivo (`Inventory stock sync failed (non-fatal)`, etc.).

**Verificado con DB real, no solo con el mock:** dos tests nuevos en
`test_integration_forecasting.py` corren el pipeline de entrenamiento
(mockeado) contra `faro_db` de verdad y hacen `SELECT` directo sobre
`training_run_metrics` — `test_e2e_training_records_model_metrics_history` (la
fila existe, con los números correctos) y
`test_retraining_the_same_session_overwrites_its_metrics_row` (reentrenar dos
veces deja **una** fila, no dos). Los 10 tests de ese archivo pasan.

**Lo que falta y es decisión aparte:** ninguna pantalla lee esta tabla
todavía. Mostrarla (en `/historial` o `/pronosticos`, como sea) es exactamente
el tipo de capacidad nueva que este documento pide anunciar antes de construir
— la tabla existe para que esa decisión ya tenga datos con qué trabajar.

### c) `storage/` no tiene backup declarado — **SIN ARREGLAR, decisión del dueño**

Los datasets subidos y los artefactos de modelos entrenados viven en disco
local bajo `storage/` (gitignored, confirmado en `CLAUDE.md`). No hay ninguna
rutina de respaldo en el repo: si el disco se corrompe o el contenedor se
recrea sin el volumen, se pierde tanto lo que subió el usuario como lo que el
entrenamiento produjo — no solo archivos, sesiones enteras quedan huérfanas.
Necesita una decisión del dueño sobre **el destino** (a dónde se copia) y
**la frecuencia**, porque las dos opciones razonables son de tamaño distinto:
un script de respaldo programado (barato, no cambia código de negocio) o
migrar el storage a algo S3-compatible (más grande, toca cómo se leen y
escriben los archivos en todo `backend/`).

---

## 7. Recorrido de /compras, /pedidos, /inventario (2026-09-01)

Primera pasada del pedido del dueño de validar el resto de pantallas, más
allá de login/subida. El conector `claude-in-chrome` no estaba disponible
(extensión no conectada), así que el recorrido usó Playwright headless con el
tenant demo sembrado — mismo mecanismo que `critical_flows.mjs`, sin dejar
script permanente por esta pasada.

**Lo que se verificó:** la carga inicial y el contenido visible de las tres
pantallas, con capturas de pantalla completas. **Lo que no:** los flujos
internos que ya lista la tabla de arriba (envío a proveedores, registrar
salida, nueva orden manual, etc.) — siguen sin caminar.

### /pedidos — sin hallazgos

Carga limpia con datos reales: 4 órdenes en los tres estados que produce el
demo (en camino, parcial, recibida), botones de acción visibles (Registrar
llegada, Enviar pedido, Abrir en WhatsApp, Copiar mensaje). Nada que reportar
en esta pasada.

### /inventario — sin hallazgos, un falso positivo descartado en el camino

El semáforo de 40 SKUs, los KPIs y los filtros por bodega cargan
correctamente. **Nota metodológica, no de producto:** una primera captura con
espera corta (6s) mostró la lista de SKUs completamente vacía bajo el
buscador — parecía un defecto de render. Con más espera (14s) todo apareció;
el contenido ya estaba en el DOM (confirmado con `getBoundingClientRect`:
`opacity:1`, tamaños reales, nada colapsado) — la captura simplemente se tomó
a mitad de un fetch todavía en vuelo. Se deja anotado porque cualquier script
futuro que camine esta pantalla puede caer en la misma trampa.

### /compras — un hallazgo real: el resumen ejecutivo compite con su propio timeout

`NarrativeCard` ("Resumen ejecutivo del día") llama a `POST
/ai/narrative/morning` (DeepSeek) en cada carga de la pantalla principal del
producto. Medido directo contra el backend: **8,8 segundos** de respuesta
real. El cliente (`compras/page.tsx:945-948`) le pone un timeout local de
**8000ms** antes de rendirse al texto de reglas — es decir, en el caso
*normal*, no en el degradado, el timeout local vence **antes** de que la
respuesta real llegue. El usuario ve el texto genérico de reglas por una
fracción de segundo y, al instante, se lo reemplaza el texto real de la IA
cuando la petición sí contesta — un parpadeo visible en la pantalla que un
comprador mira todas las mañanas.

Verificado en pantalla: con 14s de espera el recuadro seguía en "Analizando
datos…"; con 22s ya mostraba el análisis completo ("Situación actual",
"Riesgos prioritarios", "Oportunidades").

**Un segundo hallazgo relacionado, más chico:** el botón "Refresh" del mismo
recuadro (`onRefresh`, `compras/page.tsx:1522`) no tiene el timeout de 8s que
sí tiene la carga inicial — si DeepSeek está lento o caído, pulsar "Refresh"
deja el spinner girando sin salida hasta que la petición conteste o falle por
su cuenta.

**Qué se pregunta antes de tocar el código:** subir el timeout local a
~10-12s (menos parpadeo, un poco más de espera visible) vs. cachear la
respuesta por sesión/día (el resumen no cambia salvo que cambien los datos)
vs. dejarlo como está. **Decisión del dueño (2026-09-01): subir el timeout.**
Hecho — `compras/page.tsx:945-950`, de 8000ms a 11000ms. Verificado con
Playwright: en la corrida de confirmación, el recuadro pasó directo de "sin
cargar" a "Analizando datos…" (t=13,4s) al texto real de la IA (t=21,5s), sin
mostrar nunca el texto de reglas de por medio (`sawFallbackFirst: false`). El
hallazgo del botón "Refresh" sin ese resguardo **sigue sin tocar** — no era
lo que se pidió arreglar en esta pasada.

### /mi-cuenta — un hallazgo real: la columna izquierda deja un vacío del tamaño de la derecha

`mi-cuenta/page.tsx:1333` pone `<ProfileSection />` y el resto de las ocho
tarjetas (Uso y límites, Moneda, Zona horaria, Idioma/Tema, Granularidad,
WhatsApp, Mensajes del equipo, Seguridad) en un `grid` de `1fr 1fr`, dos
columnas, un solo renglón. `ProfileSection` es corta — nombre, correo, rol,
estado — mientras la columna derecha apila ocho tarjetas y es varias veces
más alta. CSS Grid estira ambas columnas al alto del renglón por defecto
(`align-items: stretch`, sin override), así que la columna izquierda queda
con una caja tan alta como la derecha y **nada que pintar en la mayor parte
de ella**: un usuario que baja la página ve una tarjeta corta a la izquierda
y, debajo, un vacío blanco del tamaño de toda la pantalla mientras la derecha
sigue mostrando tarjetas.

Verificado con capturas y con `getBoundingClientRect` sobre el nodo de
"Perfil de Usuario": el contenido existe y tiene tamaño real, simplemente
termina mucho antes que su columna. No es un defecto de datos ni de carga
— es el `grid` sin `align-items: start` (o sin repartir las tarjetas para
equilibrar el alto de las dos columnas).

**Decisión del dueño (2026-09-01): arreglarlo.** Hecho —
`mi-cuenta/page.tsx:1333`, se agregó `alignItems: 'start'` al grid.
Verificado con Playwright: antes del cambio la columna izquierda medía
2103px de alto (igual que la derecha, con todo ese espacio vacío bajo la
tarjeta de perfil); después mide 258px — su alto real, sin estirarse. Captura
de pantalla confirma la columna izquierda terminando naturalmente después de
"Perfil de Usuario" mientras la derecha sigue con sus ocho tarjetas.

### /pronosticos, /proveedores, /archivos — sin hallazgos

Las tres cargan completas, con datos reales y sin ningún API call fuera de
2xx. `/pronosticos` muestra el gráfico de forecast, el selector de modelo, la
tabla de métricas y el banner de "Encontramos problemas en tus datos" —
funcional. `/proveedores` (que CLAUDE.md marcaba como "nunca se abrió, solo
verificada por API") renderiza la tabla completa de proveedores con lead
time, variabilidad, aprendizaje y acciones — ahora sí caminada, sin
sorpresas. `/archivos` muestra la fuente de datos conectada ("Ventas Demo
Faro", 21.640 filas) detrás de un tour guiado de bienvenida que se dispara
en cada contexto de navegador nuevo (localStorage vacío) — esperado en un
script que no persiste sesión entre corridas, no un defecto para un usuario
real que ya lo cerró una vez.

**Los dos avisos de consola que aparecen en TODAS las pantallas caminadas
esta pasada** (no solo estas tres) — anotados aquí una vez porque son
sistémicos, no de una pantalla:
- `Warning: Extra attributes from the server: %s%s data-theme` — desajuste de
  hidratación de React sobre el atributo `data-theme` de `<html>`. Aparece en
  cada carga, incluido `/login`. No se investigó a fondo esta pasada; si
  vuelve a aparecer al caminar más pantallas, vale la pena rastrear su causa.
- `Failed to fetch RSC payload... Falling back to browser navigation` — esto
  es un artefacto del método de prueba (`page.goto()` hace una navegación
  dura entre rutas; un usuario real navega con `<Link>` del lado del
  cliente). No se reporta como hallazgo de producto.

---

## 8. Configuración de servicios: qué necesita el despliegue y qué se apaga sin ello (2026-09-13)

**Pedido del dueño (2026-09-10):** vender el código fuente. Que quede claro qué
variables de entorno necesita, que si falta una llave el servicio *simplemente
no se activa* —el chat, las alertas— y que todo eso se pueda configurar y
manejar cualquier error. La sesión de ese día construyó el núcleo y se cortó por
el límite semanal antes de conectar los consumidores; esto lo termina.

### Los tres huecos que tenía, y cómo quedaron

| Hueco | Cómo estaba | Cómo quedó |
|---|---|---|
| `.env.example` mentía por omisión | documentaba 20 de 45 variables | **generado** desde `backend/service_config/registry.py`, igual que `docs/configuracion.md`; un `Settings` sin descriptor pone la suite en rojo |
| Nadie podía ver el estado | `/health` decía `ok` con el chat, las alertas, el RAG y las integraciones muertos | `/health` reporta el estado de cada servicio y sobrevive a una base caída (`degraded`, no un 500 sin cuerpo); el panel `/instalacion` lo muestra con lo que se pierde en cada caso |
| La degradación era dispareja | notificaciones lo hacían bien, RAG e integraciones a medias, y «configurado pero fallando» no se distinguía de «sin configurar» | los diez consumidores leen por `service_config.resolver`; el estado `degraded` existe y sale de una prueba de conexión real |

### Lo que se construyó

- **Un registro único** (`registry.py`): 47 campos, cada uno con qué hace, si es
  obligatorio, si es secreto, si se puede editar desde la app — y **qué se
  pierde sin él**, escrito para quien tiene que decidir, no para quien programó.
- **Dos capas con precedencia visible**: entorno (piso) y panel (gana, cifrado
  en la base con la misma llave Fernet de las integraciones). El panel dice cuál
  manda en cada campo. Un secreto entra y no vuelve a salir: la lectura devuelve
  cuatro caracteres finales, y nada los revierte.
- **Prueba de conexión** por servicio, con códigos estables que llevan a acciones
  distintas: `auth_failed` (llave mala) ≠ `unreachable` (red) ≠ `timeout`
  (el proveedor aceptó y se calló). Ninguna prueba lanza excepción; reporta.
- **`/capabilities`** para cualquier usuario autenticado: booleanos, sin nombres
  de variables. Es lo que deja que una pantalla diga «el asistente no está
  disponible» *antes* de que alguien escriba, en vez de después de esperar.

### La decisión que no era mía y hay que saber

`admin` es un rol **dentro de un tenant**: toda empresa que se registra tiene
uno. Usarlo para editar las credenciales del despliegue habría dejado que
cualquiera que abre una cuenta reescribiera la llave de DeepSeek de todos. Se
agregó **`INSTANCE_ADMIN_EMAILS`** (solo entorno, nunca editable desde la
pantalla donde se pegan las credenciales). Vacío = nadie edita la configuración
de la instancia desde la app, y el panel lo dice nombrando la variable. Un
tenant sí configura **sus propios canales** (su remitente de correo, su número
de WhatsApp): el alcance sale del token, no del cuerpo del request.

### Lo que encontró caminarlo en el navegador

Tres defectos reales, los tres arreglados en el mismo paso:

1. **El aviso de «no hay modelo» vivía dentro del compositor**, que solo se
   dibuja con una conversación abierta. Un usuario llegaba a un estado vacío que
   invitaba a empezar, creaba la conversación, escribía la pregunta y *ahí*
   se enteraba. Subido por encima de la bifurcación: se ve antes del primer clic.
2. **Los códigos de error del panel no estaban en el catálogo**, así que el
   toast mostraba la prosa inglesa del backend. Nueve entradas nuevas en
   `translations.ts`, cada una nombrando el arreglo.
3. **A 400px las dos columnas exprimían el input al ancho de una palabra**: el
   campo estaba en pantalla y era imposible escribir en él. Ahora se apilan.

Y un cuarto que no es del panel: `test_endpoints.py` seguía exigiendo un reloj
de prueba (`trial_ends_at`) que el cambio de tiers del 2026-08-22 eliminó, y
`test_pagination_pages_are_disjoint_and_complete` creaba 5 sesiones contra un
techo gratis de 3. Dos rojos que acusaban al producto de bugs que no tenía.

### Cómo se verificó

- 47 tests nuevos (`test_service_config.py`, `test_service_config_i18n.py`):
  precedencia, tipos, secretos, refusals, el par de permisos en cada escritura,
  y que el catálogo de copy cubra exactamente las claves del registro.
- Caminado en navegador con la llave puesta **y sin ella**: guardar toma efecto,
  la etiqueta de origen cambia, vaciar vuelve al entorno, la prueba de conexión
  llega a DeepSeek, el tenant no ve las credenciales de la instalación, y con la
  llave quitada nada dio 500 — la app dice qué falta y sigue funcionando.

---

## Lo que se borró el 2026-08-11, y por qué

Siete documentos de planes, propuestas y auditorías ya ejecutados o superados.
Todos siguen en el historial de git; ninguno describía trabajo abierto:

| Documento | Por qué se fue |
|---|---|
| `plan_general_faro_2026-07-18.md` | Plan general superado; no lo citaba nadie |
| `features_propuestas_faro_2026-07-05.md` | Propuestas de features — justo lo que ya no se quiere |
| `auditoria_integral_faro_2026-07-04.md` | Auditoría de julio, ejecutada; sus hallazgos viven hoy como tests |
| `animaciones-plan.md` | Ejecutado. Su último pendiente (`pulse`/`slideUp` locales) está cerrado: ambos viven solo en `globals.css` |
| `pending-polish-2026-07-24.md` | Lista de pulido; su propio encabezado decía que nada era crítico |
| `friccion-onboarding-2026-07-27.md` | Ejecutado |
| `qa/2026-07-23-fullflow-walkthrough-findings.md` | Superado por `inventario-pantallas.md`, que cubre las 26 pantallas |

Los comentarios del código que citaban estos archivos se reescribieron para no
apuntar a rutas muertas.

**Lo que se conservó, y no es basura:** `api-publica.md` (documentación para el
cliente), `inventario-pantallas.md` (la tabla viva), `direccion-complemento-2026-08-10.md`
(la dirección de producto vigente), `demo-script.md`, `help/index.html` (la guía
bilingüe de usuario) y `paper/` + el PDF del motor. Las tres skills de
`.claude/skills/` —`faro-i18n`, `running-faro`, `silent-failures`— están
vigentes y CLAUDE.md las referencia.
