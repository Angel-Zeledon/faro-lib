/** Human-readable order reference (OC-000123); em dash when unnumbered. */
export function formatPoNumber(n?: number | null): string {
  return n != null ? `OC-${String(n).padStart(6, '0')}` : '—'
}
