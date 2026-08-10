# Inventario de pantallas — qué está caminado y qué no

**Creado:** 2026-08-06

Este documento existe porque "ya lo verifiqué" no significaba nada. Cada pasada
cubría las pantallas que alguien recorrió ese día, y las demás **no quedaban
limpias: quedaban desconocidas**. Sin esta lista, "cuánto falta" es una
sensación; con ella es un número.

## Qué cuenta como "caminada"

Abrir la pantalla **no** cuenta. Una caminata es:

1. Hacer clic en cada acción de la pantalla, no solo en la principal.
2. Mirar cada número que aparece y preguntarse **de dónde sale y si es cierto**.
   La mayoría de los defectos de este producto no lanzan error: muestran un
   número equivocado con toda calma.
3. Provocar el camino infeliz al menos una vez — el archivo mal formado, el rol
   sin permiso, la red caída, el plan que no incluye la función.
4. Anotar aquí la fecha y **qué se miró**, no solo que se miró.

Una pantalla caminada por un camino no queda caminada por los demás. La columna
"qué falta" es la parte honesta de la tabla.

## Estado al 2026-08-06

`Acciones` = cantidad de `onClick`/`onSubmit` en la página. Es una medida burda
del tamaño de la superficie, no de su riesgo.

| Pantalla | Ruta | Acciones | Última caminata | Qué se verificó | Qué falta |
|---|---|---:|---|---|---|
| Inventario | `/inventario` | 60 | 2026-08-09 | Semáforo, pestaña por bodega, edición de stock, "Todas" de solo lectura, etiqueta "Aún no", hint de importación; edición masiva de stock/lead time y semáforo recalculado con datos reales (cobertura, cantidad a pedir) | Registrar salida, inmovilizado, exportar PDF, vista Proveedor, eventos y temporadas, importar CSV de stock |
| Pronósticos | `/pronosticos` | 17 | 2026-08-10 | Aviso de calidad y sus 5 detalles; pestañas Forecast / Cómo se vende / Métricas / Calidad / Inventario; granularidad D/W/M/Q/Y (la agregación es coherente: 180 → 26 → 6 → 2 → 1 puntos y el promedio escala); multi-selección de modelos y banda de confianza; buscador; comparación de dos sesiones lado a lado; panel de backtest; tabla de métricas contra la API | Las tres exportaciones (Excel por SKU, "Todos los SKUs", PDF), pantalla completa, el tutorial, "Ver análisis estadístico detallado". **Cuatro hallazgos, uno arreglado y tres abiertos — ver abajo** |
| Archivos / Fuentes | `/archivos` | 40 | 2026-08-09 | Vista previa (archivo cp1252 con acentos intactos — lector distinto al del entrenamiento); editor de columnas y filas con las 360 filas; "Guardar como nuevo"; renombrar (persiste `Ñ`, `ú` y guion largo); eliminar con confirmación que nombra el archivo y limpia base **y disco**; pestaña Análisis | Conectar fuente SQL ("Nuevo elemento"), "Reemplazar archivo", buscador, tutorial de 9 pasos, correr un Análisis completo |
| Panel de compras | `/compras` | 14 | 2026-08-10 | Optimizador (horizonte, transferencias sin ciclos, explicación vs semáforo); aprobar y rechazar recomendaciones; carrito de aprobados; generar OC (queda en la base: 348 und, ₡417 600); resumen ejecutivo con datos reales; **permisos ejercidos con viewer y admin reales, en ancho normal y angosto** (ver abajo) | Envío a proveedores, selección de bodega destino, edición de cantidades, deshacer aprobación. El gate de "Crear transferencia" quedó **sin caminar**: la sesión activa no trae sugerencias de traslado en el briefing |
| Mis ventas | `/ventas` | 11 | 2026-08-09 | Subida, mapeo, gate con remediaciones, entrenamiento completo; **archivo cp1252 con `;`, fechas dd/mm/yyyy y SKUs acentuados** — acentos intactos y día-primero resuelto solo | Reusar archivo ya subido, repetir carga anterior, datos de ejemplo, cancelar a media corrida |
| Mi cuenta | `/mi-cuenta` | 23 | 2026-08-06 | Zona horaria (lectura y cambio), lista de modelos | Moneda, WhatsApp, cambio de contraseña, tema/idioma, granularidad, registros de actividad |
| Landing | `/` | 8 | 2026-08-10 | **Las promesas funcionales contrastadas contra el código**: "a la tercera recepción" = `MIN_LEAD_TIME_OBSERVATIONS = 3` exacto, y "viene en todos los planes" es cierto (no hay gate); los CTA apuntan a `/signup?demo=1`, que signup sí lee y enlaza con `demo_quickstart` | Correr el demo de verdad (crearía otro tenant), navegación por anclas, formulario de contacto, vista móvil. **La fila de cifras del hero está sin respaldo — ver abajo** |
| Asistente IA | `/asistente` | 14 | — | — | Todo (necesita Ollama o clave Anthropic) |
| Usuarios | `/usuarios` | 19 | 2026-08-09 | Crear usuario con rol (queda `pending_confirmation`, sin verificar); filtros de estado y rol; **permisos ejercidos como viewer real**: escrituras rechazadas con 403 y estado sin cambiar (ver abajo) | Editar usuario, suspender/reactivar, cambiar rol de otro, reenviar invitación, no poder degradarse a sí mismo |
| Escenarios | `/escenarios` | 6 | 2026-08-06 (parcial) | Solo el muro de plan para tenant Starter | La pantalla entera con un plan que la incluya |
| Automatización | `/automatizacion` | 14 | 2026-08-06 | Programaciones armadas, historial, zona horaria, re-anclaje | Llaves de API, webhooks, pausar/eliminar programación |
| Proveedores | `/proveedores` | 6 | 2026-08-06 (solo API) | Campos de lead time aprendido/inutilizable | La pantalla; alta y edición de proveedor; scorecard |
| Impacto | `/impacto` | 0 | 2026-08-10 | **Los cuatro números de portada reconciliados contra la base uno por uno** (ver abajo); resumen mensual; tabla de evolución; estados vacíos | Tutorial, enlaces de navegación, un mes con capital liberado real (necesita dos mediciones mensuales seguidas) |
| Integraciones | `/integraciones` | 3 | 2026-08-10 (solo el muro) | Muro de plan para tenant Starter, y **verificado contra el código que lo que promete existe**: conectores Alegra/Siigo reales, credenciales cifradas, y `run_daily_integration_syncs` corriendo desde el bucle diario del worker | **La pantalla entera con un plan que la incluya**: conectar, probar conexión, sincronizar, ver errores de credenciales. Nada del flujo real está caminado |
| Mensajes | `/mensajes` | 4 | 2026-08-10 | Lista de conversaciones, abrir una (**marca leído de verdad en la base**), enviar — el mensaje llega a `direct_messages` con acentos, guion largo y € intactos, y queda no-leído para la destinataria | Buscar persona, iniciar conversación nueva, mensajes largos, adjuntos si existen |
| Registro | `/signup` | 2 | 2026-08-09 | Alta completa (tenant + admin), rechazo por WhatsApp duplicado sin dejar filas varadas, aviso honesto cuando no se puede enviar el correo | Correo duplicado, validaciones de contraseña una por una, reenvío de verificación |
| Recuperar contraseña | `/forgot-password` | 4 | 2026-08-10 | Los **3 pasos completos**: correo desconocido (no filtra si la cuenta existe), código equivocado, código válido, contraseña corta, contraseñas que no coinciden, cambio exitoso y redirección. Verificado en base: OTP quemado (`used=t`), refresh revocado, contraseña vieja rechazada, nueva aceptada | Reenviar código ("Prueba de nuevo"), OTP vencido, límite de intentos |
| Historial | `/historial` | 7 | 2026-08-09 | Motivo de fallo en sesiones fallidas; lista completa con archivo, horizonte, granularidad y SKUs | Renombrar, eliminar, comparar. **"Activar sesión" no existe** — la sesión activa se deriva de la familia más nueva + el período activo, no se elige; estaba mal listada como acción pendiente |
| Scorecard proveedor | `/proveedores/scorecard` | 0 | 2026-08-10 | Tabla completa con datos reales (recepciones, lead time real vs declarado, tendencia, % a tiempo, fill rate, valor comprado) | Un proveedor con entregas que sí midan algo; ordenar/filtrar si existe. **Dos cifras se presentan sin el matiz que la propia app aplica — ver abajo** |
| Iniciar sesión | `/login` | 3 | 2026-08-06 | Login de tres cuentas con roles distintos | Credenciales malas, cuenta suspendida, cierre entre pestañas (verificado por evento, no con dos pestañas reales) |
| Pedidos | `/pedidos` | 3 | 2026-08-09 | Lista con OC generada (número, urgentes, unidades, estado "En camino"); registrar llegada **parcial** — suma solo lo recibido y deja la OC en `partial` | Llegada completa, nueva orden manual, enviar pedido, WhatsApp (abrir/copiar/enviarme), recibir de más |
| Planes | `/planes` | 0 | 2026-08-10 | **Los límites anunciados contrastados contra los que el backend aplica** (`entitlements/plans.py`: 1000/2/1, 5000/10/5, ilimitado) — coinciden exactos; "Tu plan actual" cae en la tarjeta correcta (tenant `professional`); los dos CTA son `mailto:` reales, coherentes con "el cobro automático llega pronto" | Verlo desde un tenant Starter y desde uno Enterprise; el aviso al chocar contra un límite |
| Restablecer contraseña | `/reset-password` | 2 | 2026-08-10 | Sin token (avisa y **deshabilita** el botón — no ofrece lo que no puede cumplir); con un token real del propio producto: cambio exitoso, contraseña vieja rechazada, sesiones cortadas; **replay del mismo enlace** (ver abajo) | Token vencido, token de otro propósito, enlace por correo — **que hoy nadie envía** |
| Verificar correo | `/verify-email` | 1 | 2026-08-09 | Token válido activa la cuenta y habilita el login | Token vencido, token ya usado, token manipulado |
| Configurar inventario | `/configurar-inventario` | 0 | 2026-08-10 | Lista priorizada por plata; guardar una fila completa (**"12,50" se guarda como 12.5**, como promete el copy); barra de avance y su recálculo; la promesa central verificada de punta a punta — el producto configurado entra al semáforo (`PEDIR_PRONTO`) y el otro queda `SIN_DATOS` | Subir archivo ("Elegir archivo"), guardar filas incompletas, el tutorial, el caso de catálogo grande |

