# Features propuestas — Faro

> Fecha: 2026-07-05
> Contexto: el flujo core (CSV → forecast → semáforo → orden de compra) ya funciona verificado de punta a punta. Esta lista propone qué construir encima, ordenado por retorno, no por novedad.
> Regla de oro: el cliente no compra modelos ML — compra **no quedarse sin stock**. Toda feature se justifica por cuánto acerca esa promesa.

---

## Horizonte 0 — Antes que cualquier feature nueva (semana 1)

Estas no son features, son los candados que impiden que las features existentes lleguen a un cliente:

| # | Qué | Por qué primero | Esfuerzo |
|---|-----|----------------|----------|
| 0.1 | **Email transaccional real** (Resend/SendGrid) | Sin esto un usuario nuevo no puede ni verificar su correo. Todo lo demás es irrelevante si nadie entra | Bajo (requiere cuenta) |
| 0.2 | **Deploy en URL pública** (Railway/Render, docker-compose ya existe) | Faro no existe fuera de tu máquina | Medio |
| 0.3 | **Stripe Checkout** (3 tiers ya definidos: $99/$299/$799) | Que alguien pueda pagar | Medio |

---

## Horizonte 1 — Crear el hábito diario (mes 1-2)

El semáforo ya funciona; el problema es que **depende de que el usuario se acuerde de entrar**. Un producto de alertas que no alerta pierde contra el Excel de siempre.

### 1.1 Alerta diaria por WhatsApp/email 🥇 (la feature #1 de toda la lista)
- **Qué:** cada mañana, si hay SKUs en PEDIR_YA, Faro envía un resumen: "3 productos se agotan antes de tu próximo pedido: Leche 1L (2 días), Arroz 5kg (3 días)… [Ver y aprobar →]". El JSON del briefing ya existe (`/inventory/morning-briefing`); falta solo el canal.
- **Por qué:** en LatAm WhatsApp tiene ~98% de apertura vs ~20% del email. Convierte a Faro de "dashboard que visito" en "asistente que me busca". Es la feature con mejor ratio impacto/esfuerzo de todo el producto.
- **Cómo:** Twilio WhatsApp Business API (o email primero con Resend, que ya estará por 0.1). El scheduler interno ya existe.
- **Esfuerzo:** Medio (email: Bajo).

### 1.2 Dataset de demostración de un clic
- **Qué:** botón "Probar con datos de ejemplo" en el quick-start que carga `demo_ventas.csv` + stock ficticio y te lleva al semáforo en 30 segundos, sin preparar nada.
- **Por qué:** el momento de mayor abandono es "tengo que armar un CSV". Ver el semáforo funcionando ANTES de invertir trabajo es lo que convierte curiosos en usuarios.
- **Esfuerzo:** Bajo (los datos y el flujo ya existen; es orquestarlos).

### 1.3 Plantilla CSV + validador con errores por fila
- **Qué:** descarga de plantilla exacta, y al subir un archivo con problemas, mensajes tipo "fila 214: fecha '31/02/2024' inválida" en vez de fallos genéricos.
- **Por qué:** el CSV mal formado es el error #1 de onboarding en este tipo de producto; cada archivo rechazado sin explicación clara es un cliente perdido.
- **Esfuerzo:** Bajo-Medio.

### 1.4 Registro de recepción de pedidos (cerrar el loop de la PO)
- **Qué:** tras generar una PO, poder marcar "llegó / llegó parcial / no llegó" y cuándo. Con eso: (a) el stock teórico se corrige solo, (b) Faro aprende el **lead time real** de cada proveedor en vez del número que el usuario adivinó.
- **Por qué:** sin feedback, el stock del sistema deriva de la realidad y las recomendaciones pierden credibilidad en semanas. Además, los lead times aprendidos son datos que el usuario no puede llevarse a un competidor — es tu switching cost.
- **Esfuerzo:** Medio.

### 1.5 Página "Faro te ahorró $X" (ROI visible y mensual)
- **Qué:** ya existe `/inventory/roi`; evolucionarla a un resumen mensual automático: quiebres evitados, capital liberado de sobrestock, % de recomendaciones seguidas.
- **Por qué:** es el argumento de renovación de la suscripción. El usuario necesita munición para justificar los $99-299/mes (a veces ante su jefe).
- **Esfuerzo:** Bajo (los datos ya se registran con log-po).

---

## Horizonte 2 — Retención y expansión de cuenta (mes 3-6)

### 2.1 Conector Siigo/Alegra (facturación Colombia)
- **Qué:** sincronización automática de ventas diarias desde el software de facturación que ya usan. El scheduler y la infraestructura de datasources ya existen.
- **Por qué:** elimina el CSV recurrente (el trabajo manual que reactiva el hábito-Excel) y mantiene el forecast siempre fresco. Es además el **moat local**: ningún competidor gringo va a integrar Siigo.
- **Esfuerzo:** Alto. Empezar por UNO solo (el que tenga tu primer cliente pagado).

