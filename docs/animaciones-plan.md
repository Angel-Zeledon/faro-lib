# Plan de animación — Faro

**Fecha:** 2026-07-28
**Alcance:** `Frontend/` (Next.js 14, App Router). Ningún cambio de backend, ningún token del semáforo, ninguna dependencia nueva.
**Restricciones heredadas:** marca Petróleo (sidebar `#0C3A40` en ambos temas, acento `#0F766E` / `#2BA79A`, claro por defecto); **un solo gesto de marca** — "el haz" de `.nav-item-active::before/::after`; el semáforo es intocable.

---

## 0. Hallazgos del reconocimiento (esto condiciona todo el plan)

### 0.1 No hay librería de animación instalada — y no debe haberla

`Frontend/package.json` declara: `clsx`, `echarts`, `echarts-for-react`, `exceljs`, `jspdf`, `lucide-react`, `next`, `react`, `react-dom`. **No hay `framer-motion`, ni `motion`, ni `react-spring`, ni `auto-animate`.** Dev: `tailwindcss` está instalado y `globals.css` usa `@tailwind base/components/utilities`, pero la app **no** estiliza con clases de Tailwind: casi todo es objeto de estilo inline, y las pocas clases (`.card`, `.data-table`, `.nav-item`, `.skeleton`) están escritas a mano dentro de `@layer components`.

**Decisión: CSS puro (transiciones + `@keyframes` en `globals.css`).** No propongo dependencia nueva. El argumento en contra de `framer-motion` es concreto:

- **Coste de bundle:** `framer-motion` v11 completo ronda 32–40 kB gzip en el cliente; incluso la ruta mínima (`LazyMotion` + `m` + `domAnimation`) queda en ~18 kB. Todo lo que propongo aquí cabe en ~60 líneas de CSS: **0 kB de JS**.
- **Coste arquitectónico:** `framer-motion` exige convertir `<div style={{...}}>` en `<motion.div animate={...}>`. En esta base de código eso significaría tocar componentes de 2.600 líneas (`app/inventory/page.tsx`) para reescribir su forma de estilar. Es una migración de sistema de estilos disfrazada de animación.
- **Lo único que CSS no puede hacer** es animar la reordenación de listas (FLIP / `layout`). Y eso está en la sección (C), rechazado por razones de producto, no de herramienta. Es decir: no hay caso de uso residual que justifique la dependencia.

Si en el futuro apareciera un caso real de FLIP, la respuesta correcta sería la View Transitions API detrás de un `@supports`, no una librería.

### 0.2 Lo que ya existe en `globals.css` (el plan extiende esto, no lo duplica)

| Elemento | Línea | Uso actual |
|---|---|---|
| `@keyframes fadeIn` | 267 | `pedidos:77`, `skus:1855`, `settings:461`, `config:1236` — inline, ad-hoc, sólo en 4 de ~15 pantallas |
| `@keyframes pulse-dot` | 272 | `components/ui/Badge.tsx:40` (prop `pulse`) |
| `@keyframes skeleton-shimmer` + `.skeleton` | 277 / 292 | `States.tsx` — bien resuelto, no se toca |
| `@keyframes toast-in` / `toast-out` | 282 / 287 | `Toast.tsx:27` — bien resuelto, no se toca |
| `.nav-item-active::before/::after` (el haz) | 240–252 | `Sidebar.tsx:227`. **Sin transición: al cambiar de ruta el haz salta.** |
| `.form-input` `transition: border-color 0.15s` | 230 | Único token de duración compartido de toda la app |
| Escena `auth-*` (18 keyframes) | 312–423 | Sólo `(auth)/layout.tsx` — superficie de marketing, fuera del alcance de este plan |
| `@media (prefers-reduced-motion: reduce)` | 425–433 | **Sólo cubre clases `auth-*`.** El resto de la app ignora la preferencia por completo |
| `html { scroll-behavior: smooth }` | 93 | Global; hoy no se desactiva con reduced-motion |

**No existen tokens de movimiento.** Las duraciones están escritas a mano en ~40 sitios, con al menos 8 valores distintos (`0.1s`, `0.12s`, `0.15s`, `0.2s`, `0.22s`, `0.25s`, `0.3s`, `0.4s`, `0.6s`).

### 0.3 Bug latente encontrado: `@keyframes spin` no está definido globalmente

`spin` se **inyecta** con `<style>` local en cuatro sitios:

- `app/(auth)/layout.tsx:52`
- `app/users/page.tsx:649`
- `components/ui/NarrativeCard.tsx:224`
- `components/ui/Spinner.tsx:15`

…pero se **consume** como string suelto en pantallas que no necesariamente montan ninguno de esos cuatro:

- `app/skus/page.tsx:1884` — `animation: 'spin 1s linear infinite'`
- `app/(auth)/verify-email/page.tsx:66` y `:138`
- `app/(auth)/reset-password/page.tsx:146`
- `app/integraciones/page.tsx`, `app/quick-start/page.tsx`

Y lo mismo con `slideUp` / `pulse`, inyectados en `app/analyst/page.tsx:654-658`.

Consecuencia real: en cualquier render donde no haya un `<Spinner>` ni una `NarrativeCard` en el árbol, **el icono "cargando" se queda congelado, quieto**. Es exactamente el peor fallo posible en un producto donde el usuario espera a que llegue un cálculo: un spinner detenido se lee como "se colgó". Esto es lo primero de la lista (A1), y es corrección de bug antes que animación.

---

## 1. Principio de movimiento

> **El movimiento sólo existe para responder una pregunta que el usuario acaba de hacerse.** Las tres preguntas legítimas son: *¿de dónde salió esto?*, *¿a dónde se fue?*, *¿sigue trabajando?*. Cualquier otra animación es decoración y no se construye.