**Resumen honesto (2026-08-10):** 24 pantallas de 25 tienen alguna caminata, y
ninguna está caminada entera. La única **sin medir** es `/asistente`, que
necesita Ollama corriendo o una clave de Anthropic.

Descartado al comprobarlo en `/planes`, para que nadie lo persiga: parecía que
dos tarjetas decían "Tu plan actual". Es una lectura mía del texto aplanado —
las dos ocurrencias viven dentro de la misma tarjeta, la correcta.

Lo que sí quedó cubierto de punta a punta el 2026-08-09 es **la cadena que
produce el dinero**, con un tenant nuevo y datos propios: registro → verificar
correo → login → subir ventas (archivo cp1252 con `;` y fechas dd/mm/yyyy) →
entrenar → semáforo → registrar stock → aprobar recomendación → generar OC →
registrar llegada parcial → stock actualizado por lo recibido, no por lo pedido.
Cero errores de consola, cero 500 y cero violaciones de FK en todo el recorrido.

## Permisos: qué se probó con un viewer real (2026-08-09)

Se creó un usuario `Solo lectura` desde la pantalla, se lo activó y se entró con
él. **El backend cumple**: `PUT /inventory/stock/{sku}`, `POST /users`,
`DELETE /tenant` y `POST /inventory/log-po` devuelven 403, y se verificó en la
base que **nada cambió** — el stock siguió en 40/406/300, no se creó el usuario
que se intentó colar, y el tenant sigue vivo. El menú lateral tampoco le muestra
"Usuarios". Esa parte está bien.

