import type { TourDefinition } from '../types'

export const integrationsTour: TourDefinition = {
  id: 'integrations-v1',
  route: '/integraciones',
  name: 'name',
  steps: [
    { title: 'intro_title', body: 'intro_body' },
    { anchor: 'int.provider', title: 'provider_title', body: 'provider_body' },
    { anchor: 'int.credentials', title: 'credentials_title', body: 'credentials_body' },
    { anchor: 'int.connect', title: 'connect_title', body: 'connect_body' },
    { title: 'live_title', body: 'live_body' },
  ],
  copy: {
    es: {
      name: 'Cómo conectar tu sistema contable',
      intro_title: 'Que los datos lleguen solos',
      intro_body: 'Sin conexión, cada actualización depende de que alguien exporte un archivo y lo suba. Eso se olvida, y un semáforo alimentado con ventas de hace tres semanas te recomienda comprar lo que ya dejó de venderse.\n\nConectar tu sistema contable quita ese paso manual: Faro va por el catálogo, las existencias y el historial de ventas por su cuenta. Es una función del plan Enterprise.',
      provider_title: 'Una conexión por sistema, para toda la empresa',
      provider_body: 'Hoy Faro se conecta con Alegra y con Siigo. La conexión no es tuya, es de la cuenta: se hace una vez y todo el equipo ve aquí su estado.\n\nConectar y desconectar queda sólo en manos de un administrador, y para conectar hace falta además tener el correo verificado. Son credenciales de un sistema externo, no una preferencia personal.',
      credentials_title: 'Qué se pega aquí',
      credentials_body: 'Cada proveedor pide lo suyo: Alegra, tu correo y un token de API; Siigo, el ID de socio, el usuario y la clave de acceso. Son credenciales de API — no la contraseña con la que entras a su web.\n\nSe guardan cifradas y no vuelven a salir: esta pantalla no puede mostrártelas después, ni a ti. Si las pierdes, se generan de nuevo del lado del proveedor.',
      connect_title: 'Conectar no es traer los datos',
      connect_body: 'Al conectar sólo comprobamos que las credenciales funcionen. Si están mal, te lo decimos aquí mismo y no se guarda nada.\n\nLa traída ocurre después, y no es poca cosa: baja el catálogo, las existencias y todo tu historial de ventas, escribe el stock y deja encolado un cálculo nuevo. Por eso cada sincronización te agrega una fila más en el historial de actualizaciones.',
      live_title: 'Cuando ya está andando',
      live_body: 'A partir de ahí sincroniza sola una vez al día, y el botón de la tarjeta fuerza una pasada cuando no quieres esperar.\n\nLa tarjeta te dice cuándo fue la última y, si algo falló, el error que devolvió el proveedor tal cual. Un token vencido se ve exactamente así: la conexión queda en error y el stock deja de refrescarse hasta que la arregles.\n\nDesconectar borra las credenciales guardadas y detiene la sincronización; volver a conectar es pegarlas de nuevo.',
    },
    en: {
      name: 'How to connect your accounting system',
      intro_title: 'Let the data arrive on its own',
      intro_body: 'Without a connection, every update depends on someone exporting a file and uploading it. That gets forgotten, and a stock signal fed with three-week-old sales recommends buying what already stopped selling.\n\nConnecting your accounting system removes that manual step: Faro fetches the catalogue, the stock on hand and the sales history by itself. It is an Enterprise plan feature.',
      provider_title: 'One connection per system, for the whole company',
      provider_body: 'Today Faro connects to Alegra and to Siigo. The connection is not yours, it belongs to the account: it is set up once and the whole team sees its state here.\n\nConnecting and disconnecting are admin-only, and connecting also requires a verified email address. These are credentials to an external system, not a personal preference.',
      credentials_title: 'What goes in here',
      credentials_body: 'Each provider asks for its own: Alegra, your email and an API token; Siigo, the partner ID, the username and the access key. These are API credentials — not the password you use to log into their website.\n\nThey are stored encrypted and never come back out: this screen cannot show them to you afterwards, not even to you. If you lose them, you generate new ones on the provider side.',
      connect_title: 'Connecting is not fetching',
      connect_body: 'Connecting only checks that the credentials work. If they are wrong we tell you right here and nothing is saved.\n\nThe fetch happens afterwards, and it is not a small thing: it pulls the catalogue, the stock on hand and your entire sales history, writes the stock in, and queues a fresh calculation. That is why every sync adds one more row to your update history.',
      live_title: 'Once it is running',
      live_body: 'From then on it syncs by itself once a day, and the button on the card forces a pass when you do not want to wait.\n\nThe card tells you when the last one was and, if something failed, the provider\'s own error verbatim. An expired token looks exactly like that: the connection sits in error and stock stops refreshing until you fix it.\n\nDisconnecting deletes the stored credentials and stops the syncing; reconnecting means pasting them again.',
    },
  },
}
