# Plan general — Faro (features nuevas, mejoras de flujos y mejoras transversales)

> Fecha: 2026-07-18 (v3 — reordenado: lanzamiento previsto en ~3 meses (≈ octubre 2026), así que la capa comercial pasa a fase de pre-lanzamiento y el foco inmediato es **diseño y día a día del usuario final**. v3 añade 8 features de nicho verificadas contra el código: mermas, margen en carrito, calendario LatAm, price breaks, calendario de caja, aprobación por WhatsApp, FEFO y conteo cíclico)
> Base: auditoría integral (2026-07-04), features propuestas (2026-07-05) y **verificación directa del código de hoy** — este plan parte de lo que realmente existe, no de lo que decían los documentos anteriores.
> Regla de oro intacta: el cliente no compra modelos ML — compra **no quedarse sin stock**. Cada ítem se justifica por cuánto acerca esa promesa.

---

## 0. Estado real verificado (qué cambió desde la auditoría del 4 de julio)

Cerrado desde entonces (verificado en código, no asumido):

| Hallazgo de la auditoría | Estado hoy |
|---|---|
| #1 Inventario sin gates de rol | ✅ `require_analyst_or_above` en las mutaciones de `inventory.py` |
| #2 OTP válido 30 horas | ✅ `_OTP_EXPIRE_MINUTES = 15` |
| #4 Next.js con CVE | ✅ Next `14.2.35` (≥ 14.2.25) |
| #6 TESTING_MODE llega a prod | ✅ Fail-fast: el server no arranca con `TESTING_MODE=true` + `ENVIRONMENT=production` |
| #8 AI con timeouts de Ollama | ✅ Migrado a Claude API (`get_local_llm_client()`, Ollama solo como fallback) |
| #9 Email de verificación no confiable | ✅ Resend primario + SMTP fallback |
| #16 Navegación de 12+ páginas | ✅ Sidebar reducido a 5 grupos, `/hoy` primero |
| #21 Sin alertas proactivas | ✅ Loop diario 8:00 UTC + WhatsApp (Twilio) — la "feature #1" de la lista de 2026-07-05 ya existe |
| 1.4 Recepción de PO | ✅ `receivePO` + lead times aprendidos + scorecard de proveedores (2.5) |
| 2.3 Simulador de eventos | ✅ `simulateEvent` end-to-end |
| 3.3 Presupuesto optimizado | ✅ (parcial) `/inventory/optimize` MILP con transferencias multi-bodega |
| 2.2 Envío de PO al proveedor | 🔶 **En vuelo en `feat/po-send-to-supplier`**: backend + tests verdes + botón "Enviar pedido" — sin commitear |

Sigue abierto (verificado hoy):

- **No hay deploy** (sin CI — `.github/workflows` no existe; docker-compose sí).
- **No hay billing** (cero referencias a Stripe).
- **No hay conectores de datos** (Siigo/Alegra/WooCommerce/Shopify): el CSV manual sigue siendo el único camino.
- `datetime.utcnow()` naive residual en 8 archivos del backend.
- Worker de entrenamiento in-process, storage en disco local, rate limiter en memoria.
- Tokens JWT en `localStorage` (el rediseño de auth en curso es visual, no toca esto).
- **Violación de capas**: `import pandas` en `backend/api/v1/` (configuration, datasources, forecasts) y `backend/inventory/service.py` — CLAUDE.md manda "no pandas en backend".
- Páginas monolíticas (forecast ~1.900 líneas, inventory ~1.250) con estilos inline.
- Sin export/borrado de tenant (Habeas Data / Ley 1581).
- Accesibilidad del semáforo (color como único canal de significado, parcialmente mitigado).