Faro es la herramienta con la que alguien decide gastar dinero real en stock. Eso impone tres reglas duras:

1. **El movimiento nunca precede a la acción, sólo la sigue.** Un clic ya decidido se ejecuta en el frame siguiente. Se puede animar la *llegada* de la UI nueva; nunca la salida de la vieja cuando eso retrasa el efecto.
2. **El movimiento nunca altera un número mientras se lee.** Los dígitos aparecen en su valor final, siempre.
3. **El color saturado sigue siendo del semáforo.** El movimiento no puede convertirse en un segundo canal de urgencia que compita con él.

### Escala de duración (tokens nuevos)

| Token | Valor | Para qué |
|---|---|---|
| `--dur-1` | `90ms` | Feedback directo bajo el cursor: hover, borde de foco, cambio de fondo de fila |
| `--dur-2` | `140ms` | Cambio de estado en el sitio: aprobar una tarjeta, activar una pestaña, entrar la página |
| `--dur-3` | `200ms` | Algo aparece o desaparece: popover, modal, barra de carrito, panel desplegable |
| `--dur-4` | `260ms` | Techo absoluto dentro de la app. Sólo el drawer móvil, que recorre la pantalla entera |

Nada dentro de la app supera 260 ms. (La escena `auth-*` usa 0.7 s–20 s: es una portada, no una herramienta, y queda fuera.)

### Curvas

| Token | Valor | Para qué |
|---|---|---|
| `--ease-out` | `cubic-bezier(0.2, 0, 0.2, 1)` | **Por defecto.** Entradas y cambios de estado: arranca rápido, frena al final |
| `--ease-in` | `cubic-bezier(0.4, 0, 1, 1)` | Sólo salidas que no bloquean nada |

Deliberadamente **no** uso `cubic-bezier(0.16, 1, 0.3, 1)` (el ease expresivo de la escena de auth). Ese *overshoot* largo es la voz de la portada; dentro de la app se lee como lentitud. Que las dos superficies suenen distinto es correcto.

### Regla arquitectónica

Los estilos inline no pueden expresar `:hover`, `:active`, `::before`, `@keyframes` ni `@media`. Por tanto: **todo lo que dependa de un estado del CSS vive como clase en `globals.css`, y el componente añade `className`.** Precedentes ya existentes: `.nav-item`, `.skeleton`, `.auth-submit`. Las `transition` de propiedades ya presentes en el objeto inline se quedan inline, pero **con lista explícita de propiedades, nunca `all`** (ver A5).

---

## 2. Propuestas

### (A) Alto valor, bajo riesgo

---

#### A1 — Hoistear `spin` (y `pulse`, `slideUp`) a `globals.css` y borrar las inyecciones `<style>`

> **Estado: la parte de `spin` ya está hecha** (commit `43bd634`). `@keyframes spin` vive en `globals.css` y las cuatro inyecciones locales están borradas; verificado en el navegador que la animación resuelve en `/skus`. Queda pendiente lo mismo para `pulse` y `slideUp` de `analyst/page.tsx`.

**Archivos:** `src/app/globals.css` (+); `src/components/ui/Spinner.tsx:15`, `src/components/ui/NarrativeCard.tsx:223-224`, `src/app/users/page.tsx:649`, `src/app/(auth)/layout.tsx:52`, `src/app/analyst/page.tsx:652-660` (−)
**Dispara:** cualquier estado de carga.
**Por qué ayuda a comprender:** un spinner congelado le dice al comprador "esto se colgó" cuando en realidad está entrenando un modelo. Es la diferencia entre esperar y recargar.

```css
/* globals.css — junto a los keyframes existentes (~línea 275) */

/* Indeterminate progress. Defined once, globally: this used to be injected by
   four different components via <style> tags, so any screen that happened not
   to mount one of them rendered a frozen spinner. */
@keyframes spin { to { transform: rotate(360deg); } }

/* Soft attention pulse for placeholder text lines (analyst thinking state). */
@keyframes pulse { 0%, 100% { opacity: 0.4; } 50% { opacity: 0.8; } }

/* Panel that grows in place from just below its final position. */
@keyframes slideUp {
  from { opacity: 0; transform: translateY(6px); }
  to   { opacity: 1; transform: translateY(0); }
}
```

Y en cada uno de los cinco componentes, borrar el `<style>{`...`}</style>`. Nada más cambia: los `animation: 'spin 1s linear infinite'` que ya existen empiezan a funcionar en todas las pantallas.

**Esfuerzo:** 20 min. **Riesgo:** nulo (sólo se añade cobertura).

---

#### A2 — Tokens de movimiento + la regla global de `prefers-reduced-motion`

**Archivo:** `src/app/globals.css`
**Por qué:** sin tokens, la sección (B) reintroduce ocho duraciones distintas a mano. Y sin la regla global, cada animación nueva es una regresión de accesibilidad.

```css
/* globals.css — dentro de @layer base, en el bloque :root de constantes de marca */
:root {
  /* Motion. One scale for the whole app: 90ms answers the cursor, 140ms
     answers a click, 200ms introduces or removes a surface, 260ms is the
     ceiling and belongs to the mobile drawer alone. The expressive
     cubic-bezier(0.16,1,0.3,1) is reserved for the auth scene — inside the
     tool it reads as lag. */
  --dur-1: 90ms;
  --dur-2: 140ms;
  --dur-3: 200ms;
  --dur-4: 260ms;
  --ease-out: cubic-bezier(0.2, 0, 0.2, 1);
  --ease-in:  cubic-bezier(0.4, 0, 1, 1);
}
```

