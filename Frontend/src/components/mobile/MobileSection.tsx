'use client'

/**
 * A titled block of a phone screen: small uppercase heading, optional action
 * on the right (e.g. "Ver todo"), optional one-line description, content.
 *
 * ```tsx
 * <MobileSection title={t('mobile.pedidos_awaiting_title')} action={<Link href="/pedidos">{t('nav.orders')}</Link>}>
 *   <MobileList>…</MobileList>
 * </MobileSection>
 * ```
 *
 * Renders a <section> labelled by its heading, so screen-reader users can jump
 * between sections. Sections stack with 20px between them.
 */
export default function MobileSection({ title, description, action, children, id, style }: {
  title?: React.ReactNode
  description?: React.ReactNode
  action?: React.ReactNode
  children: React.ReactNode
  id?: string
  style?: React.CSSProperties
}) {
  const headingId = id ? `${id}-title` : undefined
  return (
    <section id={id} aria-labelledby={headingId} style={{ marginBottom: 20, minWidth: 0, ...style }}>
      {(title || action) && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, minHeight: 32, padding: '0 4px', marginBottom: 6 }}>
          {title && (
            <h2 id={headingId} style={{
              flex: 1, minWidth: 0, margin: 0, fontSize: 12, fontWeight: 700,
              textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--dim)',
            }}>{title}</h2>
          )}
          {action && <div style={{ flexShrink: 0, fontSize: 13, fontWeight: 600 }}>{action}</div>}
        </div>
      )}
      {description && (
        <p style={{ margin: '0 4px 10px', fontSize: 13, color: 'var(--muted)', lineHeight: 1.45 }}>{description}</p>
      )}
      {children}
    </section>
  )
}
