'use client'
/**
 * Accounting integrations — connect Alegra/Siigo, sync stock+sales, watch
 * connection health. Enterprise-only (see `Feature.INTEGRATIONS` on the
 * backend): the whole page is gated behind `useEntitlements().has('integrations')`,
 * mirroring the Sidebar nav lock (components/layout/Sidebar.tsx) so a user who
 * navigates here directly (not just via the nav item) still sees the upsell
 * instead of a confusing empty/broken screen.
 *
 * Credentials are write-only: the connect form only ever sends field values to
 * the backend and never receives them back — `Integration` (lib/api.ts) has no
 * credential fields, by contract with the backend (which never returns them).
 */
import { useCallback, useEffect, useState } from 'react'
import Link from 'next/link'
import {
  Plug, RefreshCw, Trash2, CheckCircle2, XCircle, Clock, Lock,
} from 'lucide-react'
import {
  listIntegrations, connectIntegration, syncIntegration, deleteIntegration,
  type Integration, type ProviderInfo,
} from '@/lib/api'
import { useLanguage } from '@/contexts/LanguageContext'
import { useEntitlements } from '@/lib/entitlements'
import { useConfirm } from '@/components/ui/ConfirmDialog'
import { LoadingState, ErrorState, InlineError } from '@/components/ui/States'

const C = {
  surface: 'var(--surface)', card: 'var(--surface-2)', border: 'var(--border)',
  text: 'var(--text)', muted: 'var(--muted)', dim: 'var(--dim)',
  green: '#22c55e', amber: '#f59e0b', red: '#ef4444', indigo: '#818cf8',
}

const inputS: React.CSSProperties = {
  background: 'var(--surface-2)', border: `1px solid var(--border)`,
  borderRadius: 6, color: 'var(--text)', fontSize: 13, outline: 'none',
  padding: '7px 10px', width: '100%', boxSizing: 'border-box',
}

// Human-friendly display name for known providers; falls back to the raw key
// for any provider the backend registers later that the frontend hasn't
// special-cased yet.
const PROVIDER_LABEL: Record<string, string> = { alegra: 'Alegra', siigo: 'Siigo' }

function providerLabel(provider: string) {
  return PROVIDER_LABEL[provider] ?? provider
}

// Credential field keys are English identifiers shared with the backend
// (see `backend/integrations/registry.py` SUPPORTED_PROVIDERS) — the label
// shown to the user is translated via i18n keys namespaced per field.
function fieldLabel(t: (k: string) => string, field: string) {
  const key = `integrations.field_${field}`
  const translated = t(key)
  return translated === key ? field : translated
}

function fmtDate(iso: string | null, lang: 'es' | 'en') {
  if (!iso) return null
  return new Date(iso).toLocaleString(lang === 'en' ? 'en-US' : 'es-CR', {
    month: 'short', day: 'numeric', year: 'numeric', hour: '2-digit', minute: '2-digit',
  })
}

// ── Status badge ──────────────────────────────────────────────────────────────
function StatusBadge({ status }: { status: string }) {
  const { t } = useLanguage()
  const isError = status === 'error'
  const color = isError ? C.red : C.green
  const Icon = isError ? XCircle : CheckCircle2
  const label = isError ? t('integrations.status_error') : t('integrations.status_connected')
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 5,
      fontSize: 11, fontWeight: 700, padding: '3px 9px', borderRadius: 20,
      background: color + '18', color,
    }}>
      <Icon size={12} aria-hidden="true" /> {label}
    </span>
  )
}

// ── Connect form for one provider ────────────────────────────────────────────
function ConnectForm({
  provider, info, onConnect, connecting, error,
}: {
  provider: string
  info: ProviderInfo
  onConnect: (provider: string, creds: Record<string, string>) => void
  connecting: boolean
  error: string | null
}) {
  const { t } = useLanguage()
  const [values, setValues] = useState<Record<string, string>>(
    () => Object.fromEntries(info.fields.map(f => [f, '']))
  )
  const canSubmit = info.fields.every(f => values[f]?.trim().length > 0)

  return (
    <div style={{
      background: C.card, border: `1px solid ${C.border}`, borderRadius: 10,
      padding: '16px 18px', display: 'flex', flexDirection: 'column', gap: 12,
    }}>
      <div style={{ display: 'grid', gridTemplateColumns: `repeat(${Math.min(info.fields.length, 3)}, 1fr)`, gap: 12 }}>
        {info.fields.map(field => (
          <div key={field}>
            <label style={{ fontSize: 11, fontWeight: 600, color: C.dim, display: 'block', marginBottom: 4 }}>
              {fieldLabel(t, field)}
            </label>
            <input
              style={inputS}
              type={/token|key|password|secret/i.test(field) ? 'password' : 'text'}
              autoComplete="off"
              value={values[field] ?? ''}
              onChange={e => setValues(v => ({ ...v, [field]: e.target.value }))}
              aria-label={fieldLabel(t, field)}
            />
          </div>
        ))}
      </div>
      {error && <InlineError error={new Error(error)} />}
      <div>
        <button
          onClick={() => canSubmit && onConnect(provider, values)}
          disabled={!canSubmit || connecting}
          style={{
            all: 'unset', cursor: canSubmit && !connecting ? 'pointer' : 'default',
            display: 'inline-flex', alignItems: 'center', gap: 6,
            padding: '8px 16px', borderRadius: 8, fontSize: 13, fontWeight: 600,
            background: C.indigo, color: '#fff', opacity: canSubmit && !connecting ? 1 : 0.5,
          }}
        >
          <Plug size={13} aria-hidden="true" />
          {connecting ? t('integrations.connecting') : t('integrations.connect_cta')}
        </button>
      </div>
    </div>
  )
}