La regla `prefers-reduced-motion` completa está en la sección 3 (es obligatoria y se detalla allí).

**Esfuerzo:** 15 min. **Riesgo:** nulo.

---

#### A3 — Entrada de página única y consistente

**Archivos:** `src/app/globals.css` (+ `.page-enter`); `src/components/layout/AppShell.tsx:72-82`; y borrar el inline de `app/pedidos/page.tsx:77`, `app/skus/page.tsx:1855`, `app/settings/page.tsx:461`, `app/config/page.tsx:1236`
**Dispara:** cambio de ruta.
**Por qué ayuda a comprender:** hoy 4 pantallas de ~15 hacen fade y el resto aparecen de golpe, con duraciones distintas (0.25 s vs 0.3 s). La inconsistencia hace que la navegación se sienta rota, no rápida. Un fade único y corto marca "esto es contenido nuevo" sin retrasar la lectura.

```css
/* globals.css */
/* Route change: opacity only, never translate. A page the user is about to
   read must not move under their eyes — translate forces a re-fixation and
   costs more reading time than the animation saves in orientation. */
.page-enter { animation: pageEnter var(--dur-2) var(--ease-out); }
@keyframes pageEnter { from { opacity: 0; } to { opacity: 1; } }
```

```tsx
// AppShell.tsx — Shell()
import { usePathname } from 'next/navigation'
// ...
const pathname = usePathname()
// ...
        <div className="page-content" style={narrow ? { overflowX: 'hidden', padding: 12 } : undefined}>
          <ReadOnlyBanner />
          <VerifyEmailBanner />
          <DesktopOnlyNotice />
          {/* Keyed on the route so the enter animation runs on navigation and
              not on every state update inside a screen. */}
          <div key={pathname} className="page-enter">{children}</div>
        </div>
```

Nota: los banners quedan **fuera** del wrapper a propósito — son persistentes, no contenido de la ruta.

**Esfuerzo:** 45 min (incluye limpiar los 4 inline). **Riesgo:** bajo. Verificar que el `key` no rompa el scroll restoration de ninguna pantalla larga (`/inventory`).

---

#### A4 — La barra de carrito de `/hoy` entra en lugar de aparecer

**Archivos:** `src/app/hoy/page.tsx:1409-1417` (sticky, escritorio) y `src/app/hoy/HoyMobile.tsx:653-658` (`MobileCartBar`, fija)
**Dispara:** el primer `approved.length > 0`.
**Por qué ayuda a comprender:** **es el mayor déficit de comprensión de la app.** El comprador pulsa "Aprobar" en una tarjeta que está a media pantalla, y a 400 px de distancia — abajo del todo, o fuera del viewport en móvil — se materializa de golpe una barra con el total que va a comprometer. Nada conecta las dos cosas. Un deslizamiento de 200 ms desde abajo dice "esto salió de lo que acabas de hacer y vive aquí".

```css
/* globals.css */
/* The commitment bar. It materialises far from the button that summoned it,
   so it arrives from its own edge: the travel is what ties the approval to
   the total. */
.cart-bar-enter { animation: cartBarEnter var(--dur-3) var(--ease-out); }
@keyframes cartBarEnter {
  from { opacity: 0; transform: translateY(12px); }
  to   { opacity: 1; transform: translateY(0); }
}
```

```tsx
// hoy/page.tsx:1409 — sticky cart
{approved.length > 0 && (
 <div className="cart-bar-enter" style={{
  position: 'sticky', bottom: 16,
  /* ...resto sin cambios... */
 }}>
```

```tsx
// HoyMobile.tsx:653 — MobileCartBar
    <div className="cart-bar-enter" style={{
      position: 'fixed', left: 0, right: 0, bottom: 0, zIndex: 40,
      /* ...resto sin cambios... */
    }}>
```

**Importante:** sólo entrada. Al vaciar el carrito la barra desaparece de inmediato — el usuario pulsó "Limpiar" y quiere ver la pantalla despejada, no esperar 200 ms a que se vaya.

**Esfuerzo:** 30 min. **Riesgo:** bajo. En móvil la barra tiene `env(safe-area-inset-bottom)`; verificar que el `translateY` no deje ver el fondo bajo ella durante el recorrido (no lo hace: parte de +12 px, es decir por debajo del borde).

---

#### A5 — `transition: all` → lista explícita en la `ActionCard` de `/hoy`

**Archivo:** `src/app/hoy/page.tsx:196-205`
**Dispara:** aprobar / rechazar / restaurar una recomendación.
**Por qué ayuda a comprender:** la tarjeta ya tiene `transition: 'all 0.2s'`, y `all` es un error activo aquí: cuando se despliega el panel "¿por qué?" (`showWhy`, línea 267) el navegador intenta animar el `padding` y el `border-width` del contenedor a la vez que su contenido crece, lo que produce el temblor que se ve hoy. Restringir la transición a las tres propiedades que sí cambian de estado hace que aprobar se lea limpio: el raíl izquierdo pasa de color de señal a verde y el fondo se tiñe, en 140 ms.

```tsx
// hoy/page.tsx — ActionCard
 <div style={{
  border:       `1px solid ${isApproved ? '#22c55e40' : isRejected ? 'var(--border)' : accent + '30'}`,
  borderLeft:   `4px solid ${isApproved ? '#22c55e'  : isRejected ? 'var(--border)' : accent}`,
  borderRadius: 10,
  padding:      '16px 18px',
  marginBottom: 10,
  background:   isApproved ? 'rgba(34,197,94,0.03)' : isRejected ? 'var(--surface-2)' : 'var(--surface)',
  opacity:      isRejected ? 0.5 : 1,
  // Explicit list, never `all`: `all` also animates padding and border-width,
  // which is what makes the card judder while the "why" panel expands. The
  // approval colour is the green of a decision taken — it is NOT a semáforo
  // token, and the SignalBadge inside is never touched by this transition.
  transition:   'border-color var(--dur-2) var(--ease-out), background-color var(--dur-2) var(--ease-out), opacity var(--dur-2) var(--ease-out)',
 }}>
```

