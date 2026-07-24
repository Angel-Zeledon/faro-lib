# Guion de demo — Faro

Guía práctica para presentar Faro de principio a fin, con datos ricos y coherentes
ya cargados. Pensada para leerse mientras se maneja la app: cada paso trae una
frase de "qué decir" que resalta el valor de negocio.

---

## 0. Antes de empezar (setup)

### Levantar el entorno

1. **Postgres** (Docker, ya corriendo normalmente):
   ```bash
   docker start faro_db   # contenedor en :5544, user/pass postgres/postgres
   ```

2. **Backend** (puerto 8010) — desde la raíz del repo:
   ```bash
   backend/.venv/Scripts/python.exe -m uvicorn backend.main:app --port 8010
   ```

3. **Frontend** (puerto 5000) — desde `Frontend/`:
   ```bash
   set BACKEND_URL=http://localhost:8010&& npm run dev
   ```
   Si `node_modules` está roto: `npm install`. **No** correr `npm run build`
   mientras `next dev` está activo (corrompe la caché `.next`).

4. Abrir **http://localhost:5000** en Chrome.

### Login del demo

| Campo | Valor |
|-------|-------|
| Correo | `demo@faro.app` |
| Contraseña | `demo1234` |

El tenant es **enterprise** (todo desbloqueado: multi-bodega, simulador de
eventos, integraciones, asistente IA), con el correo ya verificado.

### Resetear + resembrar antes de una demo

El seed es **idempotente**: borra el tenant demo y lo reconstruye desde cero con
datos coherentes. Corre esto para dejar el demo "como nuevo":

```bash
backend/.venv/Scripts/python.exe -m backend.scripts.seed_demo
```

- Tarda **~3–4 minutos** porque entrena los pronósticos de verdad (familia diaria
  + semanal sobre 14 SKUs). Al terminar imprime el resumen y el semáforo
  (`PEDIR_YA: 2, PEDIR_PRONTO: 3, OK: 6, SOBRESTOCK: 3`).
- Se puede correr con el backend levantado; el worker no interfiere. Si estabas
  con sesión abierta en el navegador, vuelve a iniciar sesión después (el usuario
  se recrea).
- `--no-train` resetea en segundos **pero deja el semáforo vacío** (no hay
  pronóstico). Úsalo solo para pruebas rápidas, nunca para la demo real.

### Qué quedó sembrado (resumen)

- **14 SKUs** de abarrotes (Aceite de Oliva, Arroz Premium, Leche, Café, Atún…)
  con ~18 meses de ventas diarias y pronóstico entrenado (precisión ~88%).
- **3 bodegas** (principal / Norte / Sur) con reparto de demanda; **Detergente**
  está desbalanceado a propósito para disparar una sugerencia de transferencia.
- **3 proveedores** con lead times, mapeados a SKUs.
- **4 órdenes de compra** en estados distintos (recibida, parcial, en camino,
  por enviar) + **1 transferencia** cerrada.
- **2 mermas**, y un **calendario LatAm** (quincenas de Colombia + Semana Santa).

---

## 1. La historia de valor (click-through)

> Hilo conductor: **subir datos → pronóstico → semáforo de qué pedir → generar la
> orden → recibir y aprender lead time → multi-bodega y transferencias →
> multi-período → calendario/estacionalidad → editor de datos → WhatsApp.**

### A. Subir ventas → de un CSV a decisiones

1. Ir a **Mis Archivos** (`/data`). Seleccionar **"Ventas Demo Faro"**.
   > *"Todo arranca con lo que el distribuidor ya tiene: su historial de ventas
   > en un CSV. Nada de integraciones complejas para empezar."*
2. Mostrar el tamaño / filas de la fuente.
   > *"18 meses de ventas diarias, 14 productos. Con esto Faro entrena un modelo
   > por SKU."*

### B. Pronóstico → un modelo por producto

3. Ir a **Predicciones** (`/skus`). Se auto-selecciona la sesión "Demo Faro".
   > *"Faro entrena varios modelos (LightGBM, XGBoost, Prophet, Croston…) y elige
   > el mejor por SKU. Aquí el mejor modelo salió XGBoost con ~3% de error."*
