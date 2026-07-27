// Display names for the closed vocabularies the backend and ForecastingCore
// ship as raw English identifiers.
//
// `stable`, `non-stationary`, `job.completed`, `Deep Learning`, `admin` — these
// are stable machine values (CLAUDE.md keeps them English on purpose), but they
// were being printed straight into a Spanish UI. This module is the single
// place that turns one into the localized noun, keyed by the English value.
//
// Every helper degrades to the raw value rather than to an i18n key: an
// unmapped member of an open-ended set (an activity-log `action`, a new chat
// `source`) must still say something, and the raw English beats "enum.foo_bar".

type Translate = (key: string, params?: Record<string, unknown>) => string

/** Resolve `enum.<prefix>_<value>`, falling back to the raw value. */
function lookup(t: Translate, prefix: string, value: string | null | undefined): string {
  if (!value) return '—'
  // Dots and dashes are not valid in our key names ('job.completed',
  // 'non-stationary'), so normalise the same way the catalog does.
  const key = `enum.${prefix}_${value.replace(/[.-]/g, '_')}`
  const label = t(key)
  return label === key ? value : label
}

/** admin / analyst / viewer */
export const roleLabel = (t: Translate, role: string | null | undefined) =>
  lookup(t, 'role', role)

/** stable / seasonal / volatile / intermittent / short */
export const seriesTypeLabel = (t: Translate, v: string | null | undefined) =>
  lookup(t, 'series', v)

/** daily / weekly / monthly / quarterly / yearly */
export const granularityLabel = (t: Translate, v: string | null | undefined) =>
  lookup(t, 'granularity', v)

/** none / weak / moderate / strong */
export const seasonalityClassLabel = (t: Translate, v: string | null | undefined) =>
  lookup(t, 'seasonality', v)

/** increasing / decreasing / no_trend */
export const trendDirectionLabel = (t: Translate, v: string | null | undefined) =>
  lookup(t, 'trend', v)

/** stationary / non-stationary / mixed / insufficient_data */
export const stationarityLabel = (t: Translate, v: string | null | undefined) =>
  lookup(t, 'stationarity', v)

/** smooth / intermittent / erratic / lumpy */
export const crostonClassLabel = (t: Translate, v: string | null | undefined) =>
  lookup(t, 'croston', v)

/** normal / lognormal / gamma / exponential / unknown */
export const distributionLabel = (t: Translate, v: string | null | undefined) =>
  lookup(t, 'distribution', v)

/** ML / Statistical / Deep Learning */
export const modelCategoryLabel = (t: Translate, v: string | null | undefined) =>
  lookup(t, 'model_category', v?.replace(/\s+/g, '_').toLowerCase())

/** job.completed / job.failed */
export const webhookEventLabel = (t: Translate, v: string | null | undefined) =>
  lookup(t, 'webhook_event', v)

/** rag / fallback / general / off_topic / no_access / error / rag_retrieved */
export const chatSourceLabel = (t: Translate, v: string | null | undefined) =>
  lookup(t, 'chat_source', v)

/** dataset_profile / model_summary / … — the analyst's data-source filter. */
export const chatDataSourceLabel = (
  t: Translate, id: string, backendLabel: string,
) => {
  const key = `enum.chat_data_source_${id}`
  const label = t(key)
  return label === key ? backendLabel : label
}

/**
 * Activity-log actions are free text in the DB (no enum, no CHECK), so this
 * one both translates the values we do emit and humanizes the ones we don't
 * ('some_new_action' → 'Some new action') instead of printing snake_case.
 */
export function activityActionLabel(t: Translate, action: string): string {
  const key = `enum.activity_${action.replace(/[.-]/g, '_')}`
  const label = t(key)
  if (label !== key) return label
  const words = action.replace(/[._-]/g, ' ')
  return words.charAt(0).toUpperCase() + words.slice(1)
}

/**
 * One-line model description. The backend catalog ships English prose
 * (`backend/api/v1/models.py`), but the model names are a closed set, so the
 * Spanish copy lives here keyed by name and the backend text is the fallback
 * for any model this catalog does not know yet.
 */
export const modelDescription = (
  t: Translate, name: string, backendDescription: string,
) => {
  const key = `enum.model_desc_${name}`
  const label = t(key)
  return label === key ? backendDescription : label
}
