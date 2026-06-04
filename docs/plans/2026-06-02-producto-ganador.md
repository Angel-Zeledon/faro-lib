# Plan del Producto Ganador — Faro

> **Este es el plan estratégico + roadmap técnico completo.**
> Escrito el 2026-06-02. Revisarlo cada 90 días.

---

## La apuesta central

**El producto no es un motor de forecasting. Es un sistema de decisiones de compra.**

El cliente no abre Faro para ver métricas. Lo abre cada mañana para saber qué pedir hoy. Ese es el único ritual que importa. Todo lo que se construya debe servir a ese ritual o no se construye.

---

## A quién le vendemos (y a quién no)

### Cliente objetivo — Fase 1

**El jefe de compras o dueño de una empresa distribuidora o comercializadora en Colombia, México, Perú o Chile. 50 a 2.000 SKUs. Sin data scientist en el equipo. Hoy usa Excel.**

Características específicas:
- Factura entre USD 500K y USD 10M al año
- Tiene entre 1 y 5 personas en el equipo de compras/inventario
- Su problema activo: quedarse sin stock de sus mejores productos o tener bodega llena de productos que no rotan
- Su proceso actual: exportar ventas de su ERP/facturación a Excel, calcular promedios a mano, hacer el pedido "por intuición + experiencia"
- Está dispuesto a pagar USD 100–400/mes si el producto le evita un solo stockout importante

### Cliente objetivo — Fase 2

**El gerente de producción o compras de un fabricante de alimentos, bebidas o productos de consumo masivo con materias primas críticas.**

Esto se agrega en Fase 2, no antes. Requiere BOM y es más complejo de onboarding.

### A quién NO le vendemos en Fase 1

- Grandes corporaciones con SAP/Oracle (tienen equipos propios)
- E-commerce puro sin stock físico
- Servicios (no tienen inventario)
- Menos de 30 SKUs (Excel es suficiente para ellos)

---

## El nombre

**Faro.**

Un faro guía cuando hay niebla. El cliente no sabe qué va a pasar con su demanda — nosotros le damos luz para navegar sin encallar.

- Bilingüe sin traducción: igual en español e inglés
- Dominio: `faro.ai` (verificar disponibilidad)
- Tagline: *"Sabe lo que necesitas antes de que te falte."*
- Alternativa si faro.ai no está disponible: `usefaro.io` o `faro.app`

---

## La propuesta de valor en una oración

> **Faro convierte tu historial de ventas en órdenes de compra listas para enviar al proveedor.**

No "forecasting". No "ML". No "modelos". Órdenes de compra.

---

## Lo que ya existe (no reconstruir)

El codebase actual ya tiene el 60% del motor. Esto es capital — no se toca, se expone:

| Componente | Estado | Dónde está |
|---|---|---|
| Forecast ML (LightGBM, XGBoost, ARIMA, Prophet, ETS, Croston, LSTM) | ✅ Completo | `ForecastingCore/` |
| Clasificación de SKU (stable/seasonal/intermittent/trending) | ✅ Completo | `TimeSeriesAnalyzer` |
| Tratamiento de outliers por SKU | ✅ Completo | `runner.py` |
| Gap filling por SKU | ✅ Completo | `runner.py` |
| Intervalos de confianza (lower/upper) | ✅ Completo | Forecast rows |
| Inventory advisor base (service level, lead time) | ✅ Completo | `engine.get_inventory_report()` |
| AI analyst (RAG) | ✅ Completo | `backend/ai/` |
| Drift monitoring | ✅ Completo | `DriftDetector` |
| Multi-tenant auth | ✅ Completo | JWT + PostgreSQL |
| API Keys (acceso externo) | ✅ Completo | `api/v1/api_keys.py` |

Lo que falta es la **capa de inventario accionable** y el **UX de onboarding** que convierte este motor en un producto que alguien compra mañana.

---

