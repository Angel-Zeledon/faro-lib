Con estas aclaraciones, los requisitos quedarían así, mucho más aterrizados:

# 1. Órdenes de compra

## Estado actual

* No se puede seleccionar manualmente el proveedor.
* Las órdenes están demasiado ligadas al forecast.
* No parece posible crear una orden de compra manual, independientemente de una predicción.

## Propuesta

* Permitir crear órdenes de compra tanto:

  * sugeridas por el forecast,
  * como completamente manuales.
* Permitir seleccionar el proveedor al que se comprará.
* Permitir modificar las cantidades antes de generar la orden.
* Al confirmar la orden:

  * se genera automáticamente un mensaje de WhatsApp,
  * se envía al número del usuario (el número será obligatorio durante el registro),
  * el usuario simplemente reenvía ese mensaje a su proveedor.
* De esta forma no es necesario integrar WhatsApp directamente con cada proveedor y el proceso sigue siendo muy sencillo.

---

# 2. Inventario multibodega

## Estado actual

* No existe transferencia entre bodegas.
* No existe análisis para determinar si conviene transferir inventario o comprar.

## Propuesta

Implementar:

* Transferencias entre tiendas/bodegas.
* Lead time de cada transferencia.
* Costos asociados.
* Recomendación inteligente:

> "Es mejor trasladar 80 unidades desde la Bodega Central porque llega en 1 día y cuesta menos que realizar una compra nueva."

o

> "Es mejor comprar al proveedor porque el traslado demoraría demasiado."
Esto si no tiene que ser una alerta visual,solo una recomendacion
---

# 3. Forecasting

## Problemas

Las métricas muestran precisiones muy altas (95–97%) mientras que visualmente la predicción no tiene sentido.

Hay casos donde:

* la serie histórica es prácticamente plana,
* y la predicción hace un salto enorme sin explicación.

Eso hace perder confianza en el sistema.

## Mejoras

* Revisar completamente cómo se calcula el accuracy.
* Mostrar métricas realmente representativas.
* Alinear las métricas con la calidad visual del forecast.

Además:

Agregar una banda de incertidumbre.

Por ejemplo:

* Predicción central (P50)
* Banda inferior (-20%)
* Banda superior (+20%)

Aunque inicialmente sea únicamente visual, ayuda muchísimo a transmitir incertidumbre y confianza.

---

# 4. API / Webhooks

Actualmente no tiene una utilidad clara.

Opciones:

* ocultarla,
* marcarla como "Próximamente",
* o explicar claramente qué problema resuelve.

---

# 5. Configuración

El cambio de teléfono no funciona correctamente.

Debe revisarse:

* envío del código,
* validación,
* experiencia de usuario.

---

# 6. Eventos de negocio

Este probablemente sea uno de los módulos más importantes.

La aplicación debe permitir crear eventos como:

* Black Friday
* Navidad
* Semana Santa
* Promociones
* Feriados
* Eventos propios de la empresa

Pero no basta con decir que existe un evento.

Cada SKU debe reaccionar distinto.

Ejemplo:

**Black Friday**

* Televisores → ×1.7
* Celulares → ×1.5
* Arroz → ×1.0

**Navidad**

* Rompope → ×4
* Jamón → ×2
* Arroz → ×1.4
* Televisores → ×1.2

Es decir:

Cada evento debe permitir configurar multiplicadores distintos según:

* SKU
* categoría
* familia de productos

Además:

Debe existir un valor por defecto sugerido por el sistema y luego el usuario podrá modificar esos multiplicadores según su negocio.

---

# 7. Simulación de escenarios (What-if)

El usuario debería poder responder preguntas como:

* ¿Qué pasa si el Black Friday vende un 40% más?
* ¿Qué pasa si Navidad vende menos este año?
* ¿Qué pasa si un proveedor se atrasa?
* ¿Qué pasa si aumento el inventario de seguridad?
* ¿Qué pasa si hago una promoción?

La idea es partir del forecast base y aplicar distintos escenarios para evaluar su impacto antes de tomar decisiones.

---

# 8. Planes del producto

## Starter

Pensado para empresas pequeñas o con una sola bodega.

Incluye:

