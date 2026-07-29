/**
 * Guided tours ("slices"): a few short, anchored walkthroughs of the screens
 * whose value is not self-evident — the training wizard, the scenario
 * simulator, the purchase panel.
 *
 * Design rules this type encodes:
 *
 *  · A step points at a REAL element, found by `data-tour`. It never invents a
 *    position. If the anchor is absent — the screen is empty, the feature is
 *    gated, the markup moved — the step is skipped rather than shown pointing
 *    at nothing. That is what keeps a tour from quietly lying after a refactor.
 *  · Copy is i18n keys, never literals. Tours are the most user-facing thing
 *    in the app; they follow the same catalogue as everything else.
 *  · A tour never blocks. It can be dismissed at any step and never demands
 *    completion before the user may act.
 */

export interface TourStep {
  /** Value of the `data-tour` attribute on the element to highlight.
   *  Omit for an intro/outro step that belongs to the screen as a whole. */
  anchor?: string
  /** i18n key for the step title. */
  titleKey: string
  /** i18n key for the step body. */
  bodyKey: string
  /** Where to place the card relative to the anchor. 'auto' picks whichever
   *  side has room. Ignored when there is no anchor (the card centres). */
  placement?: 'auto' | 'top' | 'bottom' | 'left' | 'right'
}

export interface TourDefinition {
  /** Stable id. Used as the localStorage key, so renaming it replays the tour
   *  for everyone — which is the correct behaviour when the content changed
   *  enough to be worth seeing again. */
  id: string
  /** Route this tour belongs to, matched with startsWith. */
  route: string
  /** i18n key for the tour's name, shown in the "replay" affordance. */
  nameKey: string
  steps: TourStep[]
  /** Offer to start it automatically the first time this route is opened.
   *  Screens the user lands on mid-task should set this false and rely on the
   *  replay button instead. */
  autoStart?: boolean
}
