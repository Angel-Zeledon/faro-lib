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

## 1. Un bug confirmado, sin arreglar, que afecta a toda la app

### `ConfirmDialog` pierde la promesa de la primera confirmación

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

## 3. Tres pantallas prácticamente sin caminar, y no por descuido

Un muro de plan lo impide. El tenant de prueba es `professional`, y estas
necesitan otra cosa:

- **`/escenarios`** — solo se vio el muro de plan de un tenant Starter.
- **`/integraciones`** — solo el muro. **Nada** del flujo real: conectar, probar
  credenciales, sincronizar, ver el error cuando las credenciales fallan.
- **`/proveedores`** — verificada solo por API. La pantalla nunca se abrió; alta
  y edición de proveedor sin tocar.

**Decisión del dueño:** destrabarlas exige cambiar el plan del tenant de prueba.
Toca datos de prueba, así que no se hace sin luz verde.

---

## 4. Lo reciente, todavía sin ver en un navegador

- **El rediseño de `/api`** (commit `22fd046`): banda de cabecera, barra de clave
  fija, raíl de endpoints, secciones a dos columnas. Compila; nadie lo ha visto.
- **El arreglo de `log-po`** (commit `7866c64`): que un cuerpo vacío avise que va
  a pedir **todo** el semáforo accionable, en vez de hacerlo callado.
- En `/api` nunca se ejecutó **una escritura hasta el final**, ni la subida de
  archivo, ni `train`, ni la vista angosta, ni el caso de plan sin `api_access`.

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

**Ninguno de estos está arreglado.** Varios son un guardia de una línea con
precedente en el propio repo; otros necesitan una decisión de producto sobre qué
debería significar el número, y esos no se tocan sin que el dueño decida.

## Nivel 1 — el usuario pierde plata actuando sobre esto

### 1.1 La alerta de las 8:00 UTC lee la sesión activa al grano equivocado [verificado]

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

### 1.2 Faro inventa `current_stock = 0` y luego marca en rojo mercadería en bodega [verificado]

`SetupGapsPanel.tsx:85` — `upsertInventoryStock(sku, { current_stock: 0, ...body })`.
Si el usuario solo llenó el costo, el cero persiste. No existe columna
`current_stock_set_by`, así que nada distingue un cero inventado de uno contado:
`coverage_days = 0` → **PEDIR_YA**.

Treinta filas costeadas de producto del que hay pallets llenos → treinta órdenes
de emergencia por inventario que ya está en la estantería. Lo mismo al importar
una lista de precios sin columna de stock, y el asistente reporta puras buenas
noticias. El camino de sincronización de dataset **sí** tiene el guardia
(`service.py:465-474`); el panel de huecos y `/inventory/bulk` lo esquivan.

### 1.3 El lead time nunca se reporta como faltante, y la pantalla promete que es obligatorio

La copy (`stockSetup.ts:18`): *"necesita tres datos más: cuánto tienes, cuánto te
cuesta y cuántos días tarda en llegar. Mientras falten, ese producto no aparece
en el semáforo."*

`setup_gaps_service.py:118-137` solo comprueba `unit_cost`, `sale_price` y
`supplier`; `BLOCKING_FIELDS = ("stock", "cost")`. El SKU **sí** aparece en el
semáforo, planificado sobre 15 días inventados. Un importador con 45 días de
tránsito reordena 30 días tarde en sus mejores vendedores, ciclo tras ciclo. La
columna `lead_time_set_by` que permitiría avisarlo existe y no se lee.

### 1.4 La primera migaja de un envío parcial fija el lead time del proveedor [verificado]

`reception_service.py:330` calcula `lead_days` desde **ese** evento, y el candado
`already_observed` (`:337-343`) hace que las entregas siguientes de esa OC no se
midan nunca.