Lo que **no** está bien es lo que ve el usuario antes y después del rechazo:

1. `/inventario` le ofrece a un viewer toda la barra de escritura — "Actualizar
   stock", "Registrar salida", "Inmovilizado", "Agregar bodega", "Importar CSV"
   — le deja abrir el editor, teclear un valor y llegar hasta un botón verde y
   habilitado que dice "Guardar 1 cambio". El rechazo llega recién al guardar.
2. El aviso del rechazo **dice otra cosa que lo que pasó**: el título es "No
   tienes permiso para ver esto" cuando fue una escritura, y se contradice con
   su propio cuerpo ("Tu rol no puede hacer esto"). El usuario estaba viéndolo
   perfectamente; lo que no pudo fue guardar.
3. El segundo aviso, "Guardado incompleto — Revisa e intenta de nuevo", promete
   una salida que no existe: reintentar no va a funcionar nunca, porque no es un
   dato malo sino un permiso. Eso manda al usuario a repetir algo inútil.

Ninguno es un agujero de seguridad — el backend no cede. Son mentiras de copy y
una superficie de edición ofrecida a quien no puede usarla.

**Los tres quedaron arreglados el 2026-08-09**: `states.err_permission_title`
dejó de decir "ver" y dice "Tu rol no permite esta acción"; el guardado de stock
distingue un 403 de un dato malo y en ese caso avisa que reintentar no sirve; y
`/inventario` ya no le ofrece a un viewer el editor, "Registrar salida",
"Agregar bodega" ni la importación de CSV (que es escritura, aunque el botón
solo diga "CSV"). Las lecturas —plantilla, exportaciones, PDF— siguen para todos,
y el admin conserva todo, verificado entrando con ambos roles.

## `/archivos`: la lista miente después de guardar (2026-08-09, arreglado el 2026-08-10)

Al usar "Guardar como nuevo" en el editor, la fuente **sí se crea** —está en la
base, en disco, y se abre en el panel derecho— pero la barra lateral **no se
refresca**: sigue diciendo "1 FUENTE" y listando solo el original. Pulsar el
botón de refrescar muestra las dos. No se pierde nada; lo que falla es que la
pantalla afirma un número que no es cierto justo después de una acción exitosa.

**Causa, encontrada el 2026-08-10:** la misma pantalla tiene dos caminos que
crean una fuente y solo uno estaba bien cableado. La materialización SQL avisa
por `onDatasetCreated`, que la agrega a la lista; el editor de hoja de cálculo
avisaba por `onUpdated`, que recorre la lista buscando el id **y no encuentra
nada**, porque el id es nuevo. Por eso seleccionaba la copia a la derecha sin
sumarla a la izquierda. Ahora el editor usa el mismo canal que el camino SQL.
Caminado: la barra pasa de "4 FUENTES" a "5 FUENTES" con la copia arriba, sin
tocar refrescar, y la copia está en `datasets` con sus 360 filas.

Descartado al comprobarlo, para que nadie lo persiga: el contador **sí**
pluraliza ("2 FUENTES"). Pareció un fallo por una regex mía que hacía match
parcial, no por el producto.