**Lectura estratégica (v2):** las capacidades del producto ya existen (semáforo, alertas, PO end-to-end, recepción, scorecard). Con el lanzamiento a ~3 meses, la ventana actual es la oportunidad de convertir esas capacidades en una **experiencia diaria pulida**: que el jefe de compras resuelva su mañana completa dentro de Faro sin fricción, sin dudas y sin salir a Excel. Deploy, Stripe y CI son necesarios pero son trabajo de pre-lanzamiento (mes 3) — hacerlos hoy no mejora nada de lo que el primer usuario va a tocar.

---

## Fase 0 — Cerrar lo que está en vuelo (esta semana, esfuerzo: horas)

| # | Qué | Por qué | Estado |
|---|-----|---------|--------|
| 0.1 | Commitear y cerrar `feat/po-send-to-supplier` (backend + botón ya escritos, tests verdes, regresión completa verde: 707 passed) | Feature 2.2 completa el ciclo: la orden nace Y se envía desde Faro | 95% hecho |
| 0.2 | Terminar/commitear el rediseño de auth (login/signup) que está sin commitear | Primera impresión del producto; hoy vive mezclado en el working tree | En curso |
| 0.3 | Verificación manual del flujo completo: generar PO → enviar → recibir → scorecard | Es el loop de valor completo; nunca se ha recorrido de punta a punta como lo haría un cliente | Pendiente |

## Fase 1 — Flujo de activación: del signup al semáforo en <10 min (semanas 1–3)

El primer día del usuario. Hoy conviven `/quick-start` y el wizard de 8 pasos; el objetivo declarado (<10 min al semáforo) no se cumple con el wizard como camino visible.

| # | Qué | Por qué | Esfuerzo |
|---|-----|---------|----------|
| 1.1 | **Un solo camino feliz**: signup → "sube tu CSV o prueba con datos demo" → automapeo de columnas → defaults inteligentes → semáforo. El wizard queda como "modo avanzado" enlazado desde config | El momento de mayor intención de un trial es el minuto 1; cada paso extra es abandono | Medio |
| 1.2 | Post-login → `/hoy` directo (verificar redirect actual) y `/hoy` con estado vacío útil ("aún no tienes datos → botón al quick-start") | El valor diario a un clic; el estado vacío es el vendedor del onboarding | Bajo |
| 1.3 | Validador de CSV con errores por fila ("fila 214: fecha inválida") + plantilla descargable | El CSV mal formado es el fallo #1 de activación en esta categoría | Bajo-Medio |
| 1.4 | Demo de un clic visible en el signup/landing (el endpoint `demo.py` ya existe — falta hacerlo protagonista) | Ver el semáforo funcionando ANTES de invertir trabajo convierte curiosos en usuarios | Bajo |

## Fase 2 — El día a día: `/hoy` como cabina de mando del jefe de compras (semanas 3–7) 🥇 el corazón de esta versión del plan

Diseñado desde la mañana real del usuario: *llega la alerta de WhatsApp → abre Faro → decide qué pedir → envía las órdenes → registra lo que llegó → responde "¿tenemos X?" cuando alguien pregunta*. Cada ítem elimina una fricción concreta de ese recorrido.

