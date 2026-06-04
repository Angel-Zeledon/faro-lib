'use client'
import { useState, useEffect, useCallback, useRef } from 'react'
import {
  listDocuments, uploadDocument, deleteDocument, getDocumentStatus,
  getDocumentContentUrl, type DocumentMeta,
} from '@/lib/api'
import {
  FileText, Upload, Trash2, RefreshCw, CheckCircle2,
  XCircle, Clock, Loader2, FileType, AlertTriangle,
  X, FilePlus, Eye, Download,
} from 'lucide-react'

// ── Palette ───────────────────────────────────────────────────────────────────
const C = {
  bg:      'var(--bg)',
  surface: 'var(--surface)',
  card:    'var(--surface-2)',
  border:  'var(--border)',
  border2: 'var(--border-strong)',
  green:   '#10b981',
  blue:    '#3b82f6',
  amber:   '#f59e0b',
  red:     '#ef4444',
  indigo:  '#6366f1',
  text:    'var(--text)',
  muted:   'var(--muted)',
  dim:     'var(--dim)',
}

// ── Helpers ───────────────────────────────────────────────────────────────────
function fmtSize(bytes: number): string {
  if (bytes < 1024)       return `${bytes} B`
  if (bytes < 1024 ** 2)  return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

function fmtDate(iso: string | null): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })
}

function extColor(ext: string): string {
  const m: Record<string, string> = { pdf: C.red, docx: C.blue, doc: C.blue, txt: C.muted }
  return m[ext.toLowerCase()] ?? C.dim
}

// ── Status badge ──────────────────────────────────────────────────────────────
function StatusBadge({ status, error }: { status: string; error?: string | null }) {
  const cfg = {
    PENDING:  { icon: Clock,         color: C.amber,  label: 'Pending'  },
    INDEXING: { icon: Loader2,       color: C.blue,   label: 'Indexing' },
    INDEXED:  { icon: CheckCircle2,  color: C.green,  label: 'Indexed'  },
    FAILED:   { icon: XCircle,       color: C.red,    label: 'Failed'   },
  }[status] ?? { icon: AlertTriangle, color: C.muted, label: status }
  const Icon = cfg.icon
  return (
    <span
      title={error ?? undefined}
      style={{
        display: 'inline-flex', alignItems: 'center', gap: 4,
        padding: '2px 8px', borderRadius: 20, background: cfg.color + '18',
        color: cfg.color, fontSize: 11, fontWeight: 600,
      }}
    >
      <Icon size={10} className={status === 'INDEXING' ? 'spin' : ''} />
      {cfg.label}
    </span>
  )
}

