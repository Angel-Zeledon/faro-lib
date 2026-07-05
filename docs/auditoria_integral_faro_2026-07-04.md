# Auditoría Integral — Faro (plataforma de forecasting y decisiones de compra de inventario)

> Fecha: 2026-07-04
> Alcance: backend (FastAPI + Supabase Postgres), Frontend (Next.js 14), ForecastingCore (ML), historial de QA y estrategia de producto documentada.

---

## 1. Comprensión del producto

**Qué es:** SaaS B2B multi-tenant que convierte historial de ventas en decisiones de compra de inventario. El pipeline: subes ventas (CSV/Excel) → entrena 7+ modelos por SKU (LightGBM, XGBoost, ARIMA, Prophet, ETS, Croston, LSTM) con clasificación automática de series → cruza el forecast con stock actual → produce un semáforo diario (PEDIR_YA / PEDIR_PRONTO / OK / SOBRESTOCK), cantidades recomendadas y órdenes de compra exportables por proveedor.

**Problema que resuelve:** quiebres de stock y sobrestock en distribuidoras/comercializadoras que hoy deciden compras "a ojo" en Excel.

**Usuario objetivo:** jefe de compras o dueño de distribuidora LatAm (ancla Colombia), 50–2.000 SKUs, sin data scientist. **Modelo de negocio:** suscripción ($99/$299/$799 por tier según SKUs/usuarios/ubicaciones).

**Propuesta de valor real:** no vende "modelos ML", vende *"sabe lo que necesitas antes de que te falte"* — la página `/hoy` (briefing matutino + carrito de acciones aprobar/modificar/rechazar → PO) es el producto; todo lo demás es soporte.

**Funcionalidades identificadas:** auth multi-tenant con roles y verificación de email; wizard de forecast de 8 pasos; inventario completo (stock, bulk import, eventos, proveedores, BOM, requerimientos de producción, dead stock, historial de PO, ROI); SKU Intelligence con gráficos histórico+forecast+intervalos de confianza; analista AI con RAG sobre resultados y documentos (Pinecone + LLM local Ollama); reportes PDF/Excel; API keys, webhooks, scheduling; monitoreo de drift; i18n ES/EN.

**Faltantes importantes:** alertas proactivas (email/WhatsApp), integraciones con facturación LatAm (Siigo, Alegra) y e-commerce (WooCommerce/Shopify), multi-ubicación, billing (Stripe), y — crítico — no está desplegado: no hay producto vendible sin deploy, email transaccional confiable y hardening.

---

## Tabla maestra de hallazgos

