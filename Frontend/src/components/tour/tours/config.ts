import type { TourDefinition } from '../types'

export const configTour: TourDefinition = {
  id: 'config-v1',
  route: '/config',
  name: 'name',
  autoStart: false,
  steps: [
    { title: 'intro_title', body: 'intro_body' },
    { anchor: 'config.whatsapp', title: 'whatsapp_title', body: 'whatsapp_body' },
    { anchor: 'config.security', title: 'security_title', body: 'security_body' },
    { anchor: 'config.activity', title: 'activity_title', body: 'activity_body' },
    { anchor: 'config.models', title: 'models_title', body: 'models_body' },
  ],
  copy: {
    es: {
      name: 'Tu cuenta, tus avisos y tu rastro',
      intro_title: 'Casi todo lo de aquí es tuyo, no del negocio',
      intro_body: 'Tu nombre, tu idioma, tu tema, tu teléfono, tu contraseña: lo que cambies en esta pantalla te afecta a ti y a nadie más de tu equipo.\n\nDos cosas se leen pero no se tocan: tu rol y el estado de tu cuenta. Eso lo mueve un administrador desde Usuarios, y así nadie se asciende solo.',
      whatsapp_title: 'Por dónde te llegan las alertas',
      whatsapp_body: 'Cada mañana revisamos tu stock y te avisamos de lo que se va a acabar. El correo ya lo tenemos; el WhatsApp lo pones tú aquí.\n\nPedimos un código antes de darlo por bueno porque un dígito mal escrito manda tus alertas de inventario al teléfono de un desconocido, con nombres de productos y cantidades. Verificar es la única forma de saber que el número existe y es tuyo.\n\nSi ya no lo quieres, desvincúlalo: los avisos siguen llegando al correo.',
      security_title: 'Cambiar la contraseña pide algo más que la sesión abierta',
      security_body: 'No basta con escribir una nueva: te mandamos un código de seis dígitos al correo y hay que confirmarlo ahí mismo.\n\nEs a propósito. En una bodega la computadora queda abierta y cualquiera que pase se sienta frente a tu sesión; con este paso, quien no tenga también tu correo no puede quedarse con tu cuenta.',
      activity_title: 'Tu rastro, no el del equipo',
      activity_body: 'Esta lista es sólo tuya: lo que hiciste tú y lo que la app hizo por ti. No es la bitácora de la empresa — lo de tus compañeros no sale aquí, ni lo tuyo en la de ellos.\n\nSirve para dos preguntas muy concretas: ¿salió de verdad la alerta de esta mañana, y por cuál canal? y ¿ese ingreso fui yo? Si ves algo que no reconoces, cambia la contraseña arriba.',
      models_title: 'Lo que la plataforma sabe entrenar',
      models_body: 'Es un catálogo, no un ajuste: aquí no se enciende ni se apaga nada. Los modelos se eligen sesión por sesión en el asistente de entrenamiento.\n\nLo único que conviene mirar es la etiqueta: los que dicen "beta" todavía cambian, así que si uno de ésos gana la comparación, revisa el resultado antes de comprar apoyado en él.',
    },
    en: {
      name: 'Your account, your alerts and your trail',
      intro_title: 'Almost everything here is yours, not the company\'s',
      intro_body: 'Your name, your language, your theme, your phone, your password: what you change on this screen affects you and nobody else on your team.\n\nTwo things are readable but not editable: your role and your account status. An administrator moves those from Users, so nobody promotes themselves.',
      whatsapp_title: 'Where your alerts arrive',
      whatsapp_body: 'Every morning we check your stock and warn you about what is about to run out. We already have your email; WhatsApp is the one you add here.\n\nWe ask for a code before accepting it because one mistyped digit sends your inventory alerts to a stranger\'s phone, product names and quantities included. Verifying is the only way to know the number exists and is yours.\n\nIf you no longer want it, unlink it: the warnings keep arriving by email.',
      security_title: 'Changing the password takes more than an open session',
      security_body: 'Typing a new one is not enough: we send a six-digit code to your email and it has to be confirmed right there.\n\nThat is deliberate. In a warehouse the computer stays unlocked and whoever walks past sits down in front of your session; with this step, someone who does not also have your email cannot take over your account.',
      activity_title: 'Your trail, not the team\'s',
      activity_body: 'This list is only yours: what you did and what the app did on your behalf. It is not a company audit log — your colleagues\' activity is not here, and yours is not in theirs.\n\nIt answers two very concrete questions: did this morning\'s alert actually go out, and through which channel? and was that sign-in me? If you see something you do not recognise, change the password above.',
      models_title: 'What the platform knows how to train',
      models_body: 'This is a catalogue, not a setting: nothing is switched on or off here. Models are picked session by session in the training wizard.\n\nThe one thing worth reading is the label: the ones marked "beta" are still changing, so if one of those wins the comparison, check the result before buying on the strength of it.',
    },
  },
}
