import type { TourDefinition } from '../types'

/**
 * There is no planning-grain step. That section renders only when the tenant
 * has more than one trained grain to choose between (see PlanningSection in
 * app/config/page.tsx), so a step for it would spend most of its life as a
 * centred panel describing a control the reader cannot see.
 */
export const configTour: TourDefinition = {
  id: 'config-v2',
  route: '/config',
  name: 'name',
  autoStart: false,
  steps: [
    { title: 'intro_title', body: 'intro_body' },
    { anchor: 'config.profile', title: 'profile_title', body: 'profile_body' },
    { anchor: 'config.appearance', title: 'appearance_title', body: 'appearance_body' },
    { anchor: 'config.whatsapp', title: 'whatsapp_title', body: 'whatsapp_body' },
    { anchor: 'config.whatsapp', title: 'wacode_title', body: 'wacode_body' },
    { anchor: 'config.dm_sms', title: 'dmsms_title', body: 'dmsms_body' },
    // Two short cards rather than one long one: this anchor sits at the foot of
    // the page, so the overlay can only place its card above it, and a tall card
    // there pushes Next and Back off the bottom of the screen.
    { anchor: 'config.security', title: 'security_title', body: 'security_body' },
    { anchor: 'config.security', title: 'pwcode_title', body: 'pwcode_body' },
    { anchor: 'config.activity', title: 'activity_title', body: 'activity_body' },
    { anchor: 'config.models', title: 'models_title', body: 'models_body' },
  ],
  copy: {
    es: {
      name: 'Tu cuenta, tus avisos y tu rastro',
      intro_title: 'Casi todo lo de aquí es tuyo, no del negocio',
      intro_body: 'Tu nombre, tu idioma, tu tema, tu teléfono, tu contraseña: lo que cambies en esta pantalla te afecta a ti y a nadie más de tu equipo.\n\nDos cosas se leen pero no se tocan: tu rol y el estado de tu cuenta. Eso lo mueve un administrador desde Usuarios, y así nadie se asciende solo.',
      profile_title: 'Tu nombre es lo que ve tu equipo',
      profile_body: 'El lápiz junto al nombre lo vuelve editable; Enter guarda. Escríbelo como te llaman en la empresa — "Ana Rojas", no "arojas" ni "Admin": es lo que sale en la lista de usuarios, en los mensajes del equipo y en el historial de más abajo.\n\nEl correo se lee y no se toca: es tu usuario para entrar. Si hay que cambiarlo lo hace un administrador desde Usuarios, y la cuenta queda esperando la verificación del nuevo.\n\nRol y estado de cuenta también son de solo lectura, por lo mismo.',
      appearance_title: 'Idioma y tema, guardados en tu cuenta',
      appearance_body: 'Español o English cambia toda la interfaz al instante: menús, botones, nombres de los estados. Es tuyo y de nadie más — tu compañero puede tenerlo en el otro idioma sin que a ti te cambie nada.\n\nOscuro o Claro es puro gusto y no toca ningún dato. Con ventana y reflejos en la pantalla, Claro; en bodega o de noche, Oscuro.\n\nLos dos se guardan en tu cuenta, no en este navegador: entras desde otra computadora y la app aparece igual.',
      whatsapp_title: 'Por dónde te llegan las alertas',
      whatsapp_body: 'Cada mañana revisamos tu stock y te avisamos de lo que se va a acabar. El correo ya lo tenemos; el WhatsApp lo pones tú aquí.\n\nEl número va en formato internacional: signo de más, código de país y el número, todo pegado — +50688887777. Sin espacios, sin guiones, sin paréntesis y sin el 00 de antes. Si lo escribes como lo marcas en el teléfono, el botón no lo acepta.\n\nPedimos un código antes de darlo por bueno porque un dígito de más manda tus alertas de inventario —con nombres de productos y cantidades— al teléfono de un desconocido.',
      wacode_title: 'El código llega por WhatsApp, no por correo',
      wacode_body: 'Al darle a "Enviar código" te escribimos por WhatsApp al número que pusiste, con seis dígitos. Los escribes en la casilla y confirmas. Reenviar espera un minuto, así que revisa el chat antes de insistir.\n\nSi no llega, casi siempre es el código de país: vuelve atrás y revísalo.\n\nUna vez verificado, la tarjeta muestra el número con su sello. "Cambiar número" repite el trámite con otro teléfono; "Desvincular" lo quita y las alertas siguen llegando a tu correo, que nunca se apaga.',
      dmsms_title: 'Que no se te pierda un mensaje del equipo',
      dmsms_body: 'Esto no es del inventario: es para Mensajes, el chat interno. Con el interruptor encendido, si alguien te escribe y no estás dentro de Faro en ese momento, te mandamos un aviso corto al número de arriba — por WhatsApp, o por SMS si WhatsApp no está disponible.\n\nNecesita un número vinculado. Sin él el interruptor aparece apagado y no se deja mover.\n\nApagarlo no pierde nada: los mensajes te esperan igual dentro de la app. Es solo para enterarte estando en la bodega.',
      security_title: 'Cambiar tu contraseña',
      security_body: '"Cambiar contraseña" abre un campo para la nueva. Mínimo ocho caracteres, y el ojo de la derecha te deja verla antes de mandarla.\n\nQue sea larga y tuya: "bodega-lluvia-42" aguanta mucho más que "Faro2026", lo primero que probaría cualquiera que conozca la empresa.',
      pwcode_title: 'Y un código a tu correo',
      pwcode_body: 'Escribirla no basta: te mandamos seis dígitos al correo y hay que confirmarlos ahí mismo, antes de que caduquen.\n\nEs a propósito. En una bodega la computadora queda abierta y cualquiera se sienta frente a tu sesión; quien no tenga también tu correo no puede quedarse con tu cuenta.',
      activity_title: 'Tu rastro, no el del equipo',
      activity_body: 'Esta lista es sólo tuya: lo que hiciste tú y lo que la app hizo por ti. No es la bitácora de la empresa — lo de tus compañeros no sale aquí, ni lo tuyo en la de ellos.\n\nEl botón de arriba a la derecha filtra por tipo de acción: ingresos, cambios de contraseña, alertas enviadas. "Cargar más" trae quince registros más.\n\nSirve para dos preguntas muy concretas: ¿salió de verdad la alerta de esta mañana, y por cuál canal? y ¿ese ingreso fui yo? Si ves algo que no reconoces, cambia la contraseña arriba.',
      models_title: 'Lo que la plataforma sabe entrenar',
      models_body: 'Es un catálogo, no un ajuste: aquí no se enciende ni se apaga nada y no hay nada que llenar. Los modelos se eligen sesión por sesión en el asistente de entrenamiento.\n\nCada tarjeta trae su familia —machine learning, estadístico, deep learning— y una etiqueta. "Disponible" es de fiar. "Beta" todavía cambia: si uno de ésos gana la comparación, revisa el resultado antes de comprar apoyado en él.',
    },
    en: {
      name: 'Your account, your alerts and your trail',
      intro_title: 'Almost everything here is yours, not the company\'s',
      intro_body: 'Your name, your language, your theme, your phone, your password: what you change on this screen affects you and nobody else on your team.\n\nTwo things are readable but not editable: your role and your account status. An administrator moves those from Users, so nobody promotes themselves.',
      profile_title: 'Your name is what your team sees',
      profile_body: 'The pencil beside the name makes it editable; Enter saves. Write it the way people call you at work — "Ana Rojas", not "arojas" or "Admin": it is what shows up in the user list, in team messages and in the history further down.\n\nThe email is readable and not editable: it is your login. If it has to change, an administrator does it from Users, and the account then waits for the new address to be verified.\n\nRole and account status are read-only for the same reason.',
      appearance_title: 'Language and theme, saved to your account',
      appearance_body: 'Español or English switches the whole interface at once: menus, buttons, the names of the states. It is yours alone — a colleague can run the other language without anything changing for you.\n\nDark or Light is pure preference and touches no data. Window and glare on the screen, go Light; warehouse or night shift, Dark.\n\nBoth are saved to your account, not to this browser: log in from another computer and the app looks the same.',
      whatsapp_title: 'Where your alerts arrive',
      whatsapp_body: 'Every morning we check your stock and warn you about what is about to run out. We already have your email; WhatsApp is the one you add here.\n\nThe number goes in international format: plus sign, country code and the number, all joined up — +50688887777. No spaces, no dashes, no brackets and no leading 00. Typed the way you dial it on your phone, the button will not take it.\n\nWe ask for a code before accepting it because one digit too many sends your inventory alerts — product names and quantities included — to a stranger\'s phone.',
      wacode_title: 'The code arrives on WhatsApp, not by email',
      wacode_body: 'When you press "Send code" we message you on WhatsApp at the number you typed, with six digits. You type them into the box and confirm. Resending waits a minute, so check the chat before insisting.\n\nIf nothing arrives it is almost always the country code: go back and check it.\n\nOnce verified, the card shows the number with its badge. "Change number" repeats the whole thing with another phone; "Unlink" removes it and the alerts keep arriving by email, which never switches off.',
      dmsms_title: 'So a message from your team does not get lost',
      dmsms_body: 'This one is not about inventory: it is for Messages, the internal chat. With the switch on, if someone writes to you while you are not inside Faro, we send a short heads-up to the number above — over WhatsApp, or by SMS if WhatsApp is unavailable.\n\nIt needs a linked number. Without one the switch shows off and will not move.\n\nTurning it off loses nothing: the messages wait for you inside the app anyway. It is only for finding out while you are out in the warehouse.',
      security_title: 'Changing your password',
      security_body: '"Change password" opens a field for the new one. Eight characters minimum, and the eye on the right lets you read it back before sending.\n\nMake it long and yours: "warehouse-rain-42" holds up far better than "Faro2026", the first thing anyone who knows the company would try.',
      pwcode_title: 'And a code to your email',
      pwcode_body: 'Typing it is not enough: we send six digits to your email and they have to be confirmed right there, before they expire.\n\nThat is deliberate. In a warehouse the computer stays unlocked and whoever walks past sits down in front of your session; someone without your email too cannot take over your account.',
      activity_title: 'Your trail, not the team\'s',
      activity_body: 'This list is only yours: what you did and what the app did on your behalf. It is not a company audit log — your colleagues\' activity is not here, and yours is not in theirs.\n\nThe button at the top right filters by kind of action: sign-ins, password changes, alerts sent. "Load more" brings fifteen further records.\n\nIt answers two very concrete questions: did this morning\'s alert actually go out, and through which channel? and was that sign-in me? If you see something you do not recognise, change the password above.',
      models_title: 'What the platform knows how to train',
      models_body: 'This is a catalogue, not a setting: nothing is switched on or off here and there is nothing to fill in. Models are picked session by session in the training wizard.\n\nEach card carries its family — machine learning, statistical, deep learning — and a label. "Available" can be trusted. "Beta" is still changing: if one of those wins the comparison, check the result before buying on the strength of it.',
    },
  },
}