**Lo que seguía abierto, encontrado al verificar lo anterior:** `/compras` tenía
el mismo patrón — le mostraba "Aprobar" y "Rechazar" a un viewer, y al generar la
orden el `POST /log-po` devolvía 403. Ahí **sí** avisaba ("Tienes el CSV, pero no
pudimos registrar la orden…"), así que no era mudo; pero cerraba con "Genérala de
nuevo", que para un viewer es la misma promesa vacía que ya se corrigió en
inventario.

## `/compras`: qué se cerró el 2026-08-10

Gatear "Aprobar" y "Rechazar" no bastaba. El carrito se llena con **dos**
estados, `approved` y `modified`, y dos controles que parecían decorativos
marcan `modified` por su cuenta: cambiar la cantidad y re-apuntar la línea a
otro proveedor. Dejando cualquiera de los dos, un viewer volvía a levantar la
barra verde de "Descargar orden de compra" sin haber aprobado nada. Por eso el
gate cubre la cantidad y el selector de proveedor además de los dos botones.

Quedaron gateados por rol: aprobar/rechazar/deshacer/restaurar, la cantidad, el
selector de proveedor, "Descargar orden de compra", "Convertir en OC" del
optimizador, "Registrar llegada" de los pedidos atrasados y "Crear
transferencia". El aviso de atraso, el plan del optimizador, el porqué de cada
recomendación y todos los números siguen visibles: lo que se quita es la
decisión, no la lectura. En el lugar de los botones el viewer lee "Tu rol no
puede generar órdenes", para que la ausencia no parezca una pantalla rota.

También `downloadOC` distingue ahora el 403 del resto: si el rechazo fue el rol,
deja de decir "Genérala de nuevo" y usa el aviso de permisos. Sigue siendo
alcanzable con los botones ocultos, porque el rol se lee de una copia local que
queda vieja en cuanto un admin degrada al usuario desde otra sesión.

Caminado con las dos cuentas y en los dos anchos: el viewer no ve ninguno de
esos controles (cero `input`, cero `select` en la pantalla) y sí ve los SKUs, el
porqué y el plan; el admin los ve todos, y una orden real generada desde la
pantalla quedó en `inventory_po_log` con 348 unidades, ₡417 600 y
`approved_count = 1`. En angosto, el viewer pierde el stepper y "Agregar al
pedido"; el admin los conserva. Cero errores de consola.

**"Crear transferencia", caminado el mismo día:** el componente no se dibujaba
porque la sesión activa no traía sugerencias de traslado. Se provocó la
condición real —dejar excedente de SKU-A en Bodega Cartago mientras principal
está en cero— y el briefing la produjo sola. El viewer lee la sugerencia
completa (mover 226 desde Bodega Cartago, la cobertura que le queda al donante,
y por qué trasladar gana a comprar) **sin** el botón; el admin lo tiene, y al
pulsarlo quedó en `inventory_transfer_log` la fila Cartago → principal en
`in_transit` con 226 unidades de SKU-A. Casilla cerrada.

Dos casillas que la tabla daba por pendientes y que **no existen como acción**:
"activar sesión" en `/historial`, y el borrado de cuenta — `DELETE /tenant` y
`/tenant/export` no tienen pantalla, son solo API, así que no hay forma de
caminarlos y quedan cubiertos únicamente por tests.

## `/pronosticos`: cuatro hallazgos de la primera caminata (2026-08-10)

Sesión real, 2 SKUs, 180 puntos diarios. Los tres primeros eran variantes del
**mismo defecto de fondo**: la pantalla mostraba números de tres modelos
distintos sin decir que eran distintos. Los cuatro quedaron cerrados, y el texto
en inglés del final también.

**Observación, no defecto:** en el SKU-B la referencia `naive` gana en costo
(8.69) a todos los modelos entrenados (mejor: 8.94). El código excluye las
referencias a propósito para elegir de dónde comprar, y el motor ya lo advierte
en el log ("no model beat a naive baseline on cost_horizon"). Que el usuario no
se entere es una decisión de producto, no un error de cálculo.

Para el SKU-A, la tabla de métricas de la propia pantalla dice:

| Modelo | costo | MAE | WAPE |
|---|---:|---:|---:|
| Modelo 2 (xgboost) | **14.60** | 10.04 | 24.6% |
| Modelo 1 (lightgbm) | 16.80 | 8.79 | 21.5% |
| Modelo 3 (prophet) | 18.32 | 17.55 | **45.7%** |
| Modelo 9 (global_lgbm) | 20.88 | **8.61** | **17.9%** |

El campeón se elige por **costo asimétrico** —decisión correcta y bien
documentada: quedarse corto cuesta más que sobrar— así que gana Modelo 2. Pero:

1. **`Mejor WAPE` no era el mejor WAPE. ARREGLADO.** La tarjeta rotulaba
   "Mejor WAPE: 24.6%" justo encima de una tabla con 17.9% y 21.5%. El valor
   está bien (es el error del pronóstico del que salen las compras); la
   etiqueta afirmaba un superlativo falso. Ahora dice "WAPE del elegido".

2. **El gráfico dibujaba un modelo que no era el campeón. ARREGLADO.** En
   `backend/api/v1/forecasts.py:479` el modelo servido por defecto es
   `next(iter(sku_forecasts.keys()))` — **el primero del diccionario**. Para el
   SKU-A eso es Modelo 3, con WAPE 45.7%, mientras la orden de compra se calcula
   con Modelo 2 (24.6%). El comprador mira una curva de un modelo y pide según
   otro, y nada en pantalla lo dice: el pie dice "Modelo: Modelo 3" y la tarjeta
   de al lado "Mejor modelo: Modelo 2". Se reprodujo en otra sesión y otro SKU
   (panel B: sirve Modelo 3, campeón Modelo 9), así que es sistemático, no un
   caso. Es exactamente la deriva que el comentario de
   `backend/inventory/service.py:3037` dice haber cerrado una vez entre motor,
   semáforo y precisión: seguía viva en el gráfico. Ahora el endpoint delega en
   `best_model_by_sku` —la misma autoridad— y cae al primer modelo disponible
   solo si el campeón no tiene serie guardada. Verificado en 6 SKUs de 3
   sesiones: servido == campeón en todos, y `avail=0` no revienta.

3. **La tarjeta de la lista anunciaba el mejor MAE de cualquiera. ARREGLADO.**
   `page.tsx:421` tomaba el MAE más bajo de **todas** las filas, sin excluir
   baselines. Para el SKU-A mostraba "MAE 8.61", que es de Modelo 9, no del
   elegido (10.04). Con otros datos podría haber anunciado el MAE de una
   referencia — justo lo que el resto del código excluye a propósito porque
   "existen para ser superadas". Ahora usa el mismo campeón que la tira de
   estadísticas: SKU-A muestra 10.04 y SKU-B 5.37, ambos del modelo que compra.

4. **La misma pantalla decía 5 atípicos y 0 atípicos. ARREGLADO.** No eran dos
   implementaciones sino dos **umbrales**: el motor usa un cerco de 3×IQR
   (`outlier_iqr_factor = 3.0`) y el frontend usaba el de 1.5×IQR, el de
   "atípico leve" del manual. El motor manda —su conteo es el que alimenta el
   puntaje de calidad y las advertencias— así que el frontend adoptó su factor.
   Publica solo un **conteo**, nunca posiciones, así que los marcadores se
   siguen ubicando en el frontend y por eso la constante tiene que seguir
   igualando la del motor; queda dicho en el comentario. Verificado: el pie ya
   no reporta atípicos para el SKU-A y la pestaña Calidad sigue diciendo 0.
   **Caminado del todo el 2026-08-10.** Ninguna serie del tenant cruzaba 3× en
   ninguna granularidad, así que se entrenó una sesión con el pico a propósito
   ("Outlier demo": 150 días, un SKU que vende ~40 y un día 400). El motor
   cuenta 1, el pie dice "1 valor atípico detectado", la pestaña Calidad dice 1
   y el punto ámbar aparece dibujado sobre el pico. Las dos definiciones
   coinciden sobre datos reales, en el caso positivo y en el negativo.

   Quedó medido de paso que la contradicción era **sistemática**, no anecdótica:
   con el cerco viejo el frontend marcaba puntos en 8 de 12 series mientras el
   motor reportaba 0 en las 12.

5. **La pestaña Calidad decía "1 outliers". ARREGLADO.** Encontrado justo al
   crear esos datos: las advertencias por SKU son frases en inglés que el motor
   arma a mano (`quality.py`: `f"{outliers} outliers"`, `f"{missing} missing
   dates"`, `"Only {n} rows (min={min})"`, `"Intermittent: {pct} zeros"`) y la
   pantalla las imprimía tal cual, junto a etiquetas traducidas. Se reconstruyen
   ahora desde los campos que el motor **ya publica** —su propio conteo de
   atípicos, su propio conteo de fechas faltantes, su `has_min_history`, sus
   `series_flags`— sin inventar ni un umbral: re-derivar "¿es intermitente?"
   desde `zero_ratio` y un corte adivinado habría recreado exactamente la
   división 1.5 vs 3.0 que se acababa de reparar. Lo que el motor advierta y no
   esté modelado sigue apareciendo, en inglés, en vez de desaparecer.

   El bloque estaba **duplicado** en el archivo y el primer arreglo tocó la copia
   que no se ve; caminarlo fue lo que lo destapó. Verificado en los dos idiomas
   y en los dos casos: SPIKE-01 dice "1 venta(s) muy fuera de lo normal…" /
   "1 sale(s) far outside the normal range…", CALM-02 dice "Serie limpia".

   El tipo `QualityReport` declaraba 7 campos de los 12 que la API manda; los
   otros 5 estaban ahí desde siempre, solo invisibles para el frontend — que es
   por qué esta pantalla terminó reimprimiendo las frases del motor en vez de
   rearmarlas.

**Además, no era un número pero sí una mentira de idioma. ARREGLADO.** "Ver
detalle" imprimía el texto crudo del motor, en inglés, a un usuario español —
`SKU 'SKU-A' / model 'croston': Croston is designed for intermittent series
(zero_ratio=0% < 20%)`— y nombraba el algoritmo que **esta misma pantalla oculta
a propósito** tras "Modelo N".

La estructura para arreglarlo ya existía: cada muestra trae `code` + `context`,
y el bloque de correcciones de ese mismo panel ya usaba el patrón código → i18n
→ respaldo. Ahora las muestras lo usan igual, con tres escalones: plantilla
`runwarn.<CODE>.sample`, luego la línea neutra de `context`, y solo al final el
inglés del motor. `modelLabel` se movió a `lib/modelLabel.ts` para que el panel
use **la misma** numeración que el gráfico — una segunda copia del arreglo
habría sido una segunda numeración, que es justo lo que ese mapeo evita.

Un caso no se podía traducir sin tocar el motor: `UNSORTED_DATES` mandaba
`context: {}` y el nombre de la columna vivía solo dentro de la frase en inglés.
`leakage.py` ahora pasa `context={"column": dt_col}` — aditivo, el mensaje no
cambia. Caminado en los dos idiomas: "SKU-A: Modelo 6 no encaja con esta serie",
"Columna de fechas: fecha", "Columna que quedó fuera: precio_unitario", y sus
equivalentes en inglés. Cero prosa del motor y cero nombres de algoritmo.

**Descartado al comprobarlo:** los nombres "Modelo 1..9" parecen arbitrarios
—llegan a 9 con solo 5 botones— pero son un mapeo fijo por posición
(`MODEL_ORDER`), estable entre SKUs, exportaciones y recargas. Es deliberado y
está documentado. No perseguirlo.

## `/impacto`: los números aguantan, dos frases apuntan al mes equivocado (2026-08-10)

Primera caminata. Esta pantalla casi no tiene botones: es puro número derivado,
que es justo donde un dato falso no hace ruido. **Los cuatro de portada
reconcilian exactamente** contra `inventory_po_log`:

| En pantalla | De dónde sale | Cuadra |
|---|---|---|
| 4 órdenes generadas | 4 filas | sí |
| ₡421 058 gestionados | 3006 + 425 + 27 + 417 600 | exacto |
| 2 riesgos atendidos | suma de `skus_order_now` | sí |
| 3 de 3 recomendaciones (100%) | `approved_count` / `suggested_count` | sí |

**ARREGLADO — el resumen mensual decía "este mes" hablando de otro.** La tarjeta
cubre siempre el mes **cerrado** (el mismo período del correo mensual, decisión
deliberada y documentada en `inventory.py:1307`), así que el 10 de agosto el
encabezado decía "Resumen de julio de 2026" y el cuerpo, "no registramos ninguna
orden de compra **en este mes**" — mientras la tabla de evolución, cinco
centímetros abajo, listaba agosto con 4 pedidos. Las dos frases con "este mes"
ahora nombran el mes: "…ninguna orden de compra en julio de 2026". Caminado.

**ARREGLADO — "5 días activo" no eran días activos.** `roi_service.py` calculaba
`(última orden − primera orden).days`, o sea el **lapso** entre la primera y la
última orden. Daba 5 y parecía correcto, pero un tenant que pidiera una vez y
repitiera al año habría leído "365 días activo" habiendo usado Faro dos días.
Ahora es `COUNT(DISTINCT generated_at::date)`, que no puede exagerar: está
acotado por los días en que el comprador apareció. En pantalla pasó de 5 a **2**,
que es exactamente lo que dice la base (4 y 10 de agosto). Con test nombrado por
el fallo (`test_roi_active_days.py`) y compuerta de mutación: restaurando el
cálculo viejo se ponen rojos dos de los tres.

## `/configurar-inventario`: una frase que se vuelve falsa al avanzar (2026-08-10)

Primera caminata, con la sesión "Outlier demo" de 2 productos. Lo sustantivo
aguanta: la lista prioriza por plata, guardar una fila la escribe exacta en la
base (`250`, `12.5`, `9`) —incluido el **"12,50" con coma**, que es lo que el
copy promete leer— y la promesa central se cumple de punta a punta: el producto
configurado aparece en el semáforo con señal real y el otro queda `SIN_DATOS`.

**ARREGLADO — el encabezado se volvía falso justo al avanzar.** Decía "Con
{n} de tus {total} productos cubres el {pct}% de tu compra del mes". Con nada
configurado era cierto por coincidencia (2 de 2 = 100%). Después de configurar
el primero pasó a decir "Con **1** de tus 2 productos cubres el **100%** de tu
compra del mes" — mientras la barra, dos líneas abajo, decía "65% ya
configurado" y ese producto restante valía 34.8%.

El cálculo del backend está bien: `cumulative_pct` incluye lo ya cubierto, así
que el 100% es el total al que llegarías. Lo que fallaba era la redacción, que
lo presentaba como cobertura de un subconjunto. Ahora: "**Completando** 1 de tus
2 productos **llegas al** 100% de tu compra del mes". Las dos variantes (plata y
unidades), en los dos catálogos —`translations.ts` y el de respaldo
`i18n/stockSetup.ts`, que hay que tocar juntos o el respaldo revive el texto
viejo—.

