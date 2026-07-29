import type { TourDefinition } from './types'

/**
 * The tours themselves.
 *
 * One per screen whose value is not self-evident from looking at it. Screens
 * that explain themselves — the supplier list, settings — deliberately have
 * none: a tour over an obvious screen trains people to dismiss tours.
 *
 * Every step is short enough to read standing up. A step that needs a
 * paragraph is a sign the screen needs fixing, not narrating.
 *
 * `autoStart` is reserved for screens someone lands on with nothing to do yet.
 * /hoy and /inventory are where a buyer arrives mid-task, so those wait to be
 * asked for — interrupting someone about to place an order is worse than not
 * explaining anything.
 */
export const TOURS: TourDefinition[] = [
  {
    id: 'quick-start-v1',
    route: '/quick-start',
    nameKey: 'tour.qs.name',
    autoStart: true,
    steps: [
      { titleKey: 'tour.qs.intro_title',  bodyKey: 'tour.qs.intro_body' },
      { anchor: 'qs.upload',      titleKey: 'tour.qs.upload_title',      bodyKey: 'tour.qs.upload_body' },
      { anchor: 'qs.horizon',     titleKey: 'tour.qs.horizon_title',     bodyKey: 'tour.qs.horizon_body' },
      { anchor: 'qs.granularity', titleKey: 'tour.qs.granularity_title', bodyKey: 'tour.qs.granularity_body' },
      { titleKey: 'tour.qs.wait_title', bodyKey: 'tour.qs.wait_body' },
    ],
  },
  {
    id: 'scenarios-v1',
    route: '/scenarios',
    nameKey: 'tour.sc.name',
    autoStart: true,
    steps: [
      { titleKey: 'tour.sc.intro_title', bodyKey: 'tour.sc.intro_body' },
      { anchor: 'sc.builder', titleKey: 'tour.sc.builder_title', bodyKey: 'tour.sc.builder_body' },
      { anchor: 'sc.run',     titleKey: 'tour.sc.run_title',     bodyKey: 'tour.sc.run_body' },
      { anchor: 'sc.changes', titleKey: 'tour.sc.changes_title', bodyKey: 'tour.sc.changes_body' },
      { titleKey: 'tour.sc.safe_title', bodyKey: 'tour.sc.safe_body' },
    ],
  },
  {
    id: 'hoy-v1',
    route: '/hoy',
    nameKey: 'tour.hoy.name',
    steps: [
      { titleKey: 'tour.hoy.intro_title', bodyKey: 'tour.hoy.intro_body' },
      { anchor: 'hoy.kpis',    titleKey: 'tour.hoy.kpis_title',    bodyKey: 'tour.hoy.kpis_body' },
      { anchor: 'hoy.actions', titleKey: 'tour.hoy.actions_title', bodyKey: 'tour.hoy.actions_body' },
      { anchor: 'hoy.why',     titleKey: 'tour.hoy.why_title',     bodyKey: 'tour.hoy.why_body' },
      { titleKey: 'tour.hoy.cart_title', bodyKey: 'tour.hoy.cart_body' },
    ],
  },
  {
    id: 'inventory-v1',
    route: '/inventory',
    nameKey: 'tour.inv.name',
    steps: [
      { titleKey: 'tour.inv.intro_title', bodyKey: 'tour.inv.intro_body' },
      { anchor: 'inv.signal',  titleKey: 'tour.inv.signal_title',  bodyKey: 'tour.inv.signal_body' },
      { anchor: 'inv.suggest', titleKey: 'tour.inv.suggest_title', bodyKey: 'tour.inv.suggest_body' },
      { titleKey: 'tour.inv.config_title', bodyKey: 'tour.inv.config_body' },
    ],
  },
]