**Esfuerzo:** 15 min. **Riesgo:** nulo.

---

#### A6 — Destello (no conteo) en el total del carrito cuando cambia

**Archivos:** `src/app/globals.css`; `src/app/hoy/page.tsx:1422-1425`; `src/app/hoy/HoyMobile.tsx:667-670`
**Dispara:** el total del carrito cambia porque se aprobó, se quitó o se editó una cantidad.
**Por qué ayuda a comprender:** el ojo está en la tarjeta, no en el total. Un destello de fondo de 400 ms que **no toca los dígitos** dirige la mirada al número que acaba de cambiar sin mentir en ningún frame sobre cuánto es.

```css
/* globals.css */
/* A value the user did not look at just changed. The highlight is background
   only: the digits are correct from the first frame — see the rejected
   count-up in (C2). */
.value-changed { animation: valueChanged 420ms var(--ease-out); }
@keyframes valueChanged {
  from { background: color-mix(in srgb, var(--accent) 22%, transparent); }
  to   { background: transparent; }
}
```

```tsx
// hoy/page.tsx — el total dentro de la barra sticky.
// `key` es el propio valor: cuando cambia, React remonta el <span> y la
// animación vuelve a correr. Sin key, sólo se dispararía en el primer render.
{totalValue > 0 && (
  <span key={totalValue} className="value-changed"
        style={{ borderRadius: 4, padding: '0 3px' }}>
    {` · ${t('hoy.cart_total_label')}: ${fmtMoney(totalValue)}`}
  </span>
)}
```

**Esfuerzo:** 40 min (dos sitios + extraer el total a su propio `<span>`). **Riesgo:** bajo. El `padding: '0 3px'` compensa el radio del destello sin mover nada alrededor.

---

#### A7 — Entrada del `ConfirmDialog`

**Archivo:** `src/components/ui/ConfirmDialog.tsx:59-77`
**Dispara:** cualquier acción irreversible (borrar dataset, cancelar orden ya enviada, revocar clave).
**Por qué ayuda a comprender:** hoy el modal aparece instantáneo, y su propio docstring dice que se reserva para lo irreversible. 200 ms de fondo entrando + 140 ms de panel escalando desde 0.98 son el latido que convierte "apareció una caja" en "esto es una puerta". Es el único sitio de la app donde un instante de fricción es la función.

```css
/* globals.css */
.modal-backdrop-enter { animation: modalBackdrop var(--dur-3) var(--ease-out); }
@keyframes modalBackdrop { from { opacity: 0; } to { opacity: 1; } }

/* 0.98, not 0.9: a bigger scale reads as a bouncy toy. This is the last gate
   before something irreversible. */
.modal-panel-enter { animation: modalPanel var(--dur-2) var(--ease-out); }
@keyframes modalPanel {
  from { opacity: 0; transform: scale(0.98) translateY(-4px); }
  to   { opacity: 1; transform: scale(1) translateY(0); }
}
```

```tsx
// ConfirmDialog.tsx
        <div role="dialog" aria-modal="true" aria-label={opts.title}
          className="modal-backdrop-enter"
          style={{ position: 'fixed', inset: 0, zIndex: 10000, /* ...igual... */ }}
          onClick={() => close(false)}>
          <div onClick={(e) => e.stopPropagation()} className="modal-panel-enter"
            style={{ /* ...igual... */ }}>
```

**Sin animación de salida, en ninguno de los dos botones.** El usuario ya decidió; el modal se va en el frame siguiente. (Ver la lista de "nunca animar", punto 2.)

**Esfuerzo:** 20 min. **Riesgo:** nulo. `autoFocus` en el botón de confirmar (línea 98) sigue funcionando: el foco no espera a la animación.

---

**Esfuerzo total (A): ~3 h.** Riesgo agregado: bajo. Un solo bloque de CSS nuevo (~50 líneas), cinco borrados de `<style>`, y siete `className` añadidos.

---

### (B) Vale la pena

---

#### B1 — Despliegue en el sitio: fila expandida de `/inventory` y panel "¿por qué?" de `/hoy`

**Archivos:** `src/app/inventory/page.tsx:2589-2599` (`<tr>` expandida con `CalcExplainer` + `PlanningValues` + `SimulatorPanel`); `src/app/hoy/page.tsx:267-387` (panel `showWhy`)
**Dispara:** clic en el chevron de una fila / en "¿por qué?".
**Por qué ayuda a comprender:** ambos paneles contienen la explicación del cálculo — de dónde sale la cantidad recomendada. Hoy aparecen de golpe y empujan todo lo de abajo en un frame; la vista salta y hay que reencontrar la fila. Un crecimiento de 200 ms mantiene el anclaje visual en la fila que se pulsó.

**Esta es la única excepción admitida a "nada de layout shift en una tabla de datos"**, y sólo porque: (a) la desplaza el propio usuario, (b) desplaza la fila que él acaba de pulsar, (c) las filas de abajo se mueven igual con o sin animación — la animación sólo hace legible ese movimiento.

Técnica sin `height: auto` (que no es animable) y sin medir en JS:

```css
/* globals.css */
/* Grow-in-place without knowing the final height: the grid row goes 0fr -> 1fr
   and the child clips. No JS measurement, no height:auto. */
.reveal-panel {
  display: grid;
  grid-template-rows: 0fr;
  transition: grid-template-rows var(--dur-3) var(--ease-out);
}
.reveal-panel > * { overflow: hidden; min-height: 0; }
.reveal-panel[data-open="true"] { grid-template-rows: 1fr; }
```

```tsx
// inventory/page.tsx — la <tr> expandida
{isExpanded && item.calc_explanation && (
 <tr>
  <td colSpan={13} style={{ padding: '0 16px 12px 48px', /* ...igual... */ }}>
   <div className="reveal-panel" data-open={isExpanded}>
    <div>
     <CalcExplainer exp={item.calc_explanation} moq={item.moq} />
     <PlanningValues item={item} />
     <SimulatorPanel item={item} />
    </div>
   </div>
  </td>
 </tr>
)}
```

**Advertencia de implementación:** con `{isExpanded && ...}` el nodo se monta ya abierto y no hay transición. Hay que renderizar el `<tr>` siempre que la fila tenga `calc_explanation` y controlar sólo `data-open` — o, más simple y con menos DOM en una tabla de 100 filas, montar el `<tr>` y setear `data-open` en un `useEffect` de un frame. Recomiendo lo segundo, y **si en implementación resulta frágil, esto se degrada a "sin animación" sin bloquear nada más del plan.**

**Esfuerzo:** 1,5 h (los dos sitios, con el cuidado del montaje). **Riesgo:** medio — es el único ítem que toca el layout de una tabla.

---

#### B2 — Panel del `AlertBell`

**Archivo:** `src/components/alerts/AlertBell.tsx:292-300`
**Dispara:** clic en la campana.
**Por qué ayuda a comprender:** el popover está `position: absolute` bajo la campana; no cuesta layout. 140 ms de fade + 4 px de caída dicen "esto colgó de ese botón", que es justo la relación que un popover tiene que comunicar.

```css
/* globals.css */
/* Anchored popovers: the small drop is the tether to the trigger. */
.popover-enter { animation: popoverEnter var(--dur-2) var(--ease-out); transform-origin: top right; }
@keyframes popoverEnter {
  from { opacity: 0; transform: translateY(-4px) scale(0.99); }
  to   { opacity: 1; transform: translateY(0) scale(1); }
}
```

```tsx
// AlertBell.tsx:292
          <div className="popover-enter" style={{
            position: 'absolute', top: 'calc(100% + 8px)', right: 0,
            /* ...resto sin cambios... */
          }}>
```

**Esfuerzo:** 10 min. **Riesgo:** nulo. Reutilizable en el menú de usuario de `TopBar` y en cualquier otro popover anclado.

---

#### B3 — Paleta Ctrl+K

**Archivo:** `src/components/layout/SkuSearchOverlay.tsx:398-421`
**Dispara:** `Ctrl/Cmd+K`.
**Por qué ayuda a comprender:** la paleta se superpone a toda la app; sin entrada, la pantalla parpadea a otra cosa. 200 ms de fondo + 140 ms de panel cayendo 6 px la sitúan como una capa encima, no como una navegación.

Reutiliza `.modal-backdrop-enter` de A7, con panel propio:

```css
/* globals.css */
.palette-enter { animation: paletteEnter var(--dur-2) var(--ease-out); }
@keyframes paletteEnter {
  from { opacity: 0; transform: translateY(-6px); }
  to   { opacity: 1; transform: translateY(0); }
}
```

```tsx
// SkuSearchOverlay.tsx:398
    <div onClick={close} className="modal-backdrop-enter" style={{ position: 'fixed', inset: 0, zIndex: 300, /* ... */ }}>
      <div onClick={e => e.stopPropagation()} role="dialog" aria-modal="true" className="palette-enter" style={{ /* ... */ }}>
```

**Restricciones duras aquí:** (1) **no tocar** el `setTimeout(..., 30)` del autofocus (línea 205) — el input debe aceptar teclas antes de que termine la animación; (2) **cerrar es instantáneo**, sin animación de salida; (3) el `scrollIntoView({ block: 'nearest' })` de la navegación con flechas (línea 323) **no puede** volverse suave — con `html { scroll-behavior: smooth }` heredado, bajar 10 posiciones con la flecha se convertiría en un scroll continuo persiguiendo al cursor. Verificarlo explícitamente.

**Esfuerzo:** 25 min. **Riesgo:** bajo, con la verificación (3) hecha a mano en el navegador.

---

#### B4 — El haz entra al cambiar de ruta

**Archivo:** `src/app/globals.css:240-252`
**Dispara:** navegación.
**Por qué ayuda a comprender:** hoy el haz salta de un ítem a otro sin transición. Un barrido vertical de 220 ms sobre la barra + el degradado hace que el gesto de marca *actúe* como un faro barriendo, en lugar de ser un adorno estático. **No es un gesto nuevo: es el gesto que ya existe, animando su propia aparición.**

```css
/* globals.css — sustituye los dos pseudo-elementos existentes */
.nav-item-active::before {
  content: '';
  position: absolute; left: 0; top: 6px; bottom: 6px; width: 3px;
  border-radius: 2px;
  background: var(--sidebar-beam);
  /* The beam sweeps into place from its centre instead of appearing whole.
     transform-origin: center is what makes it read as a sweep and not a wipe. */
  animation: beamIn 220ms var(--ease-out);
  transform-origin: center;
}
.nav-item-active::after {
  content: '';
  position: absolute; left: 3px; top: 6px; bottom: 6px; width: 44px;
  border-radius: 2px;
  background: linear-gradient(90deg, rgba(76,195,181,0.25), rgba(76,195,181,0));
  pointer-events: none;
  animation: beamWashIn 260ms var(--ease-out);
}
@keyframes beamIn     { from { transform: scaleY(0.25); opacity: 0; } to { transform: scaleY(1); opacity: 1; } }
@keyframes beamWashIn { from { opacity: 0; } to { opacity: 1; } }
```