OC de 5.000 unidades el 1 de agosto; llegan 20 de muestra el 3; el resto el 10 de
septiembre. Faro aprende **2 días**. A la tercera OC así, el valor aprendido pisa
al declarado para todos los SKU de ese proveedor, el punto de reorden se
desploma, y el scorecard muestra "real 2d vs declarado 30d" con 100% a tiempo.

Ningún código revisa la observación cuando la OC se completa.

### 1.5 Registrar "no llegó nada" deja la OC muerta para siempre [verificado]

`reception_service.py:312` — si ninguna línea recibe cantidad, el estado pasa a
`not_received`, que no está en `RECEIVABLE_STATES` (`:39`), así que el guardia de
`:111-118` devuelve 409 para siempre. La OC sale además de las recepciones
atrasadas, de `isAwaitingReception` y de `get_incoming_qty`.

Cuando la mercadería llegue no hay forma de registrarla: ni stock, ni observación
de lead time. "Todavía no llegó" y "no va a llegar nunca" son el mismo estado
terminal. Se alcanza desde la interfaz poniendo ceros en las cantidades.

### 1.6 El horizonte se construye en días; la demanda y el lead time que van dentro, en períodos [verificado]

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

### 1.7 El MOQ se aplica como múltiplo, no como mínimo — y sin guardia de sobrestock

`service.py:948-949` — `raw = ceil(raw/moq) * moq`. Necesitas 520 con MOQ 500 →
recomienda **1000**, 92% de sobrepaso, con botón de convertir en OC.

Sin guardia de cobertura: SKU que vende 2/día con MOQ 1000 de contenedor →
recomienda **500 días de stock**, y al refrescar sale SOBRESTOCK.
`price_break_service.py:263-265` implementa exactamente esa comprobación y
rechaza un salto de 140 días por `would_overstock`.

No existe concepto de tamaño de empaque (`pack_size`) en ninguna parte.

### 1.8 "La recomendación siempre es múltiplo de este número" es falso en todos los caminos que llegan a una OC

`translations.ts:2985` y `:2996` afirman "siempre". El redondeo se aplica en un
solo punto (`service.py:948`) y nunca se re-aplica. Persisten `final_qty` sin él:
la edición de cantidad en la tabla, el optimizador ("Convertir en OC"), el salto
de precio (fija la cantidad al `min_qty` del escalón), el carrito manual,
`POST /log-po` y `bom_service`. El camino de traslado usa **floor**, no ceil.

MOQ 12 con escalón a 100 → el carrito queda en 100; el proveedor factura 108.

## Nivel 2 — números que no significan lo que dice su etiqueta

### 2.1 La tasa de adopción de `/impacto` usa un denominador que su copy contradice [verificado]

La copy: *"Seguiste N de M recomendaciones que **Faro te puso en frente**"*
(`translations.ts:2222`). `M` es `total_suggested`, que solo cuenta líneas que
llegaron a `log_po_generation`; `/compras` filtra `status !== 'pending'` antes de
registrar, con el comentario "el comprador nunca actuó sobre ellas".

Faro recomienda 20, apruebas 3, rechazas 1, ignoras 16 → **"75%, seguiste 3 de
4"**. La proporción real es 15%. Cinco veces inflada, siempre hacia el lado
halagador. El docstring del backend lo describe bien; la pantalla afirma algo más
fuerte. Y el pie llama a esta cifra "la métrica más honesta de valor".

### 2.2 Dos botones de `/inventario` fabrican 100% de adopción desde una descarga [verificado]

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

### 2.3 "Capital liberado de sobrestock" atribuye a Faro una resta que nadie atribuyó

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

### 2.4 "Riesgos de quiebre atendidos" cuenta líneas y afirma puntualidad que nadie mide

`roi_service.py:246` — `SUM(skus_order_now)` sobre todas las OC. 30 SKU urgentes
pedidos mensualmente durante un año se leen como **360**. Nada comprueba "a
tiempo". La tarjeta del recap lleva el matiz correcto; el titular, que es la
superficie más grande, afirma lo contrario.

### 2.5 Las órdenes que nunca llegaron cuentan como gestionadas

