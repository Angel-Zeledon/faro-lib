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