**Observación, no defecto:** el copy dice "mientras falten, ese producto no
aparece en el semáforo", y en realidad **sí** aparece, marcado `SIN_DATOS`. Es
mejor así —el producto no se esconde, se declara sin medir, que es la línea de
todo el producto— pero la frase promete otra cosa. No lo toqué.

## Recuperar contraseña: "todas las sesiones fueron revocadas" no era cierto (2026-08-10)

Caminados los 3 pasos con una cuenta real. **Casi todo aguanta**, incluidos los
caminos infelices: un correo inexistente avanza igual (no filtra si la cuenta
existe — deliberado y documentado en `auth.py`), el código equivocado se rechaza
sin dar pistas, la contraseña corta y las que no coinciden se frenan en el
cliente. Verificado en base después del cambio: el OTP quedó `used=t`, el
refresh token devuelve 401, la contraseña vieja devuelve 401 y la nueva entra.

**Lo que no era cierto.** `POST /auth/reset-password` respondía:

> `"Password updated. All sessions have been revoked."`

`update_password` borra los **refresh tokens** y nada más. Medido: con la sesión
abierta antes del cambio, después del reset el **access token seguía dando 200**
mientras su refresh daba 401. O sea: la sesión no se puede *renovar*, pero quien
ya tenga un access token conserva acceso completo —incluido escribir— hasta que
venza (15 minutos). Para el caso que motiva un reset ("creo que alguien entró"),
esos minutos son justo los que importan.

