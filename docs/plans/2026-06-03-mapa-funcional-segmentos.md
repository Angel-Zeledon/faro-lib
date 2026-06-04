# Mapa Funcional por Segmento — Faro
> Análisis completo del estado actual · 2026-06-03

---

## Inventario funcional actual

### Navegación (16 ítems)

| Ruta | Módulo | R | D | M | Notas |
|---|---|---|---|---|---|
| `/hoy` | Centro de Decisiones diario | ✅ | ✅ | ✅ | Universal — cambia el contenido por perfil |
| `/quick-start` | Onboarding simplificado | ✅ | ✅ | ✅ | Universal |
| `/dashboard` | Dashboard técnico (sesiones ML) | ⚠️ | ⚠️ | ⚠️ | Solo relevante para power users y admins |
| `/data` | Gestión de fuentes de datos | ✅ | ✅ | ✅ | Universal |
| `/forecast` | Wizard de 8 pasos | ⚠️ | ✅ | ✅ | Demasiado técnico para retail no-técnico |
| `/inventory` | Semáforo de inventario | ✅ | ✅ | ⚠️ | Manufactura necesita vista por tipo de producto |
| `/inventory/roi` | Impacto & ROI | ✅ | ✅ | ✅ | Universal — métricas cambian por segmento |
| `/inventory/suppliers` | Gestión de proveedores | ⚠️ | ✅ | ✅ | Retail corporativo lo maneja la cadena |
| `/produccion` | Planificación de producción (BOM/MRP) | ❌ | ❌ | ✅ | Exclusivo de manufactura |
| `/skus` | SKU Intelligence | ✅ | ✅ | ⚠️ | Para manufactura se llama "Productos" |
| `/reports` | Reportes y monitoreo | ✅ | ✅ | ✅ | Universal |
| `/analyst` | AI Analyst (RAG) | ✅ | ✅ | ✅ | Universal |
| `/documents` | Gestión de documentos | ⚠️ | ✅ | ✅ | Más útil en supply chain complejo |
| `/accuracy` | Precisión del forecast | ✅ | ✅ | ✅ | Universal |
| `/config` | Configuración de usuario | ✅ | ✅ | ✅ | Universal |
| `/settings` | Ajustes de app | ✅ | ✅ | ✅ | Universal |

**R** = Retail · **D** = Distribución/Mayoristas · **M** = Manufactura
✅ = Muy relevante · ⚠️ = Relevante con ajustes · ❌ = No aplica

---

## Análisis por módulo

### Forecast Studio (wizard 8 pasos)

| Paso | Contenido actual | Problema por segmento |
|---|---|---|
| 1 — Fuente | Subir CSV/Excel o SQL | Universal. OK. |
| 2 — Columnas | fecha, target, grupo, exógenas | "Target" en retail = ventas; en manufactura = producción o demanda. Terminología confunde. |
| 3 — Features | lags, rolling, calendar, Fourier | Completamente técnico. Ningún usuario no-técnico lo entiende. Debería estar oculto. |
| 4 — Modelos | LightGBM, XGBoost, ARIMA, Prophet, Croston... | Técnico. El usuario no sabe cuál elegir. "Inicio Rápido" lo resuelve, pero el wizard principal sigue mostrando esto. |
| 5 — Validación | train_ratio, WFV, splits | Completamente técnico. Ocultar. |
| 6 — Horizonte | número de períodos | Universal, pero "30 períodos" no dice si son días/semanas/meses. |
| 7 — Negocio | service_level, lead_time, holding_cost | Para retail: service level. Para manufactura: lead time de producción, no de compra. |
| 8 — Resultados | MAE, WAPE, RMSE, bias | Métricas técnicas. El usuario quiere saber si puede confiar en el número, no el MAE. |

**Diagnóstico**: El wizard completo está diseñado para un data scientist, no para un gerente de compras o director de operaciones. El "Inicio Rápido" es la solución correcta pero está relegado a un ítem del sidebar, no como entrada principal.

### Inventario (semáforo)

