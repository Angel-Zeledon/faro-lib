import type { TourDefinition } from '../types'

/**
 * The API-keys and webhooks steps were removed along with their tabs — see the
 * ENABLED map in app/settings/page.tsx for why those are hidden. A tour step
 * for a surface the user cannot reach is the exact failure `check_tours.py`
 * exists to catch, so the steps go when the screen does.
 *
 * What is left is one real feature, so the tour is two steps and stops.
 */
export const settingsTour: TourDefinition = {
  id: 'settings-v2',
  route: '/settings',
  name: 'name',
  autoStart: false,
  steps: [
    { title: 'intro_title', body: 'intro_body' },
    { anchor: 'settings.schedules', title: 'schedules_title', body: 'schedules_body' },
  ],
  copy: {
    es: {
      name: 'Reentrenar sin acordarte',
      intro_title: 'Que el pronóstico no se quede viejo solo',
      intro_body: 'Un pronóstico envejece: se entrenó con las ventas de un día concreto y, a medida que pasan las semanas, razona sobre un mundo que ya cambió. Por eso el panel de compras te avisa cuando los datos detrás del semáforo llevan tiempo sin actualizarse.\n\nEsta pantalla es la forma de que eso no dependa de que alguien se acuerde.',
      schedules_title: 'Elige una sesión y cada cuánto se rehace',
      schedules_body: 'Tomas una actualización ya terminada y una frecuencia, y la volvemos a entrenar sola.\n\nOjo con lo que reentrenar significa: el modelo se rehace sobre las ventas que haya cargadas en ese momento. Si nadie sube el archivo nuevo, un reentrenamiento diario repite los mismos números y gasta cómputo. La frecuencia que sirve es la que va detrás de tus subidas, no una más rápida.\n\nSi cargas ventas una vez al mes, reentrenar una vez al mes es exactamente lo correcto.',
    },
    en: {
      name: 'Retraining without remembering to',
      intro_title: 'Keeping the forecast from going stale on its own',
      intro_body: 'A forecast ages: it was trained on the sales of one particular day, and as weeks pass it reasons about a world that has moved on. That is why the purchasing panel warns you when the data behind the stock signal has not been refreshed in a while.\n\nThis screen is how that stops depending on someone remembering.',
      schedules_title: 'Pick an update and how often it is rebuilt',
      schedules_body: 'You take a finished update and a frequency, and we retrain it on its own.\n\nMind what retraining means: the model is rebuilt on whatever sales are loaded at that moment. If nobody uploads the new file, a daily retrain repeats the same numbers and burns compute. The frequency that helps is the one that trails your uploads, not a faster one.\n\nIf you load sales once a month, retraining once a month is exactly right.',
    },
  },
}
