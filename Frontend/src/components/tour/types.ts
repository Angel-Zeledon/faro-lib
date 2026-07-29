/**
 * Guided tours ("slices"): short, anchored walkthroughs of each screen.
 *
 * Design rules this file encodes:
 *
 *  · A step points at a REAL element, found by `data-tour`. It never invents a
 *    position. If the anchor is absent — the screen is empty, the feature is
 *    gated, the markup moved — the step renders as a centred panel rather than
 *    highlighting the wrong thing. That is what keeps a tour from quietly
 *    lying after a refactor.
 *  · A tour never blocks. It can be dismissed at any step and never demands
 *    completion before the user may act.
 *  · Each step answers WHY the thing exists and what a wrong choice costs, not
 *    what the control is called — the label is already on screen.
 *
 * ── Why the copy lives here and not in i18n/translations.ts ──────────────────
 *
 * Every other string in the app is a key in the shared catalogue. Tours are the
 * exception, and deliberately: a tour is a self-contained unit of ~10 steps
 * whose text is meaningless apart from its steps. Keeping the definition and
 * both languages in ONE file per tour means a screen's walkthrough is added,
 * reviewed or deleted as a single unit, and — the practical reason — several
 * tours can be written at once without every author queuing on the same
 * 2,600-line catalogue and its parity requirement.
 *
 * The shell strings (Next, Back, Close…) stay in translations.ts, because those
 * belong to the chrome, not to any tour.
 */

export interface TourStep {
  /** Value of the `data-tour` attribute on the element to highlight.
   *  Omit for an intro/outro step that belongs to the screen as a whole. */
  anchor?: string
  /** Copy key, resolved against this tour's own `es` / `en` maps. */
  title: string
  body: string
}

export interface TourCopy {
  es: Record<string, string>
  en: Record<string, string>
}

export interface TourDefinition {
  /** Stable id, also the localStorage key. Renaming it replays the tour for
   *  everyone — which is correct when the content changed enough to be worth
   *  seeing again. */
  id: string
  /** Route this tour belongs to, matched exactly or as a path prefix. */
  route: string
  /** Copy key for the tour's name, shown in the replay affordance. */
  name: string
  steps: TourStep[]
  /** Offer it automatically on first visit. Screens the user lands on
   *  mid-task should leave this false and rely on the launcher instead. */
  autoStart?: boolean
  copy: TourCopy
}