4. Clic en un SKU (ej. **Aceite de Oliva 1L**): ver la curva histórica + el
   pronóstico y la banda de incertidumbre.
   > *"No es una regla fija de 'pedir cuando baje de X'. Es demanda proyectada,
   > con estacionalidad de fin de semana y quincena."*
5. Cambiar la **granularidad D → W** (botones D/W/M en el gráfico) y el selector
   de **Sesión** entre "Demo Faro" y "Demo Faro · weekly".
   > *"El mismo dato se ve por día o por semana según cómo compre el cliente."*

### C. Semáforo → qué pedir hoy

6. Ir a **Inventario** (`/inventory`). Mostrar el semáforo:
   **2 Pedir YA · 3 Pedir pronto · 6 OK · 3 Sobrestock**.
   > *"Esta es la pantalla estrella: en un vistazo, qué está en riesgo y qué
   > sobra. Rojo = se agota antes de que llegue el proveedor; azul = capital
   > dormido."*
7. Señalar un **Sobrestock** (Arroz / Azúcar): 48 días de cobertura.
   > *"Aquí hay plata parada. Faro sugiere pausar el próximo pedido."*

### D. Generar la orden → del semáforo a la OC

8. Volver a **Panel de Compras** (`/hoy`). Mostrar las tarjetas "URGENTE"
   (Aceite, Harina) con la cantidad sugerida y el "≈ costo".
   > *"Faro no solo avisa: dice cuánto pedir y a qué proveedor, con el costo
   > estimado."*
9. Clic en **"Ver por qué"** de un urgente para abrir el desglose (demanda diaria,
   lead time, stock de seguridad).
   > *"Todo es explicable: el comprador ve la cuenta, no una caja negra."*
10. Clic en **Aprobar** en una tarjeta → se arma el carrito. Desde `/inventory`,
    botón **"Exportar OC"** para generar la orden.
    > *"Un clic convierte la recomendación en una orden de compra lista para
    > enviar."*

### E. Recibir y aprender lead time → cerrar el ciclo

11. Ir a **Pedidos** (`/pedidos`). Mostrar las 4 OC en estados distintos:
    **OC-000001 Recibida**, **OC-000002 Parcial**, **OC-000003/004 En camino**.
    > *"El ciclo no termina en la orden: se registra la llegada."*
12. En una OC "En camino", clic en **"Registrar llegada"** y confirmar la recepción.
    > *"Cuando registras la llegada, Faro compara la fecha real contra la
    > prometida y aprende el lead time verdadero del proveedor."*
13. Ir a **Proveedores → Scorecard** (`/inventory/suppliers/scorecard`).
    > *"Mira: Granos del Valle dice 12 días pero en la práctica entrega en 5–8.
    > Faro usa el lead time REAL para calcular cuándo pedir — no el del papel."*

### F. Multi-bodega y transferencias → mover antes de comprar

14. En **Inventario**, cambiar a la pestaña de bodega **principal**.
    > *"El mismo semáforo, pero por bodega."*
15. Buscar **Detergente 1kg**: en principal la acción NO es "comprar", es
    **"Transferir 228 desde Norte"**.
    > *"Antes de gastar en una compra, Faro revisa si otra bodega tiene
    > excedente. Aquí conviene mover stock, no comprar."*
16. (Opcional) En el **Panel de Compras** también aparece arriba:
    *"1 se resuelve moviendo stock, sin comprar"* → botón **Crear transferencia**.

### G. Multi-período → día ↔ semana

17. En el selector superior **"Ver por"**, cambiar **Día → Semana** (o al revés).
    > *"El comprador que planifica semanal ve cobertura, cantidades y lead time
    > todo en semanas — cuadra con cómo trabaja."*
18. Notar que las cantidades quedan en unidades enteras y las coberturas cambian
    de "días" a "semanas" de forma consistente con el KPI de arriba.

### H. Calendario y estacionalidad → anticipar picos

19. En **Inventario**, botón **"Eventos y temporadas (2 próximos)"** o los botones
    **"Simular: Quincena…"**.
    > *"Faro trae el calendario comercial LatAm: quincenas, Semana Santa. Un clic
    > simula el impacto del evento sobre la demanda."*
20. Correr **"Simular: Quincena"** y mostrar cómo cambian las recomendaciones con
    el multiplicador del evento.
    > *"La quincena dispara el consumo; Faro lo anticipa antes de que el semáforo
    > se ponga rojo."*