Ninguna consulta de `/impacto` filtra `reception_status`. Una OC de ₡12M nunca
entregada sigue como 40 riesgos atendidos y ₡12M gestionados. El scorecard, una
pantalla más allá, **sí** excluye las no recibidas.

### 2.6 "Compras gestionadas" tira en silencio las líneas sin costo unitario

`roi_service.py:95-101`. El caso "ninguna línea tiene costo" se resuelve
honestamente. El caso **parcial** no: 40 líneas con 6 costeadas reportan esas 6
como el total. ₡30M pueden leerse como ₡2,1M, con pinta de exacto.

### 2.7 "% a tiempo" del scorecard puntúa contra un declarado que nadie declaró

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

### 2.8 "Valor comprado" muestra un ₡0 confiado donde `/impacto` diría "no disponible"

`reception_service.py:398` — `SUM(final_qty * unit_cost)`; un costo NULL anula el
producto y SQL lo descarta. Un proveedor al que le compraste ₡40M sin costos
registrados lee **₡0** en verde. `roi_service.py:538-540` aplica la regla
opuesta para la misma cantidad. Mismos datos, dos políticas, una pantalla de
distancia.

## Nivel 3 — el mismo número, dos autoridades

### 3.1 "Exportar OC" de `/inventario` descarga cantidades distintas de las de la tabla

La tabla es consciente del período (`inventory.py:701`); el export
(`:2511`) llama a `get_inventory_status` **sin período** y re-deriva la lista en
el servidor. Mismo inquilino semanal del 1.1: la pantalla no ofrece nada que
pedir y el CSV trae 100 unidades, más una OC en `/pedidos` que el comprador nunca
vio.

Misma recomputación sin período, menor alcance: el PDF (`service.py:2536`, que
**no tiene** parámetro `period`), dead-stock, el carrito de respaldo de escalones
de precio, el disparo de prueba de alertas, `dashboard-summary`,
`production-requirements` y `simulate_event_impact`.

### 3.2 La misma fila de `/inventario` muestra dos "demanda LT" distintas [verificado]

`service.py:1432` — `avg_daily * lt_periods`, que alimenta el punto de reorden.
`service.py:1563` — `avg_daily * lead_time`, en días de calendario. **Las dos en
el mismo diccionario.** La columna "Demanda LT" pinta la segunda; expandir la
fila muestra la primera. Semanal, 10/semana, 14 días: la columna dice **140**, el
desglose dice **20**. Factor 7 en semanal, 30 en mensual. El CSV usa la de la
columna, así que contradice al desglose.

### 3.3 El bot de WhatsApp responde sobre otra sesión, a otro grano y desde otro modelo

`whatsapp/tools.py:62,106` usa `get_latest_completed_session` —un
`ORDER BY updated_at DESC LIMIT 1`— en vez de `resolve_active_session`. Es el
**único** punto de todo el producto que se salta el resolvedor. `:65` no pasa
período. Y `:114` hace `next(iter(models.values()))`: **el primer modelo del
diccionario**, que es exactamente el bug de `/pronosticos` ya arreglado en
`forecasts.py:512` y nunca portado aquí.

### 3.4 `/pronosticos` corona al campeón con una regla distinta de las tres del servidor

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

### 3.5 El estado agregado y el por bodega resuelven el proveedor distinto

`service.py:1355-1357` — `stock.supplier or primary.supplier_name`.
`service.py:1696` — `stock.get("supplier")`, **sin el respaldo del primario**.
Esa palabra propaga a cuatro resoluciones: lead time aprendido, y las reglas de
`lead_time_days`, `moq` y `service_level`.

Un SKU con proveedor en blanco en la fila y "Acme" como primario: la pestaña
"Todas" da **PEDIR_YA** con Acme en la fila; la pestaña de la bodega da
**PEDIR_PRONTO** con proveedor vacío. Un SKU, una bodega, dos señales en dos
pestañas de la misma página.

