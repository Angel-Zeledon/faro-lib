'use client'
/**
 * The "Import from file" button that opens the bulk-import dialog. Shown to
 * roles that can write only — the backend refuses a viewer anyway, and a button
 * that can only fail is a worse answer than no button.
 */
import { useState } from 'react'
import { Upload } from 'lucide-react'

import { useLanguage } from '@/contexts/LanguageContext'
import { useIsNarrow } from '@/hooks/useIsNarrow'
import { getUser } from '@/lib/auth'
import type { BulkImportKind } from '@/lib/bulkImportTypes'
import BulkImportDialog from './BulkImportDialog'

export default function BulkImportButton({ kind, onImported, tourId }: {
  kind: BulkImportKind
  onImported?: () => void
  tourId?: string
}) {
  const { t } = useLanguage()
  const narrow = useIsNarrow()
  const [open, setOpen] = useState(false)
  if (getUser()?.role === 'viewer') return null

  return (
    <>
      <button
        type="button" data-tour={tourId} data-testid={`bulk-import-${kind}`}
        onClick={() => setOpen(true)}
        style={{
          all: 'unset', cursor: 'pointer', display: 'inline-flex', alignItems: 'center',
          gap: 6, padding: '7px 12px', borderRadius: 8, fontSize: 12, fontWeight: 600,
          border: '1px solid var(--border)', color: 'var(--text)', boxSizing: 'border-box',
          ...(narrow ? { minHeight: 44, fontSize: 14, borderRadius: 10, padding: '8px 14px' } : {}),
        }}
      >
        <Upload size={13} aria-hidden="true" />
        {t(`bulk.${kind}.open_button`)}
      </button>
      {open && (
        <BulkImportDialog
          kind={kind}
          onClose={() => setOpen(false)}
          onImported={onImported}
        />
      )}
    </>
  )
}
