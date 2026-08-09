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
| Pronósticos | `/pronosticos` | 17 | — | — | Todo |
| Archivos / Fuentes | `/archivos` | 40 | — | — | Todo salvo el rename (leído en código, no caminado) |
| Panel de compras | `/compras` | 14 | 2026-08-09 | Optimizador (horizonte, transferencias sin ciclos, explicación vs semáforo); aprobar y rechazar recomendaciones; carrito de aprobados; generar OC; resumen ejecutivo con datos reales | Envío a proveedores, selección de bodega destino, edición de cantidades, deshacer aprobación |
| Mis ventas | `/ventas` | 11 | 2026-08-09 | Subida, mapeo, gate con remediaciones, entrenamiento completo; **archivo cp1252 con `;`, fechas dd/mm/yyyy y SKUs acentuados** — acentos intactos y día-primero resuelto solo | Reusar archivo ya subido, repetir carga anterior, datos de ejemplo, cancelar a media corrida |
| Mi cuenta | `/mi-cuenta` | 23 | 2026-08-06 | Zona horaria (lectura y cambio), lista de modelos | Moneda, WhatsApp, cambio de contraseña, tema/idioma, granularidad, registros de actividad |
| Landing | `/` | 8 | — | — | Todo |
| Asistente IA | `/asistente` | 14 | — | — | Todo (necesita Ollama o clave Anthropic) |
| Usuarios | `/usuarios` | 19 | 2026-08-09 | Crear usuario con rol (queda `pending_confirmation`, sin verificar); filtros de estado y rol; **permisos ejercidos como viewer real**: escrituras rechazadas con 403 y estado sin cambiar (ver abajo) | Editar usuario, suspender/reactivar, cambiar rol de otro, reenviar invitación, no poder degradarse a sí mismo |
| Escenarios | `/escenarios` | 6 | 2026-08-06 (parcial) | Solo el muro de plan para tenant Starter | La pantalla entera con un plan que la incluya |
| Automatización | `/automatizacion` | 14 | 2026-08-06 | Programaciones armadas, historial, zona horaria, re-anclaje | Llaves de API, webhooks, pausar/eliminar programación |
| Proveedores | `/proveedores` | 6 | 2026-08-06 (solo API) | Campos de lead time aprendido/inutilizable | La pantalla; alta y edición de proveedor; scorecard |
| Impacto | `/impacto` | 0 | — | — | Todo |
| Integraciones | `/integraciones` | 3 | — | — | Todo |
| Mensajes | `/mensajes` | 4 | — | — | Todo |
| Registro | `/signup` | 2 | 2026-08-09 | Alta completa (tenant + admin), rechazo por WhatsApp duplicado sin dejar filas varadas, aviso honesto cuando no se puede enviar el correo | Correo duplicado, validaciones de contraseña una por una, reenvío de verificación |
| Recuperar contraseña | `/forgot-password` | 4 | — | — | Todo (el arreglo de sesión se verificó en código, no caminado) |
| Historial | `/historial` | 7 | 2026-08-09 | Motivo de fallo en sesiones fallidas; lista completa con archivo, horizonte, granularidad y SKUs | Renombrar, eliminar, comparar. **"Activar sesión" no existe** — la sesión activa se deriva de la familia más nueva + el período activo, no se elige; estaba mal listada como acción pendiente |
| Scorecard proveedor | `/proveedores/scorecard` | 0 | — | — | Todo |
| Iniciar sesión | `/login` | 3 | 2026-08-06 | Login de tres cuentas con roles distintos | Credenciales malas, cuenta suspendida, cierre entre pestañas (verificado por evento, no con dos pestañas reales) |
| Pedidos | `/pedidos` | 3 | 2026-08-09 | Lista con OC generada (número, urgentes, unidades, estado "En camino"); registrar llegada **parcial** — suma solo lo recibido y deja la OC en `partial` | Llegada completa, nueva orden manual, enviar pedido, WhatsApp (abrir/copiar/enviarme), recibir de más |
| Planes | `/planes` | 0 | — | — | Todo |
| Restablecer contraseña | `/reset-password` | 2 | — | — | Todo |
| Verificar correo | `/verify-email` | 1 | 2026-08-09 | Token válido activa la cuenta y habilita el login | Token vencido, token ya usado, token manipulado |
| Configurar inventario | `/configurar-inventario` | 0 | — | — | Todo |

**Resumen honesto (2026-08-09):** 13 pantallas de 25 tienen alguna caminata, y
ninguna está caminada entera. Las 12 restantes están **sin medir**.

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

**Lo que sigue abierto, encontrado al verificar lo anterior:** `/compras` tiene
el mismo patrón — le muestra "Aprobar" y "Rechazar" a un viewer, y al generar la
orden el `POST /log-po` devuelve 403. Ahí **sí** avisa ("Tienes el CSV, pero no
pudimos registrar la orden…"), así que no es mudo; pero cierra con "Genérala de
nuevo", que para un viewer es la misma promesa vacía que ya se corrigió en
inventario. Falta gatear los botones por rol y distinguir el 403. **Sin
arreglar.**

Dos casillas que la tabla daba por pendientes y que **no existen como acción**:
"activar sesión" en `/historial`, y el borrado de cuenta — `DELETE /tenant` y
`/tenant/export` no tienen pantalla, son solo API, así que no hay forma de
caminarlos y quedan cubiertos únicamente por tests.

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
