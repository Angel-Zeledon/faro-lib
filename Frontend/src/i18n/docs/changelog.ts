// Help center — "Novedades" / "What's new". User-facing release notes built
// from CHANGELOG.md ("Unreleased") and the commit log; one release block per
// commit date, newest first. Dates are the commit dates, not guesses.
import type { DocSectionContent } from '@/i18n/docs/types'

export const CHANGELOG: DocSectionContent<'novedades'> = {
  es: {
    novedades: {
      title: 'Novedades',
      description:
        'Lo que cambió en StockAI, de lo más reciente a lo más antiguo, contado desde lo que ves en pantalla.',
      blocks: [
        { t: 'p', text: 'Aquí anotamos los cambios que notas al usar StockAI: pantallas nuevas, reglas que cambiaron y errores corregidos que movían tus números. Cada bloque lleva la fecha en que el cambio entró al producto.' },
        { t: 'release', date: '2026-10-02', title: 'Ingreso con Google, Apple y Facebook, y documentos legales', items: [
          '**Ingreso con Google, Apple o Facebook**, opcional: aparece solo en las instalaciones que lo activan. Si ya tienes cuenta con el mismo correo, se vincula a ella. Ver [Crear tu cuenta](/docs/primeros-pasos/crear-tu-cuenta#social-login).',
          '**Términos del servicio, Política de privacidad, Cookies y Aviso legal**, publicados y enlazados desde la página principal y la app. Al crear una cuenta ahora hay que aceptar los términos y la privacidad.',
          '**Un estante vacío sin demanda ya no es Sobrestock.** Un producto sin stock y sin ventas previstas ahora sale como OK.',
          '**Referencia de la API** rediseñada: cada endpoint con su barra de petición y ejemplos en cURL, JavaScript y Python, en [Desarrolladores](/desarrolladores).',
          'Abrir Mi cuenta ya no devuelve el tema y el idioma a los que tenías antes; el registro de actividad de Mi cuenta se muestra traducido.',
        ] },
        { t: 'release', date: '2026-10-01', title: 'StockAI en el celular, cuenta de prueba y reglas del semáforo', items: [
          '**StockAI funciona como app en el celular.** Barra de pestañas abajo (Panel, Pedidos, Inventario, Asistente y Más) y todas las pantallas rehechas para el teléfono: compras, pedidos, inventario, proveedores, ventas, fuentes de datos, análisis y cuenta. Ver [Instalar la app](/docs/primeros-pasos/instalar-la-app).',
          '**Se instala desde el navegador** con el botón «Instalar app» (en iPhone, desde Compartir → «Agregar a inicio»), y se abre directo en el Panel de compras.',
          '**Cuenta de prueba de 24 horas sin registro** desde la página principal, con datos de ejemplo ya analizados. Ver [Cuenta de prueba](/docs/primeros-pasos/crear-tu-cuenta#trial-account).',
          '**Los cortes del semáforo se pueden cambiar** en Configurar inventario, «Reglas del semáforo»: cuándo un producto pasa a Pedir YA y desde cuándo es Sobrestock, para toda la empresa, una categoría o un proveedor, con una vista previa de cuántos productos cambian. Ver [El semáforo](/docs/conceptos/semaforo).',
          '**Las órdenes de compra se pueden marcar como pagadas** y **cancelar** (y reabrir). Una orden cancelada deja de contar como en camino. Ver [Pedidos](/docs/uso-diario/pedidos).',
          '**Un pedido es una sola orden.** Si guardas dos veces el mismo pedido (por ejemplo, porque falló la conexión), StockAI te devuelve la orden ya guardada en vez de crear otra. Y toda orden abierta cuenta como en camino, la hayas enviado desde StockAI o no.',
          '**Un asistente para la web y WhatsApp.** El Asistente IA responde con los datos de tu cuenta — qué pedir, qué está atrasado, cómo vienen tus proveedores — y solo lee: no crea ni cambia nada. Ver [Asistente IA](/docs/asistente/asistente-ia).',
          '**Todo lo que haces en la app se puede hacer por la API** con una llave, con su consumo medido. Ver [API](/docs/integraciones/api).',
          'Correcciones en los cálculos: el plan cubre hasta que llega el pedido siguiente; un traslado entre bodegas mueve lo que falta y no un múltiplo de la compra mínima; un producto que no vendió ya no deja en cero la precisión de toda la actualización; el panel «Ver por qué», el desglose y el simulador muestran cifras que suman; el resumen mensual avisa cuando el total de compras solo cubre las líneas con costo.',
        ] },
        { t: 'release', date: '2026-09-30', title: 'Ahora somos StockAI, y pronósticos más exactos', items: [
          '**El producto se llama StockAI** en todas las pantallas, correos, mensajes de WhatsApp y manuales.',
          '**Servidor MCP de solo lectura**: un asistente de IA compatible (por ejemplo Claude) puede consultar tu cuenta con una llave de API y preguntar qué comprar hoy. Ver [MCP](/docs/integraciones/mcp).',
          '**Deshacer en Pedidos:** «Deshacer recepción» devuelve las unidades y quita lo que esa recepción enseñó sobre el proveedor; «Deshacer envío» marca la orden como no enviada.',
          '**Vista comprador y vista técnica** en Pronósticos: la vista comprador muestra solo lo que hace falta para decidir; la técnica suma los modelos, sus métricas y la calidad de los datos. Ver [Pronósticos](/docs/analisis/pronosticos).',
          '**Nuevas vistas en Inventario:** Plata parada, Pronóstico en plata, Costos al alza, Margen que se achica y Costo de ignorar.',
          '**La variabilidad del tiempo de entrega de cada proveedor ahora cuenta** en el stock de seguridad. Antes se pedía en la ficha del proveedor y no se usaba. Con proveedores irregulares, el colchón y la cantidad sugerida suben. Ver [Stock de seguridad](/docs/conceptos/stock-de-seguridad).',
          '**Pronósticos más exactos:** el modelo que pronostica ahora aprende de todo tu historial (antes algunos se quedaban con el 80 %); los productos de venta intermitente ya no se pronostican de más; y el modelo de temporadas ya no inventa un ciclo anual cuando tienes menos de un año de historia.',
          'Cuando un producto de venta intermitente no puede sostener el nivel de servicio pedido, el panel lo dice junto al porcentaje.',
          'El país de feriados viene ahora en Costa Rica por defecto al subir ventas.',
          '**Se retiraron las integraciones con Alegra y Siigo** y la pantalla Integraciones: nunca se habían probado con una cuenta real. Tus datos siguen entrando como archivo, por una fuente SQL o por la API.',
        ] },
        { t: 'release', date: '2026-09-16', title: '«Qué ha pasado» y una importación que pregunta', items: [
          '**Nueva pantalla «Qué ha pasado»** con todo lo que StockAI hizo en tu cuenta y por qué: entrenamientos, órdenes generadas, enviadas o no enviadas, stock importado, mermas, traslados, topes del plan, usuarios y llaves de API. Lo crítico y lo que pide atención llega también a la campana. Ver [Qué ha pasado](/docs/analisis/actividad).',
          '**La importación de inventario pregunta** si `1.250` es mil doscientos cincuenta o uno coma veinticinco, en vez de adivinar; muestra las filas leídas antes de guardar, y ofrece «No sobrescribir lo que corregí a mano».',
        ] },
      ],
    },
  },
  en: {
    novedades: {
      title: "What's new",
      description:
        'What changed in StockAI, newest first, described from what you see on screen.',
      blocks: [
        { t: 'p', text: 'Here we note the changes you notice when using StockAI: new screens, rules that changed and fixed defects that moved your numbers. Each block carries the date the change reached the product.' },
        { t: 'release', date: '2026-10-02', title: 'Sign-in with Google, Apple and Facebook, and legal documents', items: [
          '**Sign-in with Google, Apple or Facebook**, optional: it only appears on installations that switch it on. If you already have an account with the same email, it is linked to it. See [Create your account](/docs/primeros-pasos/crear-tu-cuenta#social-login).',
          '**Terms of Service, Privacy Policy, Cookies and Legal notice**, published and linked from the home page and the app. Creating an account now requires accepting the terms and the privacy policy.',
          '**An empty shelf with no demand is no longer Overstock.** A product with no stock and no forecast sales now shows as OK.',
          '**API reference** redesigned: every endpoint with its request bar and examples in cURL, JavaScript and Python, under [Developers](/desarrolladores).',
          'Opening My account no longer reverts your theme and language; the activity log on My account is shown translated.',
        ] },
        { t: 'release', date: '2026-10-01', title: 'StockAI on your phone, trial accounts and stock signal rules', items: [
          '**StockAI works as an app on your phone.** A tab bar at the bottom (Panel, Orders, Inventory, Assistant and More), and every screen rebuilt for the phone: purchasing, orders, inventory, suppliers, sales, data sources, analysis and account. See [Install the app](/docs/primeros-pasos/instalar-la-app).',
          '**It installs from the browser** with the "Install app" button (on iPhone, via Share → "Add to Home Screen"), and opens straight on the Purchasing Panel.',
          '**24-hour trial account with no sign-up** from the home page, with sample data already analysed. See [Trial account](/docs/primeros-pasos/crear-tu-cuenta#trial-account).',
          '**The stock signal cut-offs can be changed** under Set up inventory, "Stock signal rules": when a product turns Order NOW and from when it is Overstock, for the whole company, a category or a supplier, with a preview of how many products change. See [The stock signal](/docs/conceptos/semaforo).',
          '**Purchase orders can be marked as paid** and **cancelled** (and reopened). A cancelled order no longer counts as on its way. See [Orders](/docs/uso-diario/pedidos).',
          '**One cart is one order.** If you save the same cart twice (for example because the connection dropped), StockAI returns the order already saved instead of creating another. And every open order counts as on its way, whether or not you sent it from StockAI.',
          '**One assistant for the web and WhatsApp.** The AI assistant answers from your account\'s data — what to order, what is late, how your suppliers are doing — and only reads: it creates and changes nothing. See [AI assistant](/docs/asistente/asistente-ia).',
          '**Everything you do in the app can be done through the API** with a key, with its usage metered. See [API](/docs/integraciones/api).',
          'Calculation fixes: the plan covers until the next order arrives; a transfer between warehouses moves what is missing, not a multiple of the minimum order; a product that did not sell no longer zeroes the accuracy of the whole update; the "Why?" panel, the breakdown and the simulator show figures that add up; the monthly recap warns when the purchase total only covers the lines with a cost.',
        ] },
        { t: 'release', date: '2026-09-30', title: 'We are now StockAI, and more accurate forecasts', items: [
          '**The product is called StockAI** on every screen, email, WhatsApp message and manual.',
          '**Read-only MCP server**: a compatible AI assistant (Claude, for example) can query your account with an API key and ask what to buy today. See [MCP](/docs/integraciones/mcp).',
          '**Undo under Orders:** "Undo reception" takes the units back out and removes what that reception taught about the supplier; "Undo send" marks the order as not sent.',
          '**Buyer view and technical view** on Forecasts: the buyer view shows only what you need to decide; the technical one adds the models, their metrics and the data quality. See [Forecasts](/docs/analisis/pronosticos).',
          '**New Inventory views:** dead capital, forecast in money, rising costs, shrinking margin and the cost of ignoring a recommendation.',
          '**Each supplier\'s lead-time variability now counts** in the safety stock. It used to be asked for on the supplier card and never used. With irregular suppliers, the cushion and the suggested quantity go up. See [Safety stock](/docs/conceptos/stock-de-seguridad).',
          '**More accurate forecasts:** the forecasting model now learns from your whole history (some used to stop at 80% of it); slow, intermittent products are no longer over-forecast; and the seasonal model no longer invents a yearly cycle when you have less than a year of history.',
          'When an intermittent product cannot hold the requested service level, the panel says so next to the percentage.',
          'The holiday country now starts on Costa Rica when you upload sales.',
          '**The Alegra and Siigo integrations were retired**, along with the Integrations screen: they had never been tested against a real account. Your data still comes in as a file, through an SQL source or through the API.',
        ] },
        { t: 'release', date: '2026-09-16', title: '"What happened" and an import that asks', items: [
          '**New "What happened" screen** with everything StockAI did on your account and why: trainings, orders generated, sent or not sent, stock imported, shrinkage, transfers, plan ceilings, users and API keys. Anything critical or needing attention also reaches the bell. See [What happened](/docs/analisis/actividad).',
          '**The inventory import asks** whether `1.250` is one thousand two hundred fifty or one point twenty-five instead of guessing; it shows the rows it read before saving, and offers "Do not overwrite what I corrected by hand".',
        ] },
      ],
    },
  },
}