| # | Qué | Por qué (fricción que elimina) | Esfuerzo |
|---|-----|-------------------------------|----------|
| 2.1 | **Generar → enviar en un solo flujo**: al aprobar el carrito en `/hoy` y generar la PO, ofrecer inline "Enviar a proveedores ahora" (reutiliza el envío recién construido) con resumen de a quién le llega qué | Hoy el usuario genera en `/hoy` y debe irse a `/pedidos` a buscar la orden para enviarla — dos pantallas para un solo acto mental | Bajo-Medio |
| 2.2 | **Avisos de recepción vencida en `/hoy`**: "El pedido a Acme debía llegar hace 2 días — ¿llegó?" con botón directo a registrar llegada (usa los lead times reales ya aprendidos) | Cerrar el loop de recepción hoy depende de la memoria del usuario; si no registra, el stock teórico deriva y el semáforo pierde credibilidad | Medio |
| 2.3 | **Buscador global de SKU** (barra superior / Ctrl-K): escribir un producto y ver al instante stock, cobertura en días, semáforo y mini-forecast en un panel | La pregunta más frecuente del día ("¿tenemos X? ¿cuánto nos dura?") hoy exige navegar a `/skus` y buscar en una tabla | Medio |
| 2.4 | **El "por qué" de cada recomendación**: expandir una línea del carrito muestra cobertura en días, demanda diaria pronosticada, lead time usado y stock actual — en lenguaje de negocio, no de ML | El usuario no ejecuta compras que no entiende; la confianza es el producto | Bajo-Medio |
| 2.5 | **Salud de datos de proveedores**: aviso en `/hoy`/`/pedidos` ("3 proveedores sin email/WhatsApp — el envío de órdenes los omitirá") con acceso de un clic a completar la ficha | El envío de PO omite silenciosamente proveedores sin contacto; el usuario lo descubriría en el peor momento | Bajo |
| 2.6 | **Estados vacíos, de carga y de error diseñados** en las 5 pantallas diarias (`/hoy`, `/pedidos`, `/skus`, `/inventory`, `/inventory/suppliers`) + interceptor único de errores en `api.ts` con toasts estándar | El pulido percibido vive en los estados intermedios; los errores silenciosos erosionan la confianza que el semáforo construye | Medio |
| 2.7 | **Sistema de diseño**: extender el lenguaje visual del rediseño de auth al resto de la app — tokens, tipografía y componentes en `components/ui/` reemplazando estilos inline, empezando por las pantallas diarias (y de paso descomponiendo `inventory` ~1.250 líneas) | Consistencia visual entre pantallas que hoy divergen; cada refactor de estilos inline acelera todo lo que venga después | Alto (incremental, por pantalla) |
| 2.8 | **Accesibilidad + idioma**: icono y etiqueta junto a cada color del semáforo, foco visible, `aria-label` en botones de icono; mover textos hardcodeados a `translations.ts` (ej. "Registrar llegada") | El semáforo es EL artefacto central — debe leerse sin distinguir colores; y el producto es español-primero | Bajo-Medio |
| 2.9 | **Registro de mermas y salidas no-venta** (roturas, vencidos, autoconsumo, regalos): entrada rápida "salió X por motivo Y" que descuenta stock y acumula el costo de la merma | Toda salida que no es venta hace derivar el stock teórico; cuando el semáforo se equivoca por eso, el usuario le pierde la fe — y la fe es el producto. Bonus: "cuánto pierdes por merma" alimenta el ROI mensual (3.2) | Bajo |
| 2.10 | **Margen visible en el carrito de `/hoy`**: "este pedido protege $X de venta con $Y de margen" — `precio_venta` y `costo_unitario` ya existen por SKU, solo falta mostrarlos | Hace visible plata que el sistema ya conoce; convierte la aprobación del carrito en una decisión de negocio, no de logística | Bajo |

## Fase 3 — Hábito y retención (semanas 7–10)