**ARREGLADO el mismo día, con el visto bueno del dueño.** `/logout` sí podía
revocar el access token porque es una petición *autenticada*: tiene el `jti` en
la mano para la lista negra que `guards.py` consulta. El reset es un flujo **sin
autenticar** y nunca ve el token del intruso, así que el corte se expresa por
**usuario y fecha**: `users.sessions_invalid_before` (columna nueva, aditiva,
creada por la migración al arrancar) contra el `iat` del token, que ahora los
tokens llevan.

Un token sin `iat` —los emitidos antes de este cambio— no puede probar cuándo se
hizo, así que se rechaza; pero **solo** en cuentas que efectivamente cortaron.
Quien nunca cambió su contraseña tiene `NULL` y no pasa ni por esa rama, así que
sus tokens viejos siguen funcionando igual.

**La parte que casi sale mal, y que encontró la suite.** La primera versión
truncaba ambos lados al segundo, para que un login hecho en el mismo segundo que
el reset no se leyera como más viejo que el corte y dejara al usuario fuera de la
cuenta que acababa de recuperar. Un test lo atrapó. Pero al correr la selección
completa apareció el reflejo: **bajo carga, el token anterior al reset también
caía en ese mismo segundo, y sobrevivía al cambio de contraseña**. Un segundo no
alcanza para separar "emitido justo antes" de "emitido justo después".
Microsegundos sí, así que ninguno de los dos lados redondea. Ese fallo **solo
aparecía en la corrida grande**, nunca aislado.

