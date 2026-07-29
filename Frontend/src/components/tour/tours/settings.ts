import type { TourDefinition } from '../types'

/**
 * The API-keys and webhooks steps were removed along with their tabs — see the
 * ENABLED map in app/settings/page.tsx for why those are hidden. A tour step
 * for a surface the user cannot reach is the exact failure `check_tours.py`
 * exists to catch, so the steps go when the screen does.
 *
 * What is left is one real feature, walked control by control: the session
 * picker, the frequency list, the enabled box and the save/remove pair.
 */
export const settingsTour: TourDefinition = {
  id: 'settings-v3',
  route: '/automatizacion',
  name: 'name',
  autoStart: false,
  steps: [
    { title: 'intro_title', body: 'intro_body' },
    { anchor: 'settings.schedules', title: 'schedules_title', body: 'schedules_body' },
    { anchor: 'settings.session', title: 'session_title', body: 'session_body' },
    { anchor: 'settings.frequency', title: 'frequency_title', body: 'frequency_body' },
    { anchor: 'settings.enabled', title: 'enabled_title', body: 'enabled_body' },
    { anchor: 'settings.save', title: 'save_title', body: 'save_body' },
  ],
  copy: {
    es: {
      name: 'Reentrenar sin acordarte',
      intro_title: 'Que el pronóstico no se quede viejo solo',
      intro_body: 'Un pronóstico envejece: se entrenó con las ventas de un día concreto y, a medida que pasan las semanas, razona sobre un mundo que ya cambió. Por eso el panel de compras te avisa cuando los datos detrás del semáforo llevan tiempo sin actualizarse.\n\nEsta pantalla es la forma de que eso no dependa de que alguien se acuerde.',
      schedules_title: 'Qué hace exactamente un reentrenamiento',
      schedules_body: 'Tomas una actualización ya terminada y una frecuencia, y la volvemos a entrenar sola, sin que nadie abra la app.\n\nOjo con lo que eso significa: el modelo se rehace sobre las ventas que haya cargadas en ese momento, no sobre las que deberían estar. Si nadie sube el archivo nuevo, un reentrenamiento diario repite los mismos números y gasta cómputo.\n\nLa frecuencia que sirve es la que va detrás de tus subidas, nunca una más rápida. Si cargas ventas una vez al mes, reentrenar una vez al mes es exactamente lo correcto.',
      session_title: 'Cuál actualización se vuelve a entrenar',
      session_body: 'La lista trae sólo actualizaciones terminadas. Una que falló o que sigue corriendo no aparece, porque no hay nada que repetir.\n\nEl nombre es el que le pusiste tú en el asistente. Si tienes varias, elige la que está alimentando tu panel de compras: programar una vieja que ya nadie mira gasta cómputo y no mueve el semáforo.\n\nCada actualización lleva su propia programación. Cambiar de una a otra en esta lista te muestra la de ella; no borra la que dejaste atrás.',
      frequency_title: 'Cada cuánto, en palabras',
      frequency_body: 'Seis opciones. Debajo aparece la línea técnica que generan (0 6 * * 1); ignórala, está ahí para soporte.\n\n"Cada lunes a las 6am" y "Días laborables a las 6am" dejan el pronóstico listo antes de que abras. "Cada domingo a las 8am" cierra la semana. "Primer día del mes" va con quien sube ventas una vez al mes. "Todos los días a medianoche" sólo tiene sentido si de verdad cargas ventas a diario.\n\n"Cada hora" no le sirve a nadie aquí: reentrena sesenta veces sobre el mismo archivo. Está para pruebas.',
      enabled_title: 'La casilla: pausar sin perder lo configurado',
      enabled_body: 'Marcada, la programación corre. Desmarcada y guardada, se queda quieta: conserva la sesión y la frecuencia, y no entrena.\n\nEs lo que quieres en un mes raro —el inventario físico, una promoción que rompe el patrón— cuando prefieres no rehacer el modelo sobre ventas que no se van a repetir. La vuelves a marcar y sigue como antes.\n\nDesmarcarla no guarda sola: hay que darle al botón de abajo.',
      save_title: 'Guardar, comprobar y quitar',
      save_body: 'El botón de guardar deja la programación puesta; si ya había una, dice "Actualizar" y la reemplaza. Sale un "Guardado" verde y aparece la próxima ejecución con fecha y hora: ésa es la confirmación de que quedó, no el botón.\n\nRevísala. Las horas de la lista de frecuencias son UTC, así que "6am" puede caerte a otra hora del día; esa fecha ya viene en tu horario y es la que manda.\n\n"Quitar" borra la programación y pide confirmación. La sesión y su pronóstico no se tocan: sólo dejan de rehacerse solos.',
    },
    en: {
      name: 'Retraining without remembering to',
      intro_title: 'Keeping the forecast from going stale on its own',
      intro_body: 'A forecast ages: it was trained on the sales of one particular day, and as weeks pass it reasons about a world that has moved on. That is why the purchasing panel warns you when the data behind the stock signal has not been refreshed in a while.\n\nThis screen is how that stops depending on someone remembering.',
      schedules_title: 'What a retrain actually does',
      schedules_body: 'You take a finished update and a frequency, and we retrain it on its own, without anyone opening the app.\n\nMind what that means: the model is rebuilt on whatever sales are loaded at that moment, not on the ones that ought to be there. If nobody uploads the new file, a daily retrain repeats the same numbers and burns compute.\n\nThe frequency that helps is the one that trails your uploads, never a faster one. If you load sales once a month, retraining once a month is exactly right.',
      session_title: 'Which update gets rebuilt',
      session_body: 'The list carries finished updates only. One that failed or is still running does not appear, because there is nothing to repeat.\n\nThe name is the one you gave it in the wizard. If you have several, pick the one feeding your purchasing panel: scheduling an old one nobody looks at burns compute and moves no stock signal.\n\nEach update carries its own schedule. Switching between them in this list shows you that one\'s schedule; it does not delete the one you left behind.',
      frequency_title: 'How often, in plain words',
      frequency_body: 'Six options. Underneath sits the technical line they produce (0 6 * * 1); ignore it, it is there for support.\n\n"Every Monday at 6am" and "Weekdays at 6am" leave the forecast ready before you open up. "Every Sunday at 8am" closes the week. "First day of month" suits anyone who uploads sales monthly. "Every day at midnight" only makes sense if you really do load sales daily.\n\n"Every hour" helps nobody here: it retrains sixty times over the same file. It exists for testing.',
      enabled_title: 'The checkbox: pausing without losing the setup',
      enabled_body: 'Ticked, the schedule runs. Unticked and saved, it sits still: it keeps the session and the frequency, and it does not train.\n\nThat is what you want in an odd month — the physical stocktake, a promotion that breaks the pattern — when you would rather not rebuild the model on sales that will not repeat. Tick it again and it carries on as before.\n\nUnticking does not save by itself: you still have to press the button below.',
      save_title: 'Save, check and remove',
      save_body: 'The save button puts the schedule in place; if there was one already it reads "Update" and replaces it. A green "Saved" appears, and so does the next run with its date and time: that, not the button, is the confirmation it stuck.\n\nRead it. The hours in the frequency list are UTC, so "6am" may land at another time of day for you; that date is already in your own time zone and it is the one that counts.\n\n"Remove" deletes the schedule and asks for confirmation. The session and its forecast are untouched: they simply stop rebuilding themselves.',
    },
  },
}