| # | Qué | Por qué | Esfuerzo |
|---|-----|---------|----------|
| 3.1 | **Deep-links en la alerta de WhatsApp** (cada SKU en PEDIR_YA aterriza en `/hoy` con el carrito pre-filtrado) + **PWA ligera** (manifest, viewport, layout responsive de `/hoy` y `/pedidos`) | La alerta ya existe; el salto es que el clic desde el celular aterrice en una pantalla que funcione en el celular. El jefe de compras vive en WhatsApp y la bodega | Medio |
| 3.2 | **Resumen mensual "Faro te ahorró $X"** (email automático + página): quiebres evitados, capital liberado, % de recomendaciones seguidas — los datos ya se registran en log-po/ROI | Es el argumento de renovación de los $99–299/mes, a veces ante el jefe del usuario | Bajo |
| 3.3 | Scorecard de proveedores como ritual: alerta cuando un proveedor se desvía de su lead time histórico ("Acme está tardando 12 días, no 7") | Convierte los datos propietarios (lead times reales) en momentos de valor visibles | Bajo-Medio |
| 3.4 | **Calendario LatAm precargado**: quincenas, aguinaldo/primas, Día de la Madre, Semana Santa, temporada escolar, Navidad — ya cargados como eventos que alimentan el simulador existente | Hoy el usuario tendría que crear cada evento a mano. Son datos, no código nuevo; demo-able ("Faro ya sabe que viene la prima") y moat local que un competidor global no construye | Bajo |
| 3.5 | **Escalas de precio del proveedor (price breaks)**: "si pides 50 más, el costo unitario baja 8% — te conviene adelantar la compra". El MOQ ya redondea cantidades; esto es su hermano natural | Convierte la recomendación en una decisión de plata, no solo de cobertura — el consejo que el jefe de compras presume con su jefe | Medio |
| 3.6 | **Calendario de caja (cuentas por pagar)**: estructurar `payment_terms` (hoy texto libre) en días de crédito por proveedor; con las POs enviadas, construir "esta semana te vencen $12M en facturas; la compra recomendada cabe / no cabe", cruzado con el optimizador de presupuesto ya existente | La restricción real del SMB LatAm es caja, no información; ningún forecast tradicional responde esto | Medio |
| 3.7 | **Aprobar el pedido respondiendo el WhatsApp**: la alerta diaria ya llega por WhatsApp; responder "1" aprueba el borrador de PO (webhook entrante de Twilio). Habilita además el flujo analista-prepara / dueño-aprueba | Cierra el ciclo completo sin abrir el navegador — en el canal donde el dueño ya vive | Medio |

## Fase 4 — Pre-lanzamiento comercial (mes 3, antes de la fecha de salida)

Movida deliberadamente al final del período: necesaria para lanzar, pero no mejora nada de lo que el usuario toca hoy. **Excepción:** el hardening (4.3) es barato y puede adelantarse en cualquier hueco.

| # | Qué | Por qué | Esfuerzo |
|---|-----|---------|----------|
| 4.1 | **Deploy en URL pública** (Railway/Render/Fly con el docker-compose existente) + CI mínimo (pytest + tsc en GitHub Actions) | Sin URL no hay lanzamiento | Medio |
| 4.2 | **Stripe Checkout** con los 3 tiers ya definidos ($99/$299/$799) + límite de SKUs por tier (las cuotas ya existen en settings) | Que alguien pueda pagar el día 1 | Medio |
| 4.3 | Hardening: migrar los 8 `utcnow()` restantes, rate limiter a Postgres, cookies httpOnly para tokens | Residuos de seguridad de la auditoría, todos de esfuerzo bajo | Bajo |
| 4.4 | Backups: storage de datasets/artifacts a Supabase Storage o S3 (la abstracción `file_store.py` ya existe) | Datos de clientes reales en un disco sin backup = riesgo inaceptable con el primer pago | Medio |

## Fase 5 — Sistema de registro de compras (post-lanzamiento, según tracción)

| # | Qué | Por qué | Esfuerzo |
|---|-----|---------|----------|
| 5.1 | **Conector Siigo** (empezar por el software del primer cliente pagado; Alegra segundo) con recarga programada (el scheduler ya existe) | Elimina el CSV recurrente — el trabajo manual que reactiva el hábito-Excel — y es el moat local | Alto |
| 5.2 | Auto-borrador de recompra para SKUs estables clase A ("Faro preparó este pedido, apruébalo") — nunca auto-envío en v1 | Evolución natural del carrito de `/hoy` + el envío de PO. De recomendar a ejecutar | Medio |
| 5.3 | Asistente IA que devuelve **carritos ejecutables**, no párrafos ("¿qué pido si viene Semana Santa?" → líneas de PO pre-llenadas usando el simulador ya construido) | Con Claude API ya estable, es el diferenciador demo-able; el RAG y el simulador ya existen — falta unirlos | Medio-Alto |
| 5.4 | Multi-bodega completo (el optimizador MILP ya sugiere transferencias; falta stock por bodega en UI y semáforo por ubicación) | Separa el tier Starter del Professional ($99 → $299) | Alto |

