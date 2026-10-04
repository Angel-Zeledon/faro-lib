/**
 * What this browser has been told, and agreed to, about storage.
 *
 * Today StockAI stores only what is strictly necessary or remembers a choice
 * the person made (the inventory is on /cookies, i18n/legal.ts). Neither needs
 * consent — only information — so the whole interface is a one-time notice,
 * and `OPTIONAL_CATEGORIES_IN_USE` is empty.
 *
 * The categories exist so a real consent manager can slot in without a
 * rewrite, should analytics or anything else optional ever be added:
 *
 *   1. add its category to `OPTIONAL_CATEGORIES_IN_USE`;
 *   2. gate the script on `hasConsent('analytics')` — never load first and
 *      ask later;
 *   3. `StorageNotice` then has to grow an accept / reject choice (it shows
 *      only "understood" while the list is empty), and bumping
 *      `NOTICE_VERSION` re-asks everyone who saw the old notice;
 *   4. list the new storage on /cookies.
 *
 * Do NOT add an "accept all" button while nothing optional exists: asking
 * for consent to trackers that are not there is theatre, and it teaches people
 * to click through the one notice that will someday matter.
 */

export type ConsentCategory = 'necessary' | 'functional' | 'analytics' | 'marketing'

// Optional categories (needing an explicit yes) that the product actually uses.
export const OPTIONAL_CATEGORIES_IN_USE: ConsentCategory[] = []

// Bump to show the notice again to everyone, e.g. when something optional is
// added and they have to be asked.
export const NOTICE_VERSION = 1

export const NOTICE_KEY = 'stockai_storage_notice'

interface ConsentRecord {
  version: number
  seenAt: string
  granted: ConsentCategory[]
}

function read(): ConsentRecord | null {
  try {
    const raw = window.localStorage.getItem(NOTICE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as ConsentRecord
    return typeof parsed?.version === 'number' ? parsed : null
  } catch {
    // Storage blocked, private mode, or a value written by hand.
    return null
  }
}

/** Necessary and functional storage never wait on an answer; the rest do. */
export function hasConsent(category: ConsentCategory): boolean {
  if (category === 'necessary' || category === 'functional') return true
  return read()?.granted.includes(category) ?? false
}

/** Whether this browser still has to be shown the current notice. */
export function noticePending(): boolean {
  const rec = read()
  return !rec || rec.version < NOTICE_VERSION
}

/** Record that the notice was seen, with whatever optional categories were granted. */
export function recordNoticeSeen(granted: ConsentCategory[] = []): void {
  const rec: ConsentRecord = { version: NOTICE_VERSION, seenAt: new Date().toISOString(), granted }
  try {
    window.localStorage.setItem(NOTICE_KEY, JSON.stringify(rec))
  } catch {
    // Nowhere to remember it: the notice comes back next visit, which is the
    // honest outcome for a browser that keeps nothing.
  }
}
