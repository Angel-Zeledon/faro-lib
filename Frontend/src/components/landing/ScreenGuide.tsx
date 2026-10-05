'use client'
// The screen-by-screen guide, as an opt-in deep dive.
//
// It used to sit in the main flow of the landing: eighteen full-width
// screenshots between "how it works" and "how it decides", which turned the
// pitch into a manual. The owner's call (2026-10-01): the guide is for whoever
// wants to go deeper, not the main path. So the page shows a compact teaser —
// one thumbnail per chapter, a button, and the manual PDF — and the chapters
// open in a modal dialog.
//
// Three properties worth keeping:
//  · The chapter TEXT is server-rendered inside the closed <dialog>, so it is
//    still part of the document for search engines and for find-in-page once
//    open. Only the screenshots wait: they are not mounted until the first
//    open, and then load lazily as the reader scrolls the dialog.
//  · It is a native modal <dialog>: the browser makes the rest of the page
//    inert (the focus trap), Escape closes it, and focus goes back to the
//    button that opened it.
//  · The chapters and screens are rendered exactly as they were on the page —
//    same copy, same alt text, same alternating rows.
//
// /como-funciona renders the same chapters inline (TourChapters below), with
// every screenshot loading lazily as it nears view.
import { useCallback, useEffect, useRef, useState } from 'react'
import { X } from 'lucide-react'
import type { LandingCopy } from '@/i18n/landing'