| Elemento | Retail | Dist/Mayoristas | Manufactura |
|---|---|---|---|
| Semáforo 🔴🟡🟢🔵 | ✅ Compras por tienda | ✅ Reposición por ruta | ⚠️ Solo para productos terminados |
| Días de cobertura | ✅ | ✅ | ⚠️ Para manufactura importa la cobertura de materias primas, no solo terminados |
| Cantidad a pedir | ✅ (pedir al proveedor) | ✅ (pedir al proveedor) | ⚠️ Para manufactura: ¿pedir o producir? |
| ABC-XYZ | ✅ | ✅ | ✅ Universal |
| Proveedor | ⚠️ (cadenas lo gestionan centralmente) | ✅ | ✅ (para materias primas) |
| Sparkline de stock | ✅ | ✅ | ✅ |
| Vista "por proveedor" | ⚠️ | ✅ | ✅ |
| Vista "simple" | ✅ (usuarios no técnicos) | ✅ | ✅ |
| Vista "actualizar stock" | ✅ | ✅ | ✅ |
| Export OC CSV | ⚠️ (retail corporativo usa EDI) | ✅ | ✅ (para materias primas) |
| PDF ejecutivo | ✅ | ✅ | ✅ |
| Clasificación por tipo (terminado/componente/MP) | ❌ No diferencia | ❌ No diferencia | ✅ (recién agregado) |

**Diagnóstico**: El módulo de inventario funciona bien para retail y distribución. Para manufactura, la falta de diferenciación entre tipos de producto hace que el semáforo aplique igual a una materia prima que a un producto terminado, lo cual es incorrecto (la lógica de señal debería ser diferente).

### Producción (nuevo)

| Elemento | Retail | Dist/Mayoristas | Manufactura |
|---|---|---|---|
| Editor de BOM | ❌ No aplica | ❌ No aplica | ✅ Esencial |
| MRP Level 1 (explosión) | ❌ | ❌ | ✅ Esencial |
| Tipos de producto | ❌ No necesita | ❌ No necesita | ✅ Crítico |
| Plan de producción | ❌ | ❌ | ✅ |
| Detección de escasez de materias primas | ❌ | ❌ | ✅ |

### Reports

| Reporte | Retail | Dist/Mayoristas | Manufactura |
|---|---|---|---|
| Model Performance (WAPE/MAE) | ⚠️ Confuso | ⚠️ Confuso | ⚠️ Confuso |
| Data Quality | ✅ | ✅ | ✅ |
| Drift Monitor | ✅ | ✅ | ✅ |
| Export Excel/PDF | ✅ | ✅ | ✅ |
| Comparación de modelos | ❌ No relevante | ❌ | ❌ |

**Diagnóstico**: La sección de Reports muestra métricas de ML (WAPE, MAE, comparación de modelos, rankings) que ningún usuario de negocio puede interpretar. Esto debería reformatearse como "Confiabilidad del pronóstico" con lenguaje de negocio.

### SKU Intelligence

| Elemento | Retail | Dist/Mayoristas | Manufactura |
|---|---|---|---|
| Series de tiempo por SKU | ✅ | ✅ | ⚠️ Para manufactura son "productos", no "SKUs" |
| Clasificación de demanda (seasonal/stable/etc.) | ✅ | ✅ | ✅ |
| Detección de outliers | ✅ | ✅ | ✅ |
| Análisis estadístico (CV, ADF, Croston) | ❌ Técnico | ❌ Técnico | ❌ Técnico |
| STL Decomposition | ❌ Técnico | ❌ Técnico | ❌ Técnico |

**Diagnóstico**: La página de SKU Intelligence mezcla análisis de negocio útiles (tendencia, estacionalidad, outliers) con análisis estadísticos profundos (STL, ADF, Ljung-Box) que solo un data scientist puede interpretar. El 90% de los usuarios ve esto y no sabe qué hacer con la información.

### AI Analyst

Universal para todos los segmentos. El valor varía según el contexto:
- Retail: "¿Cuáles son mis productos con más riesgo de stockout esta semana?"
- Distribución: "¿Qué proveedores tienen peor fill rate?"  
- Manufactura: "¿Qué materias primas necesito comprar para producir el plan del próximo mes?"

---

## Terminología — Mapa de conflictos

