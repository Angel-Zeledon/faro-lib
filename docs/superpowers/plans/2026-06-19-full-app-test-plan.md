# Plan de testeo completo — Faro

Checklist manual para probar la app entera en el navegador. Organizado por prioridad: P0 (flujo de negocio diario, probar primero), P1 (funciones importantes secundarias), P2 (administración/configuración), P3 (estados de error y casos límite transversales).

Dataset de prueba listo: `sample_data/quickstart_demo.csv` (6 SKUs, 2 años / 730 días por SKU, columnas `date, sku, producto, cantidad_vendida, stock_actual, lead_time_dias` — auto-detección confirmada). Cada SKU tiene un patrón de demanda distinto a propósito (crecimiento + estacionalidad semanal, estable, estacionalidad anual, intermitente/spike navideño, declive, errático) para forzar casos límite reales del motor. Generado por `sample_data/generate_demo_data.py` (reproducible, `python sample_data/generate_demo_data.py`).

---

## P0 — Flujo de negocio diario

### 1. Autenticación
- [ ] Signup con email/empresa nuevos → debe llegar a pantalla "Check your email"
- [ ] Login con credenciales correctas → redirige a `/dashboard`
- [ ] Login con password incorrecta → mensaje "Invalid credentials" (no genérico "Error")
- [ ] Login con cuenta no verificada → mensaje "Please verify your email first"
- [ ] Forgot password → código OTP de 6 dígitos → reset → redirige a login
- [ ] Verify-email con link real desde el correo → "Email verified!"
- [ ] Verify-email con token inválido/expirado → mensaje claro, no crash
- [ ] Logout → vuelve a `/login`, no se puede navegar atrás a páginas protegidas

### 2. Quick Start (wizard completo)
- [ ] Subir `sample_data/quickstart_demo.csv` → Paso 2 debe auto-detectar `date`/`cantidad_vendida`/`sku`
- [ ] Click "Esto se ve bien, continuar" sin cambiar nada → entra a Paso 3 (entrenamiento)
- [ ] Esperar a que termine → debe redirigir a `/inventory?session=...` con datos poblados
- [ ] Repetir subiendo un CSV sin columna de fecha reconocible → debe mostrar el aviso nuevo ("No detectamos ninguna columna...") en vez de dropdown vacío sin explicación
- [ ] Repetir subiendo un archivo vacío (0 bytes) → debe dar error 422 claro, no pantalla rota
- [ ] Mientras entrena, refrescar la pestaña → no debe perder el estado de forma confusa

### 3. Inventario (`/inventory`)
- [ ] Con la sesión recién entrenada: la tabla debe mostrar las 3 SKUs con señales (Pedir YA / Pedir pronto / OK / Sobrestock)
- [ ] Editar stock de una SKU (✏️) → guardar → el valor se actualiza sin recargar la página
- [ ] Eliminar el stock de una SKU → confirmar diálogo → desaparece de la vista pero la SKU sigue en "Sin registro"
- [ ] Exportar orden de compra (CSV) → se descarga, contiene las SKUs correctas
- [ ] Descargar PDF resumen ejecutivo → se genera sin error
- [ ] Importar CSV de stock masivo → verifica conteo de filas importadas
- [ ] Cambiar a vista "Dead Stock" → si no hay productos parados, debe mostrar el empty-state correcto (no confundir con error)
- [ ] Agregar un evento/temporada (ej. Black Friday) → aparece en el panel de eventos
- [ ] **Desconectar la red brevemente y refrescar** → debe mostrar el banner de error nuevo con botón "Reintentar", NO la pantalla de onboarding como si no hubiera sesión

### 4. Hoy (`/hoy`)
- [ ] Briefing carga con KPIs correctos (Pedir YA, Esta semana, Precisión, Valor en bodega)
- [ ] Aprobar una acción de compra → se mueve al carrito flotante inferior
- [ ] Cambiar cantidad de una acción manualmente → se refleja en el carrito
- [ ] Descargar orden de compra desde el carrito → CSV correcto
- [ ] Rechazar todas las acciones → debe mostrar "No hay acciones pendientes"
- [ ] Resumen ejecutivo con IA carga (o cae al resumen de respaldo si la IA tarda >8s)

### 5. Producción (`/produccion`)
- [ ] Tab "Editor de BOM": seleccionar un producto, agregar un material con cantidad/unidad
- [ ] Eliminar un material de la BOM → confirmar diálogo
- [ ] Tab "Requerimientos de producción": cambiar horizonte (7/14/30/60/90 días) → recalcula
- [ ] Sin productos clasificados como "Producto terminado" → debe mostrar el empty-state guiando al Editor de BOM
- [ ] **Desconectar la red y cambiar de SKU en el editor BOM** → debe mostrar error + botón "Reintentar", no quedarse en blanco silenciosamente

### 6. Dashboard (`/dashboard`)
- [ ] Lista de sesiones recientes se ve con estado correcto (COMPLETED, RUNNING, FAILED, etc.)
- [ ] Renombrar una sesión → se actualiza en la tabla
- [ ] Eliminar una sesión → confirmar → desaparece de la lista
- [ ] **Forzar un error de red al eliminar/renombrar** → debe aparecer en el banner de errores (ya no es silencioso)
- [ ] "View all" sesiones → paginación funciona, búsqueda y filtro por estado funcionan
- [ ] Widget de inventario en la columna derecha muestra datos si hay sesión completada