export const GUIDE_CSS = `
.sg-teaser {
 margin-top: 72px; position: relative; border-radius: 18px; padding: 1px;
 background: linear-gradient(150deg, var(--lp-border-strong), var(--lp-border) 45%, var(--lp-accent-bd));
}
.sg-teaser-in { border-radius: 17px; background: var(--lp-bg2); padding: 32px 34px; display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1.15fr); gap: 40px; align-items: center; }
.sg-title { font-family: var(--font-brand), system-ui, sans-serif; font-size: clamp(22px, 2.4vw, 27px); font-weight: 600; letter-spacing: -0.025em; line-height: 1.2; color: var(--lp-text); margin: 0 0 10px; text-wrap: balance; }
.sg-lead { font-size: 14.5px; color: var(--lp-body); line-height: 1.7; margin: 0 0 14px; max-width: 52ch; }
.sg-count { font-size: 13px; color: var(--lp-muted); margin: 0 0 22px; }
.sg-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 10px 18px; }
.sg-pdf { font-size: 13.5px; font-weight: 600; color: var(--lp-accent); text-decoration: none; display: inline-flex; align-items: center; min-height: 44px; }
.sg-pdf:hover { text-decoration: underline; text-underline-offset: 3px; }
.sg-pdf-note { display: block; font-size: 12px; color: var(--lp-muted); font-weight: 500; }

/* One thumbnail per chapter. Each one opens the guide at that chapter. */
.sg-thumbs { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
.sg-thumb {
 all: unset; box-sizing: border-box; cursor: pointer; display: flex; flex-direction: column; gap: 8px; min-width: 0;
}
.sg-thumb-img {
 position: relative; border-radius: 10px; overflow: hidden; aspect-ratio: 16 / 10;
 border: 1px solid var(--lp-border); background: var(--lp-surface);
 transition: border-color 200ms ease, transform 260ms var(--lp-ease);
}
.sg-thumb-img img { display: block; width: 100%; height: 100%; object-fit: cover; object-position: 0 0; }
.sg-thumb-cap { font-size: 12.5px; font-weight: 600; color: var(--lp-body); line-height: 1.35; }
.sg-thumb:hover .sg-thumb-img { border-color: var(--lp-accent); transform: translateY(-2px); }
.sg-thumb:hover .sg-thumb-cap { color: var(--lp-accent); }
.sg-thumb:focus-visible { outline: 2px solid var(--lp-accent); outline-offset: 4px; border-radius: 10px; }

/* The dialog: a tall sheet over a dimmed page. */
.sg-dialog {
 width: min(1120px, calc(100vw - 32px)); max-width: none; height: calc(100dvh - 48px); max-height: none;
 margin: 24px auto; padding: 0; border: 1px solid var(--lp-border); border-radius: 18px;
 background: var(--lp-bg); color: var(--lp-text); overflow: hidden;
 box-shadow: 0 40px 100px -30px rgba(0,0,0,0.5);
}
.sg-dialog::backdrop { background: rgba(10,21,23,0.55); backdrop-filter: blur(3px); -webkit-backdrop-filter: blur(3px); }
.sg-dialog[open] { display: flex; flex-direction: column; animation: sg-in 360ms var(--lp-ease) both; }
@keyframes sg-in { from { opacity: 0; transform: translate3d(0, 18px, 0) scale(0.985); } to { opacity: 1; transform: none; } }
.sg-head { flex-shrink: 0; display: flex; align-items: center; gap: 16px; padding: 14px 18px 14px 28px; border-bottom: 1px solid var(--lp-border); background: var(--lp-bg); }
.sg-head h2 { font-family: var(--font-brand), system-ui, sans-serif; font-size: 19px; font-weight: 600; letter-spacing: -0.02em; margin: 0; white-space: nowrap; }
.sg-nav { display: flex; gap: 4px; overflow-x: auto; flex: 1; min-width: 0; scrollbar-width: none; }
.sg-nav::-webkit-scrollbar { display: none; }
.sg-nav button {
 all: unset; cursor: pointer; white-space: nowrap; padding: 7px 12px; border-radius: 8px;
 font-size: 13px; font-weight: 600; color: var(--lp-muted); transition: background-color 160ms ease, color 160ms ease;
}
.sg-nav button:hover { color: var(--lp-text); background: var(--lp-accent-bg); }
.sg-nav button:focus-visible, .sg-close:focus-visible { outline: 2px solid var(--lp-accent); outline-offset: 2px; }
.sg-close {
 all: unset; cursor: pointer; flex-shrink: 0; width: 40px; height: 40px; border-radius: 10px;
 display: flex; align-items: center; justify-content: center; color: var(--lp-text);
 border: 1px solid var(--lp-border); background: var(--lp-bg2);
}
.sg-close:hover { border-color: var(--lp-accent); color: var(--lp-accent); }
.sg-body { flex: 1; overflow-y: auto; overscroll-behavior: contain; padding: 36px 48px 48px; }
.sg-intro { max-width: 680px; font-size: 15.5px; color: var(--lp-body); line-height: 1.7; margin: 0 0 8px; }
.sg-chapter { scroll-margin-top: 16px; margin-top: 56px; }
.sg-row { display: grid; grid-template-columns: 1.3fr 1fr; gap: 48px; align-items: center; margin-bottom: 56px; }
.sg-row.is-flip .tour-shot { order: 2; }
.sg-shot-ph { aspect-ratio: 16 / 10; background: var(--lp-surface); }
.sg-row .sg-screen-name { font-family: var(--font-brand), system-ui, sans-serif; font-size: 21px; font-weight: 600; color: var(--lp-text); letter-spacing: -0.02em; margin: 0 0 10px; }
.sg-row p { font-size: 15px; color: var(--lp-body); line-height: 1.7; margin: 0 0 16px; }
.sg-row ul { margin: 0; padding: 0; list-style: none; display: flex; flex-direction: column; gap: 9px; }
.sg-row li { display: flex; gap: 10px; align-items: flex-start; font-size: 14px; color: var(--lp-body); line-height: 1.6; }
.sg-manual { margin-top: 24px; padding: 28px 30px; border-radius: 14px; background: var(--lp-bg2); border: 1px solid var(--lp-border); display: flex; flex-wrap: wrap; gap: 20px 32px; align-items: center; justify-content: space-between; }
.sg-manual h3 { font-family: var(--font-brand), system-ui, sans-serif; font-size: 20px; font-weight: 600; letter-spacing: -0.02em; margin: 0 0 6px; }
.sg-manual p { font-size: 14px; color: var(--lp-body); line-height: 1.65; margin: 0; max-width: 60ch; }

@media (max-width: 900px) {
 .sg-teaser-in { grid-template-columns: 1fr; gap: 28px; padding: 26px 22px; }
 .sg-row { grid-template-columns: 1fr; gap: 20px; margin-bottom: 44px; }
 .sg-row.is-flip .tour-shot { order: 0; }
 .sg-head h2 { display: none; }
 .sg-body { padding: 24px 20px 36px; }
}
@media (max-width: 760px) {
 .sg-teaser { margin-top: 48px; }
 .sg-dialog { width: 100vw; height: 100dvh; margin: 0; border-radius: 0; border: none; }
 .sg-head { padding: 10px 12px 10px 14px; }
 .sg-nav button { min-height: 44px; box-sizing: border-box; display: inline-flex; align-items: center; }
 .sg-close { width: 44px; height: 44px; }
}
@media (prefers-reduced-motion: reduce) {
 .sg-dialog[open] { animation: none; }
 .sg-thumb-img { transition: none; }
 .sg-thumb:hover .sg-thumb-img { transform: none; }
}
`

