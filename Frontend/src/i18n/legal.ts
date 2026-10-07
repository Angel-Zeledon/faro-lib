/**
 * StockAI's legal documents, in both languages: Terms of Service, Privacy
 * Policy, Cookies and local storage, and the Legal Notice (/terminos,
 * /privacidad, /cookies, /aviso-legal). The six added later (acceptable use,
 * DPA, AI, vulnerability disclosure, accessibility, commercial conditions)
 * live in ./legalExtra.ts under the same rules, and the /legal hub's copy is
 * `hub` below.
 *
 * DRAFT — TO BE REVIEWED BY A LAWYER BEFORE IT IS RELIED ON. Written by the
 * product team on 2026-10-02 from what the code does today. It is not legal
 * advice and has not been checked against Costa Rica's Ley 8968, the GDPR /
 * LOPDGDD or the LSSI-CE by anyone qualified to do so. Every value in square
 * brackets — `[RAZÓN SOCIAL]`, `[UBICACIÓN DEL SERVIDOR — …]` and the rest — is
 * a placeholder the owner has to fill in; they are deliberately visible on the
 * page so an unfinished document cannot pass for a finished one.
 *
 * Why a typed file and not `translations.ts`: same reason as `landing.ts`.
 * These are paragraphs, and the shape is a TYPE, so a section missing from one
 * language is a compile error rather than a raw key on screen.
 *
 * The rule for every sentence: it must be TRUE of the code at the date in
 * `updated`. The file that backs each factual claim is noted beside it. When
 * the product changes what it stores, sends or keeps, this file changes in the
 * same commit — and so does `TERMS_VERSION` in backend/users/terms.py when the
 * change is one users must accept again.
 *
 * Inline markup in paragraph and list strings, rendered by Legal.tsx:
 *   [label](href)  a link (internal path, `#anchor` or `mailto:`)
 *   **text**       emphasis
 */
import type { Lang } from './translations'
import type { LegalGroup, LegalKey } from '@/components/landing/legalPaths'
import { LEGAL_EXTRA_EN, LEGAL_EXTRA_ES } from './legalExtra'

export type LegalBlock =
  | string
  | { list: string[] }
  | { table: { head: string[]; rows: string[][] } }

export interface LegalSection { id: string; title: string; blocks: LegalBlock[] }

export interface LegalDoc {
  // Name in menus, footers and the breadcrumb.
  label: string
  // The H1, and the server-rendered <title>.
  title: string
  // Search description and the paragraph under the H1.
  intro: string
  // "In short": the four or five things that matter most, in plain words.
  summary: string[]
  sections: LegalSection[]
}

export interface LegalCopy {
  updated: string
  summaryTitle: string
  toc: string
  otherDocs: string
  // Footer column heading on the landing.
  footerHead: string
  questions: string
  // The /legal page that lists every document.
  hub: {
    label: string
    title: string
    intro: string
    // Footer and app link to the hub.
    allLink: string
    groups: Record<LegalGroup, string>
  }
  docs: Record<LegalKey, LegalDoc>
}

const MAIL = '[contacto@stockai.es](mailto:contacto@stockai.es)'

// ─────────────────────────────────────────────────────────────────────────────
// Español
// ─────────────────────────────────────────────────────────────────────────────