* Forecasting.
* Gestión básica de proveedores.
* Órdenes de compra.
* Sin multibodega.
* Interfaz simplificada.
* Hasta **1.000 SKUs**.

---

## Professional

Pensado para empresas con operaciones más complejas.

Incluye todo lo anterior más:

* Multibodega.
* Transferencias.
* Recomendaciones entre comprar o transferir.
* Mayor capacidad (**5.000 SKUs**).
* Herramientas avanzadas de abastecimiento.

---

## Enterprise

No sería un plan con un precio fijo.

Sería un contrato personalizado con cada empresa, incluyendo:

* precio negociado,
* cantidad de usuarios,
* cantidad de SKUs,
* infraestructura dedicada si es necesario,
* desarrollos o integraciones específicas,
* soporte prioritario.

La idea es que Enterprise sea una solución a medida y no simplemente un tercer plan con más límites.


# 9. Pantalla de Predictions

## Reducir ruido visual

La gráfica tiene demasiados controles que no aportan valor.

Eliminar botones como:

* Average
* Zoom
* Controles adicionales de navegación
* Cualquier botón que duplique gestos táctiles naturales

La interacción debe ser completamente natural:

* Pellizcar para hacer zoom.
* Arrastrar para desplazarse.
* Expandir la gráfica a pantalla completa.

No debería haber botones visibles para acciones que el usuario puede hacer con gestos.
Cosas como esto Seasonal demand
Predictability:
High
97%
accuracy
You can confidently make purchasing decisions based on this forecast., eso no deberia de salir jamas, alomucho salir el precision de cada sku al seleccionar su prediccion, pero jamas algo asi
---

## Mejorar la visualización de la gráfica

Actualmente existen problemas de renderizado.

Corregir:

* Las fechas del eje X se sobreponen.
* La gráfica pierde legibilidad.
* Quitar ese poco de botones, avg, sum, agg,dejar solo grafico de linea y barras, solo dos
* Mejorar el espaciado general.

La prioridad debe ser que la gráfica sea fácil de leer.

---

## Mantener comparación entre modelos

Sí conservar la posibilidad de visualizar distintos modelos de forecasting.

Ejemplo:

* LightGBM
* XGBoost
* AutoARIMA
* Prophet

Pero la comparación debe ser clara y sencilla.

---

# 10. Upload Sales / Gestión de sesiones

## Mostrar el horizonte de predicción

Actualmente no queda claro cuál será el horizonte utilizado.

Actualmente eso esta como un navbar o algo estatico, eso es una estupidez, debe estar en la pantalla de quick start y punto, ahi se elige granulridad y horizonte

Ejemplo:

Horizonte de predicción:

* 4 semanas
* 8 semanas
* 6 meses

No debe depender de otra pantalla.

---

## Historial de sesiones

Actualmente no existe.

Agregar una lista de sesiones anteriores.

Cada sesión debería mostrar al menos:

* Nombre
* Fecha
* Dataset utilizado
* Horizonte
* Número de SKUs
* Al darle click deberia enviarme a la ver los predictions de esa sesion
---

## Reutilizar datasets

Actualmente se puede subir información pero no volver a utilizarla.

Eso rompe completamente el flujo.

Debe ser posible:

* seleccionar un dataset previamente cargado;
* crear una nueva sesión utilizando ese mismo dataset con diferente granularidad, ponerle nombre a la session,ejem Forecast diario, Forecast mesual, etc eso debe ser un campo input;
* evitar subir nuevamente el mismo archivo.

El dataset debe convertirse en un activo reutilizable dentro del sistema.

---

# 11. Quick Start

## Configurar el horizonte aquí

El horizonte no debería vivir en un navbar flotante.

Debe definirse directamente durante el Quick Start.

Es una configuración propia de cada sesión.

---

## Eliminar el horizonte global

Actualmente el horizonte aparece en un navbar permanente.

Eso genera confusión porque parece ser una configuración global cuando realmente pertenece a una sesión específica.

Debe eliminarse de ahí y asociarse únicamente al flujo de creación de una predicción.

---

## Seleccionar datasets existentes

Durante el Quick Start el usuario debería poder elegir entre:

* Subir un nuevo archivo.
* Utilizar un dataset existente.
* Clonar una sesión anterior.

De esta manera el flujo es mucho más natural y evita trabajo repetitivo.

---