const chapterId = (i: number) => `guia-capitulo-${i + 1}`

// The chapters and their screens, under an h2 (the dialog's title, or the
// guide section's heading on /como-funciona): chapters are h3, screens h4.
// `shots` false renders a sized placeholder instead of each image — the dialog
// does that until it is first opened, so a closed guide downloads nothing.
export function TourChapters({ tour, shots }: {
  tour: LandingCopy['tour']
  shots: boolean
}) {
  return (
    <>
      {tour.chapters.map(({ chapter, when, screens }, ci) => (
        <section key={chapter} id={chapterId(ci)} className="sg-chapter" aria-labelledby={`${chapterId(ci)}-h`}>
          <div className="tour-chapter">
            <h3 id={`${chapterId(ci)}-h`} className="lp-h3" style={{ fontSize: 20, margin: '0 0 6px' }}>{chapter}</h3>
            <p style={{ fontSize: 14.5, color: 'var(--lp-muted)', margin: 0, lineHeight: 1.6 }}>{when}</p>
          </div>

          {screens.map(({ img, name, does, finds, alt }, i) => (
            <div key={img} className={`sg-row tour-row${i % 2 === 1 ? ' is-flip' : ''}`}>
              <div className="tour-shot">
                <div className="tour-shot-in">
                  {shots
                    ? <img src={img} alt={alt} loading="lazy" decoding="async" width={3200} height={2000} />
                    : <div className="sg-shot-ph" role="img" aria-label={alt} />}
                </div>
              </div>
              <div>
                <h4 className="sg-screen-name">{name}</h4>
                <p>{does}</p>
                <ul>
                  {finds.map(f => (
                    <li key={f}><span aria-hidden className="tour-dot" />{f}</li>
                  ))}
                </ul>
              </div>
            </div>
          ))}
        </section>
      ))}
    </>
  )
}

function fill(template: string, n: number, c: number) {
  return template.replace('{n}', String(n)).replace('{c}', String(c))
}