**Esfuerzo:** 20 min. **Riesgo:** bajo, pero **requiere aprobación de marca** — toca el único gesto aprobado. Si al verlo no convence, se revierte borrando cuatro líneas.

---

#### B5 — El velo del drawer móvil entra con el drawer

**Archivo:** `src/components/layout/Sidebar.tsx:142-151`
**Dispara:** abrir el menú en móvil.
**Por qué ayuda a comprender:** hoy el `<aside>` desliza en 220 ms (línea 133) pero el velo negro `rgba(0,0,0,0.5)` aparece de golpe. Se ve como dos cosas separadas, y el golpe negro es lo más brusco de la app en móvil.

```tsx
// Sidebar.tsx:142
    {isDrawer && drawerOpen && (
      <div onClick={closeDrawer} aria-hidden="true" className="modal-backdrop-enter"
           style={{ position: 'fixed', inset: 0, zIndex: 69, background: 'rgba(0,0,0,0.5)' }} />
    )}
```

Y alinear el `<aside>` con la escala: `transition: 'transform var(--dur-4) var(--ease-out)'` (260 ms en vez de 220 ms sueltos).

**Esfuerzo:** 10 min. **Riesgo:** nulo.

---

#### B6 — Fundido de esqueleto a contenido

**Archivo:** `src/components/ui/States.tsx` (nuevo wrapper opcional) + los llamantes de `LoadingState` en `/hoy`, `/pedidos`, `/inventory`
**Dispara:** llega la respuesta del backend.
**Por qué ayuda a comprender:** hoy el `SkeletonTable` desaparece y los datos aparecen en el mismo frame. Como el esqueleto tiene la forma correcta (está bien hecho), 140 ms de fade sobre el contenido cargado hacen que se lea como que el placeholder *se convirtió* en los datos, y no como un parpadeo.

Basta reutilizar `.page-enter` de A3 en el contenedor de datos:

```tsx
// pedidos/page.tsx, el brazo con datos
) : (
  <Card padding={0} overflow="hidden">
    <div className="page-enter">
      <POHistoryTable entries={history} onReceive={setReceivingPO}
                      suppliersWithoutContact={contactHealth.map(r => r.supplier)} />
    </div>
  </Card>
)
```

**Esfuerzo:** 45 min (3–4 pantallas). **Riesgo:** bajo.

---

#### B7 — `Button`: propiedades explícitas y estado de pulsación

**Archivo:** `src/components/ui/Button.tsx:40`
**Dispara:** hover / pulsación.
**Por qué ayuda a comprender:** `transition: 'all 0.15s'` también anima `padding` y `opacity` del estado `disabled`, con lo que un botón que pasa a `loading` se desvanece raro. Y no hay estado `:active` en ninguna parte de la app: en un táctil no hay confirmación de que el dedo aterrizó.

```css
/* globals.css */
.btn {
  transition: background-color var(--dur-1) var(--ease-out),
              border-color var(--dur-1) var(--ease-out),
              color var(--dur-1) var(--ease-out);
}
/* 1px, and only while held: the acknowledgement that the tap landed. It ends
   the moment the finger lifts, so it can never delay the action. */
.btn:active:not(:disabled) { transform: translateY(1px); }
```

```tsx
// Button.tsx
import clsx from 'clsx'
// ...
    <button
      {...props}
      className={clsx('btn', props.className)}
      disabled={disabled || loading}
      style={{
        /* ...igual, pero sin la línea `transition: 'all 0.15s'` ... */
        outline: 'none', userSelect: 'none',
        ...style,
      }}
    >
```

Nota: buena parte de la app no usa `Button` (usa `<button style={{ all: 'unset', ... }}>` a mano — p. ej. `hoy/page.tsx:433`). Añadir `className="btn"` a los botones de acción principales de `/hoy` y `/pedidos` es un extra barato; no es requisito.

**Esfuerzo:** 30 min. **Riesgo:** bajo.

---

**Esfuerzo total (B): ~4 h**, de las cuales 1,5 h son B1 (el único ítem con riesgo real). B1 es separable: si se complica, se corta y el resto de (B) sigue en pie.

---

### (C) Rechazado explícitamente

Esta sección importa tanto como las otras dos. El objetivo es que la app se sienta atendida, no animada.

---

**C1 — Cualquier movimiento sobre el semáforo.** Pulsar `PEDIR_YA`, hacer latir el `SignalBadge`, barrer el raíl rojo de las filas críticas de `/inventory` (`inventory/page.tsx:2497`), animar los `--signal-*`.
*Por qué no:* los tokens del semáforo son datos de accesibilidad calibrados (`globals.css:41-52`, contrastes verificados a ≥4,5:1). El movimiento sería un **segundo canal de urgencia** superpuesto a un canal que ya está calibrado, y rompería su calibración: una fila `OK` que está quieta al lado de una `PEDIR_YA` que late no se lee como "esta está bien", se lee como "esta está apagada". Además, en un catálogo de 2.000 SKUs con `PAGE_SIZE = 100`, la mitad de la página estaría en movimiento permanente. El semáforo se queda exactamente como está.

**C2 — Conteo animado (count-up) de KPIs, del total del carrito o del margen protegido.**
*Por qué no:* durante 400–800 ms la pantalla muestra un número **falso**. Un comprador que mira el total mientras corre la animación lee una cifra que no es la que va a comprometer. Es el fallo de diseño más caro posible en este producto. El destello de A6 existe precisamente para resolver la misma necesidad sin tocar los dígitos.