| # | Categoría | Hallazgo | Severidad | Impacto usuario | Impacto negocio | Propuesta de solución | Prioridad | Esfuerzo |
|---|-----------|----------|-----------|-----------------|-----------------|----------------------|-----------|----------|
| 1 | Seguridad | **Ningún endpoint de inventario valida roles** (`backend/api/v1/inventory.py` — las ~40 rutas usan solo `get_current_user`): un *viewer* puede mutar stock, proveedores, BOM, eventos y registrar POs | Crítico | Un usuario de solo lectura altera datos que disparan compras reales | Pérdida de confianza enterprise; bloqueo en due diligence | Añadir `require_role("analyst")` a todas las mutaciones + tests de pares de permisos | Alta | Bajo |
| 2 | Seguridad | OTP de reset de contraseña de 6 dígitos **válido 30 horas** (`auth.py:69-78`), con rate limit de 10 intentos/10min → ~1.800 intentos posibles por ventana | Crítico | Cuenta comprometible por fuerza bruta paciente | Incidente de seguridad pre-lanzamiento | Expiración 10–15 min, invalidar tras 5 fallos, un solo código activo | Alta | Bajo |
| 3 | Seguridad | Rate limiter de auth **en memoria por proceso** (`auth.py:38-52`): se resetea al reiniciar y no funciona con >1 worker/instancia | Alto | — | El control anti-brute-force desaparece justo al escalar | Mover a Postgres o Redis (tabla `rate_limits` con ventana deslizante) | Alta | Bajo |
| 4 | Seguridad | Access token JWT en `localStorage` (`Frontend/src/lib/auth.ts`) — exfiltrable vía XSS; agravado por dependencia `xlsx@0.18.5` (CVEs conocidas: prototype pollution/ReDoS) y Next 14.2.5 (< 14.2.25, afectado por CVE-2025-29927) | Alto | Robo de sesión ante cualquier XSS | Riesgo reputacional | Cookies httpOnly + SameSite para tokens; actualizar Next y reemplazar `xlsx` (exceljs o versión CDN parchada) | Alta | Medio |
| 5 | Seguridad | `datetime.utcnow()` naive aún en `auth.py:78` — misma clase de bug que BUG-9 ya corregido en `jwt_handler.py` (expiración desplazada por offset del host) | Medio | Ventanas de expiración incorrectas en hosts no-UTC | Bugs fantasma difíciles de reproducir | `grep utcnow` en todo el backend y migrar a `datetime.now(timezone.utc)` | Alta | Bajo |
| 6 | Seguridad | `TESTING_MODE=true` en `.env` local bypasea **todas** las cuotas/límites; un descuido lo lleva a prod | Alto | — | Plan gratis ilimitado por accidente | Fail-fast: si `TESTING_MODE=true` y `ENV=production`, negarse a arrancar | Alta | Bajo |
| 7 | Bug | Validación "fecha inicio > fecha fin" de eventos de inventario está rota y marcada `pytest.xfail` — el bug se envía silenciosamente | Alto | Eventos con rangos imposibles corrompen el forecast que los consume | Recomendaciones de compra erróneas | Arreglar el validador, quitar el xfail | Alta | Bajo |
| 8 | Bug | Endpoints AI (chat, narrador, analista) dependen de Ollama en CPU: prompts triviales >60s → timeout httpx → el usuario ve errores 500/502 | Alto | La funcionalidad "AI" parece rota | La feature diferenciadora da vergüenza en demos | Timeout configurable + streaming + modelo pequeño (gemma/qwen 2-4B) o API pagada para prod; degradar con gracia a plantillas | Alta | Medio |
| 9 | Bug/UX | Login exige email verificado pero el email sale por SMTP Gmail personal (o solo se loguea la URL): si no llega, el usuario queda **bloqueado en el signup** | Crítico | Abandono en el minuto 1 | Mata la conversión de todo el funnel | Resend/SendGrid + botón "reenviar verificación" + no exigir verificación los primeros N días | Alta | Bajo |
| 10 | Arquitectura | Worker de entrenamientos corre como thread **dentro del proceso API**: cada deploy aborta jobs (mitigado marcándolos FAILED, pero el trabajo se pierde) | Alto | "Mi entrenamiento falló sin razón" | No escala horizontal; deploys = downtime funcional | Extraer worker a proceso propio con la cola DB existente; reintentos automáticos | Media | Medio |
| 11 | Arquitectura | Storage en disco local (`storage/datasets`, `artifacts`, `documents`) — imposible correr 2 instancias; sin backups | Alto | Pérdida de datos ante fallo de disco | Bloquea escalar y da riesgo de pérdida de datos de clientes | Migrar a S3/Supabase Storage (la abstracción `file_store.py` ya existe) | Media | Medio |
| 12 | Rendimiento | DB Supabase en us-west-2 con handshake de ~3s por conexión, para clientes LatAm; pool pre-calentado lo mitiga pero toda query viaja lejos | Medio | Latencia perceptible en cada pantalla | Percepción de producto lento | Región sa-east-1 (São Paulo) o supavisor pooler; medir p95 por endpoint | Media | Bajo |
| 13 | Rendimiento | Polling (documentos cada 3s, training por jobs) pese a existir un router WebSocket; requests repetidos sin cache del briefing | Bajo | Consumo de red/battery, refresco perceptible | Costo de infra innecesario | Consolidar en el WS existente o SSE; cache de briefing con TTL | Baja | Medio |
| 14 | UI/Código | Páginas monolíticas con estilos inline: `forecast/page.tsx` **1.967 líneas**, `inventory` 1.252, `dashboard` 945, `hoy` 907. Tailwind está instalado pero casi no se usa | Medio | Inconsistencias visuales entre pantallas; bugs de UI | Velocidad de desarrollo cae con cada feature | Sistema de componentes (los 16 de `components/ui` son el inicio); extraer secciones del wizard; adoptar Tailwind de verdad | Media | Alto |
| 15 | UX | Wizard de 8 pasos para llegar al primer forecast; el objetivo declarado es <10 min al semáforo. Existe `/quick-start` pero conviven dos caminos | Alto | Fricción máxima en el momento de mayor intención | Abandono en onboarding = no hay conversión trial→pago | Un solo camino feliz: subir CSV → mapeo automático de columnas → defaults inteligentes → semáforo; el wizard queda como "modo avanzado" | Alta | Medio |
| 16 | UX | Navegación de 12+ páginas (Dashboard, Hoy, Data, Forecast, SKUs, Inventory, Suppliers, ROI, Producción, Accuracy, Analyst, Documents, Reports, Users, Config...) para un usuario no técnico | Medio | Parálisis de elección; no sabe dónde vivir | Menos engagement diario | `/hoy` como home por defecto; agrupar el resto bajo 3 secciones; ocultar Producción/BOM si el tenant no es fabricante | Media | Bajo |
| 17 | UX | Manejo de errores frontend históricamente inconsistente (bugs pasados de `res.answer` vs `res.data`, claves i18n faltantes `pw_error_*`) — sin capa uniforme de errores/toasts | Medio | Errores silenciosos o crípticos | Tickets de soporte | Interceptor único en `api.ts` con toast estándar + estado de error por página | Media | Bajo |
| 18 | Calidad | Suite de tests con patrones "que nunca fallan" (asserts either/or, verificación por eco), sin tests de mutación cross-tenant, y camino FAILED de training sin cubrir — auditoría interna del 2026-06-22 lo documenta | Alto | Bugs llegan a usuarios (ya pasó: /quality, /report, JWT expiry) | Cada release es una apuesta | Ejecutar el top-10 de fixes ya priorizado en esa auditoría; CI verde con `TESTING_MODE=false` | Alta | Medio |
| 19 | Higiene | Repo con basura: logs, `a.txt`, `26.1.1`, `=0.28.0`, PDF personal (`Guia_Influencia_Interpersonal.pdf`), decenas de `storage/artifacts/ten_*` sin ignorar, `tsconfig.tsbuildinfo` trackeado | Bajo | — | Fugas de datos de prueba al repo; ruido en cada PR | `.gitignore` para `storage/`, `*.log`, buildinfo; limpiar raíz | Media | Bajo |
| 20 | Producto | Sin billing (Stripe), sin deploy (docker-compose existe pero no hay entorno vivo), sin dominio/landing | Crítico | No puede pagar aunque quiera | Ingresos = $0 estructuralmente | Deploy en Railway/Render + Stripe Checkout con los 3 tiers ya definidos | Alta | Medio |
| 21 | Producto | Sin alertas proactivas: el valor ("avisarme antes de que falte") exige que Faro te busque; hoy el usuario debe entrar a `/hoy` | Alto | Si olvida entrar un día, hay quiebre de stock | La retención depende de un hábito no asistido | Email diario del briefing (ya existe el JSON) + WhatsApp (Twilio) para PEDIR_YA | Alta | Medio |
| 22 | Producto | Sin integraciones de datos: el CSV manual es el único camino; el dato de ventas envejece cada día | Alto | Trabajo manual recurrente; forecast desactualizado | Churn: el Excel-hábito regresa | Conectores Siigo/Alegra/WooCommerce/Shopify (empezar por 1); recarga programada con el scheduler existente | Alta | Alto |
| 23 | Seguridad | CORS con `allow_methods=["*"]`/`allow_headers=["*"]` + credentials y orígenes localhost hardcodeados en `main.py:136` | Bajo | — | Superficie innecesaria en prod | Lista explícita por entorno | Baja | Bajo |
| 24 | Accesibilidad | Colores hardcodeados (#ef4444 etc.) con significado semántico (rojo=urgente) sin texto alternativo consistente; sin evidencia de foco/ARIA en componentes propios | Medio | Usuarios daltónicos no distinguen el semáforo | Riesgo en ventas enterprise | Iconos + etiquetas junto al color (ya parcial con RecIcon); auditoría axe en CI | Media | Medio |
| 25 | Datos | Sin export/import completo del tenant ni borrado de cuenta (GDPR/Habeas Data Colombia — Ley 1581) | Medio | No puede llevarse sus datos | Bloqueo legal con clientes serios | Endpoint de export ZIP + delete tenant en cascada (los cascade-tests ya existen) | Media | Medio |

---

## 2. Los 10 bugs más importantes

1. **Inventario sin gate de roles** — cualquier viewer muta stock/BOM/proveedores/POs (`inventory.py`, todas las rutas).
2. **Bloqueo de signup si el email de verificación no llega** — login exige verificación con email service no confiable.
3. **OTP de reset válido 30 horas** con presupuesto de fuerza bruta viable (`auth.py:78`).
4. **Validación de fechas de eventos rota** y oculta bajo `xfail` — datos imposibles entran al pipeline.
5. **Timeouts de AI con Ollama/CPU** — chat, narrador y analista fallan >60s en hardware modesto.
6. **Rate limiter en memoria** — desaparece con restart o multi-worker.
7. **`datetime.utcnow()` residual** en emisión de OTP (expiraciones dependientes del huso del host).
8. **Jobs abortados en cada deploy** (worker in-process; se marcan FAILED pero el trabajo se pierde sin reintento).
9. **`xlsx@0.18.5` y Next 14.2.5 con CVEs conocidas** en el frontend.
10. **Tests falsos-verdes** (asserts either/or, sin cross-tenant PATCH/DELETE) — no es un bug de runtime, pero es la fábrica de los otros nueve: ya dejó pasar los 500 de `/quality` y `/report` y el JWT de 6 horas.

## 3. Las 20 mejoras con mayor ROI

| # | Mejora | ROI |
|---|--------|-----|
| 1 | `require_role` en todas las mutaciones de inventario | 1 día de trabajo elimina el riesgo #1 |
| 2 | Email transaccional real (Resend, ~$0) + reenviar verificación | Desbloquea el funnel completo |
| 3 | Deploy vivo (Railway/Render) con el docker-compose existente | Convierte código en producto |
| 4 | Stripe Checkout con los 3 tiers | Ingresos posibles |
| 5 | Email diario del briefing de `/hoy` | Retención sin construir features nuevas |
| 6 | OTP a 15 min + candado de intentos | Cierre de brecha crítica en horas |
| 7 | Onboarding único: CSV → automapeo de columnas → semáforo | El KPI de activación (<10 min) es la métrica que más predice conversión |
| 8 | Fail-fast de `TESTING_MODE` en producción | Seguro de $0 de costo |
| 9 | WhatsApp para alertas PEDIR_YA (Twilio) | En LatAm, WhatsApp = tasa de apertura ~98% vs ~20% email |
| 10 | `/hoy` como home post-login | El valor diario a un clic de distancia |
| 11 | Ejecutar el top-10 de la auditoría de tests (cross-tenant, permission pairs, FAILED path) | Previene la próxima ronda de bugs en prod |
| 12 | Migrar storage a S3/Supabase Storage | Habilita escalar y backups |
| 13 | Streaming + modelo pequeño para el LLM local | La feature AI pasa de "rota" a "wow" |
| 14 | DB en región sa-east-1 o pooler | Latencia percibida en toda la app |
| 15 | Interceptor de errores único en `api.ts` + toasts | Menos soporte, más confianza |
| 16 | Demo dataset de un clic ("prueba con datos de ejemplo") | Ver valor sin preparar CSV |
| 17 | Plantilla CSV descargable + validador con errores por fila | Reduce el fallo #1 de onboarding |
| 18 | Ocultar módulos no usados por tenant (BOM/Producción) | UI proporcional al usuario |
| 19 | `.gitignore` de storage/logs + limpieza de raíz | Higiene y evita fugas de datos |
| 20 | Página de accuracy como argumento de venta ("Faro acertó X%") | Convierte precisión en confianza renovable |

## 4. Las 10 funcionalidades nuevas con mayor potencial

| Funcionalidad | Problema que resuelve | Usuario gana | Negocio gana | Complejidad | Prioridad |
|---|---|---|---|---|---|
| Alertas WhatsApp/email de quiebre inminente | Olvido de revisar | No más stockouts por descuido | Retención/hábito | Media | Alta |
| Conector Siigo/Alegra (facturación Colombia) | Carga manual de ventas | Datos siempre frescos | Moat local vs. gringos | Alta | Alta |
| Envío de PO directo al proveedor (email/WhatsApp con PDF) | El ciclo termina fuera de Faro | Cierra el loop de compra | Faro se vuelve el sistema de registro | Media | Alta |
| Multi-ubicación (bodegas/tiendas) | Distribuidoras medianas la exigen | Transferencias sugeridas entre bodegas | Habilita tier Professional | Alta | Media |
| Registro de recepción de PO (llegó/parcial/no llegó) | Sin feedback, el stock teórico deriva | Lead times reales aprendidos por proveedor | Datos propietarios acumulados | Media | Alta |
| Simulador "¿qué pasa si?" (promoción, +20% demanda) | Decisiones ante eventos | Compra informada para promos | Diferenciador demo-able | Media | Media |
| Scorecard de proveedores (fill rate, lead time real) | Negociación a ciegas | Poder de negociación | Upsell analytics | Media | Media |
| Presupuesto de compra con optimización (¿qué compro con $X?) | Caja limitada, no puede comprar todo | Prioriza por ROI de cada peso | Feature enterprise | Alta | Media |
| App móvil/PWA del semáforo + aprobar PO | El jefe de compras vive en el celular | Decidir desde la bodega | Engagement diario | Media | Media |
| Benchmark anónimo por vertical ("tu rotación vs. sector") | No sabe si lo hace bien | Contexto competitivo | Network effect de datos | Alta | Baja |

## 5. Las 5 oportunidades de innovación más disruptivas

1. **Agente de compras autónomo con presupuesto**: el usuario define reglas ("nunca más de $X sin aprobar, proveedor preferido Y") y Faro genera, envía y da seguimiento a POs solo, escalando a humano solo las excepciones. Es la evolución natural del carrito aprobar/rechazar que ya existe en `/hoy`. *(Startup: empezar con "auto-aprobar recompras rutinarias".)*
2. **Lead times y confiabilidad de proveedores como dato propietario agregado**: con suficientes tenants, Faro sabe qué proveedor de qué categoría cumple — un dataset que nadie en LatAm tiene. *(Con presupuesto: marketplace de proveedores; startup: scorecard privado por tenant.)*
3. **Forecast conversacional accionable**: el RAG ya existe; el salto es que "¿qué pido esta semana si viene semana santa?" devuelva un carrito ejecutable, no un párrafo.
4. **Financiamiento de inventario embebido**: Faro sabe exactamente qué compra es rentable y cuándo; asociarse con una fintech para ofrecer crédito de compra sobre la PO recomendada (take rate sobre el crédito). Disruptivo porque monetiza la confianza del forecast.
5. **Red de demanda vertical**: si 30 distribuidores ferreteros comparten señal anónima, Faro detecta cambios de demanda regional antes de que le lleguen a cada uno — un data moat imposible de copiar para un competidor de software puro.

## 6. Puntuaciones (1–10)

| Dimensión | Nota | Justificación breve |
|---|---|---|
| UX | 5.5 | El concepto `/hoy` es excelente; el camino hasta ahí (8 pasos, verificación de email frágil, 12 páginas de nav) lo sabotea |
| UI | 6 | Consistente en intención (dark, tokens CSS), pero inline styles en páginas de 1.000–2.000 líneas garantizan divergencia |
| Funcionalidad | 7.5 | Amplitud sorprendente para su etapa: BOM, ROI, drift, RAG, webhooks — más de lo que el cliente Fase 1 necesita |
| Rendimiento | 5 | DB transcontinental, LLM en CPU con timeouts, polling; nada roto para 1 tenant, todo cruje con 50 |
| Seguridad | 4.5 | Buena base (JWT+refresh hasheado, blocklist, OTP, validación de contraseña) traicionada por los gates de rol ausentes y el OTP de 30h |
| Escalabilidad | 4 | Worker in-process, disco local, rate limit en memoria: arquitectura de 1 caja |
| Innovación | 7 | Carrito de decisiones + RAG + clasificación automática de SKUs es genuinamente más que un dashboard |
| Facilidad de uso | 5 | Para el jefe de compras sin data scientist, todavía se siente como herramienta de analista |
| **Calidad general** | **5.8** | Motor fuerte, carrocería a medio pintar, y sin ruedas (deploy/billing) |

---

## 7. Respuesta como CEO: hoja de ruta a 3 años

**Principio rector:** Faro no compite en calidad de modelo (nadie audita tu MAPE); compite en *tiempo-hasta-la-primera-decisión-correcta* y en *confianza acumulada*. Cada trimestre se justifica por una sola métrica dominante.

**T1 — Vender uno (métrica: 5 clientes pagando).** Congelar features. Cerrar los 6 hallazgos de seguridad de bajo esfuerzo (roles, OTP, TESTING_MODE, utcnow, deps), email real, deploy, Stripe, y el onboarding de un solo camino. Justificación: el costo de retrasar ingresos supera cualquier deuda técnica restante, pero vender con el gate de roles roto sería vender un incidente. La investigación de activación (Reforge/Amplitude) es unánime: la retención se decide en la primera sesión — por eso el CSV→semáforo en <10 minutos es *el* proyecto del trimestre, no uno más.

**T2–T3 — Crear el hábito (métrica: DAU/MAU > 40% en tenants pagos).** Alertas WhatsApp/email, `/hoy` como home, recepción de PO (cierra el loop y genera lead times reales), y el primer conector (Siigo). Justificación: un producto de decisiones que el usuario debe recordar visitar pierde contra el Excel que ya abre a diario; el hook model (trigger externo → acción → recompensa variable → inversión) exige que el trigger sea nuestro, no su memoria. La recepción de PO es además la inversión del usuario que hace el switching cost real.

**T4–T6 — Retener con datos que nadie más tiene (métrica: churn neto < 2%/mes).** Multi-ubicación, scorecard de proveedores, simulador de promociones, página de accuracy como ritual mensual ("Faro te ahorró $X"). En paralelo: worker separado, S3, DB regional — porque a 100 tenants la arquitectura de 1 caja se convierte en el riesgo #1. Justificación: en SaaS SMB el churn estructural es alto (~3-5%/mes); solo lo baja el dato acumulado que se perdería al irse.

**Año 2 — Ser el sistema de registro de compras (métrica: % de POs que nacen y se cierran en Faro).** Envío de PO a proveedores, agente de auto-recompra para SKUs rutinarios, API pública + marketplace de conectores. Cuando la PO vive en Faro, Faro deja de ser "una herramienta de forecast" (reemplazable) y pasa a ser el flujo de trabajo (irreemplazable) — el playbook de Toast/Square: entrar por analítica, quedarse por operación.

**Año 3 — Monetizar la confianza (métrica: revenue no-suscripción > 20%).** Financiamiento de inventario embebido sobre POs recomendadas y benchmark/red de demanda vertical. Justificación: el margen del software SMB tiene techo ($299/mes × mercado finito); el margen de mover dinero sobre decisiones que ya controlas no. Es el momento correcto — no antes — porque el crédito requiere el historial de precisión de los años 1–2 como colateral reputacional.

**Lo que deliberadamente NO haría:** más modelos ML (rendimiento decreciente, el cliente no los pide), apps nativas antes de PWA, expansión fuera de LatAm antes del año 3 (el moat es local: Siigo, WhatsApp, semana santa en el calendario de features de Colombia — eso es exactamente lo que un competidor global no va a construir).

---

**Nota final honesta:** el motor (forecasting_core) y la amplitud del backend están por encima de lo esperable para esta etapa; el riesgo del producto no es técnico sino de secuencia — hay features de año 2 (BOM, webhooks, drift) construidas antes que requisitos de semana 1 (deploy, billing, email confiable, roles en inventario). La auditoría de tests interna del 2026-06-22 ya contiene el plan de calidad correcto; ejecutarlo vale más que cualquier feature nueva de este documento.