// ── One provider card: shows connection state or the connect form ───────────
function ProviderCard({
  provider, info, connection, onConnect, onSync, onDelete, connecting, connectError, syncingId,
}: {
  provider: string
  info: ProviderInfo
  connection: Integration | undefined
  onConnect: (provider: string, creds: Record<string, string>) => void
  onSync: (id: string) => void
  onDelete: (id: string) => void
  connecting: boolean
  connectError: string | null
  syncingId: string | null
}) {
  const { t, lang } = useLanguage()
  const lastSync = connection ? fmtDate(connection.last_sync_at, lang) : null

  return (
    <div style={{
      background: C.surface, border: `1px solid ${C.border}`, borderRadius: 12,
      padding: '18px 20px', display: 'flex', flexDirection: 'column', gap: 14,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <div style={{
            width: 34, height: 34, borderRadius: 9,
            background: 'linear-gradient(135deg, #818cf8, #6366f1)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}>
            <Plug size={16} color="#fff" strokeWidth={2.5} aria-hidden="true" />
          </div>
          <div>
            <div style={{ fontSize: 14, fontWeight: 700, color: C.text }}>{providerLabel(provider)}</div>
            {connection
              ? <div style={{ fontSize: 11, color: C.dim }}>{t('integrations.connected_since')} {fmtDate(connection.created_at, lang)}</div>
              : <div style={{ fontSize: 11, color: C.dim }}>{t('integrations.not_connected')}</div>}
          </div>
        </div>

        {connection && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <StatusBadge status={connection.status} />
            <button
              onClick={() => onSync(connection.id)}
              disabled={syncingId === connection.id}
              title={t('integrations.sync_now')}
              style={{
                all: 'unset', cursor: syncingId === connection.id ? 'default' : 'pointer',
                display: 'inline-flex', alignItems: 'center', gap: 6,
                padding: '7px 12px', borderRadius: 8, fontSize: 12, fontWeight: 600,
                border: `1px solid ${C.border}`, color: C.text,
                opacity: syncingId === connection.id ? 0.6 : 1,
              }}
            >
              <RefreshCw
                size={12}
                aria-hidden="true"
                style={syncingId === connection.id ? { animation: 'integrations-spin 0.8s linear infinite' } : undefined}
              />
              {syncingId === connection.id ? t('integrations.syncing') : t('integrations.sync_now')}
            </button>
            <button
              onClick={() => onDelete(connection.id)}
              title={t('integrations.disconnect')}
              aria-label={`${t('integrations.disconnect')}: ${providerLabel(provider)}`}
              style={{ all: 'unset', cursor: 'pointer', padding: 6, borderRadius: 6, color: C.dim, display: 'flex' }}
              onMouseEnter={e => (e.currentTarget.style.color = C.red)}
              onMouseLeave={e => (e.currentTarget.style.color = C.dim)}
            >
              <Trash2 size={14} aria-hidden="true" />
            </button>
          </div>
        )}
      </div>

      {connection && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 18, fontSize: 12, color: C.muted }}>
          <span style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
            <Clock size={12} color={C.dim} aria-hidden="true" />
            {t('integrations.last_sync')}: {lastSync ?? t('integrations.never_synced')}
          </span>
        </div>
      )}

      {connection?.last_error && (
        <InlineError error={new Error(connection.last_error)} />
      )}

      {!connection && (
        <ConnectForm
          provider={provider}
          info={info}
          onConnect={onConnect}
          connecting={connecting}
          error={connectError}
        />
      )}
    </div>
  )
}