**C3 — Transiciones de ruta con desplazamiento (slide entre `/hoy` y `/pedidos`).**
*Por qué no:* implica una relación espacial entre pantallas que no existe — no hay un "adelante" y un "atrás" en esta navegación, es un conmutador de secciones. Y cuesta 200–300 ms antes del primer pixel de la pantalla que el usuario pidió. A3 (opacidad, 140 ms, sin desplazamiento) da toda la orientación que hace falta.

**C4 — Entrada escalonada (stagger) de filas de tabla o de las `ActionCard` de `/hoy`.**
*Por qué no:* con `PAGE_SIZE = 100` y 30 ms de retraso por fila, la última llega 3 segundos tarde. Aunque se limitara a las tarjetas de `/hoy`, un escalonado impide **escanear**: el comprador abre `/hoy` para ver de un golpe cuántas decisiones tiene hoy, y el escalonado le obliga a esperar a que la lista termine de formarse para saber su tamaño.

**C5 — Añadir `framer-motion` o cualquier dependencia de animación.**
*Por qué no:* ~32–40 kB gzip (v11 completa) o ~18 kB en su configuración mínima con `LazyMotion`, contra ~0 kB de las 60 líneas de CSS de este plan. Y obligaría a convertir estilos inline en props de `motion.*` dentro de archivos de 2.600 líneas. Lo único que CSS no cubre es FLIP, que está rechazado en C6 por razones de producto. Sin caso de uso residual, no hay dependencia.

**C6 — Reordenación animada (FLIP) del carrito de `/hoy` cuando se aprueba o se quita una línea.**
*Por qué no:* además de necesitar librería o FLIP manual, mueve tarjetas que el comprador está a punto de pulsar. Un objetivo de clic que se desliza es peor que un salto: el salto termina antes de que la mano llegue.

**C7 — Elevación al hover (`translateY(-2px)` + sombra) en tarjetas KPI y filas de tabla.**
*Por qué no:* en `/inventory` son 100 filas con repintado de sombra, y una tabla densa cuyas filas se levantan deja de leerse como una rejilla — que es exactamente lo que la hace comparable columna a columna. El cambio de fondo que ya existe (`data-table tbody tr:hover`, `globals.css:217`) es la cantidad correcta.

**C8 — Animación de salida en modales, toasts al confirmar, o en la paleta al cerrar.**
*Por qué no:* la decisión ya está tomada; toda animación posterior es tiempo robado. (La salida de toast que ya existe, `toast-out`, es distinta y correcta: el toast se va **solo**, no por un clic que espera un resultado.)

**C9 — Extender la escena ambiental de `auth` (haz, motas, mar) a la app.**
*Por qué no:* `/login` es una portada y puede permitirse atmósfera; `/hoy` es una herramienta que se abre 40 veces al día. Movimiento perpetuo en una pantalla de trabajo es fatiga, y compite con el semáforo por la atención.

**C10 — Barras de progreso animadas nuevas y esqueletos adicionales.**
*Por qué no:* `.skeleton` (`globals.css:292`) y `SkeletonTable`/`SkeletonCards` ya cubren el caso, con la forma correcta. Añadir shimmer al sidebar, al TopBar o a los KPIs multiplicaría superficie en movimiento sin añadir información. Igual con `transition: 'width 0.6s ease'` de las barras de precisión de `/skus:1466`: ya existe, funciona, no se toca.

**C11 — Animar la aparición o el foco de un `form-input` o del anillo de foco global.**
*Por qué no:* el anillo de `:focus-visible` (`globals.css:120-133`) es una ayuda de accesibilidad; tiene que aparecer en el frame en que el foco llega, sin excepción. Un usuario de teclado navegando rápido con Tab vería el anillo perpetuamente retrasado respecto a dónde está realmente el foco.

---

## 3. `prefers-reduced-motion: reduce` — obligatorio

Hoy la única regla (`globals.css:425-433`) cubre exclusivamente las clases `auth-*`. **Todo lo demás de la app ignora la preferencia del sistema**, incluidos el shimmer del esqueleto, los spinners, los toasts y el `scroll-behavior: smooth` global.

Regla única, al final de `globals.css`, que sustituye y generaliza el bloque actual:

```css
/* ─────────────────────────────────────────────────────────────────────────
   Reduced motion. One rule for the whole app, deliberately blunt.

   1ms, not 0s: an animation of zero duration never fires `animationend` /
   `transitionend`, so any code that awaits one would hang forever. 1ms fires
   the event on the next frame and is imperceptible.

   `scroll-behavior: auto` is not optional here — html has `scroll-behavior:
   smooth` (line 93), which the command palette's arrow-key scrollIntoView
   inherits.
   ───────────────────────────────────────────────────────────────────────── */
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 1ms !important;
    animation-iteration-count: 1 !important;
    animation-delay: 0ms !important;
    transition-duration: 1ms !important;
    transition-delay: 0ms !important;
  }
  html, * { scroll-behavior: auto !important; }

  /* Two exceptions the blanket rule gets wrong on its own. */

  /* A shimmer frozen at 1ms leaves a random gradient position across the
     placeholder — a diagonal smear that reads as a rendering bug. Flatten it. */
  .skeleton {
    animation: none !important;
    background: var(--surface-2) !important;
  }

  /* Indeterminate progress is not decoration: it is the only signal that the
     app is still working. Slowed to a crawl rather than stopped, because a
     stationary spinner means "crashed". */
  [class*="spinner"], .spin, svg[data-spinner] {
    animation-duration: 2.4s !important;
    animation-iteration-count: infinite !important;
  }

  /* Ambient auth scene: kill outright, it carries no information. */
  .auth-ring, .auth-wash, .auth-dot, .auth-drift, .auth-swell, .auth-reflection,
  .auth-bearing, .auth-cloud, .auth-bird, .auth-boat, .auth-lampglow, .auth-mote,
  .auth-submit::after { animation: none !important; }
  .auth-ring { opacity: 0.5 !important; }
  .auth-particle-layer { transition: none !important; transform: none !important; }
  [class*="auth-enter"] { animation: none !important; opacity: 1 !important; transform: none !important; }
}
```