**Según el vertical de los primeros clientes pagos** (no construir hasta conocerlo — a un ferretero no le sirven, a uno de alimentos/farma le son casi obligatorias):

| # | Qué | Por qué | Esfuerzo |
|---|-----|---------|----------|
| 5.5 | **Lotes y vencimientos (FEFO)**: "tienes 40 días de cobertura pero el lote vence en 20 — promuévelo o devuélvelo". El SOBRESTOCK que además vence es pérdida directa | Casi obligatorio en alimentos, farma y consumo masivo; irrelevante en otros verticales — por eso espera al vertical real | Alto (dimensión de datos nueva) |
| 5.6 | **Conteo cíclico guiado + escáner de código de barras**: "hoy cuenta estos 10 SKUs clase A" desde el celular, con la cámara del PWA como escáner | Ataca la misma raíz que las mermas (2.9): exactitud del stock. Tiene sentido cuando el PWA de 3.1 ya exista | Medio-Alto |

## Deuda técnica transversal (1 ítem por semana, en paralelo)

Ordenada por riesgo real (los ítems de diseño/UX que estaban aquí se movieron a la Fase 2, que ahora es su lugar natural):

1. Sacar pandas de `backend/api/v1/` y `backend/inventory/service.py` hacia ForecastingCore o un módulo de transformación dedicado — hoy viola la separación de capas que CLAUDE.md declara.
2. Worker de entrenamiento como proceso separado (la cola en DB ya existe) — cada deploy mata jobs.
3. DB o pooler en región cercana a LatAm (hoy us-west-2, ~3s handshake).
4. Tests: pares de permisos y cross-tenant en los endpoints nuevos (send-PO ya los tiene; optimizer/warehouses/simulator hay que auditarlos).
5. Export ZIP del tenant + borrado en cascada (Costa Rica: **Ley 8968** de Protección de la Persona frente al Tratamiento de sus Datos Personales; Colombia: Ley 1581; GDPR) — necesario antes de clientes serios; a más tardar junto a la Fase 4.

---

---

## Actualización 2026-07-21

- **Plan-based entitlements mergeado a `main`** (feature nueva, fuera del alcance original de este plan): feature gating (17 features) + límites numéricos (SKUs/usuarios/ubicaciones/sesiones) + trial Starter de 14 días con read-only al vencer, todo derivado de `tenant.plan`. Enforcement en el chokepoint `upsert_stock` + guards de API; catálogo único en `backend/entitlements/`. Suite de la feature verde; barrido definitivo cerró 5 huecos sucesivos de bypass/escritura parcial de límites.
- **Riesgo abierto #2 RESUELTO:** el detector de recepción vencida (`_effective_lead_time`) ya no confía en el promedio aprendido con n=1; exige `MIN_LEAD_TIME_OBSERVATIONS` (=3), igual que el cálculo del semáforo. (rama `fix/lead-time-min-observations`)
- **Riesgo abierto #6 PARCIAL:** el export CSV de la orden ahora etiqueta el lead time y su origen (columnas "Lead time (días)" y "Origen lead time" = Aprendido/Configurado). Falta la etiqueta in-app en `/inventory` (requiere pasar `lead_time_source` al tipo del front) — pendiente menor.
- **0.3 (verificación en navegador) — parcial:** verificada end-to-end la UI nueva de entitlements en el navegador (backend :8010 + front :5000): signup crea tenant `starter` + trial de 14 días; nav bloquea "Asistente IA" con candado + tooltip "Disponible en un plan superior"; click abre el UpsellModal ("Ver planes" → `/planes`); al vencer el trial aparece el banner read-only. **Sin issues.** Follow-ups conocidos: UpsellModal usa copy genérico (no nombra el plan) y la ruta `/planes` aún no existe. El recorrido del flujo diario con datos (semáforo→PO→recepción) sigue pendiente.

