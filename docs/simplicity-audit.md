# Auditoría de simplicidad: cómo ofrecer todo sin complicar al usuario básico

Fecha: 2026-10-05. Solo lectura de código: no se arrancó backend, base de datos,
servidor de desarrollo ni navegador. Los conteos de controles salen de leer el
código (marcados "estático"), no de un recorrido en navegador; antes de
actuar sobre cualquier número, el guard de la sección 4 debe medirlos de verdad.

## 0. Resumen ejecutivo

El producto ya tiene la regla correcta en varios sitios y no la aplica en los
demás. Lo que ya se revela solo cuando hace falta:

- Bodegas: `useWarehouses().multi` (`Frontend/src/components/inventory/WarehouseControls.tsx:90`)
  es `warehouses.length >= 2`. Con una sola bodega desaparecen la pestaña
  Transferencias (`app/pedidos/page.tsx:213,222`), el selector de destino del
  carrito (`app/compras/page.tsx:2001`, `HoyMobile.tsx:432`) y el filtro por
  bodega de Inventario (`app/inventario/page.tsx:1184,1256`).
- Horizonte/periodo: `PlanningControl` no pinta nada si hay un solo periodo
  (`components/layout/PlanningControl.tsx`, `available_periods.length <= 1`).
- Pestaña "Reutilizar archivo" y "Clonar" del asistente de carga solo existen
  si hay datasets/sesiones previas (`app/ventas/page.tsx:1427-1433`).
- Auditoría: la pestaña solo es para admin (`app/actividad/page.tsx:62`).
- Pronósticos: vista "comprador" vs "técnica" (`useForecastView`,
  `app/pronosticos/page.tsx:99-117`); Métricas, Calidad y Comparar solo existen
  en la técnica. Inventario arranca en "Qué pedir" (`app/inventario/page.tsx:2046`).

Lo que falta: el resto del producto (22 entradas de `SCREENS`) se enseña igual a
quien tiene 20 productos que a quien tiene 20.000. Hay cuatro focos de ruido:

1. **El panel de compras (`/compras`) es la pantalla estrella y tiene ~10
   bloques** además de las tarjetas (sección 3).
2. **El asistente de carga pide 4 decisiones antes de subir el archivo**
   (nombre, horizonte, nivel de detalle, país) cuando la respuesta por defecto
   sirve al 95% (`app/ventas/page.tsx:495-562`).
3. **El sidebar muestra 11 entradas a todos** (6 diarias + 4 "herramientas" +
   Configuración; `navItems.ts:81,86`), incluidas Escenarios, Mensajes y
   Actividad que un negocio de 1-2 personas casi no abre.
4. **Jerga** sin traducir al lenguaje del dueño: SKU, MOQ, WAPE, semáforo,
   backtest, huella, granularidad (sección 6).