Caminado contra el servidor real después de reiniciar: sesión abierta → reset →
el access token anterior devuelve **401** (antes daba 200) y el login inmediato
devuelve **200**, sin bloqueo. El endpoint volvió a poder decir "All sessions
have been signed out" porque ahora es cierto. Cinco tests nombrados por el fallo,
con compuerta de mutación (comentar la llamada del guard pone dos en rojo), y 222
tests de la vecindad de auth en verde.

## `/reset-password`: el enlace servía dos veces (2026-08-10)

Caminada con un token real emitido por el propio producto. Lo que aguanta: sin
token la pantalla avisa **y deshabilita** el botón —no ofrece lo que no puede
cumplir, que es el patrón contrario al que hubo que corregir en `/inventario`—;
con token válido cambia la contraseña, rechaza la anterior y corta las sesiones.

**ARREGLADO — el mismo enlace cambiaba la contraseña dos veces.** El OTP sí se
quema (`pw_change_codes.used`), pero el token que se recibe a cambio era un JWT
firmado sin nada que lo marcara gastado: seguía valiendo sus 15 minutos.
Reproducido contra el servidor: tras un reset completo, **reenviar el mismo
token devolvía 200** y dejaba la contraseña en otra distinta, sacando de la
cuenta al dueño que acababa de recuperarla.

Pesa más que un replay cualquiera porque **ese token viaja en la URL** de esta
pantalla: sobrevive en el historial del navegador, en una pantalla compartida, en
los registros de cualquier proxy.

El arreglo no necesitó maquinaria nueva: el token ya lleva `jti` y la lista negra
que usa `/logout` es exactamente el almacén correcto. Se quema **después** de un
cambio exitoso, nunca antes — una contraseña rechazada por débil debe dejar el
enlace usable, o el primer error de tecleo le cuesta al usuario su única vuelta.
Verificado en vivo: primer uso 200, replay rechazado con `reset_token_invalid`
(que sí tiene copy en español), la contraseña del dueño entra y la del replay no.

**Anotado, no es defecto pero conviene saberlo:** `send_password_reset_email`
—la que manda un *enlace*— existe y tiene test, pero **nadie la llama**. El
producto manda un código de 6 dígitos, no un enlace. O sea que esta pantalla hoy
solo se alcanza con un token que ningún correo produce; el endpoint detrás, en
cambio, es el que usa el paso 3 de `/forgot-password` y está muy vivo.

## Landing: lo específico se cumple, la cifra del hero no (2026-08-10)