### 3.6 El optimizador ignora todas las entradas de proveedor que usa el semáforo

`optimizer_service.py:220-230` lee `inventory_stock.lead_time_days` crudo —sin
regla, sin procedencia, sin lead time aprendido— y el MOQ no se pasa: no existe
campo de MOQ en `OptimizationInput`. `/hoy` planifica sobre 45 días *"aprendido
de tus recepciones"* y MOQ 500; `/planning` resuelve sobre 15 y 137 unidades.
Ambas pantallas ofrecen convertir en OC.

### 3.7 "Cuál es la bodega por defecto" tiene dos respuestas

`warehouse_service.py:145-148` mira la bandera `is_default` y luego el nombre;
`service.py:1222` solo el nombre, porque solo tiene filas de stock a mano. El
docstring de la clave afirma que ambas responden igual "en todas partes". No.

Si la primera bodega es "Bodega Sur" y "principal" se creó después, el 100% de la
demanda va a Bodega Sur mientras la fila agregada toma costo, lead time, MOQ y
proveedor de principal — y con ellos el "valor en bodega" del titular.

## Nivel 4 — afirmaciones sin respaldo

### 4.1 "Serie limpia — sin advertencias" es estructuralmente incapaz de decir otra cosa [verificado]

`runner.py:135` fija `"date_freq": None` (solo se lee, nunca se asigna) y
`quality.py:249` devuelve 0 si no hay frecuencia. Para **toda** sesión entrenada
`missing_dates = 0`, la advertencia nunca se dispara y el puntaje nunca pierde
sus 20 puntos: el piso queda en 0,65 contra un umbral de "Baja" de 0,45, así que
**"Baja" es inalcanzable**. El pie del gráfico del mismo SKU puede decir "7
huecos detectados" a una pestaña de distancia.

También incondicional en ese pie: la palabra "interpolado". El valor por defecto
es `gap_fill = "leave"` — no se rellena nada.

### 4.2 `/escenarios`: "Atraso de proveedor" no hace nada, y reporta eso como resultado

`scenarios/service.py:304-319` escribe el lead time en la fila pero nunca pone
`lead_time_set_by`, y `stock_defaults_service.py:314-318` solo honra el valor de
la fila si esa columna está puesta. Para todo SKU cuyo lead time no se configuró
a mano —la mayoría— gana la regla de proveedor o el default de 15.

Simulas "mi proveedor se atrasa 21 días" antes de temporada alta, la pantalla
responde *"Este escenario no cambia ninguna decisión de compra"*, no compras por
adelantado, y quiebras.

### 4.3 `/pedidos` en móvil afirma "todos" sobre una ventana de 50

`'mobile.pedidos_awaiting_none'` dice *"ya registraste la llegada de **todos** tus
pedidos"*. La consulta es `ORDER BY generated_at DESC LIMIT 50`: la orden 51 en
`pending` es invisible, y un distribuidor que genera una OC diaria pasa de 50 en
menos de dos meses. Además `not_received` cae en "cerradas", así que la pantalla
afirma la *llegada* de mercadería que demostrablemente no llegó.

### 4.4 `/inventario`: "Todo el inventario está bien cubierto" ignora `SIN_DATOS`

La frase se empuja cuando no hay líneas de acción y hay algún OK. `summary.sin_datos`
viaja en el payload y no se lee. Cinco SKU en OK y 500 sin registro de stock
producen una frase verde afirmando que *todo* el inventario está cubierto.

Adyacente: `service.py:1414` pone `coverage_days = 9999` cuando la demanda media
es 0, lo que da **SOBRESTOCK**, y luego la cobertura se anula para mostrar. La
fila queda con insignia azul de sobrestock, cobertura "—" y la leyenda "considera
pausar el pedido". "No sabemos" pintado como "tienes de sobra".

### 4.5 La landing lleva quince cifras de resultado sin fuente, y dos afirmaciones falsas

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

### 4.6 El traslado afirma un plazo y una comparación de precio que no hizo

