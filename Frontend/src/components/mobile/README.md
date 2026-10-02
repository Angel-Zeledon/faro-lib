# Mobile shell and primitives

Phones (≤ 768px, `useIsNarrow()`) get a native-app shell; desktop is untouched.
Build each phone screen by branching on `useIsNarrow()` and composing the
primitives below. Import from `@/components/mobile`.

## The shell (already wired in `AppShell` — nothing to do per screen)

- **Bottom tab bar** (`MobileTabBar`): Panel `/compras`, Pedidos `/pedidos`,
  Inventario `/inventario`, Asistente `/asistente`, and **Más** — a sheet with
  every other screen grouped like the sidebar (`layout/navItems.ts`), language,
  theme, install, report a problem, user and log out. Unread-message badge on
  Más. Hides while the on-screen keyboard is up. No sidebar/drawer on phones.
- **Compact header** (`TopBar` narrow branch): title, back button on nested
  routes, `⋯` (messages, tour, report a problem) and the notifications bell.
  Title = `useMobileHeader` override → TopBar title map → nav label.
- **Safe areas**: `viewport-fit=cover`; tab bar, sheets and header pad with
  `env(safe-area-inset-*)`.
- **`--mobile-nav-h`** (CSS variable, phones only) = tab bar height incl. the
  home-indicator inset. `.page-content` is already padded by it. Anything you
  pin to the bottom must use `bottom: var(--mobile-nav-h, 0px)` — or just use
  `StickyActionBar`.
- `DesktopOnlyNotice` still shows on routes not in `MOBILE_READY`
  (`DesktopOnlyNotice.tsx`). **When your screen works on a phone, add its route
  to `MOBILE_READY`** — links that warn "opens a desktop screen"
  (`isMobileReady`) then stop warning automatically.

## Primitives

### `MobileList` + `MobileCard` — tables become cards

```tsx
<MobileList ariaLabel={t('nav.orders')}>
  {orders.map(o => (
    <MobileCard
      key={o.id}
      title={o.po_number}                       // one line, ellipsised
      subtitle={`${o.supplier} · ${date}`}      // one line, ellipsised
      value={formatMoney(o.total)}              // right side, tabular
      valueCaption={t('mobile.pedidos_card_skus', { n: o.lines })}
      status={{ label: t('po.reception_pending'), tone: 'warning' }}
      onClick={() => setDetail(o)}              // or href="/…"; chevron appears
    />
  ))}
</MobileList>
```

Props: `title`, `subtitle?`, `value?`, `valueCaption?`, `status? {label, tone}`,
`leading?` (icon/avatar), `children?` (extra row: chips, actions), `href?` |
`onClick?`, `chevron?`, `selected?`, `ariaLabel?`. Min height 56px; the whole
card is the tap target and is a real `<a>`/`<button>`.
`MobileList` props: `ariaLabel?`, `inset?` (rounded group, default true), `style?`.

`StatusBadge {label, tone}` — tones `danger | warning | success | info | neutral`.
`signalTone(signal)` maps the semáforo: PEDIR_YA→danger, PEDIR_PRONTO→warning,
OK→success, SOBRESTOCK→info, else neutral (same colours as desktop).

### Table → cards pattern (`MobileTableCards`)

Keep the desktop table byte-for-byte; branch:

```tsx
const narrow = useIsNarrow()
return narrow
  ? <MobileTableCards rows={items} getKey={i => i.sku} empty={<Empty/>}
      toCard={i => ({
        title: i.display_name || i.sku, subtitle: i.sku,
        value: fmtNum(i.current_stock), valueCaption: t('inventory.col_current_stock'),
        status: { label: t(`signal.${i.signal}`), tone: signalTone(i.signal) },
        onClick: () => setDetail(i),
      })} />
  : <table>…unchanged…</table>
```

Card = name, one secondary fact, the number the person came for, the status.
Every other column goes to the detail view (a `BottomSheet` or a detail state
with `useMobileHeader`). Do not cram columns into the card.