| Término actual | Retail | Distribución | Manufactura |
|---|---|---|---|
| "SKU" | ✅ Correcto | ✅ Correcto | ⚠️ También "producto", "referencia", "código" |
| "Ventas" | ✅ | ✅ | ❌ Deberían decir "demanda" o "producción" |
| "Pedido al proveedor" | ⚠️ Cadenas lo llaman "reposición" | ✅ | ✅ Para materias primas |
| "Reposición" | ✅ | ✅ | ⚠️ En manufactura es "producción" |
| "Forecast de ventas" | ✅ | ✅ | ⚠️ En manufactura es "plan de demanda" o "forecast de producción" |
| "Días de cobertura" | ✅ | ✅ | ✅ Universal |
| "Safety stock" | ✅ | ✅ | ✅ Universal |
| "Lead time" | ✅ (del proveedor) | ✅ (del proveedor) | ⚠️ En manufactura hay lead time de compra Y de producción |
| "Proveedor" | ⚠️ | ✅ | ✅ |
| "MAE / WAPE / RMSE" | ❌ Nadie lo entiende | ❌ | ❌ |
| "Walk-forward CV" | ❌ | ❌ | ❌ |
| "Bias" | ❌ Sin contexto de negocio | ❌ | ❌ |
| "LightGBM / Prophet / ARIMA" | ❌ | ❌ | ❌ |

---

## Problemas actuales por segmento

### Retail

1. **El wizard de 8 pasos es demasiado técnico** para un gerente de tienda. El "Inicio Rápido" existe pero no es la entrada principal.
2. **El Dashboard principal muestra sesiones de ML**, no el estado del negocio. Un director de retail quiere ver sell-through, cobertura por categoría, y riesgos por tienda.
3. **No existe vista de "categorías"** — en retail la planificación se hace por categoría primero, luego por SKU.
4. **No existe "Forecast por tienda"** — un retailer con 10 tiendas necesita ver el inventario por ubicación.
5. **Terminología de proveedor**: las cadenas de retail no "gestionan proveedores" individualmente — lo hace la central de compras. El módulo de proveedores puede confundir.
6. **SKU Intelligence** muestra análisis estadísticos que un gerente de tienda nunca usará (STL decomposition, ADF test).

### Distribución / Mayoristas

Este es el segmento mejor cubierto actualmente. Los problemas son menores:

1. **Fill rate de clientes no está visible** — los distribuidores se miden por qué porcentaje de los pedidos de sus clientes pudieron cumplir, no solo por su propio inventario.
2. **No existe vista de "rutas"** — un distribuidor con rutas de entrega quiere ver el inventario por ruta/zona, no solo el total.
3. **Reporte ejecutivo** muestra métricas de ML (WAPE) en lugar de métricas de negocio (fill rate, capital de trabajo, rotación).
4. **El módulo de producción aparece en el sidebar** aunque no aplica para distribuidores puros.

### Manufactura / Productores

1. **El inventario no diferencia tipos de producto** — una materia prima y un producto terminado reciben el mismo semáforo con la misma lógica, lo cual es incorrecto.
2. **El BOM recién fue creado** pero no está integrado en el flujo principal.
3. **"Pedir al proveedor" no aplica para todos los ítems** — para un producto terminado, la respuesta es "producir más", no "pedir al proveedor".
4. **La terminología de "ventas"** confunde en manufactura — producen bienes, no necesariamente "venden" en el sentido retail.
5. **No existe capacidad de producción** — el MRP calcula cuánto material se necesita pero no si hay capacidad (máquinas, mano de obra) para producirlo.
6. **No existe "órdenes de producción"** — un fabricante necesita convertir el plan en órdenes de trabajo.

---

## Evaluación: ¿Arquitectura de perfiles?

**Respuesta directa: Sí, definitivamente.**

La solución no es crear tres plataformas distintas. Es una plataforma con un **motor compartido** y **experiencias especializadas** por tipo de empresa.

### Lo que es 100% compartido (no tocar)

- Motor de forecasting (AutoML, modelos, validación)
- Base de datos y esquema de tablas
- Sistema de autenticación y RBAC
- API REST completa
- Cálculos de safety stock y reorder point
- ABC-XYZ classification
- Alertas y webhooks
- AI Analyst (RAG)
- Export (PDF, Excel, CSV)

### Lo que se adapta por perfil