**Lo que la regla debe desactivar, comprobado uno por uno:**

| Elemento | Efecto con reduced-motion |
|---|---|
| `.page-enter` (A3), `.cart-bar-enter` (A4) | Aparecen ya presentes, sin fade ni desplazamiento |
| `.value-changed` (A6) | El destello no ocurre; **el número sigue siendo correcto**, que es lo único que importaba |
| `.modal-backdrop-enter` / `.modal-panel-enter` (A7) / `.palette-enter` (B3) | Modal y paleta aparecen instantáneos, con foco correcto |
| `.reveal-panel` (B1) | El panel se abre de golpe — su comportamiento actual, aceptable |
| Haz `beamIn` (B4) | Estático, exactamente como hoy |
| `.btn:active` (B7) | El `transform` no es una transición, así que **se conserva** — es feedback táctil directo, no movimiento decorativo, y debe quedarse |
| `.skeleton` | Fondo plano `--surface-2` (excepción explícita) |
| Spinners | Siguen girando, a 2,4 s por vuelta (excepción explícita) |
| `html { scroll-behavior: smooth }` | Anulado a `auto` |
| Escena `auth-*` | Completamente detenida |

**Verificación obligatoria:** DevTools → Rendering → *Emulate CSS media feature prefers-reduced-motion: reduce*, y recorrer `/hoy` (aprobar → carrito), `/inventory` (expandir fila), Ctrl+K, y un `ConfirmDialog`. **Ninguna** de esas rutas puede quedar bloqueada esperando un evento que no llega.

---

## 4. Lo que nunca se anima

1. **El semáforo.** Los cinco estados (`PEDIR_YA`, `PEDIR_PRONTO`, `OK`, `SOBRESTOCK`, `SIN_DATOS`), sus tokens `--signal-*`, el `SignalBadge` y cualquier raíl o borde teñido con ellos. Ni pulso, ni brillo, ni recoloreado, ni transición de un estado a otro. El color saturado es del semáforo y sólo del semáforo, y su calibración de contraste no admite un canal de movimiento encima.
2. **Nada que retrase un clic ya hecho.** Aprobar una recomendación, generar la orden, guardar una recepción, confirmar en un `ConfirmDialog`, cerrar la paleta. El cambio de estado ocurre en el frame siguiente; sólo puede animarse la **llegada** de una superficie nueva, nunca la salida de la vieja ni el intervalo antes del efecto. Corolario: **cero animaciones de salida en modales, diálogos y la paleta**.
3. **Ningún desplazamiento de layout en una tabla de datos.** Ordenar, filtrar, cambiar de página, entrar en modo edición masiva, o el hover sobre `.data-table tbody tr` no mueven ninguna fila ni un píxel. Única excepción, argumentada en B1: la fila que el propio usuario expandió, y sólo su propio panel de detalle.
4. **Los dígitos.** Totales, KPIs, cantidades, márgenes, días de cobertura: siempre en su valor final desde el primer frame. Se puede destacar el contenedor (A6); nunca el número.
5. **El sidebar como superficie.** Es el portador invariante de la marca. No cambia de color, no se desvanece, no se desplaza (salvo el drawer móvil, que es otro componente). El haz de B4 es la única excepción, y sólo en el momento del cambio de ruta.
6. **El anillo de foco.** `:focus-visible` aparece instantáneo, siempre (C11).

---

## 5. Orden de ejecución y esfuerzo

| Grupo | Contenido | Esfuerzo | Riesgo |
|---|---|---|---|
| **A1–A2** | Bug del `spin` + tokens + regla de reduced-motion | **~1 h** | Nulo — hacer primero, es corrección de bug |
| **A3–A7** | Entrada de página, barra de carrito, `ActionCard`, destello del total, `ConfirmDialog` | **~2 h** | Bajo |
| **B2, B3, B5, B6, B7** | Popover, paleta, velo del drawer, fundido de esqueleto, `Button` | **~2 h** | Bajo |
| **B1** | Despliegue en el sitio (fila de `/inventory` + "¿por qué?" de `/hoy`) | **~1,5 h** | Medio — separable, se puede cortar |
| **B4** | Haz animado | **~20 min** | Bajo, pero **requiere visto bueno de marca** |
| **C** | — | **0 h** | — |
| | **Total** | **~7 h** | |

**Huella:** ~70 líneas nuevas en `globals.css`, cinco bloques `<style>` borrados, y ~14 archivos con un `className` añadido o una cadena de `transition` reescrita. Ninguna dependencia nueva, ningún cambio de arquitectura de estilos.

**Verificación por grupo:**
- `cd Frontend && npx tsc --noEmit`
- Navegador, ambos temas (claro por defecto y oscuro), en `/hoy`, `/inventory`, `/pedidos` y la paleta.
- Emulación de `prefers-reduced-motion: reduce` sobre las mismas rutas.
- Viewport estrecho para `HoyMobile` y el drawer.
- **No** ejecutar `npm run build` con `next dev` levantado (corrompe la caché `.next`).