const es: LegalCopy = {
  updated: 'Última actualización: 2 de octubre de 2026',
  summaryTitle: 'En corto',
  toc: 'Contenido',
  otherDocs: 'Otros documentos legales',
  footerHead: 'Legal',
  questions: `¿Dudas sobre este documento? Escríbenos a ${MAIL}.`,
  hub: {
    label: 'Legal',
    title: 'Documentos legales',
    intro: 'Todo lo que rige el uso de StockAI en un solo lugar: términos, privacidad, uso aceptable, inteligencia artificial, condiciones para empresas y seguridad.',
    allLink: 'Todos los documentos',
    groups: {
      core: 'Lo básico',
      use: 'Cómo se usa StockAI',
      business: 'Para empresas clientes',
      security: 'Seguridad',
    },
  },
  docs: {
    ...LEGAL_EXTRA_ES,
    // ── Términos ────────────────────────────────────────────────────────────
    terms: {
      label: 'Términos del servicio',
      title: 'Términos y condiciones del servicio',
      intro: 'Las reglas para usar StockAI: qué te damos, qué te pedimos, cómo se compra el código fuente y hasta dónde llega nuestra responsabilidad.',
      summary: [
        'StockAI te ayuda a decidir qué comprar. **La decisión de compra es tuya**, y también sus consecuencias.',
        'StockAI se vende únicamente como código fuente, por USD 14.999 en un pago único, para instalarlo en tu propia infraestructura. Los detalles de la entrega se acuerdan escribiéndonos.',
        'Tus datos son tuyos. Solo los usamos para darte el servicio.',
        'No hay un acuerdo de disponibilidad (SLA) salvo que lo firmemos por separado.',
      ],
      sections: [
        {
          id: 'acceptance',
          title: 'Quiénes somos y cómo aceptas estos términos',
          blocks: [
            'StockAI es un servicio de [RAZÓN SOCIAL] (en adelante, «StockAI», «nosotros»). Los datos que nos identifican están en el [aviso legal](/aviso-legal).',
            'Aceptas estos términos al crear una cuenta (marcando la casilla del formulario) o al entrar a una cuenta de prueba. Guardamos la fecha y la versión de los términos que aceptaste.',
            'Si creas la cuenta en nombre de una empresa, confirmas que puedes obligarla, y estos términos rigen entre esa empresa y nosotros.',
          ],
        },
        {
          id: 'service',
          title: 'El servicio',
          blocks: [
            'StockAI es una aplicación web para decidir compras de inventario. A partir del historial de ventas que subes, entrena modelos de pronóstico para cada producto, calcula un semáforo de stock (PEDIR YA, PEDIR PRONTO, OK, SOBRESTOCK), propone cantidades y proveedores, genera órdenes de compra y registra recepciones.',
            'El servicio cambia con el tiempo: podemos agregar, cambiar o retirar funciones. Si retiramos algo importante que usas, te avisamos con antelación razonable.',
          ],
        },
        {
          id: 'accounts',
          title: 'Tu cuenta',
          blocks: [
            {
              list: [
                'StockAI es para uso profesional. Para crear una cuenta debes ser mayor de edad.',
                'Danos datos reales y mantenlos al día: los usamos para escribirte sobre tu cuenta.',
                'Cuida tu contraseña. Lo que se haga con tu usuario es responsabilidad de tu cuenta, salvo que nos avises de un acceso indebido.',
                'El administrador de una cuenta decide a quién invita y con qué rol (administrador, analista o lector), y responde por el uso que hagan esas personas.',
              ],
            },
          ],
        },
        {
          id: 'plans',
          title: 'Compra del código fuente',
          blocks: [
            'StockAI se vende únicamente como código fuente: el código fuente completo, para instalarlo en tu propia infraestructura y operarlo como tuyo. El precio es de **USD 14.999**, en un pago único.',
            'Escríbenos y cerramos la compra y los detalles de la entrega contigo.',
            '[LICENCIA, ENTREGA, SOPORTE Y REEMBOLSO DE LA COMPRA — confirmar con el propietario]',
          ],
        },
        {
          id: 'trial',
          title: 'Cuenta de prueba',
          blocks: [
            'Desde [la página de prueba](/prueba) puedes entrar sin registrarte a una cuenta temporal con datos de ejemplo. Dura **24 horas** y después se borra con todo lo que tenga. Si mientras tanto nos pediste hablar, la conservamos en solo lectura hasta que te respondamos, y luego se borra.',
            'La contraseña de la prueba se muestra una sola vez. No subas a una cuenta de prueba datos que no quieras perder.',
          ],
        },
        {
          id: 'acceptable-use',
          title: 'Uso aceptable',
          blocks: [
            'No puedes usar StockAI para:',
            {
              list: [
                'subir datos que no tengas derecho a usar, o datos personales de terceros sin una base legal para tratarlos;',
                'intentar entrar a cuentas o datos que no son tuyos, o probar la seguridad del servicio sin nuestro permiso escrito;',
                'saturar el servicio, saltarte sus límites o automatizar la creación de cuentas;',
                'enviar a tus proveedores o contactos, a través de StockAI, mensajes engañosos o no solicitados;',
                'revender el servicio o copiarlo para construir uno que compita con él.',
              ],
            },
          ],
        },
        {
          id: 'api',
          title: 'La API',
          blocks: [
            'Un administrador puede crear claves de API (empiezan por `sk_live_`) de lectura o de escritura. La clave completa se muestra una sola vez; nosotros guardamos solo una huella de ella. Guárdala como una contraseña: lo que se haga con tu clave cuenta como hecho por tu cuenta.',
            {
              list: [
                'Cada clave puede hacer hasta 120 llamadas por minuto.',
                'Contamos las llamadas que llegan a un endpoint, por día y por clave; las rechazadas no cuentan.',
                'El servidor MCP para asistentes de IA solo lee: no puede crear, cambiar ni borrar nada.',
                'Podemos revocar una clave que se use contra estos términos o que ponga en riesgo el servicio.',
              ],
            },
          ],
        },
        {
          id: 'decisions',
          title: 'Pronósticos y recomendaciones: tú decides',
          blocks: [
            'Los pronósticos son estimaciones hechas con tu historial. Pueden fallar: la demanda cambia por razones que no están en tus datos, y un modelo que acertó ayer puede equivocarse mañana. El semáforo, las cantidades sugeridas, la elección de proveedor, las órdenes de compra y las respuestas del asistente de IA son **apoyo para decidir**, no instrucciones.',
            'Revisa cada orden antes de enviarla. **No garantizamos la exactitud de ningún pronóstico ni ningún resultado comercial**, y no respondemos por decisiones de compra, quiebres de stock, sobrestock, ventas perdidas o costos que resulten de usar o no usar las recomendaciones.',
          ],
        },
        {
          id: 'your-data',
          title: 'Tus datos',
          blocks: [
            'Los datos que subes son tuyos. Nos das permiso para guardarlos, procesarlos y mostrarlos solo en la medida necesaria para darte el servicio, como explica la [política de privacidad](/privacidad). No los vendemos ni los usamos para publicidad, y los modelos de tu cuenta se entrenan solo con los datos de tu cuenta.',
            'Si tus datos incluyen información de personas (por ejemplo, contactos de proveedores o nombres de clientes en tus ventas), tú eres responsable de tenerla legítimamente, y nosotros la tratamos por cuenta tuya y según tus instrucciones.',
          ],
        },
        {
          id: 'availability',
          title: 'Disponibilidad',
          blocks: [
            'Trabajamos para que StockAI esté disponible y respaldamos la base de datos todas las noches, pero **no ofrecemos un acuerdo de nivel de servicio (SLA)** salvo que lo firmemos por separado. Puede haber cortes por mantenimiento, fallas o causas fuera de nuestro control.',
            'Conserva tus archivos originales: StockAI no reemplaza tu sistema de registro contable o de ventas.',
          ],
        },
        {
          id: 'termination',
          title: 'Suspensión y terminación',
          blocks: [
            'Puedes dejar de usar StockAI cuando quieras y pedirnos que borremos tu cuenta.',
            'Podemos suspender o cerrar una cuenta si incumple estos términos, pone en riesgo el servicio o a otros clientes, o si la ley nos lo exige. Salvo urgencia, te avisamos antes y te damos oportunidad de corregirlo.',
          ],
        },
        {
          id: 'after',
          title: 'Tus datos al terminar',
          blocks: [
            'Antes de cerrar tu cuenta puedes pedirnos una copia completa de tus datos (un archivo ZIP con cada tabla de tu empresa). Al borrar la cuenta eliminamos todas sus tablas y archivos; las copias de seguridad que aún los contengan se eliminan solas en un plazo de 14 días.',
          ],
        },
        {
          id: 'ip',
          title: 'Propiedad intelectual',
          blocks: [
            'El software, la marca StockAI, el diseño y la documentación son nuestros o de nuestros licenciantes. Te damos un derecho de uso del servicio, no exclusivo e intransferible, mientras tu cuenta esté activa.',
            'Si nos envías sugerencias, podemos usarlas para mejorar el producto sin obligación hacia ti.',
          ],
        },
        {
          id: 'liability',
          title: 'Límite de responsabilidad',
          blocks: [
            'El servicio se ofrece «tal cual» y «según disponibilidad». En la medida en que la ley lo permita:',
            {
              list: [
                'no respondemos por daños indirectos, lucro cesante, pérdida de ventas o de datos, ni por decisiones comerciales tomadas con el servicio;',
                'nuestra responsabilidad total frente a ti queda limitada a [LÍMITE DE RESPONSABILIDAD — confirmar con el propietario].',
              ],
            },
            'Nada en estos términos limita derechos que la ley de tu país no permita limitar.',
          ],
        },
        {
          id: 'changes',
          title: 'Cambios a estos términos',
          blocks: [
            'Si cambiamos estos términos, actualizamos la fecha de arriba. Si el cambio es importante, te avisamos por correo o dentro de la aplicación antes de que entre en vigor. Seguir usando StockAI después de esa fecha significa que aceptas la nueva versión.',
          ],
        },
        {
          id: 'law',
          title: 'Ley aplicable',
          blocks: [
            'Estos términos se rigen por las leyes de [PAÍS — Costa Rica, confirmar con el propietario], y cualquier disputa se resolverá ante los tribunales de [CIUDAD — confirmar con el propietario], salvo que la ley de protección al consumidor de tu país te dé derecho a otro fuero.',
          ],
        },
        {
          id: 'contact',
          title: 'Contacto',
          blocks: [`Escríbenos a ${MAIL}.`],
        },
      ],
    },

    // ── Privacidad ──────────────────────────────────────────────────────────
    privacy: {
      label: 'Privacidad',
      title: 'Política de privacidad',
      intro: 'Qué datos guarda StockAI, para qué los usa, con quién los comparte, cuánto tiempo los conserva y cómo puedes verlos, llevártelos o borrarlos.',
      summary: [
        'Usamos tus datos solo para darte el servicio. **No los vendemos ni hacemos publicidad con ellos.** No hay rastreadores ni analítica en la web.',
        'Los pronósticos de tu cuenta se entrenan solo con los datos de tu cuenta. Ninguna otra empresa ve tus datos.',
        'Si usas las funciones de inteligencia artificial, parte de tus datos se envía a DeepSeek, un proveedor con sede en China.',
        `Puedes pedir una copia completa de tus datos o su borrado escribiendo a ${MAIL}.`,
      ],
      sections: [
        {
          id: 'controller',
          title: 'Quién es responsable de tus datos',
          blocks: [
            'El responsable es [RAZÓN SOCIAL], [CÉDULA JURÍDICA / NIF], con domicilio en [DIRECCIÓN]. Puedes escribirnos a ' + MAIL + '. Los datos completos están en el [aviso legal](/aviso-legal).',
            'Para los datos de **tu cuenta** (tu nombre, tu correo), somos responsables. Para los **datos de tu negocio** que subes y que mencionan a personas —por ejemplo, el contacto de un proveedor o el nombre de un cliente en tu historial de ventas— el responsable es tu empresa, y nosotros los tratamos por cuenta suya, solo para darle el servicio.',
          ],
        },
        {
          id: 'data',
          title: 'Qué datos guardamos',
          blocks: [
            {
              table: {
                head: ['Tipo', 'Qué incluye', 'De dónde sale'],
                rows: [
                  ['Cuenta', 'Nombre, correo, número de WhatsApp, nombre de la empresa, rol, fecha en que aceptaste los términos. La contraseña se guarda solo como hash bcrypt: nadie puede leerla.', 'Del formulario de registro, o de quien te invitó a la cuenta'],
                  ['Datos de tu negocio', 'Historial de ventas, inventario, productos, precios y costos, proveedores y sus contactos, órdenes de compra, recepciones, documentos que subas al asistente y tus conversaciones con él, y las conexiones a bases de datos que configures.', 'De ti: archivos, formularios, conexiones y la API'],
                  ['Actividad en la cuenta', 'Qué acción hizo cada usuario y cuándo (por ejemplo, generar una orden o subir un archivo). Puedes verla en Mi cuenta.', 'Del uso de la aplicación'],
                  ['Uso de la API', 'Número de llamadas por día y por clave. De cada clave guardamos solo una huella y sus últimos cuatro caracteres.', 'De las llamadas a la API'],
                  ['Datos técnicos', 'Dirección IP, fecha y hora, dirección pedida y errores, en los registros del servidor. Los intentos de inicio de sesión (por correo) y la creación de cuentas de prueba (por dirección IP) se cuentan para frenar abusos.', 'De tu navegador o integración, automáticamente'],
                ],
              },
            },
            '**Lo que no recogemos:** no usamos analítica, píxeles ni cookies de publicidad, y no cobramos con tarjeta dentro de la aplicación, así que no guardamos datos de pago. Más detalle en la [política de cookies](/cookies).',
          ],
        },
        {
          id: 'purposes',
          title: 'Para qué los usamos',
          blocks: [
            {
              table: {
                head: ['Para qué', 'Base legal'],
                rows: [
                  ['Darte el servicio: tu cuenta, los pronósticos, el semáforo, las órdenes de compra y la API.', 'Ejecución del contrato'],
                  ['Enviarte los correos del servicio (verificar tu correo, recuperar tu contraseña) y las alertas que tú actives por correo, WhatsApp o SMS.', 'Ejecución del contrato'],
                  ['Responderte cuando nos escribes.', 'Ejecución del contrato e interés legítimo'],
                  ['Proteger el servicio: límites de intentos, registros técnicos, detección de abusos.', 'Interés legítimo'],
                  ['Cumplir obligaciones legales y atender requerimientos de autoridades.', 'Obligación legal'],
                ],
              },
            },
            '**Los modelos se entrenan solo con los datos de tu cuenta.** Cuando StockAI entrena un pronóstico, usa el historial que subiste a tu cuenta y nada más; no mezclamos datos de distintas empresas ni los usamos para entrenar modelos de otros clientes.',
            'No vendemos tus datos, no los compartimos con anunciantes y no los usamos para enviarte publicidad.',
          ],
        },
        {
          id: 'ai',
          title: 'Funciones con inteligencia artificial',
          blocks: [
            'Algunas funciones usan un modelo de lenguaje de **DeepSeek**: el asistente (en la aplicación y por WhatsApp), el resumen de la mañana en el Panel de compras, la explicación de un pronóstico y el diagnóstico de calidad de un archivo. El resumen del Panel de compras se pide al abrir esa pantalla.',
            'Cuando se usan, enviamos a DeepSeek la pregunta y los datos de tu cuenta que hacen falta para responderla —por ejemplo, códigos y nombres de productos, cantidades, el estado del semáforo, proveedores y montos—. No enviamos contraseñas ni credenciales. DeepSeek tiene su sede en China y trata esos datos según sus propias condiciones.',
            `Hoy la inteligencia artificial no se puede apagar por cuenta desde la aplicación. Si no quieres que tus datos lleguen a DeepSeek, escríbenos a ${MAIL} antes de usar StockAI. Si el servicio no tiene configurado DeepSeek, no se envía nada y esas pantallas responden con texto calculado por reglas.`,
          ],
        },
        {
          id: 'processors',
          title: 'Con quién los compartimos (encargados)',
          blocks: [
            'Solo compartimos datos con los proveedores que necesitamos para operar StockAI, y solo lo necesario para su tarea:',
            {
              table: {
                head: ['Proveedor', 'Para qué', 'Qué datos', 'Dónde'],
                rows: [
                  ['Hostinger', 'Servidor (VPS) donde corren la aplicación y la base de datos, y correo de stockai.es', 'Todos los datos del servicio', '[UBICACIÓN DEL SERVIDOR — confirmar con el propietario]'],
                  ['DeepSeek', 'Funciones de inteligencia artificial', 'La pregunta y los datos necesarios para responderla', 'China'],
                  ['Proveedor de correo: Hostinger (SMTP) o Resend, según la configuración', 'Enviar correos del servicio y alertas', 'Tu dirección de correo y el contenido del mensaje', '[UBICACIÓN — confirmar con el propietario]'],
                  ['Twilio (solo si WhatsApp o SMS están activados)', 'Enviar y recibir mensajes de WhatsApp y SMS', 'Tu número y el contenido del mensaje', 'Estados Unidos'],
                ],
              },
            },
            'No usamos servicios de analítica ni redes de publicidad. Las tipografías de la web se sirven desde nuestro propio servidor, sin llamar a terceros.',
            'También podemos revelar datos si una ley o una autoridad competente nos lo exige.',
          ],
        },
        {
          id: 'transfers',
          title: 'Transferencias internacionales',
          blocks: [
            'Por los proveedores de arriba, tus datos pueden tratarse fuera de tu país: en [UBICACIÓN DEL SERVIDOR — confirmar con el propietario] (servidor), en China (DeepSeek, si usas las funciones de IA) y en Estados Unidos (Twilio, si activas WhatsApp o SMS).',
            'Si estás en la Unión Europea o en España, ten en cuenta que China no cuenta con una decisión de adecuación de la Comisión Europea. Por eso te contamos qué funciones envían datos a DeepSeek y cómo evitarlo.',
          ],
        },
        {
          id: 'retention',
          title: 'Cuánto tiempo los conservamos',
          blocks: [
            {
              table: {
                head: ['Datos', 'Plazo'],
                rows: [
                  ['Cuenta, datos del negocio y actividad', 'Mientras tu cuenta exista.'],
                  ['Cuenta de prueba', '24 horas desde que se crea; después se borra entera. Si nos pediste hablar, se conserva en solo lectura hasta que te respondamos.'],
                  ['Al borrar una cuenta', 'Se eliminan todas sus tablas y archivos de una vez.'],
                  ['Copias de seguridad', 'Se hacen cada noche y se guardan 14 días. Lo que borras desaparece también de las copias en ese plazo.'],
                  ['Conteo de intentos (inicio de sesión, cuentas de prueba)', 'Solo se usa dentro de su ventana: de minutos a 24 horas.'],
                  ['Registros técnicos del servidor', '[PLAZO DE LOS REGISTROS DEL SERVIDOR — confirmar con el propietario]'],
                ],
              },
            },
          ],
        },
        {
          id: 'security',
          title: 'Cómo los protegemos',
          blocks: [
            {
              list: [
                '**Cada empresa ve solo sus datos.** Cada consulta a la base de datos va filtrada por la cuenta de quien la hace.',
                'Permisos por rol: administrador, analista y lector. Solo un analista o un administrador puede cambiar datos.',
                'Las contraseñas se guardan con bcrypt y las claves de API como huella: ni nosotros podemos leerlas.',
                'Las credenciales de conexiones y servicios (por ejemplo, la contraseña de una base de datos que conectes) se guardan cifradas con Fernet.',
                'Las sesiones caducan a los 15 minutos y se renuevan solas mientras la pestaña esté abierta. Al cambiar tu contraseña se cierran las sesiones anteriores.',
                'La conexión con la aplicación va cifrada (HTTPS).',
              ],
            },
            'Ningún sistema es infalible. Si ocurre un incidente que afecte tus datos, te avisaremos y avisaremos a la autoridad cuando la ley lo exija.',
          ],
        },
        {
          id: 'rights',
          title: 'Tus derechos y cómo ejercerlos',
          blocks: [
            'Puedes pedirnos en cualquier momento:',
            {
              list: [
                '**Acceso:** saber qué datos tuyos tenemos.',
                '**Rectificación:** corregirlos. Tu nombre y tu número los puedes cambiar tú mismo en Mi cuenta.',
                '**Borrado:** eliminar tu cuenta y todos sus datos.',
                '**Portabilidad:** una copia completa de los datos de tu empresa en un archivo ZIP, con una tabla por archivo.',
                '**Oposición y limitación:** que dejemos de tratar tus datos para algún fin, o que lo limitemos.',
              ],
            },
            `Escríbenos a ${MAIL} desde el correo de tu cuenta. Hoy la exportación y el borrado los ejecuta un administrador de la cuenta con la API, o los hacemos nosotros cuando nos lo pides. Te respondemos en un plazo máximo de un mes, o antes si la ley de tu país lo exige.`,
            'Si te invitó una empresa, su administrador controla los datos del negocio: las solicitudes sobre esos datos las atendemos junto con él.',
            'Si no quedas conforme, puedes reclamar ante la autoridad de protección de datos de tu país: en Costa Rica, la Agencia de Protección de Datos de los Habitantes (PRODHAB); en España, la Agencia Española de Protección de Datos (AEPD).',
          ],
        },
        {
          id: 'frameworks',
          title: 'Leyes aplicables',
          blocks: [
            'Tratamos los datos conforme a la Ley 8968 de Costa Rica (Protección de la Persona frente al Tratamiento de sus Datos Personales) y, para usuarios en la Unión Europea o en España, al Reglamento General de Protección de Datos (RGPD) y la Ley Orgánica 3/2018 (LOPDGDD). No tenemos certificaciones ni auditorías de cumplimiento.',
          ],
        },
        {
          id: 'minors',
          title: 'Menores de edad',
          blocks: ['StockAI es un servicio para empresas. No está dirigido a menores de edad y no recogemos sus datos a sabiendas.'],
        },
        {
          id: 'changes',
          title: 'Cambios a esta política',
          blocks: ['Si cambiamos esta política, actualizamos la fecha de arriba. Si el cambio es importante, te avisamos por correo o dentro de la aplicación antes de que entre en vigor.'],
        },
      ],
    },

    // ── Cookies ─────────────────────────────────────────────────────────────
    cookies: {
      label: 'Cookies',
      title: 'Política de cookies y almacenamiento local',
      intro: 'StockAI no usa cookies de analítica ni de publicidad. Esto es exactamente lo que guarda en tu navegador, para qué y por cuánto tiempo.',
      summary: [
        '**La aplicación no crea cookies.** Lo que guarda va en el almacenamiento local de tu navegador.',
        'Todo lo que guarda es necesario para que funcione o recuerda una preferencia tuya, como el idioma o el tema.',
        'No hay rastreadores, analítica ni publicidad, propios ni de terceros. Por eso no te pedimos que aceptes nada: solo te informamos.',
      ],
      sections: [
        {
          id: 'what',
          title: 'Qué son las cookies y el almacenamiento local',
          blocks: [
            'Las cookies son pequeños archivos que un sitio guarda en tu navegador y que viajan con cada visita. El almacenamiento local (localStorage) y el de sesión (sessionStorage) son parecidos, pero se quedan en tu navegador: no se envían solos al servidor. El de sesión se borra al cerrar la pestaña.',
          ],
        },
        {
          id: 'inventory',
          title: 'Lo que guardamos',
          blocks: [
            '**Necesario:** sin esto la aplicación no puede funcionar. **Funcional:** recuerda una elección tuya; si lo borras, la aplicación vuelve a sus valores normales.',
            {
              table: {
                head: ['Nombre', 'Para qué', 'Tipo', 'Dónde y cuánto dura'],
                rows: [
                  ['fp_access_token', 'Tu sesión: demuestra al servidor que iniciaste sesión. Caduca a los 15 minutos y se renueva sola.', 'Necesario', 'localStorage, hasta que cierres sesión'],
                  ['fp_refresh_token', 'Renovar tu sesión sin pedirte la contraseña otra vez.', 'Necesario', 'sessionStorage, hasta que cierres la pestaña o la sesión'],
                  ['fp_user', 'Tu identificador, nombre, correo, rol y empresa, para mostrarlos en pantalla.', 'Necesario', 'localStorage, hasta que cierres sesión'],
                  ['stockai_trial_account', 'El usuario y la contraseña de tu cuenta de prueba, para que al recargar veas la misma cuenta.', 'Necesario', 'sessionStorage, hasta que cierres la pestaña'],
                  ['stockai_storage_notice', 'Recordar que ya viste el aviso sobre este almacenamiento.', 'Necesario', 'localStorage, hasta que lo borres'],
                  ['lang', 'El idioma (español o inglés).', 'Funcional', 'localStorage, hasta que lo borres'],
                  ['theme', 'El tema claro u oscuro.', 'Funcional', 'localStorage, hasta que lo borres'],
                  ['fp_sidebar_collapsed', 'Si la barra lateral está recogida.', 'Funcional', 'localStorage, hasta que lo borres'],
                  ['fp_tours_seen', 'Qué recorridos guiados ya viste, para no repetirlos.', 'Funcional', 'localStorage, hasta que lo borres'],
                  ['stockai.forecast_view', 'La vista que elegiste en la pantalla de pronósticos.', 'Funcional', 'localStorage, hasta que lo borres'],
                  ['stockai_intro_seen', 'Mostrar la introducción solo una vez por sesión.', 'Funcional', 'sessionStorage, hasta que cierres la pestaña'],
                  ['faro_verify_banner_dismissed', 'Que cerraste el aviso de verificar tu correo.', 'Funcional', 'sessionStorage, hasta que cierres la pestaña'],
                  ['fp_mobile_notice_dismissed', 'Que cerraste el aviso de pantalla pequeña.', 'Funcional', 'sessionStorage, hasta que cierres la pestaña'],
                  ['stockai-v1-static', 'Archivos de la aplicación y la página «sin conexión», para que la app instalada abra rápido. No contiene datos tuyos.', 'Funcional', 'Caché del navegador (service worker), solo dentro de la aplicación, hasta la próxima versión'],
                ],
              },
            },
            'Tu idioma y tu tema también se guardan en tu cuenta, para que te sigan en otro dispositivo.',
          ],
        },
        {
          id: 'third-parties',
          title: 'Terceros',
          blocks: [
            'Ninguno. La web y la aplicación no cargan scripts, píxeles ni tipografías de otros sitios: todo se sirve desde nuestro servidor, y la política de seguridad del navegador (CSP) bloquea cualquier conexión a otro dominio.',
          ],
        },
        {
          id: 'consent',
          title: 'Por qué no te pedimos que aceptes',
          blocks: [
            'Lo que guardamos es estrictamente necesario para darte el servicio que pediste o recuerda una preferencia que tú elegiste. Para eso la ley no exige consentimiento, solo que te informemos, y por eso ves un aviso la primera vez y no un botón de «aceptar todo».',
            'Si algún día agregamos analítica u otro almacenamiento que no sea necesario, te pediremos permiso antes de activarlo y actualizaremos esta página.',
          ],
        },
        {
          id: 'control',
          title: 'Cómo borrarlo',
          blocks: [
            'Al cerrar sesión se borran los datos de sesión. Puedes borrar todo lo demás desde la configuración de tu navegador (en «Privacidad» o «Datos de sitios»). Si borras lo necesario, tendrás que volver a iniciar sesión; si borras lo funcional, la aplicación volverá a su idioma y tema por defecto.',
          ],
        },
        {
          id: 'changes',
          title: 'Cambios',
          blocks: ['Si cambia lo que guardamos, actualizamos esta tabla y la fecha de arriba.'],
        },
      ],
    },

    // ── Aviso legal ─────────────────────────────────────────────────────────
    notice: {
      label: 'Aviso legal',
      title: 'Aviso legal',
      intro: 'Quién está detrás de StockAI y de stockai.es, y las condiciones para usar este sitio web.',
      summary: [
        'stockai.es es el sitio de StockAI, un servicio de [RAZÓN SOCIAL].',
        `Para cualquier asunto legal, escríbenos a ${MAIL}.`,
      ],
      sections: [
        {
          id: 'owner',
          title: 'Titular del sitio',
          blocks: [
            {
              table: {
                head: ['Dato', 'Valor'],
                rows: [
                  ['Titular', '[RAZÓN SOCIAL]'],
                  ['Identificación fiscal', '[CÉDULA JURÍDICA / NIF]'],
                  ['Domicilio', '[DIRECCIÓN]'],
                  ['Datos registrales', '[DATOS DE INSCRIPCIÓN EN EL REGISTRO, si aplica]'],
                  ['Correo', MAIL],
                  ['Dominio', 'stockai.es'],
                ],
              },
            },
          ],
        },
        {
          id: 'purpose',
          title: 'Objeto',
          blocks: [
            'Este sitio presenta StockAI y da acceso a la aplicación. El uso de la aplicación se rige por los [términos del servicio](/terminos) y el tratamiento de datos por la [política de privacidad](/privacidad).',
          ],
        },
        {
          id: 'use',
          title: 'Uso del sitio',
          blocks: [
            'Puedes navegar por el sitio libremente. Te pedimos que no lo uses para fines ilícitos, que no intentes dañarlo ni acceder a partes no públicas, y que no copies su contenido para presentarlo como propio.',
          ],
        },
        {
          id: 'ip',
          title: 'Propiedad intelectual',
          blocks: [
            'Los textos, imágenes, capturas, el diseño, el software y la marca StockAI son propiedad del titular o de sus licenciantes. Puedes citarlos o enlazarlos mencionando la fuente; para cualquier otro uso, pídenos permiso.',
          ],
        },
        {
          id: 'liability',
          title: 'Responsabilidad',
          blocks: [
            'Cuidamos que la información del sitio sea correcta y esté al día, pero puede contener errores o quedar desactualizada; las condiciones que valen son las de los términos del servicio y las que acordemos por escrito. No respondemos por el contenido de sitios de terceros a los que enlacemos.',
          ],
        },
        {
          id: 'law',
          title: 'Ley aplicable',
          blocks: [
            'Este aviso se rige por las leyes de [PAÍS — Costa Rica, confirmar con el propietario]. Para usuarios en España se aplica además la Ley 34/2002 de servicios de la sociedad de la información y de comercio electrónico (LSSI-CE).',
          ],
        },
      ],
    },
  },
}

