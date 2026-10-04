// How a model id is shown to a distributor.
//
// A distributor buys stock; nothing in that job is helped by learning that one
// of these series was fitted by a gradient-boosted tree. Every surface that
// renders a model id goes through `modelLabel` — the single mapping — so
// "Modelo 3" is the same algorithm on every SKU, in every export and after
// every reload. A per-render or per-SKU numbering would be worse than the
// jargon: the same label would mean two different things on two rows.
//
// It lives here rather than inside /pronosticos because the run-warnings panel
// needs it too: that panel was printing `model 'croston'` at a user the rest of
// the screen was carefully shielding from the word. A second copy of the array
// would be a second numbering, which is the one failure mode this mapping
// exists to prevent.

export type Translate = (key: string, params?: Record<string, unknown>) => string

// A model's POSITION here is the number the user sees, so entries must only
// ever be appended — reordering or removing one silently renumbers models the
// user has already learned.
// `global_lgbm` is appended, never inserted: it is a candidate like the rest —
// it competes on the same table and can win a SKU — so it gets a number, and
// appending is what keeps every number the user has already learned intact.
export const MODEL_ORDER = [
  'lightgbm', 'xgboost', 'prophet', 'arima', 'ets', 'croston', 'sarimax', 'lstm', 'global_lgbm',
]

// Baselines are not one of the candidates: they are the "what if we didn't
// forecast at all" yardstick every trained model has to beat. Giving them a
// number would present them as an option worth picking; naming what they
// actually do explains why they are in the table at all.
// `ensemble` is the engine's per-SKU inverse-MAE blend of the models above
// (pipeline.py `_generate_forecast_df`). It is neither one of the candidates
// nor a yardstick, so a number would misfile it — it is what you get when the
// numbered models are combined, and the label says exactly that.
const NAMED_LABEL_KEYS: Record<string, string> = {
  ensemble:       'skus.model_combined',
  naive:          'skus.model_baseline_last_value',
  seasonal_naive: 'skus.model_baseline_season',
  historical_avg: 'skus.model_baseline_average',
}

export function modelLabel(t: Translate, id: string | null | undefined): string {
  if (!id) return '—'
  const key = id.toLowerCase()
  const namedKey = NAMED_LABEL_KEYS[key]
  if (namedKey) return t(namedKey)
  const idx = MODEL_ORDER.indexOf(key)
  // An id outside the list means the engine gained a model this screen has not
  // been told about. A generic label keeps the jargon hidden and is a visible
  // signal to append the id to MODEL_ORDER; minting a number on the fly would
  // be worse, because such a number could not survive the next release.
  if (idx < 0) return t('skus.model_other')
  return t('skus.model_numbered', { n: idx + 1 })
}