`transfer_lane_service.py:14-18` resuelve un par sin configurar a **1 día y costo
cero**, y lo dice: *"deliberadamente optimista"*. El carril resuelto lleva
`is_default: True` y `_evaluate_transfer_lane` nunca lo mete en `params`, así que
la UI no puede distinguir un carril medido del inventado. Peor: cuando no hay
costo unitario el test de precio se salta entero, `saving` queda en null — y el
código igual devuelve `reason_code = "transfer_faster_and_cheaper"`, así que la
frase sigue afirmando *"cuesta menos que comprar"*. Retiene la cifra y mantiene
la afirmación.

### 4.7 Más, cortas

- **`50% anticipo` se lee como 50 días de crédito.** `cash_service.py:34-37`
  busca `anticipad`, no `anticipo`. Cae al extractor de números → 50. Reporta
  `terms_known: True`, contradiciendo la promesa del módulo de que lo ilegible
  queda en None. Misma familia: `2x30` → 2, `30/60/90` → 30.
- **Dos escalas de precio de proveedores distintos se fusionan.**
  `price_break_service.py:298-312` agrupa solo por SKU y nombra al dueño del
  escalón más bajo. El panel puede decir "Andina: sube a 500 y ahorras ~1400"
  sobre un precio que Andina nunca ofreció.
- **No se puede reactivar un proveedor.** La baja es lógica, no hay ruta de
  reactivación en ninguna parte, y el índice único no excluye inactivos: volver a
  darlo de alta con el mismo nombre da un **500 genérico**.
- **Dar de baja un proveedor saca del control de caja plata que aún debes.**
  `cash_service.py:132-138` solo carga activos, así que una OC enviada e impagada
  pasa a `unknown_terms` y sale de `committed_total`. Y sus SKU vuelven a 15 días
  mientras una regla de `stock_defaults` con el mismo nombre sigue aplicando: las
  dos mitades de "el lead time de este proveedor" reaccionan en direcciones
  opuestas.
- **`unit_cost = 0` vuelve un SKU invisible para el optimizador, sin marcarlo.**
  Todos los coeficientes quedan en cero, así que dejar su demanda sin cubrir es
  gratis para el minimizador, y `assumed_unit_cost` no se activa porque comprueba
  `is None`. `/hoy` lo pinta PEDIR_YA; `/planning` lo omite.
- **Dos compradores pueden recibir dos planes distintos del optimizador.** El
  motor permite 2 solves concurrentes en **un** hilo, y la espera incluye la cola:
  el segundo cae al atajo voraz. Ambos ven "el plan de optimización"; solo uno
  ve el aviso.
- **El aviso de "esto es el atajo" está dentro del bloque que solo se pinta si
  hay órdenes o traslados.** Un atajo que no encuentra nada no muestra nada. "No
  hay nada que hacer", "degradamos a una regla más simple" y "el problema era
  demasiado grande" se ven idénticos: un espacio en blanco.
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

1. Cerrar la suite completa limpia (`python scripts/run_tests.py`, sin servidores
   de desarrollo arriba).
2. Arreglar `ConfirmDialog` — los tres defectos. Es bug, no opción.
3. Caminar `/api`: el rediseño, una escritura de punta a punta, subida y `train`.
4. Bajar por la tabla de la sección 2, pantalla por pantalla, arreglando lo que
   aparezca y anotando la caminata en `inventario-pantallas.md`.
5. Con luz verde: subir el plan del tenant de prueba y caminar las tres de la
   sección 3.

---

## Dos cosas que son decisión del dueño, no arreglos

- **Subir el plan del tenant de prueba**, para caminar escenarios,
  integraciones, proveedores y los muros desde el otro lado.
- **Tests end-to-end del frontend.** Es la única forma de que "sin bugs" se
  sostenga en el tiempo en vez de repetirse a mano en cada cambio. Pero es
  **capacidad nueva**, no un arreglo, así que no se empieza sin que se pida.

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