Ninguna de las propuestas de las secciones 2 y 5 necesita un ajuste, modo
interruptor ni feature gate: todas se calculan de conteos que la app ya tiene
(bodegas, proveedores, sesiones completadas, usuarios, llaves, productos). Lo
que SÍ necesitaría una opción nueva está aparte, en 5.3 ("necesita aprobación
del dueño").

Dato útil para la sección 4: **ya existe un arnés Playwright** en el repo
(`Frontend/tests/lib/harness.mjs`, `smoke.mjs`, `critical_flows.mjs`,
`virgin_screens.mjs`; `playwright ^1.60` en `Frontend/package.json`). La premisa
"el frontend no tiene runner" es cierta para un runner de tests, pero no para
scripts de navegador: el guard se construye encima de lo que hay.

## 1. Inventario de pantallas: básico vs avanzado

Leyenda: B = el usuario básico (1 bodega, <100 productos, 1-2 usuarios, sube
ventas una vez al mes, quiere "qué pido hoy") lo necesita en la primera vista.
A = avanzado/empresa. Fuente de rutas: `SCREENS` en `components/layout/navItems.ts:35-77`.

### 1.1 Entradas del menú lateral (11) y la barra móvil

Desktop: `NAV` (6) + `TOOLS_NAV` (4) + Configuración fijada abajo
(`navItems.ts:81,86,92`). Móvil: 4 pestañas (Panel, Pedidos, Inventario,
Asistente) + "Más" (`components/mobile/MobileTabBar.tsx:28-31`).

| Ruta | Etiqueta (es) | Básico necesita | Avanzado/empresa hoy visible | Regla "aparece cuando hace falta" |
|---|---|---|---|---|
| `/compras` | Panel de compras | Tarjetas "qué pedir", aprobar, generar orden | Optimizador/transferencias, "Anticípate", "Cambios de demanda", recomendaciones del sistema, capital, narrativa, supuestos, receptions | Ver 3.1: núcleo arriba; el resto detrás de una sola fila plegable "Más análisis" que se abre sola solo si el bloque tiene datos accionables |
| `/pedidos` | Pedidos | Lista de órdenes, marcar recibida | Pestaña Transferencias, filtro pagado, "Nuevo pedido manual" | Transferencias ya se oculta con 1 bodega. Filtro "pagado" solo si hay >=1 pedido con pago registrado |
| `/inventario` | Inventario | Vista "Qué pedir" | Vista Todos, Por proveedor, menú Análisis (5 vistas: dinero parado, costos al alza, margen, pronóstico en dinero, costo de ignorar), Actualizar stock, filtros por bodega | Por proveedor solo si hay >=2 proveedores. Menú Análisis solo con >=1 sesión completada y costos cargados |
| `/proveedores` | Proveedores | Nombre, tiempo de entrega, contacto | Variabilidad, términos de pago, scorecard, catálogo (`tab 'mine'/'catalog'`, `inventario/page.tsx:1466`) | Entrada del menú solo si hay >=1 proveedor, o la tarjeta "agrega tu primer proveedor" en el panel. Scorecard solo con >=3 recepciones del proveedor |
| `/pronosticos` | Pronósticos | Gráfico de un producto y su semáforo | Pestañas Patrón/Métricas/Calidad, comparar sesiones, panel de lineage (`RunLineagePanel`, `pronosticos/page.tsx:517`), backtest de política (`PolicyBacktestPanel`), tabla de WAPE | La vista técnica ya está oculta por defecto; falta ocultar el panel de lineage y backtest dentro de la vista comprador (hoy `RunLineagePanel` se pinta sin comprobar la vista) |
| `/asistente` | Asistente IA | Una pregunta en lenguaje natural | Fuentes de datos, chips "¿Qué es el WAPE?" | Ya es simple; solo bajar los chips técnicos |
| `/ventas` | Mis ventas (carga) | Subir archivo | Nombre, horizonte, detalle, país, reutilizar, clonar | Ver 4 y 5: valores por defecto, ajustes plegados |
| `/escenarios` | Escenarios | Nada | Simulador "qué pasa si" (16 pasos de tour) | Solo con >=1 sesión completada; dentro de Pronósticos, no en el menú lateral |
| `/mensajes` | Mensajes | Nada | Mensajería 1-a-1 entre usuarios | Solo con >=2 usuarios en el tenant (con 1 usuario no tiene con quién hablar) |
| `/actividad` | Qué ha pasado | Nada diario | Feed + pestaña Auditoría (solo admin) | Entrada del menú solo con >=2 usuarios; con 1 es la campana. Auditoría ya es solo-admin |
| `/configuracion` | Configuración (hub) | Cuenta, subir ventas | 5 secciones y 9 filas | Ver 1.3 |

Hallazgo (c): un dueño de 1-2 usuarios ve **11 entradas** a la izquierda de las
que opera **3** cada día (Panel, Pedidos, Inventario). Objetivo: **5 visibles**
(Panel, Pedidos, Inventario, Pronósticos, Configuración) y que Proveedores,
Asistente, Mensajes y Actividad aparezcan por las reglas de la tabla. El
Asistente IA es de las pocas cosas que un dueño sin formación sí usa; mantenerlo
visible si `/service-config/capabilities` indica que el asistente está activo.

### 1.2 Pantallas secundarias (hijas en `SCREENS`)

| Ruta | `parent` | Básico | Avanzado | Regla |
|---|---|---|---|---|
| `/configurar-inventario` | `/inventario` | Stock y tiempos de entrega por producto | Reglas del semáforo (umbrales), importación masiva (`setupStock.import.*`), fuentes SQL | La importación masiva y SQL solo se ofrecen cuando hay >20 productos sin dato; con <20, edición en línea |
| `/impacto` | `/pronosticos` | Un número: "te ahorraste X" | Reporte mensual por correo, comparación vs baseline (16 pasos de tour) | Solo con >=2 meses de historia y >=1 orden recibida |
| `/historial` | `/pronosticos` | Ver la última actualización | Lista de sesiones, "comparar con la realidad", archivar | Solo con >=2 sesiones completadas (hoy siempre pestaña de las 4 de `ANALYSIS_TABS`, `navItems.ts:96`) |
| `/mi-cuenta` | `/configuracion` | Nombre, contraseña, idioma | Sesiones activas, 2FA/social si activo | Ya aislado en Configuración |
| `/usuarios` | `/configuracion` (admin) | Invitar a una persona | Roles/permisos finos (`users.perm_*`) | Con 1 usuario, empty state "Invita a quien compra contigo" y roles detrás de "Cambiar permisos" |
| `/archivos` | `/configuracion` | Ver mi archivo y reemplazarlo | Editor SQL, conexión, análisis, edición (`data.tab_*`, `archivos/page.tsx:1669-1670`) | Pestañas de editor SQL y análisis solo si el dataset es de tipo SQL / tiene >=1 sesión. Para un CSV: "Ver" y "Reemplazar" |
| `/automatizacion` | `/configuracion` (admin) | Reentrenar el día que sube ventas (automático) | Programaciones, llaves de API, webhooks (3 pestañas, `automatizacion/page.tsx:808-810`) | Entrada en el hub solo si hay >=1 programación/llave/webhook, o con empty state "esto es opcional" |
| `/api` | `/configuracion` | Nada | Explorador de API pública | Ya está al final; solo mostrar la fila en el hub si el tenant tiene >=1 llave API |
| `/instalacion` | `/configuracion` (admin) | Nada (es del operador de la instalación) | Credenciales del despliegue | Solo si el usuario está en `INSTANCE_ADMIN_EMAILS` (hoy: adminOnly + gate interno, `navItems.ts:67-70`); ocultar la fila, no solo el contenido |

### 1.3 El hub de Configuración (`app/configuracion/page.tsx`)

Desktop: banda de cuenta + 5 tarjetas (`SECTIONS`, líneas 36-54: Equipo 1 fila,
Datos 3 filas, Automatización y API 2, Actividad 1, Instalación 1) + tarjeta
Legal = **hasta 9 filas + cuenta + legal**. El hub está bien pensado como índice,
pero la sección "Automatización y API" y "Instalación" las ve un admin
básico sin tener nada que ver. Regla: construir `SECTIONS` filtrando por conteos
(no solo por rol): `connect` solo si el tenant tiene programaciones, llaves o
webhooks; si no, una única línea discreta "Conectar con otros sistemas" que
explica en una frase y enlaza. `installation` solo para operadores.
`/configurar-inventario` pasa a ser una tarjeta de empty state en Inventario
cuando faltan datos, no una fila permanente.

### 1.4 Tours (`components/tour/tours/`)

15 tours, **165 pasos** en total (hoy 16, inventario 16, escenarios 16, roi 16,
skus 14, quickStart 12, sessions 11, ...). `quickStart` es `autoStart: true`
(`quickStart.ts:8`), así que el primer contacto es un tour de 12 pasos sobre una
pantalla de 4 campos. Los tours de /hoy, /inventario, /roi y /escenarios
explican controles avanzados (`hoy.transfers`, `hoy.cart_warehouse`) que un
usuario básico nunca ve. Propuesta: el tour solo incluye los pasos cuyo ancla
existe en la pantalla (el motor ya salta pasos sin ancla; verificar en
`TourOverlay`), y reducir quickStart a 4 pasos (subir, columnas, esperar,
resultado). Es riesgo bajo porque los textos ya están en cada módulo.

## 2. Conjunto de reglas "aparece cuando hace falta"

Todas usan datos que la API ya devuelve. Ninguna crea ajuste, modo, interruptor
ni compuerta de funcionalidad (el límite sigue siendo un número, nunca una
puerta cerrada: `CLAUDE.md`, "Two usage tiers"). Es presentación derivada de
conteos; el usuario puede navegar a cualquier ruta por URL o por la paleta de
comandos (`components/command`), que lista todo.

| # | Regla | Dato de entrada (ya existe) | Efecto |
|---|---|---|---|
| R1 | Bodegas | `useWarehouses().multi` (>=2) | Ya implementada. Mantener y extender a la columna "Bodega" de tablas y a "Transferencias" en la paleta |
| R2 | Proveedores | conteo `suppliers` del tenant | 0: ocultar columna Proveedor, vista "Por proveedor" y selector por tarjeta del Panel; mostrar "Agrega tu primer proveedor" con un solo botón. >=1: mostrar. Hoy `ActionCard` pinta el selector siempre (`compras/page.tsx:239+109`) |
| R3 | Comparador y sesiones | sesiones `COMPLETED` >= 2 | Ocultar "Comparar", `/historial`, y "Comparar con la realidad" con <2 |
| R4 | Auditoría | rol admin | Ya implementada (`actividad/page.tsx:62`) |
| R5 | Usuarios | conteo de usuarios del tenant | 1: ocultar Mensajes y la fila Actividad; en Usuarios, un empty state de invitar |
| R6 | Automatización | conteo de programaciones/llaves/webhooks | 0: fila del hub con descripción de una línea "opcional", sin pestañas hasta abrir |
| R7 | API/MCP | conteo de llaves API | 0: ocultar `/api` del hub y de la barra de comandos salvo búsqueda explícita |
| R8 | Optimizador | respuesta del optimizador | Mostrar el bloque solo si `orders.length + transfers.length > 0`; hoy muestra título, subtítulo y nota "vs semáforo" aunque estén vacíos (`compras/page.tsx:2176-2188`) |
| R9 | Supuestos | `assumptions.count > 0` | Ya condicional; conservar |
| R10 | Escenarios, Impacto | >=1 sesión completada (+ recepciones) | Pestañas en `ANALYSIS_TABS` solo si aplican |
| R11 | Configurar inventario masivo/SQL | productos sin dato > 20 | Menos: edición en línea |
| R12 | Datos del asistente de carga | datasets previos | Ya implementada para "reutilizar/clonar" |
| R13 | Jerga técnica | vista del pronóstico (`useForecastView`) | WAPE, métricas y lineage solo en la vista técnica |
| R14 | Campaña/ejemplo demo | tenant sin sesiones | Demo visible solo en estado vacío (ya es así: `hoy.empty_cta_demo`) |
| R15 | Tour | pantalla real | Pasos sin ancla visible se omiten |

Implementación común (una sola vez): un hook `useTenantFacts()` en
`Frontend/src/hooks/` que reúne conteos ya devueltos por endpoints existentes
(`/warehouses`, `/suppliers`, `/sessions`, `/users`, `/automation/*`), cacheados
en un contexto, y `navItems.canSee(screen, role, facts)` extendido con un
predicado opcional `visibleWhen?: (facts) => boolean`. Si algún conteo no está
disponible hoy en un solo endpoint, la alternativa sin backend nuevo es leer las
listas que cada pantalla ya consulta; **un endpoint de "resumen del tenant" sería
una capacidad nueva y se lista en 5.3.**

## 3. Conteo de controles en la primera vista

Método estático: tour anchors (`data-tour`), `<button>/<Link>/<select>/<input>`
y `onClick` en el árbol de la pantalla con datos. No es un recorrido en
navegador; ver sección 4 para el medidor real.

### 3.1 Primera pantalla tras el login: `/compras` (desktop)

El login redirige a `/hoy` (`app/(auth)/login/page.tsx:42`), que
`next.config` reescribe a `/compras` (`next.config.*:85`). Un tenant nuevo ve
`HoyEmptyState` (`compras/page.tsx:781-815`): título, cuerpo, 3 viñetas y 2
botones (subir, demo) — **bien**: enseña un paso. Un tenant con datos ve:

Cromo fijo (en toda pantalla): sidebar 11 entradas + cuenta/cerrar sesión
(`Sidebar.tsx`), barra superior con búsqueda, `TrainingPill`, campana,
mensajes y menú desbordado (`TopBar.tsx`) = **~19 controles**.

Cuerpo de `/compras` con datos (`compras/page.tsx`):

| Bloque | Línea | Controles aprox. |
|---|---|---|
| Insignia + saludo + fecha/sesión | ~1612-1640 | 0 |
| `DataFreshness` | ~1646 | 1 (enlace) |
| `StaleDataBanner` (si hay) | ~1680 | 1 |
| `AssumptionsBanner` | ~1688 | 1 |
| `SupplierLeadTimeAlertBanner` | ~1699 | 1+ |
| Recepciones vencidas | 1705-1745 | 1 por pedido |
| Pedidos pendientes | 1747 | 1 |
| 4 KPIs | 1769 | 0 |
| Narrativa | 1810 | 1-2 |
| Transferencias (solo multibodega) | 1832 | n |
| Tarjetas urgentes | 1839 | **5 por tarjeta** (proveedor, cantidad, "por qué", aprobar, rechazar; `ActionCard` 239-618) |
| Tarjetas "esta semana" | 1875 | 5 por tarjeta |
| Carrito flotante | 1933 | 3-4 (limpiar, destino, generar, descargar) |
| Optimizador | 2176 | 1 por orden sugerida |
| Anticípate | 2284 | n |
| Cambios de demanda | 2300 | n |
| Recomendaciones del sistema | 2345 | n |
| Oportunidades de capital | 2376 | n |
| Pie: actualizar | 2432 | 1 |

Resumen: **~12 secciones visibles y ~30 controles fijos (grep: 30 apariciones
en `page.tsx`, más 5 por tarjeta), con 5 tarjetas = ~55 controles + ~19 de
cromo.** El tour de la pantalla tiene 16 pasos (`tours/hoy.ts`).

Objetivo para usuario básico: **<=5 secciones y <=12 controles fijos** más las
tarjetas. Cómo: (1) mover Optimizador, Anticípate, Cambios de demanda,
Recomendaciones y Capital debajo de un único bloque plegado "Más análisis",
(2) en cada tarjeta dejar visible cantidad editable + Aprobar + Rechazar y
mover proveedor y "Por qué" a un detalle expandible cuando hay 0-1 proveedores
(R2), (3) KPIs de 4 a 3 (quitar "valor de inventario" cuando no hay costos),
(4) banners en un solo "Datos a revisar" con contador.

### 3.2 Otras pantallas (conteo estático de manejadores, `grep`)

| Pantalla | Manejadores | Observación |
|---|---|---|
| `/inventario` | **150** (`page.tsx`, 4626 líneas) | La más densa: 3 pestañas + menú de 5 análisis + actualizar stock + filtros + tablas de ~10 columnas (`inventario/page.tsx:3084-3093`). Objetivo: "Qué pedir" con 4 columnas |
| `/archivos` | 66 | 4 pestañas por dataset |
| `/mi-cuenta` | 64 | Pantalla larga de preferencias |
| `/ventas` (carga) | 26 | Paso 1: nombre + 3 chips horizonte + 4 chips detalle + selector país (11 opciones) + dropzone + demo + 3 pestañas = **~24 controles antes de subir** |
| `/escenarios` | 34 | Solo avanzado |
| `/historial` | 32 | Solo con >=2 sesiones |
| `/compras` | 30 + tarjetas | Ver 3.1 |
| `/automatizacion` | 25 | 3 pestañas |
| `/usuarios` | 24 | roles |
| `/proveedores` | 20 | tabla de 8 columnas |
| `/asistente` | 20 | ok |
| `/pronosticos` | 8 | Delegado en `components/forecast/*` |
| `/pedidos` | 6 | Simple, ejemplo a seguir |
| `/impacto`, `/api`, `/instalacion`, `/mensajes`, `/actividad` | 3-8 | |

Los conteos son de `grep -c "<button|<Link|<select|<input|onClick"`; subcomponentes
importados no están incluidos, así que son cotas inferiores.

## 4. La ruta básica y dónde se complica

### 4.1 Pasos hoy

Cuenta nueva -> primera carga -> "qué pedir" -> hacer un pedido.

1. **Registro** (`app/(auth)/signup/page.tsx`): ~5 campos + checkbox de
   términos (`:231-312`). Aceptable. Alternativa de prueba sin registro:
   `/prueba` -> `router.replace('/ventas?demo=1')` (`(auth)/prueba/page.tsx:134`).
2. **Verificar correo** (`verify-email`): paso obligatorio fuera de la app.
3. **Login** -> `/hoy` -> `/compras` -> estado vacío con "Subir mi historial"
   (`compras/page.tsx:781-815`). Un clic. Bien.
4. **`/ventas` paso 1** (`ventas/page.tsx:1380-1560`): arranca el **tour
   quickStart de 12 pasos** (`autoStart`, `quickStart.ts:8`), luego título +
   barra de 3 pasos + texto con "fecha, producto y cantidad vendida" + el
   formulario `PlanSettings` (`:495-562`): nombre, "¿Hasta cuándo?" (3 chips),
   "Nivel de detalle del plan" (4 chips: Auto/Diario/Semanal/Mensual) y "¿En qué
   país vendes?" (select) y recién después el dropzone. **Jerga y pasos de
   más**: "Nivel de detalle" (`qs.plan_granularity_label`) y "horizonte" son
   decisiones del analista; "Auto (recomendado)" ya existe y debería ser el
   único camino por defecto.
5. **Paso 2 "Confirma columnas"** (`:1558-1750`): `qs.confirm_title`; mapeo de
   fecha/SKU/demanda con selects. La adivinanza automática ya existe
   (`qs.mapping_reused`); solo preguntar cuando la confianza es baja y saltarse
   el paso si todas las columnas se detectaron con certeza.
6. **Paso 3 "El sistema aprende"** (`:1770-1802`, `qs.learning_*`): espera. El
   texto `qs.family_note` ("vistas por período (diaria y semanal)", `translations.ts:873`)
   introduce otra jerga.
7. **Redirige al Panel** con el semáforo: de aquí es el cuerpo de 3.1.
8. **Aprobar y generar orden** (`compras/page.tsx:1933-2025`): barra verde
   flotante, "Generar orden" (`hoy.btn_download_po`) y modal de envío por
   correo/WhatsApp (`hoy.generate_send_*`, `:2040-2117`). Sin proveedor, la línea
   queda "suelta" y no se puede enviar (`tours/hoy.ts` `supplier_body`): un
   dueño de tienda de 1 proveedor no tiene por qué ver un selector.
9. **Recepción** (`/pedidos` o botón "Registrar llegada" del Panel,
   `compras/page.tsx:1727-1737`): bien resuelto.

Pasos/clics en el camino feliz hoy: registro (1) + verificar (1) + login (1) +
subir (3 pantallas, ~6 clics con ajustes por defecto) + aprobar N + generar (1) +
enviar (1-2): **~15 clics y 5 pantallas**. Objetivo: **<=10 clics y 4
pantallas** (saltar columnas confirmadas, ocultar los 4 ajustes del plan, no
mostrar el selector de proveedor con 0 proveedores).

### 4.2 Puntos de jerga y pasos de más (citas)

- `ventas/page.tsx:495-562` — 4 ajustes del plan antes del dropzone.
- `ventas/page.tsx:408-419` — presets de horizonte y detalle.
- `compras/page.tsx:2176-2188` — título, subtítulo y "vs semáforo" del
  optimizador visibles aunque no haya órdenes; texto con "horizonte" y "semáforo"
  (`hoy.optimizer_vs_semaforo`, `translations.ts:2387`).
- `compras/page.tsx:239+` — `ActionCard`: selector de proveedor y "por qué"
  siempre.
- `inventario/page.tsx:2576-2587` — 3 pestañas + 5 vistas de análisis
  (`inventory.view_dead_capital`, `view_margin_erosion`, `view_cost_of_ignoring`).
- `pronosticos/page.tsx:517` — `RunLineagePanel` ("Cómo se produjo este
  pronóstico", huella, JSON, `translations.ts:2043-2073`) sin atender la vista
  comprador.
- `components/tour/tours/quickStart.ts:8` — tour de 12 pasos en auto-inicio.
- `navItems.ts:86` — `TOOLS = ['/ventas','/escenarios','/mensajes','/actividad']`
  siempre en el menú.

### 4.3 Guard de regresión: recorrido automatizado de la ruta básica

Problema que resuelve: nadie nota que un bloque nuevo sube de 12 a 20 controles
hasta que se queja un cliente. Un control de CI está fuera de alcance (regla del
dueño: sin CI/CD), así que el guard es un script de `scripts/`/`Frontend/tests/`
que se corre a mano antes de etiquetar una versión (como `scripts/SMOKE.md`).

Construcción, sobre lo que ya hay (`Frontend/tests/lib/harness.mjs`, Playwright
crudo; `createReporter`, `check`, `login`):

1. Archivo nuevo `Frontend/tests/basic_path.mjs`, mismo estilo que
   `virgin_screens.mjs` (variables `SMOKE_BASE`, credenciales por entorno).
2. Datos: tenant **nuevo** por corrida mediante `POST /trial` (existe,
   `backend/trial/`; crea un tenant desechable con 30 SKUs) o `signup` por API;
   subir `scripts/sample_sales.csv` (ya está en el repo, <100 SKUs).
3. Pasos con aserciones: login -> `/compras` -> clic "Subir mi historial" ->
   subir CSV -> confirmar columnas si aparece -> esperar el semáforo -> aprobar
   la primera tarjeta -> "Generar orden" -> verificar fila en `/pedidos`.
4. En cada pantalla visitada, medir y comparar con un presupuesto versionado
   en `Frontend/tests/basic_path.budget.json`:
   - `clicksToComplete` (clics del camino feliz),
   - `screens` (rutas distintas),
   - `visibleControls` = elementos visibles y habilitados de
     `button, a[href], select, input, [role=tab]` (sin los de `[aria-hidden]`),
     separando cromo de cuerpo (`nav`, `header` vs `main`),
   - `visibleSections` = `main h1, main h2, main [role=tabpanel]` visibles,
   - `rawI18nKeys` = texto que coincide con `/\b[a-z]+\.[a-z_]+\b/` (ya lo
     verifica `virgin_screens.mjs`),
   - `jargon` = ocurrencias de una lista corta (SKU, MOQ, WAPE, backtest,
     lineage, granularidad) en el texto visible de la ruta.
5. Falla (`process.exit(1)`) si cualquier métrica supera el presupuesto; un
   cambio intencional exige editar el JSON en el mismo commit (la subida del
   presupuesto queda en el diff y es revisable). Se imprime una tabla
   "pantalla | controles | presupuesto" para ver deriva.
6. Variante con bodega única y con 2 bodegas, 1 proveedor y 0 proveedores,
   para probar que R1/R2 se cumplen: con 0 proveedores `select` de proveedor
   debe contar 0.
7. Primera versión: **medición sin falla** para fijar el presupuesto actual
   (línea base), luego bajarlo al objetivo a medida que se aplican cambios de la
   sección 5.

Riesgo: depende de un backend y frontend levantados (restricciones de
memoria: correr aislado, no en paralelo con la suite; `python scripts/run_tests.py`
rechaza arrancar con un servidor dev activo y este script lo requiere, así que
nunca en la misma sesión).

## 5. Lista priorizada de cambios

Esfuerzo: S (<0,5 día), M (1-2 días), L (3+). Riesgo de romper: B/M/A.

### 5.1 Sin opciones nuevas (solo presentación derivada de conteos)

| P | Cambio | Pantallas | Esf. | Riesgo |
|---|---|---|---|---|
| 1 | Plegar los 4 ajustes del plan en "Opciones" cerrado por defecto; subir con Auto/8 semanas/país detectado de la cuenta (mantener valores actuales de `useState`) | `/ventas` | S | B |
| 2 | Salto de "Confirma columnas" cuando el mapeo se detectó con certeza (el backend ya devuelve el reuso `mapping_reused`) | `/ventas` | M | M: un mapeo mal adivinado envenena todo lo que sigue; mantener el paso visible cuando haya duda |
| 3 | Panel: agrupar Optimizador/Anticípate/Demanda/Recs/Capital en una sola sección plegada "Más análisis"; R8 oculta el optimizador vacío | `/compras`, `HoyMobile` | M | B |
| 4 | Selector de proveedor y fila de proveedor en tarjeta solo si hay >=1 proveedor (R2) | `/compras`, `/inventario`, `/pedidos` | S | B |
| 5 | Sidebar de 11 a 5 entradas fijas; Proveedores, Mensajes, Actividad, Escenarios por reglas R2/R3/R5/R10 (siguen en paleta y URL) | `navItems.ts`, `Sidebar.tsx`, `MobileTabBar` | M | M: los usuarios que ya los conocen los buscan; la paleta (Ctrl K) y "Más" los listan |
| 6 | `/historial` y `Comparar` solo con >=2 sesiones completadas (R3); `ANALYSIS_TABS` filtrado | `/pronosticos`, `/historial` | S | B |
| 7 | Hub: filas condicionadas por conteo (R6, R7), `/instalacion` solo para operadores | `/configuracion` | S | B |
| 8 | Quitar `RunLineagePanel` y `PolicyBacktestPanel` de la vista comprador (R13) | `/pronosticos` | S | B |
| 9 | Inventario: "Qué pedir" con 4 columnas; "Por proveedor" solo con >=2 proveedores; menú Análisis solo con sesión completada y costos | `/inventario` | M | M (4626 líneas, 150 manejadores) |
| 10 | Tours: omitir pasos sin ancla visible; quickStart 12 -> 4 pasos | `components/tour/*` | S | B |
| 11 | Jerga: aplicar la tabla de la sección 6 (solo cambios de valores `es`/`en` en `translations.ts`, claves intactas) | todo | M | B |
| 12 | Banners de `/compras` (frescura, supuestos, atraso de proveedores, vencidas) en una sola franja "Datos a revisar (N)" | `/compras` | M | M |
| 13 | Empty states que enseñan un solo paso en Proveedores, Usuarios, Automatización, Historial, Escenarios (patrón ya existente: `HoyEmptyState` y `orders.empty_*`) | varias | S | B |
| 14 | Guard `basic_path.mjs` (4.3) | `Frontend/tests/` | M | B |

Orden sugerido: 14 (medir la línea base) -> 11 y 1 -> 7, 8, 6, 4 -> 3, 12 -> 5, 10 -> 9, 2.
Cada paso se verifica con el guard y un recorrido manual en navegador (regla del
proyecto: "testear es usar la app").

### 5.2 Notas de coherencia con CLAUDE.md

- "Stability over scope": todas las filas anteriores son retirar, plegar o
  condicionar lo que ya existe; ninguna agrega una función, endpoint o campo.
  Eso las encuadra como estabilidad/usabilidad, pero **el dueño debe nombrarlas
  como prioridad** (CLAUDE.md, punto 2) antes de ejecutar: este documento es
  un informe, no una autorización.
- Los textos se editan solo en `Frontend/src/i18n/translations.ts` (es y en,
  claves en inglés) y `landing.ts`; usar el skill `stockai-i18n` y sus scripts.
- No tocar las rutas (`/hoy`, `/pedidos`...), ni los valores persistidos
  (`PEDIR_YA`...): están fuera de alcance por decisión del dueño.

### 5.3 Necesita aprobación del dueño (requeriría una opción, modo, endpoint o campo nuevo)

1. **Interruptor "Modo simple / Modo avanzado"** o "Mostrar todo" por usuario.
   Sería una opción nueva y roza la regla de "sin feature gates"; las reglas R1-R15
   evitan precisamente esto. Hoy ya existe un estado oculto en `localStorage` (`adv`,
   `inventario/page.tsx:2046`) sin interfaz que lo escriba: decidir si se elimina
   o se formaliza.
2. **Endpoint `GET /tenant/facts`** (conteos de bodegas, proveedores, sesiones,
   usuarios, llaves en una sola llamada) para no hacer 5 llamadas al abrir la
   app. Es un endpoint nuevo; la alternativa sin él es reusar las listas ya
   consultadas.
3. **Asistente de bienvenida con checklist** ("1. sube ventas, 2. revisa 3. pide")
   persistido por tenant (campo `onboarding_state`). Es una pantalla y un
   campo nuevos.
4. **Recordar plegados/abiertos por usuario en servidor** (hoy solo
   `localStorage`/`sessionStorage`, p. ej. `rememberOrigin`).
5. **Plantillas de carga** ("tengo Excel de Alegra/Siigo") por sistema de
   origen: una capacidad nueva, y los conectores ERP fueron retirados el
   2026-09-20.
6. **Un "perfil de negocio" elegido al registrarse** (tienda / distribuidor /
   multi-sucursal) que preconfigure qué se muestra. Es un campo y una pantalla
   nuevos; las reglas por conteo lo vuelven innecesario, así que se desaconseja.
7. **Ocultar entradas del menú por rol más fino** (p. ej. "comprador" vs
   "analista"): requiere un rol nuevo en el backend.

## 6. Auditoría de jerga

Fuente: `Frontend/src/i18n/translations.ts` (valores `es`). La numeración de
línea es de la rama actual. Las claves no cambian; solo el texto.

| Término | Dónde aparece (clave) | Un dueño de negocio... | Alternativa en lenguaje llano |
|---|---|---|---|
| SKU / SKUs | `hoy.kpi_total_skus` ("Productos vigilados", ya bien); `sessions.col_skus`, `data.col_sku` ("SKU"), `errors.field.sku`, `errors.stock_sku_not_found`, `mobile.pedidos_card_skus` ("{n} SKUs"), `qs.field_sku`, `hoy.empty_bullet_2` | no lo conoce o lo llama "código" | "Producto" / "Código del producto"; "{n} productos" |
| Tiempo de entrega / lead time | `qs.field_lead_time`, `inventory.col_lead_time`, `inventory.col_lead_time_days` ("Días de entrega"), `suppliers.table_lead_time`, `hoy.assumptions_body` | ya dice "entrega", bien | "Días que tarda en llegar" |
| Variabilidad (±d) | `suppliers.table_variability`, `errors.field.lead_time_std` | no | "Cuánto se atrasa a veces" |
| MOQ / compra mínima | `inventory.calc_step_rounded_moq` ("Redondeado al MOQ", `translations.ts:3063`), `inventory.sim_footer_note` ("respetan el MOQ", `:1642`); la forma buena ya existe en `hoy.assumption_field_moq` ("la compra mínima"), `setupStock.import.field.moq` ("Compra mínima") | no | "Pedido mínimo del proveedor"; reemplazar "MOQ" en los 2 textos restantes |
| WAPE | `skus.stat_champion_wape` ("WAPE del elegido", `:2731`), `skus.col_wape` (`:2812`), `skus.pdf_col_wape`, `analyst.chip_what_is_wape` ("¿Qué es el WAPE?", `:2655`), `lineage.model_line` ("error medio {wape}") | no | "Error promedio" / "Qué tanto se equivoca"; en la vista comprador ocultar la columna |
| Semáforo | `hoy.optimizer_vs_semaforo`, `qs.subtitle` ("semáforo de inventario"), `hub.inventory_setup_desc` ("reglas del semáforo"), `errors.signal_thresholds_*` | la metáfora funciona (colores rojo/ámbar/verde) | Mantener como "semáforo" solo junto a los colores; en texto corrido: "alerta de stock" |
| PEDIR_YA / PEDIR_PRONTO / SOBRESTOCK | valores persistidos (fuera de alcance, ver CLAUDE.md); etiquetas mostradas por `enumLabels` | "Pedir ya" ok; "Sobrestock" es técnico | "Pedir ya", "Pedir pronto", "Sobra mercadería" |
| Backtest / prueba contra datos históricos | `audit.action.session.backtest_started` (ya "prueba contra datos históricos", `:2132`), sección `Policy backtest` (`:2870`) | no | "Prueba con el pasado: ¿habría acertado?"; solo vista técnica |
| Lineage / huella / manifiesto | `lineage.title` ("Cómo se produjo este pronóstico", bien), `lineage.data_fingerprint` ("Huella del archivo"), `lineage.forecast_fingerprint` ("Huella del pronóstico"), `errors.manifest_not_available`, `lineage.engine_version`, `lineage.configuration_note` ("archivo JSON") | no | Panel solo vista técnica/admin; "Huella" -> "Identificador"; ya evita "lineage" en español |
| Granularidad / nivel de detalle | `qs.plan_granularity_label` ("Nivel de detalle del plan"), `sessions.col_granularity` ("Detalle"), `skus.xls_granularity`, `dqissue.granularity_conflict.fix` | "detalle" casi ok, pero no sabe qué elegir | Quitar de la carga (Auto); si queda: "Cada cuánto resumimos tus ventas: por día / semana / mes" |
| Horizonte | `planning.horizon_label` ("Horizonte"), `errors.field.horizon`, `qs.plan_horizon_label` ("¿Hasta cuándo quieres planificar?", bien) | "horizonte" no | "Hasta cuándo planificar"; reemplazar "Horizonte" por "Planificas hasta" (como `sessions.col_horizon`) |
| Sesión / sesiones | `nav.sessions` ("Historial", bien), `sessions.*`, `chat.no_sessions`, `settings.session_label`, `users.perm_manage_sessions`, `errors.session_*` (~10) | confunde con "sesión de login" (`auth.login_title` también usa "sesión") | "Actualización" / "Cálculo" (ya aparece en `errors.manifest_not_available`: "Esta actualización") |
| Nivel de servicio | `errors.field.service_level`, `tours/hoy.ts` (`assumptions_body`) | no | "Qué tan seguro quieres estar de no quedarte sin producto" |
| Punto de reorden / stock de seguridad / cobertura | `tours/hoy.ts` (`why_body`), `inventory.calc_*` | cobertura sí ("días de stock") | "Cuándo pedir" / "Colchón" / "Días de stock" |
| Webhook / endpoint / API / llave de API / MCP | `settings.tab_webhooks`, `settings.webhooks_desc`, `settings.endpoint_url` (`:724`), `hub.automation_desc` ("llaves de API y webhooks"), `nav.api` | no | Ocultar (R6/R7); si se muestra: "Avisos automáticos a otro sistema"; "API" solo para quien la pide |
| Transferencia | `transfers.tab_transfers`, `hoy.transfers` | "pasar mercadería entre bodegas" | Ya claro; solo con 2+ bodegas (R1) |
| Entrenar / entrenamiento / modelo | `enum.webhook_event_job_completed` ("Entrenamiento completado"), `skus.xls_model`, `errors.session_still_training`, `qs.step3` ("El sistema aprende", bien) | "entrenar" es de analista | "Calcular" / "Actualizar pronóstico"; mantener "El sistema aprende" |
| ROI / Impacto | `nav.roi` ("Impacto", bien), `cmd.alias.roi`, `enum.activity_monthly_roi_email` ("retorno") | ok | "Cuánto ahorraste" |
| Baseline, champion, drift, SHAP | `skus.stat_champion_wape`, `errors.drift_no_reference_dataset`, `errors.shap_not_available` | no | Solo vista técnica |
| Capital en sobrestock / dinero parado | `inventory.view_dead_capital` ("Dinero parado", bien), `hoy.section_capital_opportunities` | bien | Mantener; es el mejor ejemplo del lenguaje del dueño |

Criterio para no romper nada: la lista de términos del guard (4.3) debe
incluir la tabla anterior; el skill `stockai-i18n` verifica paridad es/en.

## 7. Qué no es parte de esta auditoría

No se midió en navegador, no se arrancó ningún servicio y no se modificó código
de producto. Los conteos son estáticos; algunas pantallas (`/archivos`,
`/mi-cuenta`, `/inventario`) tienen subcomponentes con más controles que los
contados. Antes de implementar, correr el guard en modo medición (4.3, paso 7)
para fijar la línea base real y revisar las reglas propuestas contra el
recorrido de un tenant nuevo.