| Elemento | Retail | Distribución | Manufactura |
|---|---|---|---|
| **Sidebar** | Sin "Producción" | Sin "Producción" | Con "Producción" |
| **Dashboard Hoy** | Prioriza: fill rate por categoría, cobertura por tienda | Prioriza: fill rate clientes, rutas en riesgo, proveedor delays | Prioriza: escasez de materias primas, plan de producción |
| **Inventario — título** | "Gestión de inventario" | "Reposición y compras" | "Materiales e inventario" |
| **Inventario — columnas visibles** | Stock, días, señal, proveedor opcional | Stock, días, señal, proveedor, MOQ | Stock, días, señal, **tipo de producto** |
| **Forecast Studio** | Entrada = "ventas por producto" | Entrada = "ventas por SKU" | Entrada = "demanda de productos terminados" |
| **Reports** | Fill rate, cobertura, turnover | Fill rate clientes, KPIs de ruta | Utilización de plan de producción |
| **SKU Intelligence** | "Análisis de producto" (ocultar estadísticas) | "Análisis de SKU" | "Análisis de producto terminado" |
| **Terminología** | "Tienda", "categoría", "sell-through" | "Ruta", "cliente", "reposición" | "Producción", "materia prima", "lote" |
| **Quick Start** | "Sube las ventas de tu tienda" | "Sube tu historial de ventas" | "Sube la demanda de tus productos terminados" |

### Implementación recomendada

**Paso 1 — Campo `business_profile` en tenant:**
```
retail | distributor | manufacturer
```
Se configura durante el signup o en la primera pantalla de onboarding.

**Paso 2 — Context/hook de perfil:**
```typescript
const { profile } = useBusinessProfile()
// profile = 'retail' | 'distributor' | 'manufacturer'
```

**Paso 3 — Sidebar dinámico:**
El sidebar ya está definido como un array `NAV`. Se filtra según perfil:
```typescript
const visibleNav = NAV.filter(item => !item.onlyFor || item.onlyFor.includes(profile))
```

**Paso 4 — Terminología dinámica:**
Un diccionario de términos por perfil:
```typescript
const TERMS = {
  retail:        { target: 'ventas', group: 'tienda', sku: 'producto' },
  distributor:   { target: 'ventas', group: 'cliente', sku: 'SKU' },
  manufacturer:  { target: 'demanda', group: 'producto', sku: 'referencia' },
}
```

**Paso 5 — Hoy page por perfil:**
El briefing ya está modularizado. Agregar secciones condicionales:
- Retail: agrega "Cobertura por categoría"
- Manufactura: prioriza "Escasez de materias primas" sobre "Compras urgentes"

---

## Roadmap de personalización

### Inmediato (esta semana)

1. **Agregar `business_profile` al tenant** durante signup
2. **Filtrar Producción del sidebar** para retail y distribución
3. **Ocultar métricas técnicas** en Reports (MAE/WAPE como cifra cruda → mostrar como "precisión %")

### Corto plazo (2-4 semanas)

4. **Terminología dinámica** — cambiar labels según perfil en los módulos principales
5. **Quick Start personalizado** — el texto y los ejemplos cambian según el tipo de empresa
6. **Dashboard Hoy adaptado** — diferentes KPIs prioritarios según perfil

### Mediano plazo (1-3 meses)

7. **Vista de categorías** para retail (forecast y semáforo agrupado por categoría)
8. **Vista de rutas** para distribución (inventario por zona geográfica)
9. **Integración BOM ↔ Inventario** para manufactura (el semáforo de un producto terminado muestra automáticamente si hay materiales disponibles)
10. **Onboarding por vertical** — cada tipo de empresa tiene su propio flujo de 3 pasos adaptado

---

## Conclusión

El producto actual cubre ~85% de las necesidades de distribuidores, ~65% de retail y ~50% de manufactura. Las brechas no son de motor (el forecasting funciona para todos), sino de experiencia: terminología, jerarquía de información, y visibilidad de módulos relevantes.

La estrategia correcta es una arquitectura de verticales con motor compartido. El costo de implementar los perfiles es bajo (principalmente cambios de UI y terminología), y el beneficio es que cada tipo de cliente siente que el producto fue diseñado para su industria.

**La regla de oro:** El usuario debería poder describir su trabajo en sus propios términos y ver esos mismos términos reflejados en la plataforma.