## Arquitectura del producto ganador

```
CAPA 1 — INPUT
  Historial de ventas (CSV / integración)
  Stock actual por SKU (tabla editable)
  Parámetros de negocio (lead time, costo, proveedor)

        ↓

CAPA 2 — MOTOR (ya existe)
  Clasificación automática de SKU
  Selección automática de modelo
  Forecast con intervalos de confianza
  Inventory advisor

        ↓

CAPA 3 — OUTPUT ACCIONABLE (lo que falta)
  Semáforo diario por SKU
  Cantidad recomendada a pedir
  Orden de compra en PDF/Excel
  Alertas por email / WhatsApp

        ↓

CAPA 4 — INTELIGENCIA (diferenciador)
  AI analyst ("¿por qué me recomienda pedir 400?")
  Explicación del forecast en lenguaje natural
  ROI mensual ("evitaste $8M en sobrestock este mes")
```

---

## Roadmap por fases

### FASE 0 — El MVP vendible (semanas 1–4)

**Objetivo: Conseguir los primeros 5 clientes de pago.**

El producto tiene que funcionar con este flujo en menos de 10 minutos desde el signup:

```
1. Sube tu historial de ventas (CSV con fecha, SKU, cantidad)
2. Faro detecta columnas automáticamente y muestra preview
3. Faro entrena los modelos (la parte que ya funciona)
4. Ves el dashboard de inventario con semáforo por SKU
5. Descargas la orden de compra lista para enviar al proveedor
```

**Features a construir:**

#### F0.1 — Módulo de stock actual (backend + frontend)

Tabla por SKU con:
- `stock_actual` (unidades en bodega hoy)
- `stock_minimo` (umbral de seguridad definido por el usuario)
- `lead_time_dias` (días que tarda el proveedor)
- `costo_unitario` (para calcular valor del inventario)
- `proveedor` (texto libre o FK a tabla de proveedores)
- `unidad_minima_pedido` (MOQ)

**Por qué es crítico:** Sin saber cuánto hay en bodega, ninguna recomendación es accionable. Es el dato que falta hoy.

**Implementación:**
- Migración: tabla `inventory_stock` en PostgreSQL
- API: `GET/POST/PATCH /api/v1/inventory/stock` (por tenant, por session o global)
- Frontend: tabla editable en `/inventory` con import desde CSV
- El usuario puede editarla en la UI o importar CSV con columnas `sku, stock_actual`

#### F0.2 — Cálculo de días de cobertura

```python
dias_cobertura = stock_actual / demanda_diaria_pronosticada

demanda_diaria_pronosticada = sum(forecast_proximos_lead_time_dias) / lead_time_dias
```

Esta cifra es el corazón del semáforo.

#### F0.3 — Semáforo de inventario

Regla de negocio basada en días de cobertura vs lead time:

```python
if dias_cobertura < lead_time_dias * 0.5:
    señal = "PEDIR_YA"       # 🔴 — vas a romper stock antes que llegue el pedido
elif dias_cobertura < lead_time_dias * 1.2:
    señal = "PEDIR_PRONTO"   # 🟡 — tienes un colchón mínimo
elif dias_cobertura < lead_time_dias * 3:
    señal = "OK"             # 🟢 — stock saludable
else:
    señal = "SOBRESTOCK"     # 🔵 — considera no pedir o liquidar
```

Los multiplicadores (0.5, 1.2, 3) son configurables por el usuario (o por tipo de SKU en fases posteriores).

#### F0.4 — Cantidad recomendada a pedir

```python
demanda_en_lead_time = sum(forecast[hoy:hoy+lead_time_dias])

# Safety stock usando el intervalo de confianza del forecast
# upper - mean = incertidumbre, calibrada por nivel de servicio
incertidumbre_diaria = (forecast_upper - forecast_mean) / lead_time_dias
safety_stock = Z(service_level) * incertidumbre_diaria * sqrt(lead_time_dias)
# Z(0.95) = 1.645, Z(0.99) = 2.326

cantidad_recomendada = max(0,
    demanda_en_lead_time + safety_stock - stock_actual
)

# Redondear al MOQ si está definido
if moq > 0:
    cantidad_recomendada = ceil(cantidad_recomendada / moq) * moq
```