### `BottomSheet` — details, forms, pickers

```tsx
<BottomSheet open={!!detail} onClose={() => setDetail(null)} title={detail?.name}
  footer={<button className="mobile-btn mobile-btn-primary" onClick={save}>{t('common.save')}</button>}>
  …content scrolls inside…
</BottomSheet>
```

Props: `open`, `onClose`, `title`, `children`, `footer?`, `maxHeight?` (default
`88dvh`), `initialFocusRef?`, `hideCloseButton?`. Portal, `role="dialog"`,
`aria-modal`, labelled by the title, focus trap, Esc, tap-the-dim, × (44px),
drag the handle/header down to close, body scroll lock, focus restored.
Transform/opacity only; reduced motion respected.

### `StickyActionBar` — the screen's primary action

```tsx
<StickyActionBar hidden={!dirty}>
  <button className="mobile-btn mobile-btn-secondary" onClick={discard}>{t('inventory.btn_discard')}</button>
  <button className="mobile-btn mobile-btn-primary" onClick={save}>{t('common.save')}</button>
</StickyActionBar>
```

Pinned above the tab bar; renders an in-flow spacer of its own height so the
last row is never covered. `hidden` slides it away without unmounting.
Button classes (globals.css): `.mobile-btn` + `-primary | -secondary | -danger`
(48px, share the row).

### `MobileTabs` — scrollable segmented tabs

```tsx
<MobileTabs ariaLabel={t('inventory.views_aria')} value={view} onChange={setView}
  tabs={[{ id: 'simple', label: t('inventory.view_simple'), icon: <Package size={14}/> },
         { id: 'capital', label: t('inventory.view_dead_capital'), badge: 3 }]} />
```

Props: `tabs [{id, label, icon?, badge?}]`, `value`, `onChange`, `ariaLabel`,
`panelId?`, `style?`. Swipe-scrolls, snaps, fades the edge with more tabs,
keeps the active tab in view, ←/→/Home/End, 44px tall. Live on `/inventario`.

### `MobileSection` — titled block

```tsx
<MobileSection id="awaiting" title={t('mobile.pedidos_awaiting_title')} action={<Link href="…">…</Link>}
  description="optional one-liner">
  <MobileList>…</MobileList>
</MobileSection>
```

### `ComposerDock` — chat composer above the tab bar / keyboard

```tsx
<ComposerDock ariaLabel={t('messages.placeholder')} onHeightChange={() => scrollPageToBottom()}>
  <textarea … style={{ fontSize: 16 }} /> <button aria-label={t('messages.send')}>…</button>
</ComposerDock>
```

Pinned at `bottom: var(--mobile-nav-h)`, and right above the on-screen keyboard
while it is up (`useKeyboardInset()` reads `visualViewport`). Renders its own
spacer. Chat screens let the page scroll (`.page-content`) and call
`scrollPageToBottom()` when the thread grows. Live on `/mensajes` and
`/asistente`.

### `useMobileHeader` — title/back for in-page detail views

```tsx
useMobileHeader(detail ? { title: detail.name, onBack: () => setDetail(null) } : null)
```

`{ title?, onBack?, backHref? }`; `null` hands the header back to the route.
Cleared on unmount.

### `useIsNarrow()`

`false` on the server and first paint, the real answer one frame later
(`hooks/useIsNarrow.ts`). Breakpoint `NARROW_BREAKPOINT_PX` = 768.

## Rules of thumb

- Tap targets ≥ 44px (cards 56, buttons 48). Inputs 16px font (no iOS zoom),
  `inputMode="numeric"` for quantities.
- No horizontal page scroll: `document.documentElement.scrollWidth <= innerWidth`
  and `.page-content` scrollWidth ≤ clientWidth at 360px.
- Motion: transform/opacity, `var(--dur-*)` + `var(--ease-out)`; the global
  reduced-motion rule in globals.css already neutralises it.
- Copy goes in `i18n/translations.ts` (es + en), keys in English.
- Add your route to `MOBILE_READY` when it is done.