// ─────────────────────────────────────────────────────────────────────────────
// English — the same documents, claim for claim. Placeholders stay as they are
// in Spanish: they are notes to the owner, not copy.
// ─────────────────────────────────────────────────────────────────────────────

const en: LegalCopy = {
  updated: 'Last updated: October 2, 2026',
  summaryTitle: 'In short',
  toc: 'Contents',
  otherDocs: 'Other legal documents',
  footerHead: 'Legal',
  questions: `Questions about this document? Write to us at ${MAIL}.`,
  hub: {
    label: 'Legal',
    title: 'Legal documents',
    intro: 'Everything that governs the use of StockAI in one place: terms, privacy, acceptable use, artificial intelligence, business conditions and security.',
    allLink: 'All documents',
    groups: {
      core: 'The basics',
      use: 'How StockAI is used',
      business: 'For business customers',
      security: 'Security',
    },
  },
  docs: {
    ...LEGAL_EXTRA_EN,
    terms: {
      label: 'Terms of Service',
      title: 'Terms of Service',
      intro: 'The rules for using StockAI: what we give you, what we ask of you, how the source code is bought and how far our liability goes.',
      summary: [
        'StockAI helps you decide what to buy. **The purchasing decision is yours**, and so are its consequences.',
        'StockAI is sold only as source code, for USD 14,999 as a one-time payment, to install on your own infrastructure. The details of delivery are agreed by writing to us.',
        'Your data is yours. We only use it to provide the service.',
        'There is no service-level agreement (SLA) unless we sign one separately.',
      ],
      sections: [
        {
          id: 'acceptance',
          title: 'Who we are and how you accept these terms',
          blocks: [
            'StockAI is a service of [RAZÓN SOCIAL] ("StockAI", "we"). The details that identify us are in the [legal notice](/aviso-legal).',
            'You accept these terms when you create an account (by ticking the box on the form) or enter a trial account. We record the date and the version of the terms you accepted.',
            'If you create the account on behalf of a company, you confirm you can bind it, and these terms apply between that company and us.',
          ],
        },
        {
          id: 'service',
          title: 'The service',
          blocks: [
            'StockAI is a web application for inventory purchasing decisions. From the sales history you upload, it trains forecasting models for each product, computes a stock signal (ORDER NOW, ORDER SOON, OK, OVERSTOCK), suggests quantities and suppliers, generates purchase orders and records receptions.',
            'The service changes over time: we may add, change or retire features. If we retire something important you use, we will tell you reasonably in advance.',
          ],
        },
        {
          id: 'accounts',
          title: 'Your account',
          blocks: [
            {
              list: [
                'StockAI is for professional use. You must be of legal age to create an account.',
                'Give us real details and keep them current: we use them to write to you about your account.',
                'Look after your password. What is done with your user is your account\'s responsibility, unless you tell us about unauthorised access.',
                'An account\'s administrator decides whom to invite and with which role (administrator, analyst or viewer), and answers for how those people use it.',
              ],
            },
          ],
        },
        {
          id: 'plans',
          title: 'Source code purchase',
          blocks: [
            'StockAI is sold only as source code: the complete source code, to install on your own infrastructure and operate as your own. The price is **USD 14,999**, as a one-time payment.',
            'Write to us and we will close the purchase and the details of delivery with you.',
            '[LICENCIA, ENTREGA, SOPORTE Y REEMBOLSO DE LA COMPRA — confirmar con el propietario]',
          ],
        },
        {
          id: 'trial',
          title: 'Trial account',
          blocks: [
            'From [the trial page](/prueba) you can enter a temporary account with sample data without signing up. It lasts **24 hours** and is then erased with everything in it. If you asked us to talk in the meantime, we keep it read-only until we answer, and then it is erased.',
            'The trial password is shown only once. Do not upload to a trial account any data you do not want to lose.',
          ],
        },
        {
          id: 'acceptable-use',
          title: 'Acceptable use',
          blocks: [
            'You may not use StockAI to:',
            {
              list: [
                'upload data you have no right to use, or third parties\' personal data without a legal basis to process it;',
                'try to access accounts or data that are not yours, or test the service\'s security without our written permission;',
                'overload the service, get around its limits or automate account creation;',
                'send your suppliers or contacts misleading or unsolicited messages through StockAI;',
                'resell the service or copy it to build a competing one.',
              ],
            },
          ],
        },
        {
          id: 'api',
          title: 'The API',
          blocks: [
            'An administrator can create read or write API keys (they start with `sk_live_`). The full key is shown only once; we keep only a fingerprint of it. Store it like a password: whatever is done with your key counts as done by your account.',
            {
              list: [
                'Each key can make up to 120 calls per minute.',
                'We count calls that reach an endpoint, by day and by key; refused calls do not count.',
                'The MCP server for AI assistants only reads: it cannot create, change or delete anything.',
                'We may revoke a key used against these terms or that puts the service at risk.',
              ],
            },
          ],
        },
        {
          id: 'decisions',
          title: 'Forecasts and recommendations: you decide',
          blocks: [
            'Forecasts are estimates made from your history. They can be wrong: demand changes for reasons that are not in your data, and a model that was right yesterday can be wrong tomorrow. The stock signal, suggested quantities, supplier choice, purchase orders and the AI assistant\'s answers are **decision support**, not instructions.',
            'Review every order before you send it. **We do not guarantee the accuracy of any forecast or any business outcome**, and we are not liable for purchasing decisions, stockouts, overstock, lost sales or costs that result from following or not following the recommendations.',
          ],
        },
        {
          id: 'your-data',
          title: 'Your data',
          blocks: [
            'The data you upload is yours. You allow us to store, process and display it only as far as needed to provide the service, as the [privacy policy](/privacidad) explains. We do not sell it or use it for advertising, and your account\'s models are trained only on your account\'s data.',
            'If your data includes information about people (for example, supplier contacts or customer names in your sales), you are responsible for holding it lawfully, and we process it on your behalf and on your instructions.',
          ],
        },
        {
          id: 'availability',
          title: 'Availability',
          blocks: [
            'We work to keep StockAI available and back up the database every night, but **we do not offer a service-level agreement (SLA)** unless we sign one separately. There may be outages for maintenance, failures or causes outside our control.',
            'Keep your original files: StockAI does not replace your accounting or sales system of record.',
          ],
        },
        {
          id: 'termination',
          title: 'Suspension and termination',
          blocks: [
            'You can stop using StockAI whenever you want and ask us to delete your account.',
            'We may suspend or close an account if it breaches these terms, puts the service or other customers at risk, or if the law requires it. Except in an emergency, we warn you first and give you a chance to fix it.',
          ],
        },
        {
          id: 'after',
          title: 'Your data when you leave',
          blocks: [
            'Before closing your account you can ask us for a complete copy of your data (a ZIP file with every table of your company). When the account is deleted we erase all its tables and files; backups that still contain them expire on their own within 14 days.',
          ],
        },
        {
          id: 'ip',
          title: 'Intellectual property',
          blocks: [
            'The software, the StockAI brand, the design and the documentation belong to us or our licensors. We grant you a non-exclusive, non-transferable right to use the service while your account is active.',
            'If you send us suggestions, we may use them to improve the product with no obligation to you.',
          ],
        },
        {
          id: 'liability',
          title: 'Limitation of liability',
          blocks: [
            'The service is provided "as is" and "as available". To the extent the law allows:',
            {
              list: [
                'we are not liable for indirect damages, loss of profit, lost sales or lost data, or for business decisions made with the service;',
                'our total liability to you is limited to [LÍMITE DE RESPONSABILIDAD — confirmar con el propietario].',
              ],
            },
            'Nothing in these terms limits rights that the law of your country does not allow to be limited.',
          ],
        },
        {
          id: 'changes',
          title: 'Changes to these terms',
          blocks: [
            'If we change these terms, we update the date above. If the change is significant, we tell you by email or inside the application before it takes effect. Continuing to use StockAI after that date means you accept the new version.',
          ],
        },
        {
          id: 'law',
          title: 'Governing law',
          blocks: [
            'These terms are governed by the laws of [PAÍS — Costa Rica, confirmar con el propietario], and any dispute will be settled before the courts of [CIUDAD — confirmar con el propietario], unless the consumer-protection law of your country entitles you to another forum.',
          ],
        },
        {
          id: 'contact',
          title: 'Contact',
          blocks: [`Write to us at ${MAIL}.`],
        },
      ],
    },

    privacy: {
      label: 'Privacy',
      title: 'Privacy Policy',
      intro: 'What data StockAI keeps, what it uses it for, whom it shares it with, how long it keeps it and how you can see it, take it with you or delete it.',
      summary: [
        'We use your data only to provide the service. **We do not sell it or use it for advertising.** There are no trackers or analytics on the site.',
        'Your account\'s forecasts are trained only on your account\'s data. No other company sees your data.',
        'If you use the artificial-intelligence features, part of your data is sent to DeepSeek, a provider based in China.',
        `You can ask for a complete copy of your data, or for its deletion, by writing to ${MAIL}.`,
      ],
      sections: [
        {
          id: 'controller',
          title: 'Who is responsible for your data',
          blocks: [
            'The controller is [RAZÓN SOCIAL], [CÉDULA JURÍDICA / NIF], with registered address at [DIRECCIÓN]. You can write to us at ' + MAIL + '. Full details are in the [legal notice](/aviso-legal).',
            'For your **account** data (your name, your email), we are the controller. For the **business data** you upload that mentions people — for example, a supplier\'s contact or a customer\'s name in your sales history — the controller is your company, and we process it on its behalf, only to provide it the service.',
          ],
        },
        {
          id: 'data',
          title: 'What data we keep',
          blocks: [
            {
              table: {
                head: ['Type', 'What it includes', 'Where it comes from'],
                rows: [
                  ['Account', 'Name, email, WhatsApp number, company name, role, the date you accepted the terms. The password is stored only as a bcrypt hash: nobody can read it.', 'The sign-up form, or whoever invited you to the account'],
                  ['Your business data', 'Sales history, inventory, products, prices and costs, suppliers and their contacts, purchase orders, receptions, documents you upload to the assistant and your conversations with it, and the database connections you set up.', 'You: files, forms, connections and the API'],
                  ['Account activity', 'Which action each user took and when (for example, generating an order or uploading a file). You can see it in My account.', 'Use of the application'],
                  ['API usage', 'Number of calls per day and per key. For each key we keep only a fingerprint and its last four characters.', 'Calls to the API'],
                  ['Technical data', 'IP address, date and time, requested address and errors, in the server logs. Sign-in attempts (by email) and trial-account creation (by IP address) are counted to stop abuse.', 'Your browser or integration, automatically'],
                ],
              },
            },
            '**What we do not collect:** we use no analytics, pixels or advertising cookies, and we take no card payments inside the application, so we hold no payment data. More detail in the [cookie policy](/cookies).',
          ],
        },
        {
          id: 'purposes',
          title: 'What we use it for',
          blocks: [
            {
              table: {
                head: ['Purpose', 'Legal basis'],
                rows: [
                  ['Providing the service: your account, forecasts, the stock signal, purchase orders and the API.', 'Performance of the contract'],
                  ['Sending you service emails (verifying your email, recovering your password) and the alerts you switch on by email, WhatsApp or SMS.', 'Performance of the contract'],
                  ['Answering you when you write to us.', 'Performance of the contract and legitimate interest'],
                  ['Protecting the service: attempt limits, technical logs, abuse detection.', 'Legitimate interest'],
                  ['Meeting legal obligations and requests from authorities.', 'Legal obligation'],
                ],
              },
            },
            '**Models are trained only on your account\'s data.** When StockAI trains a forecast, it uses the history you uploaded to your account and nothing else; we do not mix data from different companies or use it to train other customers\' models.',
            'We do not sell your data, do not share it with advertisers and do not use it to send you advertising.',
          ],
        },
        {
          id: 'ai',
          title: 'Artificial-intelligence features',
          blocks: [
            'Some features use a language model from **DeepSeek**: the assistant (in the application and over WhatsApp), the morning summary on the Purchasing Panel, the explanation of a forecast and the quality check of a file. The Purchasing Panel summary is requested when you open that screen.',
            'When they are used, we send DeepSeek the question and the data from your account needed to answer it — for example, product codes and names, quantities, the stock signal, suppliers and amounts. We do not send passwords or credentials. DeepSeek is based in China and processes that data under its own terms.',
            `Today artificial intelligence cannot be switched off per account from the application. If you do not want your data to reach DeepSeek, write to ${MAIL} before using StockAI. If the service has no DeepSeek configured, nothing is sent and those screens answer with rule-based text.`,
          ],
        },
        {
          id: 'processors',
          title: 'Whom we share it with (processors)',
          blocks: [
            'We share data only with the providers we need to run StockAI, and only what their task needs:',
            {
              table: {
                head: ['Provider', 'What for', 'What data', 'Where'],
                rows: [
                  ['Hostinger', 'Server (VPS) running the application and the database, and stockai.es email', 'All service data', '[UBICACIÓN DEL SERVIDOR — confirmar con el propietario]'],
                  ['DeepSeek', 'Artificial-intelligence features', 'The question and the data needed to answer it', 'China'],
                  ['Email provider: Hostinger (SMTP) or Resend, depending on configuration', 'Sending service emails and alerts', 'Your email address and the message content', '[UBICACIÓN — confirmar con el propietario]'],
                  ['Twilio (only if WhatsApp or SMS are switched on)', 'Sending and receiving WhatsApp and SMS messages', 'Your number and the message content', 'United States'],
                ],
              },
            },
            'We use no analytics services or advertising networks. The site\'s fonts are served from our own server, with no calls to third parties.',
            'We may also disclose data if a law or a competent authority requires it.',
          ],
        },
        {
          id: 'transfers',
          title: 'International transfers',
          blocks: [
            'Because of the providers above, your data may be processed outside your country: in [UBICACIÓN DEL SERVIDOR — confirmar con el propietario] (server), in China (DeepSeek, if you use the AI features) and in the United States (Twilio, if you switch on WhatsApp or SMS).',
            'If you are in the European Union or Spain, note that China has no adequacy decision from the European Commission. That is why we tell you which features send data to DeepSeek and how to avoid it.',
          ],
        },
        {
          id: 'retention',
          title: 'How long we keep it',
          blocks: [
            {
              table: {
                head: ['Data', 'Period'],
                rows: [
                  ['Account, business data and activity', 'As long as your account exists.'],
                  ['Trial account', '24 hours from creation; then erased entirely. If you asked us to talk, it is kept read-only until we answer.'],
                  ['When an account is deleted', 'All its tables and files are erased at once.'],
                  ['Backups', 'Made every night and kept 14 days. What you delete also disappears from the backups within that period.'],
                  ['Attempt counts (sign-in, trial accounts)', 'Only used within their window: minutes to 24 hours.'],
                  ['Server technical logs', '[PLAZO DE LOS REGISTROS DEL SERVIDOR — confirmar con el propietario]'],
                ],
              },
            },
          ],
        },
        {
          id: 'security',
          title: 'How we protect it',
          blocks: [
            {
              list: [
                '**Each company sees only its own data.** Every database query is filtered by the account of whoever makes it.',
                'Role-based permissions: administrator, analyst and viewer. Only an analyst or an administrator can change data.',
                'Passwords are stored with bcrypt and API keys as a fingerprint: not even we can read them.',
                'Credentials for connections and services (for example, the password of a database you connect) are stored encrypted with Fernet.',
                'Sessions expire after 15 minutes and renew themselves while the tab is open. Changing your password ends earlier sessions.',
                'The connection to the application is encrypted (HTTPS).',
              ],
            },
            'No system is infallible. If an incident affects your data, we will tell you and notify the authority where the law requires it.',
          ],
        },
        {
          id: 'rights',
          title: 'Your rights and how to exercise them',
          blocks: [
            'You can ask us at any time for:',
            {
              list: [
                '**Access:** knowing what data of yours we hold.',
                '**Rectification:** correcting it. You can change your name and number yourself in My account.',
                '**Erasure:** deleting your account and all its data.',
                '**Portability:** a complete copy of your company\'s data in a ZIP file, one table per file.',
                '**Objection and restriction:** that we stop processing your data for some purpose, or restrict it.',
              ],
            },
            `Write to ${MAIL} from your account\'s email address. Today export and deletion are run by an account administrator through the API, or by us when you ask. We answer within one month at most, or sooner if the law of your country requires it.`,
            'If a company invited you, its administrator controls the business data: we handle requests about that data together with them.',
            'If you are not satisfied, you can complain to the data-protection authority of your country: in Costa Rica, the Agencia de Protección de Datos de los Habitantes (PRODHAB); in Spain, the Agencia Española de Protección de Datos (AEPD).',
          ],
        },
        {
          id: 'frameworks',
          title: 'Applicable laws',
          blocks: [
            'We process data under Costa Rica\'s Law 8968 (Protection of the Person in the Processing of Personal Data) and, for users in the European Union or Spain, the General Data Protection Regulation (GDPR) and Organic Law 3/2018 (LOPDGDD). We hold no certifications or compliance audits.',
          ],
        },
        {
          id: 'minors',
          title: 'Minors',
          blocks: ['StockAI is a service for businesses. It is not aimed at minors and we do not knowingly collect their data.'],
        },
        {
          id: 'changes',
          title: 'Changes to this policy',
          blocks: ['If we change this policy, we update the date above. If the change is significant, we tell you by email or inside the application before it takes effect.'],
        },
      ],
    },

    cookies: {
      label: 'Cookies',
      title: 'Cookies and local storage policy',
      intro: 'StockAI uses no analytics or advertising cookies. This is exactly what it stores in your browser, what for and for how long.',
      summary: [
        '**The application creates no cookies.** What it stores goes in your browser\'s local storage.',
        'Everything it stores is either needed for it to work or remembers a choice of yours, such as language or theme.',
        'There are no trackers, analytics or advertising, ours or third parties\'. That is why we do not ask you to accept anything: we only inform you.',
      ],
      sections: [
        {
          id: 'what',
          title: 'What cookies and local storage are',
          blocks: [
            'Cookies are small files a site stores in your browser that travel with every visit. Local storage (localStorage) and session storage (sessionStorage) are similar, but they stay in your browser: they are not sent to the server on their own. Session storage is cleared when you close the tab.',
          ],
        },
        {
          id: 'inventory',
          title: 'What we store',
          blocks: [
            '**Necessary:** without it the application cannot work. **Functional:** remembers a choice of yours; if you delete it, the application goes back to its defaults.',
            {
              table: {
                head: ['Name', 'What for', 'Type', 'Where and how long'],
                rows: [
                  ['fp_access_token', 'Your session: proves to the server that you signed in. Expires after 15 minutes and renews itself.', 'Necessary', 'localStorage, until you sign out'],
                  ['fp_refresh_token', 'Renewing your session without asking for your password again.', 'Necessary', 'sessionStorage, until you close the tab or sign out'],
                  ['fp_user', 'Your identifier, name, email, role and company, to show them on screen.', 'Necessary', 'localStorage, until you sign out'],
                  ['stockai_trial_account', 'Your trial account\'s user and password, so reloading shows the same account.', 'Necessary', 'sessionStorage, until you close the tab'],
                  ['stockai_storage_notice', 'Remembering that you have seen the notice about this storage.', 'Necessary', 'localStorage, until you delete it'],
                  ['lang', 'The language (Spanish or English).', 'Functional', 'localStorage, until you delete it'],
                  ['theme', 'The light or dark theme.', 'Functional', 'localStorage, until you delete it'],
                  ['fp_sidebar_collapsed', 'Whether the sidebar is collapsed.', 'Functional', 'localStorage, until you delete it'],
                  ['fp_tours_seen', 'Which guided tours you have seen, so they are not repeated.', 'Functional', 'localStorage, until you delete it'],
                  ['stockai.forecast_view', 'The view you chose on the forecast screen.', 'Functional', 'localStorage, until you delete it'],
                  ['stockai_intro_seen', 'Showing the introduction only once per session.', 'Functional', 'sessionStorage, until you close the tab'],
                  ['faro_verify_banner_dismissed', 'That you closed the verify-your-email notice.', 'Functional', 'sessionStorage, until you close the tab'],
                  ['fp_mobile_notice_dismissed', 'That you closed the small-screen notice.', 'Functional', 'sessionStorage, until you close the tab'],
                  ['stockai-v1-static', 'Application files and the "offline" page, so the installed app opens fast. Contains none of your data.', 'Functional', 'Browser cache (service worker), only inside the application, until the next version'],
                ],
              },
            },
            'Your language and theme are also saved in your account, so they follow you to another device.',
          ],
        },
        {
          id: 'third-parties',
          title: 'Third parties',
          blocks: [
            'None. The site and the application load no scripts, pixels or fonts from other sites: everything is served from our server, and the browser security policy (CSP) blocks any connection to another domain.',
          ],
        },
        {
          id: 'consent',
          title: 'Why we do not ask you to accept',
          blocks: [
            'What we store is strictly necessary to provide the service you asked for, or remembers a preference you chose. For that the law requires no consent, only that we inform you — which is why you see a notice the first time and not an "accept all" button.',
            'If we ever add analytics or any other storage that is not necessary, we will ask your permission before switching it on and update this page.',
          ],
        },
        {
          id: 'control',
          title: 'How to delete it',
          blocks: [
            'Signing out deletes the session data. You can delete everything else from your browser settings (under "Privacy" or "Site data"). If you delete what is necessary, you will have to sign in again; if you delete what is functional, the application goes back to its default language and theme.',
          ],
        },
        {
          id: 'changes',
          title: 'Changes',
          blocks: ['If what we store changes, we update this table and the date above.'],
        },
      ],
    },

    notice: {
      label: 'Legal notice',
      title: 'Legal notice',
      intro: 'Who is behind StockAI and stockai.es, and the conditions for using this website.',
      summary: [
        'stockai.es is the website of StockAI, a service of [RAZÓN SOCIAL].',
        `For any legal matter, write to us at ${MAIL}.`,
      ],
      sections: [
        {
          id: 'owner',
          title: 'Website owner',
          blocks: [
            {
              table: {
                head: ['Detail', 'Value'],
                rows: [
                  ['Owner', '[RAZÓN SOCIAL]'],
                  ['Tax ID', '[CÉDULA JURÍDICA / NIF]'],
                  ['Registered address', '[DIRECCIÓN]'],
                  ['Registry details', '[DATOS DE INSCRIPCIÓN EN EL REGISTRO, si aplica]'],
                  ['Email', MAIL],
                  ['Domain', 'stockai.es'],
                ],
              },
            },
          ],
        },
        {
          id: 'purpose',
          title: 'Purpose',
          blocks: [
            'This website presents StockAI and gives access to the application. Use of the application is governed by the [terms of service](/terminos) and data processing by the [privacy policy](/privacidad).',
          ],
        },
        {
          id: 'use',
          title: 'Use of the website',
          blocks: [
            'You may browse the website freely. We ask that you do not use it for unlawful purposes, try to damage it or access non-public parts, or copy its content to present it as your own.',
          ],
        },
        {
          id: 'ip',
          title: 'Intellectual property',
          blocks: [
            'The texts, images, screenshots, design, software and the StockAI brand belong to the owner or its licensors. You may quote or link to them citing the source; for any other use, ask us for permission.',
          ],
        },
        {
          id: 'liability',
          title: 'Liability',
          blocks: [
            'We take care that the information on the website is correct and current, but it may contain errors or become outdated; the conditions that apply are those in the terms of service and whatever we agree in writing. We are not responsible for the content of third-party sites we link to.',
          ],
        },
        {
          id: 'law',
          title: 'Governing law',
          blocks: [
            'This notice is governed by the laws of [PAÍS — Costa Rica, confirmar con el propietario]. For users in Spain, Law 34/2002 on information-society services and electronic commerce (LSSI-CE) also applies.',
          ],
        },
      ],
    },
  },
}

export const LEGAL: Record<Lang, LegalCopy> = { es, en }