**Los intervalos de confianza ya existen en el forecast.** Solo hay que consumirlos.

#### F0.5 — Dashboard de inventario (la pantalla principal)

**Esta pasa a ser la pantalla de inicio del producto, no el dashboard de métricas de ML.**

Columnas: SKU | Señal | Días de cobertura | Stock actual | Demanda próx. 30d | Recomendación | Proveedor

Funciones:
- Filtro por señal (solo los 🔴 y 🟡)
- Ordenar por urgencia (días de cobertura asc)
- Selección múltiple → generar orden de compra
- Exportar a CSV/Excel

#### F0.6 — Generación de orden de compra

El usuario selecciona SKUs → clic en "Generar PO" → descarga Excel con:

```
Orden de Compra — Faro
Fecha: 2026-06-02
Proveedor: Distribuidora Pérez

SKU        Descripción   Cantidad recomendada   Costo unit.   Total
SKU-001    Arroz 1kg     400 unidades           $1,200        $480,000
SKU-042    Aceite 1L     120 unidades           $3,500        $420,000
─────────────────────────────────────────────────────────────────
Total                                                          $900,000
```

Agrupado por proveedor. Un Excel por proveedor o todo en uno con sheets.

#### F0.7 — Onboarding simplificado

El wizard actual de 8 pasos es para usuarios avanzados. Necesita una ruta rápida:

```
Paso 1: Sube tu archivo de ventas
  → Auto-detectar columnas (fecha, SKU, cantidad)
  → Preview de 10 filas
  → "¿Esto se ve bien?" → Sí / Corregir columnas

Paso 2: Configura tu negocio
  → Lead time general (días) — default: 15
  → Nivel de servicio — default: 95%
  → Estos se pueden ajustar por SKU después

Paso 3: Listo
  → Faro entrena en background (2-5 min)
  → Email cuando está listo: "Tu tablero de inventario está listo"
```

El usuario llega al semáforo sin haber visto un WAPE, un MAE ni un parámetro de modelo.

---

### FASE 1 — El producto que retiene (semanas 5–12)

**Objetivo: NRR > 100%. Los clientes que entran no se van.**

#### F1.1 — Gestión de proveedores

Tabla de proveedores por tenant:
- Nombre, contacto, email, teléfono, WhatsApp
- Lead time por defecto
- Condiciones de pago (30/60/90 días)
- Por SKU: qué proveedor, lead time específico, MOQ específico

Esto permite generar POs con datos del proveedor y enviarlas por email desde la app.

#### F1.2 — Clasificación ABC-XYZ visible y accionable

Combinar valor económico (ABC) con variabilidad del forecast (XYZ):

```
A = top 80% del revenue
B = siguiente 15%
C = últimos 5%

X = CV < 0.5 (demanda estable, forecast confiable)
Y = CV 0.5–1.0 (variabilidad moderada)
Z = CV > 1.0 (muy errática, forecast difícil)
```

El CV ya se calcula en el `TimeSeriesAnalyzer`. Solo hay que exponerlo.

Políticas recomendadas por cuadrante (mostrarlas en la UI):
- **AX**: Revisar semanalmente, safety stock bajo (demanda predecible y valiosa)
- **AZ**: Revisar semanalmente, safety stock ALTO (valiosa pero impredecible)
- **CZ**: Considerar discontinuar (poco valor y muy difícil de predecir)

#### F1.3 — Historial de inventario y bucle de feedback

Guardar el estado del stock cada vez que el usuario lo actualiza. Con el tiempo:

```
Semana 1: Recomendamos pedir 400. Pediste 380.
Semana 3: Stock llegó a 0 → Stockout detectado.
Semana 4: "La semana pasada tuviste stockout en SKU-001.
            Aumentamos el safety stock de 50 a 80 unidades."
```

Este bucle de feedback es el inicio del **data flywheel** — el moat del producto. Los modelos se calibran con el comportamiento real del cliente, no solo con el historial de ventas.

#### F1.4 — Sistema de alertas

Email automático (y WhatsApp en v2) cuando:
- Hay SKUs en 🔴 que no han sido atendidos en 48h
- Un SKU cruzó de 🟢 a 🟡 (umbral de alerta anticipada)
- Se detecta drift en la demanda de un SKU relevante
- El accuracy del forecast cayó más del 15% vs el mes anterior

Formato del email:

```
Asunto: ⚠️ 3 productos en riesgo de stockout — Faro

Buenos días, Andrés.

Tienes 3 productos que necesitan atención hoy:

🔴 Arroz 1kg (SKU-001)    → 2 días de cobertura  → Pedir 400 unidades
🔴 Aceite 1L (SKU-042)    → 1 día de cobertura   → Pedir 200 unidades
🟡 Sal 1kg (SKU-117)      → 9 días de cobertura  → Pedir pronto

[Ver tablero completo →]  [Generar orden de compra →]
```

#### F1.5 — Intelligence Panel por SKU (hacer visible lo que ya existe)

Hoy la clasificación, el routing y el accuracy están en la base de datos pero el usuario nunca los ve. Crear una vista por SKU que muestre:

```
SKU-001 — Arroz 1kg

Tipo de demanda: Estacional (pico en diciembre)
Modelo seleccionado: Prophet (mejor WAPE: 7.2%)
Variabilidad: Moderada (CV = 0.68 → clase Y)
Tendencia: Crecimiento +4% mensual
Forecast accuracy: 92.8% (últimas 4 semanas)

Última anomalía: Semana del 15 mayo — ventas 340% sobre lo normal
  (posible promoción no registrada)
```

Esto genera CONFIANZA. El usuario entiende por qué el sistema recomienda lo que recomienda.

---

### FASE 2 — El producto que expande (meses 3–6)

**Objetivo: Aumentar ARPU. Un cliente paga 3x porque usa 3x más valor.**

#### F2.1 — Multi-ubicación

El mismo SKU en múltiples bodegas o tiendas. Forecast por ubicación. Recomendaciones de:
- Compra a proveedor (si todas las ubicaciones tienen bajo stock)
- **Transferencia entre ubicaciones** ("La Bodega Norte tiene sobrestock de SKU-001, La Bodega Sur tiene riesgo de stockout — transferir 50 unidades")

#### F2.2 — Módulo de promociones

El usuario registra eventos próximos:

```
Evento: Black Friday
Fechas: 28 Nov - 2 Dic
SKUs afectados: todos / selección
Factor multiplicador estimado: 2.5x
```

Faro ajusta el forecast para esas fechas y genera recomendaciones de compra anticipadas. Después del evento, compara el multiplicador real vs estimado y lo calibra para la próxima vez.

#### F2.3 — Integraciones

En orden de ROI para el LatAm market:

1. **Google Sheets** (más fácil, más común) — sync bidireccional de stock actual
2. **Siigo** (software contable más popular en Colombia) — importar ventas automáticamente
3. **WooCommerce / Shopify** — para distribuidores con canal digital
4. **WhatsApp Business API** — alertas donde el usuario ya está
5. **API pública** (ya existe con API Keys) — para clientes que quieren integrar su ERP propio

#### F2.4 — Dashboard de impacto financiero

La métrica que justifica la suscripción:

```
Abril 2026 — Tu resumen con Faro

Capital en inventario:         $48M COP
Capital liberado vs mes ant.:  $12M COP  ✅
Stockouts evitados:            7 eventos (est. $18M COP en ventas protegidas)
Productos con sobrestock:      3 SKUs ($6M COP inmovilizado — acción recomendada)
Forecast accuracy promedio:    91.4%

ROI estimado del mes:          ~$30M COP ahorrados / $299.000 COP pagados
                                → 100x retorno
```

Esta pantalla es la que el cliente le muestra a su jefe para justificar la herramienta. Es también el argumento de ventas más poderoso.

#### F2.5 — Colaboración en equipo

- Comentarios por SKU: "El proveedor está de vacaciones hasta el 20"
- Flujo de aprobación para órdenes grandes: Analista propone → Gerente aprueba
- Log de decisiones: quién aprobó qué orden y cuándo
- Roles granulares (el RBAC ya existe en el backend)

---

### FASE 3 — Dominio de mercado (meses 6–12)

**Objetivo: Ser la herramienta estándar para distribuidores en Colombia. Expandir a México.**

#### F3.1 — BOM para fabricantes

El usuario sube su lista de materiales:

```
Producto terminado | Materia prima | Cantidad por unidad
Arroz 1kg (SKU-001) | Arroz a granel | 1.05 kg
Arroz 1kg (SKU-001) | Bolsa plástica  | 1 und
Arroz 1kg (SKU-001) | Etiqueta        | 1 und
```

Faro explota el BOM contra el forecast de producto terminado y genera:

```
Para producir 400 unidades de Arroz 1kg necesitas:
- 420 kg de arroz a granel (10 días de lead time — pedir AHORA)
- 400 bolsas plásticas (5 días de lead time — OK)
- 400 etiquetas (3 días de lead time — OK)
```

#### F3.2 — Forecast jerárquico

Niveles de agregación configurables:

```
SKU → Subcategoría → Categoría → Línea → Total empresa
```

Con reconciliación automática (los forecasts de los niveles inferiores suman al nivel superior). Útil para:
- Gerentes que quieren ver el forecast por categoría
- Compradores que trabajan a nivel SKU
- Dirección que quiere ver el forecast consolidado

#### F3.3 — Faro API + marketplace de integraciones

Con las API Keys ya implementadas, abrir un programa de partners:
- Consultores de supply chain que integran Faro con ERPs de sus clientes
- ISVs que embeben Faro en sus propios productos
- Revenue share del 20% para partners que traigan clientes

---

## El modelo de negocio

### Pricing (en USD, facturación en COP también disponible)

| Plan | Precio/mes | SKUs | Usuarios | Ubicaciones | Soporte |
|---|---|---|---|---|---|
| **Starter** | $99 | 500 | 2 | 1 | Email |
| **Professional** | $299 | 5.000 | 10 | 5 | Email + chat |
| **Enterprise** | $799 | Ilimitado | Ilimitado | Ilimitado | Dedicado + API + SLA |

**Prueba gratuita:** 14 días sin tarjeta de crédito. El onboarding debe mostrar valor en los primeros 10 minutos — si el usuario ve su semáforo y su primera recomendación de compra, convierte.

**Churn kill:** el moat real es el historial calibrado. Después de 6 meses de uso, el modelo conoce los patrones específicos del negocio del cliente. Migrar a otra herramienta implica perder esa calibración.

---

## Go-to-market

### Fase 0: 0–5 clientes (meses 1–2)

**Completamente manual. No automatizar nada todavía.**

- 10 llamadas a distribuidores/gerentes de compras en contactos personales y de primer grado
- Ofrecer Fase 0 gratis a cambio de feedback semanal y permiso de caso de estudio
- El objetivo no es revenue — es validar que el semáforo y el PO generation resuelven el problema real
- Una vez que 3 de los 5 lo usen más de 3 veces por semana, el producto está listo para vender

### Fase 1: 5–50 clientes (meses 3–6)