## Actualización 2026-07-22

- **0.3 COMPLETO — recorrido en navegador del flujo diario con datos, sin errores de consola.** Tenant nuevo (`verificacion-0-3@example.com`, Distribuidora Verificacion 0.3) recorrido end-to-end contra backend :8010 + front :5000:
  - Signup → pantalla "Revisa tu correo" (email de verificación salió por SMTP real) → login rebota a `/hoy` con estado vacío real y CTA de demo.
  - "Probar con datos demo" → auto-entrenamiento (~90 s) → redirige al semáforo poblado: 5 SKUs, 2 PEDIR_YA / 3 SOBRESTOCK, valor de inventario en colones (₡27 590). Vista Proveedor agrupa con urgencias, lead time, ABC y cantidad editable.
  - `/hoy`: cards urgentes con "Ver por qué" (números coherentes: 40 uds / 33,1 por día = 1 día; reorden 345 ≈ 33,1×10; lead time etiquetado "configurado por ti"), Aprobar/Rechazar con Deshacer, carrito con total, "Descargar orden de compra" → `POST /inventory/log-po` 201 y fila + ítems en `inventory_po_log`/`inventory_po_items`.
  - `/pedidos`: aviso reactivo de proveedores sin email/WhatsApp con "Completar ficha" (crea el proveedor prellenado); "Enviar pedido" → **"Enviado parcialmente"** correcto: email SMTP al proveedor con ficha, omitido el que no tiene contacto; `sent_at` grabado.
  - "Registrar llegada" con recepción parcial (Arroz 400/475) → estado "Parcial", stock actualizado en DB (+312/+400), 2 observaciones en `supplier_lead_time_obs` (0 días — mismo día; inocuo por el mínimo de 3 observaciones), y el semáforo se recalcula: ambos PEDIR_YA pasan a "Pedir pronto" y el Arroz sugiere pedir las 75 unidades faltantes.
- **Hallazgos menores del recorrido — los 3 primeros RESUELTOS el mismo día** (rama `polish/po-flow-0-3-findings`, mergeada a `main`; spec `docs/superpowers/specs/2026-07-22-po-flow-polish-design.md`):
  1. ✅ Confirm de "Enviar pedido": reemplazado por `ConfirmDialog` que muestra ANTES del envío a qué proveedores llega y cuáles se omiten por falta de contacto (sin timer que perder).
  2. ✅ Número de orden legible: `po_number` correlativo por tenant (migración + backfill con offset anti-colisión + índice único), mostrado como `OC-000123` en el asunto/cuerpo del email al proveedor y columna "Orden" en `/pedidos`.
  3. ✅ Copy del carrito: amarrado al margen ("El margen protegido no incluye N SKU(s) sin precio de venta registrado"); cuando ningún aprobado tiene precio, invitación a registrar precios.
  4. Sigue pendiente (ya conocido): etiqueta de origen del lead time in-app en `/inventory` y la ruta `/planes` del UpsellModal.
- **Fix extra:** test pre-existente `test_chats.py::TestRateLimit` roto por el gate de entitlements (403 antes del rate limit al apagar `testing_mode`) — el test ahora sube el tenant a `professional`.
- **Follow-ups nuevos del review final de esa rama (menores, no bloqueantes):**
  - El preview del diálogo de envío compara nombres de proveedor **case-sensitive**; el backend resuelve case-insensitive — un desajuste de mayúsculas listaría un proveedor como "se enviará" y el envío real lo omitiría. Fix de una línea (comparar en lowercase).
  - Gate del caveat de margen (`priced.length > 0`) vs gate de la fila de margen (`salesProtected > 0`) difieren cuando `sale_price === 0`.
  - `convertOrderToPO` (`hoy/page.tsx`) no deshabilita el botón en vuelo — un doble click crea 2 POs idénticas (observado en vivo: OC-000002/3). Pre-existente a la rama.
  - Asignación de `po_number` reintenta 1 vez ante carrera de 2; una carrera de 3+ simultáneos aún daría 500 (aceptado: volumen humano).
  - `SendPOButton` muestra "Enviando…" mientras el diálogo de confirmación está abierto (funciona como guard de doble click; etiqueta imprecisa).

