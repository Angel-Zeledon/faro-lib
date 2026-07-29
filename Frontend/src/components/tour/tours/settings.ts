import type { TourDefinition } from '../types'

export const settingsTour: TourDefinition = {
  id: 'settings-v1',
  route: '/settings',
  name: 'name',
  autoStart: false,
  steps: [
    { title: 'intro_title', body: 'intro_body' },
    { anchor: 'settings.api-keys', title: 'keys_title', body: 'keys_body' },
    { anchor: 'settings.webhooks', title: 'webhooks_title', body: 'webhooks_body' },
    { anchor: 'settings.schedules', title: 'schedules_title', body: 'schedules_body' },
  ],
  copy: {
    es: {
      name: 'Conectar Faro con lo demás',
      intro_title: 'Esta pantalla es fontanería, no compras',
      intro_body: 'Nada de lo que hay aquí cambia una cantidad sugerida ni un semáforo. Son tres formas de que Faro converse con otros sistemas: dejar que uno lea tus datos, que se entere cuando pasa algo, y que el pronóstico se vuelva a entrenar solo.\n\nSi no tienes un ERP ni alguien de sistemas, la única pestaña que te va a servir es la tercera.',
      keys_title: 'Las llaves todavía no abren nada',
      keys_body: 'Una API key sirve para que otro sistema — tu ERP, un script, una hoja conectada — lea tus pronósticos sin entrar por la pantalla.\n\nHoy puedes crearlas, pero no autentican: el acceso por API aún no está encendido. Lo decimos aquí para que nadie prometa una integración para el lunes apoyada en una llave que va a devolver "no autorizado".\n\nCuando funcionen, la llave se muestra una sola vez, al crearla. Si se pierde, no se recupera: se revoca y se crea otra.',
      webhooks_title: 'Un aviso a tu sistema cuando termina el entrenamiento',
      webhooks_body: 'Le das una URL tuya y te hacemos un POST cuando un entrenamiento termina o falla. Eso es todo lo que existe hoy: dos eventos, ambos sobre entrenamientos. No es un canal de alertas de stock — eso va por correo y WhatsApp.\n\nEs un intento único, sin reintentos: si tu servidor está caído en ese momento, ese aviso se pierde y no vuelve. Úsalo para enterarte, no como la única forma de saber si el modelo corrió.',
      schedules_title: 'Reentrenar sin que nadie se acuerde',
      schedules_body: 'Eliges una sesión ya terminada y una frecuencia, y la volvemos a entrenar sola. Es el antídoto contra el aviso de "datos viejos" del panel de compras.\n\nOjo con lo que reentrenar significa: el modelo se rehace sobre las ventas que haya cargadas. Si nadie sube el archivo nuevo, un reentrenamiento diario sólo repite los mismos números y gasta cómputo. La frecuencia que sirve es la que va detrás de tus subidas, no una más rápida.',
    },
    en: {
      name: 'Connecting Faro to everything else',
      intro_title: 'This screen is plumbing, not purchasing',
      intro_body: 'Nothing here changes a suggested quantity or a stock signal. These are three ways for Faro to talk to other systems: letting one read your data, letting one hear when something happens, and having the forecast retrain itself.\n\nIf you have no ERP and no one on IT, the only tab that will serve you is the third one.',
      keys_title: 'The keys do not open anything yet',
      keys_body: 'An API key lets another system — your ERP, a script, a connected spreadsheet — read your forecasts without going through the screen.\n\nYou can create them today, but they do not authenticate: API access is not switched on yet. We say it here so nobody promises an integration for Monday resting on a key that will answer "unauthorised".\n\nWhen they do work, the key is shown once, at creation. Lose it and it is not recoverable: you revoke it and create another.',
      webhooks_title: 'A ping to your system when training ends',
      webhooks_body: 'You give us a URL of yours and we POST to it when a training run finishes or fails. That is all that exists today: two events, both about training runs. It is not a stock-alert channel — those go by email and WhatsApp.\n\nIt is a single attempt with no retries: if your server is down at that moment, that notification is lost and does not come back. Use it to find out, not as your only way of knowing the model ran.',
      schedules_title: 'Retraining without anyone remembering to',
      schedules_body: 'You pick a finished session and a frequency, and we retrain it on its own. It is the antidote to the "stale data" warning on the purchasing panel.\n\nMind what retraining means: the model is rebuilt on whatever sales are loaded. If nobody uploads the new file, a daily retrain just repeats the same numbers and burns compute. The frequency that helps is the one that trails your uploads, not a faster one.',
    },
  },
}