- **LinkedIn**: Contenido educativo sobre gestión de inventario (no sobre Faro). "Cómo calcular tu safety stock", "Los 5 errores más comunes al hacer forecasting en Excel". Audiencia: jefes de compras, gerentes de operaciones, dueños de distribuidoras.
- **FENALCO** (Colombia), **CAINTRA** (México): Presentaciones en gremios sectoriales. Un cliente del gremio trae 5 más.
- **Referidos**: 2 meses gratis por cada cliente referido. En la industria de distribución, todos se conocen.
- **Demo en video**: Un video de 3 minutos mostrando "de Excel al semáforo en 8 minutos". Sin pitching, solo mostrando el producto.

### Fase 2: 50–500 clientes (meses 6–18)

- **Canal de integradores**: Contadores y consultores que ya tienen relaciones con distribuidores. Revenue share del 20%.
- **SEO**: Posicionamiento en términos como "software de inventario para distribuidores Colombia", "reposición automática de stock"
- **Expansion revenue**: Upsell de ubicaciones adicionales, integraciones premium, seats de usuario

---

## El moat (por qué no nos pueden copiar fácilmente)

1. **Data flywheel**: Los modelos se calibran con el comportamiento real de cada cliente. Después de 6 meses, el sistema conoce los patrones estacionales específicos de ese negocio, sus anomalías recurrentes, la variabilidad de sus proveedores. Ningún competidor nuevo puede replicar eso.

2. **Switching cost**: Una vez que el cliente tiene sus proveedores, MOQs, lead times y 2 años de historial calibrado en Faro, el costo de migrar es alto — no por precio, sino por pérdida de inteligencia acumulada.

3. **Localización LatAm**: Los feriados de Colombia (Semana Santa, festivos movibles) ya están en el modelo. Entender los ciclos de demanda locales (quincenas, días de mercado, temporadas específicas de la región) es difícil de replicar desde afuera.

4. **Integración operacional**: Cuando Faro está integrado con Siigo y WhatsApp del cliente, cambiar implica reconfigurar flujos de trabajo que el equipo ya tiene internalizados.

---

## Métricas de éxito por fase

### Fase 0 (semana 4)
- 5 usuarios activos con datos reales
- Al menos 3 usaron el feature de PO generation
- Time-to-value < 10 minutos (medido en sesión)

### Fase 1 (mes 3)
- MRR: USD 3.000 (10 clientes de pago)
- Daily Active Usage rate > 60% (herramienta de uso diario, no semanal)
- Churn mensual < 3%
- NPS > 50

### Fase 2 (mes 6)
- MRR: USD 15.000
- ARPU promedio > USD 200
- NRR > 105% (expansión neta)
- Al menos 1 cliente Enterprise ($799/mes)

### Fase 3 (mes 12)
- MRR: USD 50.000
- Presencia en Colombia + México
- 3+ casos de estudio publicados con ROI medido

---

## El error más costoso que puedes cometer

Seguir construyendo features de ML antes de construir la capa de acción.

El motor ya es bueno. Lo que falta es la pantalla que un jefe de compras abre a las 8am y le dice exactamente qué hacer. Esa pantalla vale más que 10 modelos adicionales.

**El primer commit de Fase 0 debe ser la tabla de stock actual. No otro modelo.**

---

## Próximos pasos concretos (esta semana)

1. **Verificar disponibilidad de faro.ai** y alternativas (usefaro.io, faro.app)
2. **Crear la migración de `inventory_stock`** en el backend (tabla de stock actual por SKU)
3. **Calcular días de cobertura** en el endpoint de forecast existente
4. **Construir el semáforo** en el frontend como nueva pestaña en el dashboard
5. **Llamar a 3 distribuidores** (contactos actuales) y mostrarles el semáforo aunque sea en papel

El plan entero depende de que esas 5 cosas pasen esta semana.

---

*Faro — Sabe lo que necesitas antes de que te falte.*