// ── Confirm delete modal ──────────────────────────────────────────────────────
function DeleteModal({ doc, onConfirm, onCancel }: {
  doc: DocumentMeta
  onConfirm: () => void
  onCancel: () => void
}) {
  return (
    <div style={{
      position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.5)',
      display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 9999,
    }}>
      <div style={{
        background: C.surface, border: `1px solid ${C.border2}`,
        borderRadius: 12, padding: 28, maxWidth: 420, width: '90%',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
          <AlertTriangle size={20} color={C.red} />
          <span style={{ fontWeight: 700, fontSize: 16, color: C.text }}>Delete document?</span>
        </div>
        <p style={{ color: C.muted, fontSize: 13, marginBottom: 20, lineHeight: 1.5 }}>
          <strong style={{ color: C.text }}>{doc.name}</strong> will be permanently removed,
          including all indexed chunks in the vector store.
          This cannot be undone.
        </p>
        <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
          <button
            onClick={onCancel}
            style={{
              padding: '8px 16px', borderRadius: 7, border: `1px solid ${C.border2}`,
              background: 'transparent', color: C.muted, cursor: 'pointer', fontSize: 13,
            }}
          >
            Cancel
          </button>
          <button
            data-testid="confirm-delete"
            onClick={onConfirm}
            style={{
              padding: '8px 16px', borderRadius: 7, border: 'none',
              background: C.red, color: '#fff', cursor: 'pointer', fontSize: 13, fontWeight: 600,
            }}
          >
            Delete
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Drop zone ─────────────────────────────────────────────────────────────────
function DropZone({ onFiles }: { onFiles: (files: File[]) => void }) {
  const [dragging, setDragging] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setDragging(false)
    const files = Array.from(e.dataTransfer.files).filter(
      f => /\.(pdf|docx|doc|txt)$/i.test(f.name)
    )
    if (files.length) onFiles(files)
  }, [onFiles])

  return (
    <div
      onDragOver={e => { e.preventDefault(); setDragging(true) }}
      onDragLeave={() => setDragging(false)}
      onDrop={handleDrop}
      onClick={() => inputRef.current?.click()}
      style={{
        border: `2px dashed ${dragging ? C.indigo : C.border2}`,
        borderRadius: 12, padding: '40px 24px',
        display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 10,
        cursor: 'pointer', background: dragging ? C.indigo + '08' : 'transparent',
        transition: 'all 0.15s', userSelect: 'none',
      }}
    >
      <div style={{
        width: 48, height: 48, borderRadius: 12,
        background: C.indigo + '18', display: 'flex', alignItems: 'center', justifyContent: 'center',
      }}>
        <FilePlus size={22} color={C.indigo} />
      </div>
      <div style={{ textAlign: 'center' }}>
        <div style={{ fontWeight: 600, color: C.text, fontSize: 14 }}>
          Drop files here or <span style={{ color: C.indigo }}>browse</span>
        </div>
        <div style={{ color: C.dim, fontSize: 12, marginTop: 4 }}>
          PDF, DOCX, TXT — max 50 MB each
        </div>
      </div>
      <input
        ref={inputRef}
        type="file"
        accept=".pdf,.docx,.doc,.txt"
        multiple
        style={{ display: 'none' }}
        onChange={e => {
          const files = Array.from(e.target.files ?? [])
          if (files.length) onFiles(files)
          e.target.value = ''
        }}
      />
    </div>
  )
}

// ── Upload progress item ──────────────────────────────────────────────────────
interface UploadItem { file: File; status: 'uploading' | 'done' | 'error'; error?: string }

function UploadQueue({ items, onClear }: { items: UploadItem[]; onClear: () => void }) {
  if (!items.length) return null
  return (
    <div style={{
      background: C.card, border: `1px solid ${C.border}`,
      borderRadius: 10, overflow: 'hidden', marginBottom: 20,
    }}>
      <div style={{
        padding: '10px 14px', borderBottom: `1px solid ${C.border}`,
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      }}>
        <span style={{ fontSize: 12, fontWeight: 600, color: C.muted }}>UPLOADING</span>
        <button onClick={onClear} style={{ all: 'unset', cursor: 'pointer', color: C.dim }}>
          <X size={14} />
        </button>
      </div>
      {items.map((item, i) => (
        <div key={i} style={{
          display: 'flex', alignItems: 'center', gap: 10,
          padding: '8px 14px', borderBottom: i < items.length - 1 ? `1px solid ${C.border}` : 'none',
        }}>
          {item.status === 'uploading' && <Loader2 size={14} color={C.blue} className="spin" />}
          {item.status === 'done'      && <CheckCircle2 size={14} color={C.green} />}
          {item.status === 'error'     && <XCircle size={14} color={C.red} />}
          <span style={{ flex: 1, fontSize: 13, color: C.text, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {item.file.name}
          </span>
          {item.error && <span style={{ fontSize: 11, color: C.red }}>{item.error}</span>}
        </div>
      ))}
    </div>
  )
}

// ── Document viewer modal ─────────────────────────────────────────────────────
function DocViewer({ doc, onClose }: { doc: DocumentMeta; onClose: () => void }) {
  const url = getDocumentContentUrl(doc.id)
  const ext = doc.file_type.toLowerCase()
  const canEmbed = ext === 'pdf' || ext === 'txt'

  return (
    <div style={{
      position: 'fixed', inset: 0, zIndex: 9999,
      background: 'rgba(0,0,0,0.72)',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      padding: 24,
    }}
      onClick={e => { if (e.target === e.currentTarget) onClose() }}
    >
      <div style={{
        background: C.surface, borderRadius: 14, width: '100%', maxWidth: 960,
        height: '90vh', display: 'flex', flexDirection: 'column',
        border: `1px solid ${C.border2}`, overflow: 'hidden',
      }}>
        {/* Header */}
        <div style={{
          display: 'flex', alignItems: 'center', gap: 10,
          padding: '12px 16px', borderBottom: `1px solid ${C.border}`,
          flexShrink: 0,
        }}>
          <FileType size={15} color={extColor(ext)} />
          <span style={{ flex: 1, fontSize: 13, fontWeight: 600, color: C.text,
            overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {doc.name}
          </span>
          <a
            href={url}
            download={doc.name}
            style={{
              display: 'flex', alignItems: 'center', gap: 5,
              padding: '5px 12px', borderRadius: 7, fontSize: 12,
              background: C.indigo + '18', border: `1px solid ${C.indigo}30`,
              color: C.indigo, textDecoration: 'none', fontWeight: 500,
            }}
          >
            <Download size={12} /> Download
          </a>
          <button
            onClick={onClose}
            style={{
              all: 'unset', cursor: 'pointer', padding: 6, borderRadius: 6,
              color: C.dim, display: 'flex', alignItems: 'center',
            }}
          >
            <X size={16} />
          </button>
        </div>

        {/* Content */}
        <div style={{ flex: 1, overflow: 'hidden', background: C.card }}>
          {ext === 'pdf' && (
            <iframe
              src={url}
              title={doc.name}
              style={{ width: '100%', height: '100%', border: 'none' }}
            />
          )}
          {ext === 'txt' && (
            <TxtViewer url={url} />
          )}
          {!canEmbed && (
            <div style={{
              display: 'flex', flexDirection: 'column', alignItems: 'center',
              justifyContent: 'center', height: '100%', gap: 14,
            }}>
              <FileType size={48} color={extColor(ext)} style={{ opacity: 0.5 }} />
              <div style={{ fontSize: 14, color: C.muted, textAlign: 'center' }}>
                <strong style={{ color: C.text }}>.{ext.toUpperCase()}</strong> files cannot be previewed in the browser.
              </div>
              <a
                href={url}
                download={doc.name}
                style={{
                  display: 'flex', alignItems: 'center', gap: 6,
                  padding: '10px 20px', borderRadius: 8, fontSize: 13, fontWeight: 600,
                  background: C.indigo, color: '#fff', textDecoration: 'none',
                }}
              >
                <Download size={14} /> Download file
              </a>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function TxtViewer({ url }: { url: string }) {
  const [text, setText] = useState<string | null>(null)
  const [err, setErr]   = useState(false)
  useEffect(() => {
    fetch(url, { credentials: 'include', headers: { Authorization: `Bearer ${localStorage.getItem('auth_token')}` } })
      .then(r => r.text())
      .then(setText)
      .catch(() => setErr(true))
  }, [url])
  if (err) return <div style={{ padding: 24, color: C.red }}>Failed to load file.</div>
  if (!text) return <div style={{ display: 'flex', justifyContent: 'center', padding: 48 }}><Loader2 size={20} color={C.indigo} className="spin" /></div>
  return (
    <pre style={{
      margin: 0, padding: 24, height: '100%', overflowY: 'auto',
      fontFamily: "'JetBrains Mono', monospace", fontSize: 13, lineHeight: 1.7,
      color: C.text, whiteSpace: 'pre-wrap', wordBreak: 'break-word',
    }}>
      {text}
    </pre>
  )
}

// ── Document row ──────────────────────────────────────────────────────────────
function DocRow({ doc, onDelete, onView }: { doc: DocumentMeta; onDelete: (d: DocumentMeta) => void; onView: (d: DocumentMeta) => void }) {
  return (
    <div style={{
      display: 'grid',
      gridTemplateColumns: '2fr 80px 90px 80px 100px 140px 68px',
      alignItems: 'center', gap: 12,
      padding: '12px 16px',
      borderBottom: `1px solid ${C.border}`,
      transition: 'background 0.1s',
      cursor: 'pointer',
    }}
      onClick={() => onView(doc)}
      onMouseEnter={e => (e.currentTarget.style.background = C.card)}
      onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
    >
      {/* Name + icon */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
        <FileType size={16} color={extColor(doc.file_type)} style={{ flexShrink: 0 }} />
        <span style={{
          fontSize: 13, fontWeight: 500, color: C.text,
          overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
        }} title={doc.name}>
          {doc.name}
        </span>
      </div>

      {/* Type */}
      <span style={{ fontSize: 11, color: C.dim, textTransform: 'uppercase', fontWeight: 600 }}>
        {doc.file_type}
      </span>

      {/* Size */}
      <span style={{ fontSize: 12, color: C.muted }}>{fmtSize(doc.file_size)}</span>

      {/* Pages */}
      <span style={{ fontSize: 12, color: C.muted }}>
        {doc.page_count != null ? `${doc.page_count} pg` : '—'}
      </span>

      {/* Status */}
      <StatusBadge status={doc.status} error={doc.error} />

      {/* Date */}
      <span style={{ fontSize: 12, color: C.dim }}>{fmtDate(doc.uploaded_at)}</span>

      {/* Actions */}
      <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
        <button
          onClick={e => { e.stopPropagation(); onView(doc) }}
          title="View document"
          style={{
            all: 'unset', cursor: 'pointer', color: C.dim,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            padding: 4, borderRadius: 5, transition: 'color 0.15s',
          }}
          onMouseEnter={e => (e.currentTarget.style.color = C.indigo)}
          onMouseLeave={e => (e.currentTarget.style.color = C.dim)}
        >
          <Eye size={14} />
        </button>
        <button
          onClick={e => { e.stopPropagation(); onDelete(doc) }}
          title="Delete document"
          style={{
            all: 'unset', cursor: 'pointer', color: C.dim,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            padding: 4, borderRadius: 5, transition: 'color 0.15s',
          }}
          onMouseEnter={e => (e.currentTarget.style.color = C.red)}
          onMouseLeave={e => (e.currentTarget.style.color = C.dim)}
        >
          <Trash2 size={14} />
        </button>
      </div>
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────
export default function DocumentsPage() {
  const [docs, setDocs]             = useState<DocumentMeta[]>([])
  const [loading, setLoading]       = useState(true)
  const [error, setError]           = useState<string | null>(null)
  const [uploads, setUploads]       = useState<UploadItem[]>([])
  const [toDelete, setToDelete]     = useState<DocumentMeta | null>(null)
  const [viewing, setViewing]       = useState<DocumentMeta | null>(null)

  const load = useCallback(async () => {
    try {
      setError(null)
      const data = await listDocuments()
      setDocs(data)
    } catch (e: unknown) {
      setError((e as Error).message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  // Poll documents that are still PENDING or INDEXING every 3 s
  useEffect(() => {
    const active = docs.filter(d => d.status === 'PENDING' || d.status === 'INDEXING')
    if (active.length === 0) return

    const id = setInterval(async () => {
      const updates = await Promise.allSettled(active.map(d => getDocumentStatus(d.id)))
      setDocs(prev => {
        let changed = false
        const next = prev.map(doc => {
          const i = active.findIndex(a => a.id === doc.id)
          if (i < 0) return doc
          const r = updates[i]
          if (r.status !== 'fulfilled') return doc
          const newStatus = r.value.status as DocumentMeta['status']
          if (doc.status === newStatus) return doc
          changed = true
          return { ...doc, status: newStatus, chunk_count: r.value.chunk_count, page_count: r.value.page_count, error: r.value.error }
        })
        return changed ? next : prev
      })
    }, 3000)

    return () => clearInterval(id)
  }, [docs])

  async function handleFiles(files: File[]) {
    // Use object identity of UploadItem objects to track state per file
    const newItems: UploadItem[] = files.map(file => ({ file, status: 'uploading' as const }))
    setUploads(prev => [...prev, ...newItems])

    await Promise.all(newItems.map(async (item) => {
      const fd = new FormData()
      fd.append('file', item.file)
      try {
        const doc = await uploadDocument(fd)
        setDocs(prev => [doc, ...prev])
        setUploads(prev => prev.map(u => u === item ? { ...u, status: 'done' as const } : u))
      } catch (e: unknown) {
        setUploads(prev => prev.map(u =>
          u === item ? { ...u, status: 'error' as const, error: (e as Error).message } : u
        ))
      }
    }))
  }

  async function confirmDelete() {
    if (!toDelete) return
    try {
      await deleteDocument(toDelete.id)
      setDocs(prev => prev.filter(d => d.id !== toDelete.id))
    } catch (e: unknown) {
      setError((e as Error).message)
    } finally {
      setToDelete(null)
    }
  }

  const indexed   = docs.filter(d => d.status === 'INDEXED').length
  const pending   = docs.filter(d => d.status === 'PENDING' || d.status === 'INDEXING').length
  const failed    = docs.filter(d => d.status === 'FAILED').length
  const totalSize = docs.reduce((s, d) => s + d.file_size, 0)

  return (
    <div style={{ padding: '28px 32px', background: C.bg, minHeight: '100vh' }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 24 }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 22, fontWeight: 700, color: C.text, letterSpacing: '-0.02em' }}>
            Documents
          </h1>
          <p style={{ margin: '4px 0 0', fontSize: 13, color: C.muted }}>
            Upload PDF, DOCX, and TXT files to make them searchable by the AI Analyst.
          </p>
        </div>
        <button
          onClick={load}
          title="Refresh"
          style={{
            all: 'unset', cursor: 'pointer', display: 'flex', alignItems: 'center',
            gap: 6, padding: '8px 14px', borderRadius: 8, border: `1px solid ${C.border}`,
            fontSize: 13, color: C.muted, background: C.surface,
          }}
        >
          <RefreshCw size={13} />
          Refresh
        </button>
      </div>

      {/* Stats */}
      {docs.length > 0 && (
        <div style={{ display: 'flex', gap: 12, marginBottom: 24, flexWrap: 'wrap' }}>
          {[
            { label: 'Total', value: docs.length, color: C.indigo },
            { label: 'Indexed', value: indexed, color: C.green },
            { label: 'Processing', value: pending, color: C.amber },
            { label: 'Failed', value: failed, color: failed > 0 ? C.red : C.dim },
            { label: 'Size', value: fmtSize(totalSize), color: C.muted },
          ].map(s => (
            <div key={s.label} style={{
              padding: '10px 16px', borderRadius: 8,
              border: `1px solid ${C.border}`, background: C.surface,
              minWidth: 90,
            }}>
              <div style={{ fontSize: 18, fontWeight: 700, color: s.color }}>{s.value}</div>
              <div style={{ fontSize: 11, color: C.dim, marginTop: 2 }}>{s.label}</div>
            </div>
          ))}
        </div>
      )}

      {/* Upload queue */}
      <UploadQueue items={uploads} onClear={() => setUploads([])} />

      {/* Drop zone */}
      <div style={{ marginBottom: 24 }}>
        <DropZone onFiles={handleFiles} />
      </div>

      {/* Error */}
      {error && (
        <div style={{
          display: 'flex', alignItems: 'center', gap: 8,
          padding: '10px 14px', borderRadius: 8,
          background: C.red + '12', border: `1px solid ${C.red}30`,
          color: C.red, fontSize: 13, marginBottom: 16,
        }}>
          <AlertTriangle size={14} />
          {error}
          <button onClick={() => setError(null)} style={{ all: 'unset', cursor: 'pointer', marginLeft: 'auto' }}>
            <X size={14} />
          </button>
        </div>
      )}

      {/* Table */}
      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: '60px 0' }}>
          <Loader2 size={24} color={C.indigo} className="spin" />
        </div>
      ) : docs.length === 0 ? (
        <div style={{
          display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 12,
          padding: '60px 0', color: C.dim, textAlign: 'center',
        }}>
          <FileText size={36} color={C.border2} />
          <div style={{ fontSize: 15, fontWeight: 600, color: C.muted }}>No documents yet</div>
          <div style={{ fontSize: 13 }}>Upload PDF, DOCX, or TXT files above to get started.</div>
        </div>
      ) : (
        <div style={{
          background: C.surface, border: `1px solid ${C.border}`,
          borderRadius: 10, overflow: 'hidden',
        }}>
          {/* Table header */}
          <div style={{
            display: 'grid',
            gridTemplateColumns: '2fr 80px 90px 80px 100px 140px 68px',
            gap: 12, padding: '8px 16px',
            borderBottom: `1px solid ${C.border}`,
            background: C.card,
          }}>
            {['Document', 'Type', 'Size', 'Pages', 'Status', 'Uploaded', ''].map(h => (
              <span key={h} style={{ fontSize: 11, fontWeight: 600, color: C.dim, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                {h}
              </span>
            ))}
          </div>
          {docs.map(doc => (
            <DocRow key={doc.id} doc={doc} onDelete={setToDelete} onView={setViewing} />
          ))}
        </div>
      )}

      {/* Delete confirmation */}
      {toDelete && (
        <DeleteModal
          doc={toDelete}
          onConfirm={confirmDelete}
          onCancel={() => setToDelete(null)}
        />
      )}

      {/* Document viewer */}
      {viewing && <DocViewer doc={viewing} onClose={() => setViewing(null)} />}

      <style>{`
        @keyframes spin { from { transform: rotate(0deg) } to { transform: rotate(360deg) } }
        .spin { animation: spin 1s linear infinite }
      `}</style>
    </div>
  )
}
