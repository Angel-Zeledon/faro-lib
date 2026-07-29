import type { TourDefinition } from './types'

/**
 * The tours themselves.
 *
 * One per screen whose value is not self-evident from looking at it. Screens
 * that explain themselves — the supplier list, settings — deliberately have
 * none: a tour over an obvious screen trains people to dismiss tours.
 *
 * Each step answers WHY the thing exists and what a wrong choice costs, not
 * what the button is labelled — the label is already on screen. That is why
 * the bodies run to a short paragraph rather than a caption.
 *
 * `autoStart` is reserved for screens someone lands on with nothing to do yet.
 * /hoy and /inventory are where a buyer arrives mid-task, so those wait to be
 * asked for — interrupting someone about to place an order is worse than not
 * explaining anything.
 */
export const TOURS: TourDefinition[] = [
  {
    id: 'quick-start-v2',
    route: '/quick-start',
    nameKey: 'tour.qs.name',
    autoStart: true,
    steps: [
      { titleKey: 'tour.qs.intro_title',  bodyKey: 'tour.qs.intro_body' },
      { anchor: 'qs.steps',       titleKey: 'tour.qs.steps_title',       bodyKey: 'tour.qs.steps_body' },
      { anchor: 'qs.tabs',        titleKey: 'tour.qs.tabs_title',        bodyKey: 'tour.qs.tabs_body' },
      { anchor: 'qs.upload',      titleKey: 'tour.qs.upload_title',      bodyKey: 'tour.qs.upload_body' },
      { anchor: 'qs.name',        titleKey: 'tour.qs.name_title',        bodyKey: 'tour.qs.name_body' },
      { anchor: 'qs.horizon',     titleKey: 'tour.qs.horizon_title',     bodyKey: 'tour.qs.horizon_body' },
      { anchor: 'qs.granularity', titleKey: 'tour.qs.granularity_title', bodyKey: 'tour.qs.granularity_body' },
      { titleKey: 'tour.qs.training_title', bodyKey: 'tour.qs.training_body' },
      { titleKey: 'tour.qs.wait_title',     bodyKey: 'tour.qs.wait_body' },
    ],
  },
  {
    id: 'scenarios-v2',
    route: '/scenarios',
    nameKey: 'tour.sc.name',
    autoStart: true,
    steps: [
      { titleKey: 'tour.sc.intro_title', bodyKey: 'tour.sc.intro_body' },
      { anchor: 'sc.session', titleKey: 'tour.sc.session_title', bodyKey: 'tour.sc.session_body' },
      { anchor: 'sc.builder', titleKey: 'tour.sc.builder_title', bodyKey: 'tour.sc.builder_body' },
      { anchor: 'sc.rule',    titleKey: 'tour.sc.rule_title',    bodyKey: 'tour.sc.rule_body' },
      { anchor: 'sc.run',     titleKey: 'tour.sc.run_title',     bodyKey: 'tour.sc.run_body' },
      { anchor: 'sc.compare', titleKey: 'tour.sc.compare_title', bodyKey: 'tour.sc.compare_body' },
      { anchor: 'sc.changes', titleKey: 'tour.sc.changes_title', bodyKey: 'tour.sc.changes_body' },
      { anchor: 'sc.save',    titleKey: 'tour.sc.save_title',    bodyKey: 'tour.sc.save_body' },
      { titleKey: 'tour.sc.safe_title', bodyKey: 'tour.sc.safe_body' },
    ],
  },
  {
    id: 'hoy-v2',
    route: '/hoy',
    nameKey: 'tour.hoy.name',
    steps: [
      { titleKey: 'tour.hoy.intro_title', bodyKey: 'tour.hoy.intro_body' },
      { anchor: 'hoy.freshness',   titleKey: 'tour.hoy.freshness_title',   bodyKey: 'tour.hoy.freshness_body' },
      { anchor: 'hoy.assumptions', titleKey: 'tour.hoy.assumptions_title', bodyKey: 'tour.hoy.assumptions_body' },
      { anchor: 'hoy.receptions',  titleKey: 'tour.hoy.receptions_title',  bodyKey: 'tour.hoy.receptions_body' },
      { anchor: 'hoy.kpis',        titleKey: 'tour.hoy.kpis_title',        bodyKey: 'tour.hoy.kpis_body' },
      { anchor: 'hoy.narrative',   titleKey: 'tour.hoy.narrative_title',   bodyKey: 'tour.hoy.narrative_body' },
      { anchor: 'hoy.transfers',   titleKey: 'tour.hoy.transfers_title',   bodyKey: 'tour.hoy.transfers_body' },
      { anchor: 'hoy.actions',     titleKey: 'tour.hoy.actions_title',     bodyKey: 'tour.hoy.actions_body' },
      { anchor: 'hoy.why',         titleKey: 'tour.hoy.why_title',         bodyKey: 'tour.hoy.why_body' },
      { anchor: 'hoy.cart',        titleKey: 'tour.hoy.cart_title',        bodyKey: 'tour.hoy.cart_body' },
    ],
  },
  {
    id: 'inventory-v2',
    route: '/inventory',
    nameKey: 'tour.inv.name',
    steps: [
      { titleKey: 'tour.inv.intro_title', bodyKey: 'tour.inv.intro_body' },
      { anchor: 'inv.views',    titleKey: 'tour.inv.views_title',    bodyKey: 'tour.inv.views_body' },
      { anchor: 'inv.signal',   titleKey: 'tour.inv.signal_title',   bodyKey: 'tour.inv.signal_body' },
      { anchor: 'inv.coverage', titleKey: 'tour.inv.coverage_title', bodyKey: 'tour.inv.coverage_body' },
      { anchor: 'inv.suggest',  titleKey: 'tour.inv.suggest_title',  bodyKey: 'tour.inv.suggest_body' },
      { anchor: 'inv.leadtime', titleKey: 'tour.inv.leadtime_title', bodyKey: 'tour.inv.leadtime_body' },
      { anchor: 'inv.expand',   titleKey: 'tour.inv.expand_title',   bodyKey: 'tour.inv.expand_body' },
      { anchor: 'inv.export',   titleKey: 'tour.inv.export_title',   bodyKey: 'tour.inv.export_body' },
      { titleKey: 'tour.inv.config_title', bodyKey: 'tour.inv.config_body' },
    ],
  },
]
