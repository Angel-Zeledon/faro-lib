# Ventas por correo (inbound e-mail)

Una pyme reenvía su reporte de ventas (CSV o Excel) a una dirección privada de
su cuenta y el archivo entra como si lo hubiera subido. Sin iniciar sesión y sin
integraciones. Viene APAGADO: sin dominio ni secreto configurados, la tarjeta
dice "esta instalación todavía no puede recibir correo" y el webhook responde el
error estructurado `inbound_email_disabled` (HTTP 503).

## Qué necesita el dueño de la instalación para encenderlo

1. **Una cuenta en un proveedor de correo entrante** que pueda reenviar cada
   mensaje a una URL: Postmark (Inbound), Mailgun (Routes), Resend (Inbound) o
   cualquier relay propio. Esto es lo único que cuesta tiempo o dinero.
2. **Un dominio (o subdominio) solo para recibir**, por ejemplo `in.stockai.es`,
   con su registro **MX** apuntando al proveedor. No uses el dominio donde ya
   tienes buzones reales: el MX lo reemplazaría.
3. **Dos variables de entorno** (solo entorno, no se editan desde `/instalacion`):

   | Variable | Qué es |
   |---|---|
   | `INBOUND_EMAIL_DOMAIN` | El dominio del paso 2. Las direcciones son `sales+<token>@<dominio>`. |
   | `INBOUND_EMAIL_SECRET` | Secreto largo y aleatorio que autentica al proveedor. |

4. **Apuntar el proveedor al webhook**: `POST https://<tu-app>/api/v1/inbound/email`.

Reinicia el backend. La tarjeta "Ventas por correo" aparece activa en
Configuración, sección Datos, para los administradores.

## El contrato del webhook

`POST /api/v1/inbound/email`

**Content-Type permitido:** `application/json` o `multipart/form-data`
(cualquier otro: 415 `inbound_email_unsupported_content_type`).

**Tamaño máximo:** `MAX_UPLOAD_SIZE_MB` x 1.4 + 1 MB (el 1.4 cubre el base64).
Más grande: 413 `inbound_email_too_large`, sin leer el cuerpo.

**Autenticación** (cualquiera de las dos; sin ella, 401 `inbound_email_unauthorized`):

- Cabecera de firma, para un relay propio o un Worker:
  `X-StockAI-Signature: t=<unix segundos>,v1=<hex>` donde
  `v1 = HMAC_SHA256(INBOUND_EMAIL_SECRET, "<t>." + cuerpo_crudo)`.
  Se rechaza si `t` difiere más de 300 segundos de la hora del servidor
  (ventana anti-replay). La comparación es en tiempo constante.
- HTTP Basic, para proveedores que no firman con nuestro esquema (Postmark,
  Mailgun): configura la URL del webhook como
  `https://usuario:<INBOUND_EMAIL_SECRET>@<tu-app>/api/v1/inbound/email`.
  El usuario se ignora; la contraseña es el secreto. Basic no tiene ventana de
  tiempo, pero un reenvío del mismo mensaje no hace nada dos veces (ver abajo).

**Formas de cuerpo aceptadas** (los nombres de campo son tolerantes):

- JSON genérico: `{"from", "to", "message_id", "attachments":[{"filename","content_base64","content_type"}]}`
- JSON Postmark: `FromFull.Email`, `OriginalRecipient`, `MessageID`,
  `Attachments:[{Name, Content, ContentType}]`
- JSON Resend: `{"type":"email.received","data":{...forma genérica...}}`
- multipart Mailgun/SendGrid: campos `sender`|`from`, `recipient`|`to`,
  `Message-Id`; cada parte con archivo es un adjunto.

Un adjunto que el proveedor entrega solo como URL (sin bytes) no se puede leer:
el mensaje queda registrado como rechazado, nunca ignorado en silencio.

**Respuesta:** 200 con `{"outcome": ..., "results": [...]}` incluso cuando el
correo se rechaza (para que el proveedor no reintente). No revela si una
dirección o un remitente existen.

## Cómo se decide qué pasa con cada correo

1. **Destinatario -> cuenta.** El token de `sales+<token>@<dominio>` identifica
   la cuenta. Token desconocido: se descarta sin registrar nada.
2. **Remitente.** Debe ser un usuario verificado y activo de esa cuenta con rol
   `analyst` o `admin`, o una dirección de la lista que el administrador edita
   en la tarjeta. Un remitente desconocido se registra (`unknown_sender`) y
   **nunca se le responde**: contestar a cualquiera es la forma de convertir el
   buzón en fuente de backscatter.
3. **Adjuntos.** Solo `.csv .xlsx .xls .parquet .json`. Pasan por el mismo
   código que `POST /datasets` (`datasets.service.upload_dataset`): el tope de
   tamaño del plan (`max_dataset_size_mb`), el tope global
   `MAX_UPLOAD_SIZE_MB` y el rechazo de bytes NUL.
4. **Columnas.** Si las columnas del archivo contienen las de la última
   asignación confirmada de la cuenta (fecha, producto, cantidad), el resultado
   es `ingested` y se calcula el seguimiento de precisión como en una subida
   normal. Si no hay asignación confirmada o las columnas cambiaron, el archivo
   se guarda como `needs_review`, **no se adivina nada** y la cuenta recibe un
   aviso en la campana pidiendo confirmar las columnas en la app.
5. **Reentrenamiento.** Nunca se inicia un entrenamiento por sí solo. Solo si la
   cuenta ya tiene una programación activa (`scheduled_jobs.enabled`) cuya
   fuente de datos es un archivo con exactamente las mismas columnas, se
   reemplaza el archivo de esa fuente (el anterior queda en `previous/`, igual
   que "reemplazar archivo") y se lanza esa programación una vez.
6. **Idempotencia.** Una restricción única `(tenant, message_id, sha256 del
   adjunto)` hace que el reintento de un proveedor no haga nada la segunda vez.
   El mismo archivo en un mensaje distinto se registra como
   `duplicate_attachment` y no se guarda dos veces.

Cada adjunto recibido queda en `inbound_email_messages` (quién, cuándo, archivo,
resultado `ingested | needs_review | rejected` y código de motivo), y los datos
que entran quedan en el registro de auditoría como
`audit.inbound_email.received` con actor `system`. Regenerar la dirección y
cambiar los remitentes también se auditan (sin escribir nunca el token).

## Límites que conviene conocer

- La cabecera `From` de un correo no está autenticada por nosotros (no
  verificamos SPF/DKIM). La puerta real es el token secreto de la dirección:
  por eso la tarjeta pide no compartirla y permite regenerarla. Regenerar
  invalida la dirección anterior al instante.
- Un administrador y solo un administrador ve la dirección; las rutas
  `/inbound-email` y `/inbound/email` no son invocables con una llave
  `sk_live_*`.
- Si hay muchos remitentes desconocidos (más de 50 por hora en una cuenta)
  dejamos de escribir filas nuevas para no llenar la tabla; siguen yendo al log.