### I. Editor de datos → corregir sin salir de la app

21. En **Mis Archivos**, seleccionar la fuente → **Editar**.
    > *"Si el cliente detecta un dato malo, lo corrige aquí mismo."*
22. Cambiar una celda de `cantidad` → botón **"Guardar como nuevo"** (crea una
    versión nueva sin tocar el original).
    > *"Guardar como nuevo deja el dataset original intacto y crea una versión
    > lista para reentrenar."*
    > ⚠️ Ojo: la tabla del editor carga TODAS las filas del archivo (ver
    > "Limitaciones"). No hay que hacer scroll por las ~7.5k filas; basta mostrar
    > el encabezado, editar una celda y guardar.

### J. WhatsApp → alertas y bot

23. Ir a **Configuración** (`/config`), sección **"Vincular WhatsApp"**: el número
    ya está puesto (`+506 8888 7777`), botón **"Enviar código"**.
    > *"El comprador vincula su WhatsApp y recibe las alertas de inventario ahí
    > mismo, y puede consultar sus compras conversando con el bot."*
    Ver la nota honesta sobre el bot más abajo.

### K. Cierre — el idioma y el resto

24. Botón **ES / EN** (barra lateral o `/config`): togglear a inglés y volver.
    > *"Producto bilingüe de fábrica."*
25. **Ctrl-K** (o el buscador arriba): buscar un producto y ver el desglose por
    bodega.
26. Mencionar **Impacto** (`/inventory/roi`): ROI acumulado, adopción de
    recomendaciones, capital liberado.
    > *"Y todo esto se mide: cuántas recomendaciones siguió, cuánto capital
    > liberó."*

---

## 2. Nota honesta sobre el bot de WhatsApp

- **Alertas salientes (outbound): funcionan de verdad.** Con las credenciales de
  Twilio (sandbox) configuradas, Faro envía las alertas diarias de inventario por
  WhatsApp. Sin credenciales, el envío queda como no-op registrado en logs.
- **Bot conversacional entrante (inbound): requiere montaje extra.** Para el
  round-trip en vivo (el usuario le escribe al bot y este responde con sus datos)
  hace falta:
  1. Un **túnel público** (ej. `ngrok`) apuntando al webhook del backend, porque
     Twilio necesita una URL pública para entregar los mensajes entrantes.
  2. **Crédito de Anthropic** (`ANTHROPIC_API_KEY`) para las respuestas del bot
     inteligente; hoy el bot corre en **modo genérico** (`WHATSAPP_BOT_GENERIC_MODE`)
     como stopgap cuando no hay LLM financiado.
- **Recomendación para la demo:** mostrar la **UI de vinculación** en
  `/config` y **explicar** el bot. Si el presentador quiere el round-trip en vivo,
  levantar `ngrok` + poner `ANTHROPIC_API_KEY` con crédito **antes** de la sesión.

---

## 3. Limitaciones conocidas / "no hagas clic aquí"

- **Editor de datos con archivo grande.** El editor renderiza TODAS las filas del
  dataset. El archivo demo tiene ~7.5k filas, así que la tabla tarda un momento en
  pintar y el scroll se siente pesado. Para la demo: abrir el editor, editar UNA
  celda visible y usar "Guardar como nuevo"; no hacer scroll por toda la tabla.
- **"Resumen ejecutivo del día" (Panel de Compras).** El texto narrativo lo genera
  el LLM. Si `ANTHROPIC_API_KEY` no tiene crédito, puede quedarse en
  "Analizando datos…". No es un error de datos; el resto del panel funciona. Si
  molesta, no esperar a que cargue esa tarjeta.
- **"Cambios en demanda" con -99%.** En el Panel de Compras, esta sección compara
  el último día real contra el pronóstico y a veces muestra caídas grandes (ruido
  del último punto). Es informativo, no un bug; conviene no detenerse ahí.
- **Asistente IA / chat.** Igual que el bot, depende de `ANTHROPIC_API_KEY` con
  crédito. Sin él, responde en modo limitado.
- **Reseed = re-login.** Si corres `seed_demo` con la sesión abierta, el usuario se
  recrea; vuelve a iniciar sesión.