export function ScreenGuide({ tour, manual, lang, primaryClass }: {
  tour: LandingCopy['tour']
  manual: LandingCopy['manual']
  lang: 'es' | 'en'
  // The page's own button class, so the trigger matches every other CTA.
  primaryClass: string
}) {
  const dialogRef = useRef<HTMLDialogElement>(null)
  const bodyRef = useRef<HTMLDivElement>(null)
  const openerRef = useRef<HTMLElement | null>(null)
  // Screenshots are mounted on the first open and stay mounted afterwards, so
  // closing and reopening does not reload them.
  const [shotsMounted, setShotsMounted] = useState(false)
  const [pendingChapter, setPendingChapter] = useState<number | null>(null)

  const screenCount = tour.chapters.reduce((sum, ch) => sum + ch.screens.length, 0)

  const scrollToChapter = useCallback((i: number) => {
    const body = bodyRef.current
    const target = body?.querySelector<HTMLElement>(`#${chapterId(i)}`)
    if (!body || !target) return
    // Measured against the scroll container itself: `offsetTop` is relative to
    // the dialog, which also counts the sticky header and overshoots by it.
    const top = target.getBoundingClientRect().top - body.getBoundingClientRect().top + body.scrollTop - 12
    body.scrollTo({ top: i === 0 ? 0 : top, behavior: 'auto' })
  }, [])

  const open = (chapter: number, opener: HTMLElement) => {
    const dialog = dialogRef.current
    if (!dialog || dialog.open) return
    openerRef.current = opener
    setShotsMounted(true)
    dialog.showModal()
    // A modal dialog makes the page inert but does not stop it scrolling
    // underneath on every browser.
    document.documentElement.style.overflow = 'hidden'
    setPendingChapter(chapter)
  }

  // Scroll after React has committed the opened state, so offsets are real.
  useEffect(() => {
    if (pendingChapter === null) return
    scrollToChapter(pendingChapter)
    setPendingChapter(null)
  }, [pendingChapter, scrollToChapter])

  useEffect(() => {
    const dialog = dialogRef.current
    if (!dialog) return
    // `close` fires for every way out: the button, Escape (via `cancel`) and
    // a click on the backdrop.
    const onClose = () => {
      document.documentElement.style.overflow = ''
      openerRef.current?.focus()
    }
    const onBackdrop = (e: MouseEvent) => { if (e.target === dialog) dialog.close() }
    dialog.addEventListener('close', onClose)
    dialog.addEventListener('click', onBackdrop)
    return () => {
      dialog.removeEventListener('close', onClose)
      dialog.removeEventListener('click', onBackdrop)
      document.documentElement.style.overflow = ''
    }
  }, [])

  const pdfHref = `/stockai-manual-${lang}.pdf`

  return (
    <>
      <style dangerouslySetInnerHTML={{ __html: GUIDE_CSS }} />

      <div className="sg-teaser" data-reveal>
        <div className="sg-teaser-in">
          <div>
            <h3 className="sg-title">{tour.teaserTitle}</h3>
            <p className="sg-lead">{tour.teaserLead}</p>
            <p className="sg-count">{fill(tour.count, screenCount, tour.chapters.length)}</p>
            <div className="sg-actions">
              <button
                type="button"
                className={primaryClass}
                aria-haspopup="dialog"
                onClick={e => open(0, e.currentTarget)}
              >
                {tour.open}
              </button>
              <a href={pdfHref} download className="sg-pdf">
                <span>
                  {manual.cta}
                  <span className="sg-pdf-note">{manual.note}</span>
                </span>
              </a>
            </div>
          </div>

          <div className="sg-thumbs">
            {tour.chapters.map(({ chapter, screens }, i) => (
              <button
                key={chapter}
                type="button"
                className="sg-thumb"
                aria-haspopup="dialog"
                onClick={e => open(i, e.currentTarget)}
              >
                <span className="sg-thumb-img">
                  <img src={screens[0].img} alt={screens[0].alt} loading="lazy" decoding="async" width={3200} height={2000} />
                </span>
                <span className="sg-thumb-cap">{chapter}</span>
              </button>
            ))}
          </div>
        </div>
      </div>

      <dialog ref={dialogRef} className="sg-dialog" aria-labelledby="sg-dialog-title">
        <div className="sg-head">
          <h2 id="sg-dialog-title">{tour.title}</h2>
          <nav className="sg-nav" aria-label={tour.chaptersNav}>
            {tour.chapters.map(({ chapter }, i) => (
              <button key={chapter} type="button" onClick={() => scrollToChapter(i)}>{chapter}</button>
            ))}
          </nav>
          <button type="button" className="sg-close" aria-label={tour.close} autoFocus onClick={() => dialogRef.current?.close()}>
            <X size={20} aria-hidden />
          </button>
        </div>

        <div className="sg-body" ref={bodyRef}>
          <p className="sg-intro">{tour.lead}</p>

          <TourChapters tour={tour} shots={shotsMounted} />

          <div className="sg-manual">
            <div>
              <h3>{manual.title}</h3>
              <p>{manual.body}</p>
            </div>
            <div>
              <a href={pdfHref} download className={`${primaryClass} btn-sm`}>{manual.cta}</a>
              <div style={{ fontSize: 12, color: 'var(--lp-muted)', marginTop: 9 }}>{manual.note}</div>
            </div>
          </div>
        </div>
      </dialog>
    </>
  )
}
