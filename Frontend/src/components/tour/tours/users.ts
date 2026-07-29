import type { TourDefinition } from '../types'

export const usersTour: TourDefinition = {
  id: 'users-v1',
  route: '/users',
  name: 'name',
  autoStart: false,
  steps: [
    { title: 'intro_title', body: 'intro_body' },
    { anchor: 'users.role', title: 'role_title', body: 'role_body' },
    { anchor: 'users.status', title: 'status_title', body: 'status_body' },
    { anchor: 'users.permissions', title: 'permissions_title', body: 'permissions_body' },
    { anchor: 'users.invite', title: 'invite_title', body: 'invite_body' },
  ],
  copy: {
    es: {
      name: 'Quién entra y qué puede hacer',
      intro_title: 'Aquí se decide quién gasta dinero',
      intro_body: 'Todo lo demás en Faro es una recomendación. Esta pantalla define quién puede convertirla en una compra de verdad.\n\nUna cuenta compartida entre tres personas es cómoda hasta el día en que sale una orden de ocho mil dólares y nadie sabe quién la aprobó. Una persona, un correo, un rol.',
      role_title: 'Los tres roles, sin adornos',
      role_body: 'Solo lectura: ve todo — pronósticos, semáforo, reportes — y no cambia nada. Ni aprueba compras, ni sube ventas, ni entrena.\n\nAnalista: hace el trabajo diario completo. Aprueba órdenes, registra recepciones, sube archivos, lanza entrenamientos.\n\nAdministrador: lo mismo que el analista, más esta pantalla y los ajustes que aplican a toda la cuenta.\n\nLa frontera que importa está entre solo lectura y analista: es la línea entre mirar y comprometer plata.',
      status_title: 'Invitado, activo, suspendido',
      status_body: 'Quien aparece como pendiente todavía no ha puesto su contraseña: está en la lista, pero no puede entrar. Si el correo no le llegó, reenvíaselo desde aquí antes de volver a invitarlo.\n\nCuando alguien deja la empresa, suspéndelo o desactívalo: le cerramos la sesión en ese momento y deja de entrar.\n\nBorrar es otra cosa. Es permanente y no hace falta para quitarle el acceso.',
      permissions_title: 'La lista fina de permisos',
      permissions_body: 'El escudo abre el detalle por persona: ver inventario, gestionarlo, exportar datos, correr entrenamientos.\n\nSirve para dejar por escrito qué debería tocar cada quien, y se guarda por usuario. Pero conviene saberlo: hoy lo que el servidor obedece es el rol. Marcarle "gestionar inventario" a alguien de solo lectura no le habilita el botón — para eso hay que subirlo a analista.',
      invite_title: 'Invitar es mandar un correo, no repartir una clave',
      invite_body: 'Escribes el correo, eliges el rol, y la persona recibe un enlace donde pone su propia contraseña. Tú nunca ves ni escribes esa clave, y por eso nadie termina prestando la suya.\n\nEl rol que elijas aplica desde el primer minuto. Si dudas, entra a alguien como solo lectura y súbelo después: cuesta menos dar un permiso que explicar una orden de compra que no debió salir.',
    },
    en: {
      name: 'Who gets in and what they can do',
      intro_title: 'This is where you decide who spends money',
      intro_body: 'Everything else in Faro is a recommendation. This screen defines who can turn one into an actual purchase.\n\nOne account shared by three people is convenient until the day an eight-thousand-dollar order goes out and nobody knows who approved it. One person, one email, one role.',
      role_title: 'The three roles, plainly',
      role_body: 'Read only: sees everything — forecasts, stock signal, reports — and changes nothing. No approving purchases, no uploading sales, no training.\n\nAnalyst: does the whole daily job. Approves orders, records receptions, uploads files, launches training runs.\n\nAdministrator: everything the analyst does, plus this screen and the settings that apply to the whole account.\n\nThe boundary that matters is between read only and analyst: it is the line between looking and committing money.',
      status_title: 'Invited, active, suspended',
      status_body: 'Anyone showing as pending has not set a password yet: they are on the list, but they cannot get in. If the email never arrived, resend it from here before inviting them again.\n\nWhen someone leaves the company, suspend or deactivate them: we close their session there and then and they stop getting in.\n\nDeleting is a different thing. It is permanent, and it is not what you need in order to cut off access.',
      permissions_title: 'The fine-grained permission list',
      permissions_body: 'The shield opens the per-person detail: view inventory, manage it, export data, run training.\n\nIt is useful for writing down what each person should be touching, and it is stored per user. But know this: what the server actually obeys today is the role. Ticking "manage inventory" for a read-only user does not enable the button — for that, promote them to analyst.',
      invite_title: 'Inviting is sending an email, not handing out a password',
      invite_body: 'You type the email, pick the role, and the person gets a link where they set their own password. You never see or type that password, which is why nobody ends up lending theirs.\n\nThe role you pick applies from minute one. When in doubt, bring someone in as read only and promote them later: granting one permission costs less than explaining a purchase order that should never have gone out.',
    },
  },
}