---

## P1 — Funciones importantes

### 7. Forecast Studio (`/forecast`, los 9 pasos)
- [ ] Flujo completo: seleccionar fuente de datos → confirmar columnas → features → modelos → validación → forecast/horizonte → negocio → entrenar → ver resultados
- [ ] Paso de resultados: exportar a Excel y PDF
- [ ] Editar un override manual de un valor de forecast → guardar → mensaje de éxito
- [ ] **Forzar error al guardar un override** → debe mostrar mensaje de error (antes era silencioso)
- [ ] Seleccionar una SKU para ver su serie de forecast → tabla de valores carga
- [ ] **Forzar error de red al seleccionar SKU** → debe mostrar error específico, no "Selecciona un SKU" engañoso
- [ ] Abrir `/forecast?session=<id_invalido>` directamente por URL → debe mostrar mensaje de restauración fallida, no quedar en blanco

### 8. SKUs (`/skus`)
- [ ] Selector de sesión + lista de SKUs con búsqueda
- [ ] Ver detalle de una SKU (forecast, inventario, calidad de datos)
- [ ] Modo comparación entre dos sesiones
- [ ] Exportación masiva (bulk export) — verificar progreso y manejo de fallos individuales
- [ ] **Forzar error al cargar métricas/inventario/calidad** → debe verse el banner de error, no datos parciales sin aviso

### 9. Accuracy (`/accuracy`)
- [ ] Subir archivo de "actuals" (valores reales) → debe matchear filas y mostrar WAPE actualizado
- [ ] Gráfico de WAPE en el tiempo se renderiza
- [ ] Tabla por SKU ordenable por MAE/WAPE
- [ ] Alerta de "Accuracy degraded" cuando WAPE > threshold

### 10. Reports (`/reports`)
- [ ] Tabs: Confiabilidad del forecast / Calidad de datos / Cambios en demanda / Descargar resultados
- [ ] **Forzar error en una de las 3 cargas (metrics/quality/results)** → debe verse qué parte falló específicamente, no datos parciales silenciosos

### 11. Analyst — Chat IA (`/analyst`)
- [ ] Crear chat nuevo, enviar mensaje, recibir respuesta
- [ ] Cambiar entre chats existentes → mensajes correctos de cada chat (sin mezclarse)
- [ ] **Forzar error al cargar mensajes de un chat** → debe mostrar error + reintentar, NO los mensajes del chat anterior
- [ ] Preguntas sugeridas funcionan como acceso rápido
- [ ] Favoritos / búsqueda de chats

### 12. Inventario — subpáginas
- [ ] `/inventory/roi`: historial de OCs generadas, KPIs de ROI, tabla de historial
- [ ] `/inventory/suppliers`: crear/editar/eliminar proveedor, validación de nombre obligatorio

---

## P2 — Administración y configuración

### 13. Users (`/users`) — requiere rol admin
- [ ] Crear usuario, editar, cambiar rol, activar/desactivar
- [ ] Reenviar verificación de email
- [ ] Editar permisos granulares
- [ ] Cambiar contraseña propia (`/users/me/change-password`)

### 14. Settings / Config / Perfil
- [ ] Cambiar tema (claro/oscuro) y idioma → persiste tras recargar
- [ ] Editar perfil (nombre, datos de cuenta)
- [ ] Ver lista de modelos disponibles en la plataforma

### 15. Data (`/data`)
- [ ] Conectar fuente de datos por archivo
- [ ] Conectar fuente SQL (test-connection, ejecutar query)
- [ ] Editar/eliminar fuente de datos
- [ ] Preview de datos antes de usar

### 16. Documents (`/documents`)
- [ ] Subir documento, ver estado de procesamiento, ver contenido
- [ ] Eliminar documento

---

## P3 — Casos transversales (probar al final, cruzando varias páginas)

- [ ] **Navegar a una URL inexistente** (ej. `/esto-no-existe`) → debe mostrar la página 404 nueva con botón "Volver al panel", no la página default de Next.js
- [ ] **Provocar un crash de React** (si se puede, ej. editando temporalmente un componente) → debe mostrar `error.tsx` con mensaje + botón "Try again", no pantalla blanca
- [ ] **Dataset gigante**: subir un CSV de 1000+ SKUs / 15000+ filas en Quick Start → la inspección debe completar sin timeout; luego revisar cómo se comporta `/skus` e `/inventory` con tantas filas (scroll, performance visual)
- [ ] **Token expirado**: esperar a que expire el JWT (o borrarlo manualmente del storage) y hacer una acción → debe redirigir a `/login` limpiamente, sin loop de redirección
- [ ] **Doble clic rápido en botones de acción** (guardar, eliminar, exportar) → no debe disparar la acción duplicada (verificar que los botones se deshabilitan durante el loading)
- [ ] **Sesión sin SKU/grupo configurado** (subir CSV de un solo producto sin columna sku) → entrenar y confirmar que LightGBM ya no falla silenciosamente (bug corregido esta sesión) — revisar logs del backend, no debe aparecer `pandas dtypes must be int, float or bool`

---

## Qué reportar si algo falla

Para cada falla: página, acción exacta, qué esperabas vs qué pasó, y si hay mensaje de error — copiarlo textual. Eso permite diagnosticar sin necesidad de reproducir a ciegas.