## Estado de ejecución — actualizado 2026-07-19

Cerrado y mergeado a `main` (suite backend **818 passed, 19 skipped**; `tsc --noEmit` exit 0):

| Ítem | Estado | Nota |
|---|---|---|
| Fase 0 (0.1, 0.2) | ✅ | 0.3 (recorrido manual end-to-end) **sigue pendiente** |
| 1.1, 1.4 | ✅ | camino único signup→semáforo + demo protagonista |
| 1.2 | ✅ | `/hoy` con estado vacío real + login rebota a `/hoy` |
| 1.3 | ✅ | validador CSV por fila + plantilla canónica descargable |
| 2.1, 2.2, 2.3, 2.9 | ✅ | generar→enviar, recepción vencida, buscador global, mermas |
| 2.4 | ✅ | + cambio de fondo: el lead time **aprendido** ahora alimenta el cálculo, no solo el texto |
| 2.5 | ✅ | endpoint real; eliminó lógica de negocio que vivía en el frontend |
| 2.10 | ✅ | margen `None` ≠ `0`; margen negativo ya no se aplasta a 0 |
| 3.3 | ✅ | regla SPC 3-sigma con mediana + IQR/1.349 |
| 3.4 | ✅ | catálogo LatAm, fechas móviles calculadas — **Costa Rica (mercado objetivo, `DEFAULT_COUNTRY`) y Colombia poblados** |
| 2.8 | ✅ | contraste del semáforo estaba roto (2.1:1 y 3.4:1, reprobaban AA) — corregido |
| 2.6 | ✅ | `669f940` (2026-07-19): `ApiError` con kind estable + interceptor único en `request()` → toast estándar vía `ApiErrorBridge`; primitivos `Skeleton`/`EmptyState`/`ErrorState` en `components/ui/States.tsx`, en uso en las 5 pantallas diarias — la tabla no se había actualizado |
| 5.4 (backend) | ✅ | 2026-07-22, rama `feat/multi-warehouse`: semáforo por (SKU, bodega) con pasada de red (acción `transfer` cuando otra bodega puede donar manteniendo ≥30 días), transferencias enviar→recibir atómicas con parciales, PO con bodega destino, demanda por bodega (forecast por tienda `sku│store` o `demand_share` manual). Spec: `docs/superpowers/specs/2026-07-22-multi-warehouse-complete-design.md`. **UI pendiente** (plan aparte: slices 6–7) |

**Riesgos abiertos de lo recién mergeado:**

1. **Nada verificado en navegador.** Cuatro clusters de UI integrados con typecheck y tests de backend, cero verificación visual. Es el pendiente #1 y coincide con el ítem 0.3.
2. **Inconsistencia de confianza en `supplier_lead_time_obs`:** 3.3 exige ≥6 recepciones para alertar, pero 2.4 confía en el promedio aprendido con **1 sola observación** (`AVG` sin `HAVING COUNT(*) >= N`). Una recepción atípica sobrescribe el lead time configurado de ese proveedor en todos sus SKUs. Propuesta: mínimo 3 observaciones.
3. **Validación de fechas más estricta** (1.3): un CSV con formato de fecha exótico en todas las filas puede ahora cruzar el umbral fatal del 20% y bloquear una subida que antes pasaba.
4. **3.3 no se ve en tenants nuevos:** ≥6 recepciones registradas antes de la primera alerta posible.
5. Multiplicadores del calendario (×1.4 quincena, ×2.2 Black Friday, ×2.0 Día de la Madre) son **estimaciones informadas, no ajustadas con datos reales**. Sirven para demo; como predicción son un supuesto. Mejora natural: aprenderlos por tenant desde el historial.
7. **DOCUMENTACIÓN DESALINEADA CON EL MERCADO REAL.** El mercado ancla es **Costa Rica** (confirmado 2026-07-19), pero `docs/auditoria_integral_faro_2026-07-04.md`, `docs/plans/2026-06-02-producto-ganador.md` y `docs/features_propuestas_faro_2026-07-05.md` siguen diciendo "ancla Colombia". Tres consecuencias reales, no cosméticas:
   - El **moat de conectores documentado es Siigo/Alegra — software contable colombiano**. Para CR hay que definir el equivalente real; el ítem 5.1 del plan ("Conector Siigo") está apuntando al país equivocado.
   - El régimen legal citado (Ley 1581) es colombiano; para CR aplica la Ley 8968.
   - Moneda y formato: colón (CRC), no COP.