Lo llamativo es que **las promesas concretas aguantan**. La landing describe el
aprendizaje de plazos con un detalle que se puede falsear:

> "A la tercera recepción de ese proveedor deja de usar el plazo que escribiste
> y empieza a usar el promedio observado — y te dice cuál de los dos está
> aplicando. Esto viene en todos los planes."

`MIN_LEAD_TIME_OBSERVATIONS = 3` — la tercera recepción, exacto. Y no hay ningún
gate de plan sobre eso, así que "todos los planes" es cierto. Los CTA apuntan a
`/signup?demo=1`, bandera que la pantalla de registro sí lee y que enlaza con el
endpoint `demo_quickstart`. Nada de eso es humo.

**Lo que sí conviene mirar: "94% Precisión promedio de pronóstico".** Esta semana
el producto le mostró a su propio usuario 75.1%, 75.2% y 89% en sesiones reales.
La cifra del hero no es la que la aplicación enseña. Puede que salga de un
benchmark o de una aspiración legítima —eso no lo sé—, pero hoy un comprador que
entra por la landing y llega a `/pronosticos` ve dos números distintos sobre lo
mismo.

Las otras tres del hero (−75% de tiempo, 50K+ SKUs por instancia, 1 día de
implementación) no tienen dentro del repositorio nada contra qué contrastarlas.
Ojo con la de 50K+: los planes topan en 5.000 SKUs salvo Enterprise, así que
"soportados por instancia" y "lo que tu plan te deja" no son lo mismo y están a
dos clics de distancia.

**No lo toqué.** Una cifra de marketing es una decisión de negocio —y
potencialmente un compromiso legal—, no un defecto que me corresponda corregir
por mi cuenta. Queda anotado con la evidencia medida.

## Proveedores y su scorecard: el mismo dato, con matiz y sin él (2026-08-10)

`/proveedores` tiene la mejor columna de copy del producto. Explica **por qué**
no aprendió todavía, proveedor por proveedor:

> "Registré 3 entregas, pero todas llegaron el mismo día que las pediste, así que
> no dicen nada del plazo de este proveedor. Sigo usando los 10 días que
> configuraste."

Eso es exactamente la promesa de la landing, cumplida y además protegida contra
datos degenerados: tiene sus 3 recepciones, y aun así se niega a aprender de
ellas porque no miden nada.

**SIN ARREGLAR — el scorecard presenta esos mismos números planos.** Para ese
proveedor muestra `LEAD TIME REAL 0d` junto a `DECLARADO 10d`, sin una palabra
del matiz que la otra pantalla acaba de dar. Un comprador que entra directo al
scorecard concluye que Andina entrega el mismo día y baja su plazo — que es
precisamente la decisión equivocada que `/proveedores` se esfuerza en evitar. El
`% A TIEMPO 100%` sale de lo mismo: entregas de cero días son trivialmente
puntuales.

Y hay una segunda: **`TENDENCIA: Estable` calculada sobre UNA recepción**
(Granos del Valle). Una tendencia necesita al menos dos puntos; con uno, la
palabra honesta es "todavía no sé".

Es la misma familia que los 5-vs-0 atípicos de `/pronosticos`: dos superficies
sobre el mismo dato, una con criterio y la otra sin él. **No lo arreglé porque
qué mostrar cuando el dato es degenerado es decisión de producto** — se puede
ocultar la cifra, marcarla como no concluyente, o reusar literalmente la frase de
`/proveedores`. Mi recomendación es la tercera: la frase ya existe, ya está
probada con usuarios reales y es la que evita la compra tardía.

## Por qué la suite no sustituye esto

El 2026-08-06 la suite del backend pasó **2407 tests en verde mientras 27
defectos estaban vivos** — entre ellos que cerrar sesión no cerraba sesión y que
un archivo latinoamericano perdía el 60% de sus filas.

No era que los tests mintieran: al romper el código a propósito en tres puntos
críticos, los tres se pusieron rojos. El problema es que **ninguno apuntaba a
esos comportamientos**. Todos se escribieron para confirmar que una función hace
lo que su autor quiso; el defecto vivía justamente en lo que el autor creía.

Los únicos tests que encontraron algo en ese lote llevan el nombre de un fallo,
no de una función: `test_notification_delivery_honesty`,
`test_stock_seeding_zero`, `test_transfer_rejection_reason`.

## Reglas que salen de ahí

1. **Un test nace de un fallo, no de una función.** No se escribe un test porque
   exista un endpoint. Se escribe porque se observó —o se sospecha con motivo—
   una mentira visible para el usuario.
2. **Compuerta de mutación.** Ningún test nuevo entra sin demostrar que se pone
   rojo al romper su objetivo. Se rompe la línea que vigila, se corre, tiene que
   fallar. Cuesta segundos y convierte "el test pasa" en evidencia.
3. **El descubrimiento sale de caminar, no de la suite.** La suite defiende lo
   ya sabido; no encuentra nada nuevo. Esta tabla es el mapa del descubrimiento.
4. **Nada de topes silenciosos.** Si una pasada cubre solo parte de una
   pantalla, se anota en "qué falta". Una casilla vacía significa desconocido,
   nunca correcto.