// ── Locked / upsell state for tenants without the entitlement ───────────────
function LockedState() {
  const { t } = useLanguage()
  return (
    <div style={{
      background: C.surface, border: `1px solid ${C.border}`, borderRadius: 12,
      padding: '48px 40px', maxWidth: 560, margin: '0 auto', width: '100%', textAlign: 'center',
    }}>
      <div style={{
        width: 48, height: 48, borderRadius: 13, margin: '0 auto 16px',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        background: 'rgba(129,140,248,0.12)', border: '1px solid rgba(129,140,248,0.28)',
        color: C.indigo,
      }}>
        <Lock size={22} aria-hidden="true" />
      </div>
      <h2 style={{ fontSize: 17, fontWeight: 700, color: C.text, margin: '0 0 8px' }}>
        {t('integrations.locked_title')}
      </h2>
      <p style={{ fontSize: 13, color: C.muted, margin: '0 0 20px', lineHeight: 1.65 }}>
        {t('integrations.locked_body')}
      </p>
      <Link
        href="/planes"
        style={{
          display: 'inline-flex', alignItems: 'center', gap: 7,
          padding: '10px 20px', borderRadius: 9,
          fontSize: 13, fontWeight: 700, textDecoration: 'none',
          background: C.indigo, color: '#fff',
        }}
      >
        {t('entitlements.upsell_cta')}
      </Link>
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────
export default function IntegrationsPage() {
  const { t } = useLanguage()
  const { has, loading: entLoading } = useEntitlements()
  const confirm = useConfirm()

  const [providers, setProviders] = useState<Record<string, ProviderInfo>>({})
  const [connections, setConnections] = useState<Integration[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<unknown>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [connectingProvider, setConnectingProvider] = useState<string | null>(null)
  const [connectError, setConnectError] = useState<Record<string, string | null>>({})
  const [syncingId, setSyncingId] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true); setLoadError(null)
    try {
      const res = await listIntegrations({ silent: true })
      setProviders(res.providers)
      setConnections(res.connections)
    } catch (e: unknown) {
      setLoadError(e)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { if (has('integrations')) load() }, [load, has])

  async function handleConnect(provider: string, creds: Record<string, string>) {
    setConnectingProvider(provider)
    setConnectError(prev => ({ ...prev, [provider]: null }))
    try {
      await connectIntegration(provider, creds)
      await load()
    } catch (e: unknown) {
      setConnectError(prev => ({
        ...prev, [provider]: e instanceof Error ? e.message : t('integrations.err_connecting'),
      }))
    } finally {
      setConnectingProvider(null)
    }
  }

  async function handleSync(id: string) {
    setSyncingId(id); setActionError(null)
    try {
      await syncIntegration(id)
      await load()
    } catch (e: unknown) {
      setActionError(e instanceof Error ? e.message : t('integrations.err_syncing'))
    } finally {
      setSyncingId(null)
    }
  }

  async function handleDelete(id: string) {
    const conn = connections.find(c => c.id === id)
    if (!(await confirm({
      title: `${t('integrations.disconnect_confirm_q')} ${providerLabel(conn?.provider ?? '')}?`,
      message: t('integrations.disconnect_confirm_warn'),
      danger: true,
    }))) return
    setActionError(null)
    try {
      await deleteIntegration(id)
      await load()
    } catch (e: unknown) {
      setActionError(e instanceof Error ? e.message : t('integrations.err_disconnecting'))
    }
  }

  if (entLoading) {
    return (
      <div style={{ padding: 8 }}>
        <LoadingState label={t('integrations.loading_label')} />
      </div>
    )
  }

  if (!has('integrations')) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 20, animation: 'fadeIn 0.3s ease-out' }}>
        <LockedState />
      </div>
    )
  }

  const connectionByProvider = Object.fromEntries(connections.map(c => [c.provider, c]))

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20, animation: 'fadeIn 0.3s ease-out' }}>
      <style>{`@keyframes integrations-spin { to { transform: rotate(360deg) } }`}</style>

      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <div style={{
          width: 36, height: 36, borderRadius: 9,
          background: 'linear-gradient(135deg, #818cf8, #6366f1)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}>
          <Plug size={17} color="#fff" strokeWidth={2.5} aria-hidden="true" />
        </div>
        <div>
          <h1 style={{ margin: 0, fontSize: 16, fontWeight: 700, color: C.text, letterSpacing: '-0.02em' }}>
            {t('integrations.page_title')}
          </h1>
          <p style={{ margin: 0, fontSize: 11, color: C.dim }}>
            {t('integrations.page_subtitle')}
          </p>
        </div>
      </div>

      {actionError && (
        <InlineError error={new Error(actionError)} onDismiss={() => setActionError(null)} />
      )}

      {loading ? (
        <LoadingState label={t('integrations.loading_label')} />
      ) : loadError ? (
        <ErrorState error={loadError} onRetry={load} />
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          {Object.entries(providers).map(([provider, info]) => (
            <ProviderCard
              key={provider}
              provider={provider}
              info={info}
              connection={connectionByProvider[provider]}
              onConnect={handleConnect}
              onSync={handleSync}
              onDelete={handleDelete}
              connecting={connectingProvider === provider}
              connectError={connectError[provider] ?? null}
              syncingId={syncingId}
            />
          ))}
          {Object.keys(providers).length === 0 && (
            <div style={{ fontSize: 13, color: C.dim, textAlign: 'center', padding: '32px 0' }}>
              {t('integrations.no_providers')}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
