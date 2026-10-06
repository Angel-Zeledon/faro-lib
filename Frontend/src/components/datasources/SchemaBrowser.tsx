'use client'
/**
 * The tables a SQL connection can read, next to the query editor.
 *
 * Writing `SELECT ... FROM` against an ERP you did not design starts with
 * "what is it called?". This lists the tables and views the login can read
 * (row counts are the engine's own ESTIMATES — never a COUNT(*) on a
 * customer's big table), expands a table to its columns, and drops a ready
 * statement into the editor. That statement is built and quoted by the SERVER
 * for the engine (a table called `order` or `Ventas 2024` just works) and has
 * already passed the same read-only check every query goes through.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { ChevronDown, ChevronRight, Eye, RefreshCw, Table2, Search, CornerDownLeft } from 'lucide-react'
import Spinner from '@/components/ui/Spinner'
import { useLanguage } from '@/contexts/LanguageContext'
import { useErrorDetail } from '@/components/ui/States'
import { getSqlSchema, getSqlTableColumns } from '@/lib/api'
import type { SchemaColumns, SchemaTable, SchemaTables } from '@/lib/types'

const C = {
  surface: 'var(--surface)', card: 'var(--surface-2)', inset: 'var(--surface-3)',
  border: 'var(--border)', border2: 'var(--border-strong)',
  text: 'var(--text)', muted: 'var(--muted)', green: 'var(--accent)', red: 'var(--danger)',
}
const MONO = "ui-monospace, 'JetBrains Mono', 'SF Mono', 'Cascadia Mono', Consolas, monospace"

const keyOf = (tb: SchemaTable) => `${tb.schema ?? ''}\u0000${tb.name}`

export default function SchemaBrowser({ sourceId, onInsert, compact }: {
  sourceId: string
  onInsert: (sql: string) => void
  compact?: boolean
}) {
  const { t, lang } = useLanguage()
  const errorDetail = useErrorDetail()
  const [data, setData] = useState<SchemaTables | null>(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState<string | null>(null)
  const [filter, setFilter] = useState('')
  const [open, setOpen] = useState<string | null>(null)
  const [columns, setColumns] = useState<Record<string, SchemaColumns | 'loading' | { error: string }>>({})

  const load = useCallback(async (refresh = false) => {
    setLoading(true); setErr(null)
    try {
      setData(await getSqlSchema(sourceId, refresh))
      if (refresh) setColumns({})
    } catch (e) { setErr(errorDetail(e)) }
    finally { setLoading(false) }
  }, [sourceId]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { setData(null); setColumns({}); setOpen(null); load(false) }, [load])

  const toggle = async (tb: SchemaTable) => {
    const k = keyOf(tb)
    if (open === k) { setOpen(null); return }
    setOpen(k)
    if (columns[k] && columns[k] !== 'loading' && !('error' in (columns[k] as object))) return
    setColumns(c => ({ ...c, [k]: 'loading' }))
    try {
      const cols = await getSqlTableColumns(sourceId, tb.schema, tb.name)
      setColumns(c => ({ ...c, [k]: cols }))
    } catch (e) {
      setColumns(c => ({ ...c, [k]: { error: errorDetail(e) } }))
    }
  }

  const shown = useMemo(() => {
    const q = filter.trim().toLowerCase()
    const all = data?.tables ?? []
    return q ? all.filter(tb => `${tb.schema ?? ''}.${tb.name}`.toLowerCase().includes(q)) : all
  }, [data, filter])

  const fmtN = (n: number) => n.toLocaleString(lang === 'en' ? 'en-US' : 'es-CR')
  const readAt = data?.cached_at
    ? new Date(data.cached_at).toLocaleTimeString(lang === 'en' ? 'en-US' : 'es-CR', { hour: '2-digit', minute: '2-digit' })
    : ''

  return (
    <div style={{ display: 'flex', flexDirection: 'column', minHeight: 0, height: compact ? 'auto' : '100%',
      border: `1px solid ${C.border}`, borderRadius: 10, background: C.surface, overflow: 'hidden' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '8px 10px', borderBottom: `1px solid ${C.border}` }}>
        <Table2 size={13} aria-hidden="true" style={{ color: C.muted }} />
        <span style={{ color: C.muted, fontSize: 10, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
          {t('data.schema.title')}{data ? ` · ${fmtN(data.tables.length)}` : ''}
        </span>
        <button type="button" onClick={() => load(true)} disabled={loading} aria-label={t('data.schema.refresh')}
          title={readAt ? t('data.schema.read_at', { when: readAt }) : t('data.schema.refresh')}
          style={{ marginLeft: 'auto', background: 'transparent', border: 'none', color: C.muted,
            cursor: loading ? 'default' : 'pointer', display: 'flex', padding: 6, minWidth: 32, minHeight: 32,
            alignItems: 'center', justifyContent: 'center' }}>
          {loading ? <Spinner size={12} /> : <RefreshCw size={13} />}
        </button>
      </div>
      <div style={{ padding: '8px 10px', borderBottom: `1px solid ${C.border}`, position: 'relative' }}>
        <Search size={12} aria-hidden="true" style={{ position: 'absolute', left: 18, top: '50%', transform: 'translateY(-50%)', color: C.muted }} />
        <input value={filter} onChange={e => setFilter(e.target.value)} placeholder={t('data.schema.filter_ph')}
          aria-label={t('data.schema.filter_ph')} name="schema_filter"
          style={{ boxSizing: 'border-box', width: '100%', padding: '6px 8px 6px 26px', borderRadius: 7,
            border: `1px solid ${C.border2}`, background: C.inset, color: C.text, outline: 'none',
            fontSize: compact ? 16 : 12 }} />
      </div>

      <div style={{ overflowY: 'auto', flex: 1, minHeight: 0, maxHeight: compact ? 320 : undefined }}>
        {loading && !data ? (
          <div role="status" style={{ display: 'flex', alignItems: 'center', gap: 8, padding: 14, color: C.muted, fontSize: 12 }}>
            <Spinner size={12} /> {t('data.schema.loading')}
          </div>
        ) : err ? (
          <div role="alert" style={{ margin: 10, padding: '8px 10px', borderRadius: 8, borderLeft: `3px solid ${C.red}`,
            background: C.surface, color: C.red, fontSize: 12, lineHeight: 1.5, border: `1px solid ${C.border}` }}>
            {err}
          </div>
        ) : data && data.tables.length === 0 ? (
          <p style={{ padding: 14, color: C.muted, fontSize: 12, margin: 0 }}>{t('data.schema.empty')}</p>
        ) : shown.length === 0 ? (
          <p style={{ padding: 14, color: C.muted, fontSize: 12, margin: 0 }}>{t('data.schema.no_match')}</p>
        ) : (
          <ul style={{ listStyle: 'none', margin: 0, padding: '4px 0' }}>
            {shown.map(tb => {
              const k = keyOf(tb)
              const isOpen = open === k
              const cols = columns[k]
              return (
                <li key={k}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 4, padding: '0 6px 0 4px' }}>
                    <button type="button" onClick={() => toggle(tb)} aria-expanded={isOpen}
                      style={{ display: 'flex', alignItems: 'center', gap: 5, flex: 1, minWidth: 0, background: 'transparent',
                        border: 'none', color: C.text, cursor: 'pointer', padding: '5px 4px', textAlign: 'left',
                        minHeight: compact ? 44 : 28 }}>
                      {isOpen ? <ChevronDown size={12} aria-hidden="true" style={{ color: C.muted, flexShrink: 0 }} />
                        : <ChevronRight size={12} aria-hidden="true" style={{ color: C.muted, flexShrink: 0 }} />}
                      {tb.kind === 'view'
                        ? <Eye size={12} aria-label={t('data.schema.view')} style={{ color: C.muted, flexShrink: 0 }} />
                        : <Table2 size={12} aria-hidden="true" style={{ color: C.muted, flexShrink: 0 }} />}
                      <span style={{ fontFamily: MONO, fontSize: 11.5, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {tb.schema ? <span style={{ color: C.muted }}>{tb.schema}.</span> : null}{tb.name}
                      </span>
                      {tb.row_estimate != null && (
                        <span style={{ marginLeft: 'auto', color: C.muted, fontSize: 10.5, fontVariantNumeric: 'tabular-nums', flexShrink: 0 }}>
                          {t('data.schema.rows_estimate', { n: fmtN(tb.row_estimate) })}
                        </span>
                      )}
                    </button>
                    <button type="button" disabled={!tb.select_sql}
                      onClick={() => tb.select_sql && onInsert(tb.select_sql)}
                      aria-label={`${t('data.schema.insert_select')}: ${tb.name}`}
                      title={tb.select_sql ? t('data.schema.insert_select') : t('data.schema.no_preview')}
                      style={{ background: 'transparent', border: 'none', color: tb.select_sql ? C.green : C.muted,
                        cursor: tb.select_sql ? 'pointer' : 'not-allowed', display: 'flex', alignItems: 'center',
                        justifyContent: 'center', minWidth: compact ? 44 : 28, minHeight: compact ? 44 : 28, opacity: tb.select_sql ? 1 : 0.5 }}>
                      <CornerDownLeft size={13} aria-hidden="true" />
                    </button>
                  </div>
                  {isOpen && (
                    <div style={{ padding: '2px 10px 8px 30px' }}>
                      {cols === 'loading' || cols === undefined ? (
                        <span style={{ display: 'flex', alignItems: 'center', gap: 6, color: C.muted, fontSize: 11.5 }}>
                          <Spinner size={10} /> {t('data.schema.columns_loading')}
                        </span>
                      ) : 'error' in cols ? (
                        <span role="alert" style={{ color: C.red, fontSize: 11.5 }}>{cols.error}</span>
                      ) : (
                        <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
                          {cols.columns.map(col => (
                            <li key={col.name} style={{ display: 'flex', gap: 8, fontSize: 11, lineHeight: 1.7, minWidth: 0 }}>
                              <span style={{ fontFamily: MONO, color: C.text, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{col.name}</span>
                              <span style={{ fontFamily: MONO, color: C.muted, marginLeft: 'auto', flexShrink: 0 }}>
                                {col.type}{col.nullable ? '' : ' · NOT NULL'}
                              </span>
                            </li>
                          ))}
                        </ul>
                      )}
                    </div>
                  )}
                </li>
              )
            })}
          </ul>
        )}
        {data?.truncated && (
          <p style={{ padding: '6px 12px 10px', color: C.muted, fontSize: 11, margin: 0 }}>
            {t('data.schema.truncated', { cap: data.cap })}
          </p>
        )}
      </div>
    </div>
  )
}