### 2.2 Envío de PO directo al proveedor
- **Qué:** botón "Enviar pedido" que manda el PDF de la orden por email/WhatsApp al proveedor (los proveedores ya tienen email/whatsapp en su ficha).
- **Por qué:** hoy el ciclo termina en "descargar CSV" y el usuario sale de Faro para terminar su trabajo. Si la orden nace Y se envía desde Faro, Faro se vuelve el sistema donde viven las compras — irreemplazable.
- **Esfuerzo:** Medio (depende de 0.1).

### 2.3 Simulador "¿qué pasa si...?"
- **Qué:** sobre un evento (ya existen eventos/temporadas), ver el impacto proyectado: "Con la promo del Día de la Madre (+40% demanda estimada), necesitarías pedir 320 unidades extra de X antes del 25/04".
- **Por qué:** las decisiones difíciles del jefe de compras son las excepciones (promos, temporadas), no la rutina. Es la feature más "demo-able" para vender.
- **Esfuerzo:** Medio (multiplicadores de eventos ya existen en el motor).

### 2.4 Multi-ubicación (bodegas/tiendas)
- **Qué:** stock y semáforo por bodega, con sugerencia de transferencias internas antes de comprar ("tienes 200 unidades ociosas en bodega Norte — muévelas antes de pedir").
- **Por qué:** es el requisito que separa el tier Starter del Professional ($99 → $299). El motor ya soporta (sku, store); falta la capa de inventario y UI.
- **Esfuerzo:** Alto.

### 2.5 Scorecard de proveedores
- **Qué:** con los datos de 1.4 acumulados: cumplimiento de fechas, lead time real vs prometido, fill rate por proveedor.
- **Por qué:** poder de negociación tangible para el usuario ("me entregas a 12 días, no a 7 como prometiste") y upsell natural de analytics.
- **Esfuerzo:** Medio (depende de 1.4).

---

## Horizonte 3 — Diferenciación difícil de copiar (mes 6+)

### 3.1 Auto-recompra de SKUs rutinarios
- **Qué:** para SKUs estables clase A, regla opcional: "si cae a PEDIR_YA y el monto es menor a $X con el proveedor habitual, genera y envía la PO sola; avísame". El carrito aprobar/rechazar de `/hoy` ya es el 70% de esta feature.
- **Por qué:** es la evolución natural del producto: de recomendar a ejecutar. Nadie en el segmento SMB LatAm lo ofrece.
- **Esfuerzo:** Alto (empezar por auto-borrador, no auto-envío).

### 3.2 Asistente IA que devuelve acciones, no párrafos
- **Qué:** el RAG ya existe; el salto es que "¿qué pido esta semana si viene Semana Santa?" devuelva un **carrito ejecutable** (líneas de PO pre-llenadas) en vez de texto.
- **Por qué:** convierte la feature IA de "chat curioso" en herramienta de trabajo. Requiere resolver antes el LLM de producción (Ollama en CPU no da el ancho; considerar API con presupuesto por tenant).
- **Esfuerzo:** Alto.

### 3.3 Presupuesto de compra optimizado
- **Qué:** "tengo $5M COP esta semana, ¿qué compro?" — priorización de la PO por riesgo de quiebre × margen, recortada al presupuesto.
- **Por qué:** la restricción real del cliente pequeño es caja, no información. Ningún forecast tradicional responde esta pregunta.
- **Esfuerzo:** Alto.

### 3.4 PWA / vista móvil del semáforo
- **Qué:** el semáforo y el aprobar/rechazar de `/hoy` usables desde el celular (PWA, no app nativa).
- **Por qué:** el jefe de compras vive en el celular y en la bodega, no frente a un monitor. Complementa 1.1: la alerta de WhatsApp lleva a una pantalla que funciona en el teléfono.
- **Esfuerzo:** Medio.

---

## Lo que deliberadamente NO agregaría ahora

- **Más modelos ML** — el motor ya tiene 8; el cliente no pide el noveno. Rendimiento marginal ≈ 0.
- **App nativa iOS/Android** — PWA primero; una app son 2 codebases más de mantenimiento.
- **Features enterprise** (SSO, API pública, white-label) — antes del primer cliente de $799 son especulación.
- **Más análisis/dashboards** — Faro ya tiene más pantallas de análisis de las que su usuario objetivo consume; el déficit está en la capa de **acción** (alertas, envío de PO, recepción), no en la de información.

## Orden de ejecución recomendado

```
0.1 Email → 0.2 Deploy → 0.3 Stripe        (desbloqueo comercial)
→ 1.2 Demo un clic + 1.3 Validador CSV     (activación)
→ 1.1 Alerta WhatsApp/email                (hábito — la joya)
→ 1.4 Recepción de PO + 1.5 ROI mensual    (retención + datos propios)
→ 2.2 Envío de PO → 2.1 Conector Siigo     (sistema de registro)
→ 2.3 Simulador → 2.5 Scorecard → 2.4 Multi-bodega
→ Horizonte 3 según tracción
```

La lógica de la secuencia: primero que puedan **entrar y pagar**, luego que **vuelvan todos los días**, luego que **no puedan irse** (datos acumulados: lead times reales, historial de decisiones, ROI), y solo al final lo espectacular.
