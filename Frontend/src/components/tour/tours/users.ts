import type { TourDefinition } from '../types'

/**
 * The permissions step went with the checkboxes it described — see
 * PER_USER_PERMISSIONS_ENABLED in app/users/page.tsx. Its copy had to explain
 * that the control did not do what it looked like it did, which was the right
 * thing to write and the wrong thing to need.
 *
 * `users.actions` carries two steps rather than one: the cell holds four
 * different controls and the status dropdown alone changes whether a person
 * can log in. Splitting it keeps each body short enough to be read.
 */
export const usersTour: TourDefinition = {
  id: 'users-v3',
  route: '/usuarios',
  name: 'name',
  autoStart: false,
  steps: [
    { title: 'intro_title', body: 'intro_body' },
    { anchor: 'users.invite', title: 'invite_title', body: 'invite_body' },
    { anchor: 'users.role', title: 'role_title', body: 'role_body' },
    { anchor: 'users.status', title: 'status_title', body: 'status_body' },
    { anchor: 'users.actions', title: 'rowstatus_title', body: 'rowstatus_body' },
    { anchor: 'users.actions', title: 'rowactions_title', body: 'rowactions_body' },
    { anchor: 'users.filters', title: 'filters_title', body: 'filters_body' },
  ],
  copy: {
    es: {
      name: 'Quién entra y qué puede hacer',
      intro_title: 'Aquí se decide quién gasta dinero',
      intro_body: 'Todo lo demás en Faro es una recomendación. Esta pantalla define quién puede convertirla en una compra de verdad.\n\nUna cuenta compartida entre tres personas es cómoda hasta el día en que sale una orden de ocho mil dólares y nadie sabe quién la aprobó. Una persona, un correo, un rol.',
      invite_title: 'Invitar: tres campos y un correo',
      invite_body: '"Crear usuario" abre un formulario con tres campos.\n\nNombre completo es opcional, pero escríbelo: es lo que vas a ver en la lista y en el historial. Sin él la fila queda con un guion y tienes que reconocer a la gente por su correo.\n\nCorreo es el único obligatorio, y tiene que ser uno que la persona abra de verdad: ahí le llega el enlace donde pone su propia contraseña. Una letra mal escrita y la invitación se pierde — nadie entra, y toca borrar la cuenta e invitar de nuevo.\n\nRol viene puesto en Analista. Cámbialo antes de guardar.',
      role_title: 'Los tres roles, sin adornos',
      role_body: 'La misma lista aparece al invitar y al editar. Estas son las opciones:\n\nSolo lectura: ve todo — pronósticos, semáforo, reportes — y no cambia nada. Ni aprueba compras, ni sube ventas, ni entrena.\n\nAnalista: hace el trabajo diario completo. Aprueba órdenes, registra recepciones, sube archivos, lanza entrenamientos.\n\nAdministrador: lo mismo que el analista, más esta pantalla y los ajustes de toda la cuenta.\n\nLa frontera que importa está entre solo lectura y analista: es la línea entre mirar y comprometer plata. Si dudas, entra a alguien como solo lectura y súbelo después.',
      status_title: 'Los cuatro estados de esta columna',
      status_body: 'Activo: entra y trabaja con normalidad.\n\nPendiente: lo invitaste y todavía no ha puesto su contraseña. Está en la lista y no puede entrar.\n\nInactivo y Suspendido: no entra. Para Faro los dos bloquean igual; la diferencia es lo que le cuentas al siguiente que lea la fila. Inactivo es "ya no trabaja aquí"; suspendido es "le cortamos el acceso a propósito mientras se aclara algo".\n\nEl estado no se escribe a mano: se cambia desde la flechita de la derecha.',
      rowstatus_title: 'La flechita: dejar entrar o dejar fuera',
      rowstatus_body: 'Abre tres opciones — Activo, Inactivo, Suspendido — y se aplica de una, sin pantalla de confirmación. Ten claro a quién le estás dando clic.\n\nAl pasar a alguien a Inactivo o Suspendido le cortamos la renovación de la sesión: en minutos se queda fuera, y no vuelve a entrar aunque sepa su contraseña. Es lo que quieres el día que alguien deja la empresa.\n\nDevolverlo a Activo le devuelve el acceso con la misma contraseña de siempre; no hay que invitarlo otra vez.\n\nSobre tu propia fila no aparece: nadie puede dejarse fuera a sí mismo.',
      rowactions_title: 'Editar, reenviar y borrar',
      rowactions_body: 'El lápiz abre el mismo formulario de la invitación: corrige el nombre, cámbiale el rol o arregla un correo mal escrito. Ojo con el correo: al cambiarlo la fila vuelve a Pendiente y esa persona queda fuera hasta que abra el mensaje y confirme.\n\nCuando alguien está Pendiente aparece además un sobre: reenvía el correo de bienvenida. Úsalo antes de invitar dos veces a la misma persona.\n\nEl basurero borra la cuenta para siempre y pide confirmación. Para quitarle el acceso no hace falta: suspéndelo. Borrar es para cuentas que no debieron existir.',
      filters_title: 'Buscar cuando la lista crece',
      filters_body: 'La caja de búsqueda filtra por nombre y por correo mientras escribes, y le basta un pedazo: "vega" encuentra a Laura Vega, "@gmail" saca a todos los de ese dominio. No hay que escribir el correo completo ni darle Enter.\n\nAl lado van dos listas, una por estado y otra por rol. Combínalas para preguntas concretas: quién sigue pendiente de confirmar, o cuántos administradores hay de verdad.\n\nLa tabla muestra 20 por página. Con un equipo chico casi no vas a usar esto, y ese es el mejor escenario.',
    },
    en: {
      name: 'Who gets in and what they can do',
      intro_title: 'This is where you decide who spends money',
      intro_body: 'Everything else in Faro is a recommendation. This screen defines who can turn one into an actual purchase.\n\nOne account shared by three people is convenient until the day an eight-thousand-dollar order goes out and nobody knows who approved it. One person, one email, one role.',
      invite_title: 'Inviting: three fields and an email',
      invite_body: '"Create user" opens a form with three fields.\n\nFull name is optional, but type it: it is what you will see in the list and in the history. Without it the row shows a dash and you have to recognise people by their email.\n\nEmail is the only required one, and it has to be an inbox the person actually opens: that is where the link to set their own password goes. One wrong letter and the invitation is lost — nobody gets in, and you have to delete the account and invite again.\n\nRole comes preset to Analyst. Change it before saving.',
      role_title: 'The three roles, plainly',
      role_body: 'The same list shows up when inviting and when editing. These are the options:\n\nRead only: sees everything — forecasts, stock signal, reports — and changes nothing. No approving purchases, no uploading sales, no training.\n\nAnalyst: does the whole daily job. Approves orders, records receptions, uploads files, launches training runs.\n\nAdministrator: everything the analyst does, plus this screen and the account-wide settings.\n\nThe boundary that matters is between read only and analyst: it is the line between looking and committing money. When in doubt, bring someone in as read only and promote them later.',
      status_title: 'The four states in this column',
      status_body: 'Active: gets in and works normally.\n\nPending: you invited them and they have not set a password yet. They are on the list and they cannot get in.\n\nInactive and Suspended: no entry. For Faro both block the same way; the difference is what you are telling the next person who reads the row. Inactive means "no longer works here"; suspended means "we cut their access on purpose while something is sorted out".\n\nThe state is not typed by hand: you change it from the little arrow on the right.',
      rowstatus_title: 'The little arrow: let them in or lock them out',
      rowstatus_body: 'It opens three options — Active, Inactive, Suspended — and applies at once, with no confirmation screen. Be sure whose row you are clicking.\n\nMoving someone to Inactive or Suspended cuts the renewal of their session: within minutes they are out, and they do not get back in even knowing their password. That is what you want the day someone leaves the company.\n\nPutting them back to Active restores access with the same password as always; no need to invite them again.\n\nIt does not appear on your own row: nobody can lock themselves out.',
      rowactions_title: 'Edit, resend and delete',
      rowactions_body: 'The pencil opens the same form as the invitation: fix the name, change the role or correct a mistyped email. Careful with the email: changing it puts the row back to Pending, and that person is locked out until they open the message and confirm.\n\nWhen someone is Pending an envelope also appears: it resends the welcome email. Use it before inviting the same person twice.\n\nThe bin deletes the account for good and asks for confirmation. You do not need it to cut off access: suspend them instead. Deleting is for accounts that should never have existed.',
      filters_title: 'Searching once the list grows',
      filters_body: 'The search box filters by name and by email as you type, and a fragment is enough: "vega" finds Laura Vega, "@gmail" pulls everyone on that domain. No need to type the whole address or press Enter.\n\nBeside it are two lists, one by status and one by role. Combine them for concrete questions: who is still pending confirmation, or how many administrators there really are.\n\nThe table shows 20 per page. With a small team you will barely use this, and that is the best case.',
    },
  },
}