6. `/inventory` y el export CSV muestran el lead time aprendido sin etiquetar su origen.

**Siguiente:** 0.3 (recorrido manual) → 2.6 (estados vacíos/carga/error + interceptor en `api.ts`) → 2.7 (sistema de diseño). 2.6 y 2.7 se dejaron fuera del lote paralelo a propósito: tocan las mismas 5 pantallas diarias que este lote acaba de mover.

---

## Orden de ejecución recomendado

```
Fase 0 (cerrar rama 2.2 + auth)                                  ← esta semana
→ 1.1 Camino único CSV→semáforo + 1.4 Demo protagonista          ← activación (sem 1-3)
→ 1.2 /hoy como home + 1.3 Validador CSV
→ 2.1 Generar→enviar en un flujo + 2.5 Salud de proveedores      ← día a día (sem 3-7)
→ 2.2 Recepción vencida en /hoy + 2.4 "Por qué" de cada línea
→ 2.3 Buscador global + 2.6 Estados/toasts
→ 2.9 Mermas + 2.10 Margen en carrito           (baratas, refuerzan la verdad del semáforo)
→ 2.7 Sistema de diseño + 2.8 Accesibilidad     (incremental, por pantalla tocada)
→ 3.4 Calendario LatAm                          (quick win demo-able, en cualquier hueco)
→ 3.1 Deep-links WhatsApp + PWA → 3.2 ROI mensual → 3.3 Alertas scorecard   ← hábito (sem 7-10)
→ 3.5 Price breaks → 3.6 Calendario de caja → 3.7 Aprobar por WhatsApp      ← si el ritmo lo permite; si no, post-lanzamiento
→ Fase 4 completa (deploy + CI + Stripe + hardening + backups)   ← mes 3, pre-lanzamiento
→ Fase 5 según tracción post-lanzamiento
Deuda técnica: 1 ítem por semana en paralelo, empezando por pandas-en-API.
```

**La lógica (v2):** con 3 meses de pista, el tiempo escaso no es el de infraestructura (deploy + Stripe caben cómodos en el mes 3) sino el de **iterar la experiencia diaria hasta que se sienta inevitable**. Primero que el usuario nuevo llegue solo al semáforo (Fase 1), luego que su mañana completa — decidir, enviar, recibir, consultar — viva en Faro sin fricción (Fase 2), luego que el hábito lo sostenga el producto y no la memoria (Fase 3), y al final la caja registradora (Fase 4).

## Lo que deliberadamente NO haría ahora

- Más modelos ML (el noveno modelo no vende más que el octavo).
- App nativa (PWA cubre el caso móvil real).
- Features enterprise (SSO, white-label) antes del primer cliente de $799.
- Más dashboards analíticos — el déficit sigue estando en acción y pulido del flujo diario, no en información.
- Adelantar Stripe/deploy "para avanzar": son tareas acotadas de mes 3; hacerlas hoy es robarle semanas a la capa que el usuario sí toca.
- Rediseños big-bang: el sistema de diseño (2.7) avanza pantalla por pantalla, siempre montado sobre una mejora funcional de esa pantalla.
