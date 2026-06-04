'use client'
import { createContext, useContext, useEffect, useState, useCallback } from 'react'

type Lang = 'es' | 'en'

const T = {
  es: {
    configuration:   'Configuración',
    user_profile:    'Perfil de Usuario',
    app_settings:    'Configuración de App',
    available_models:'Modelos Disponibles',
    activity_logs:   'Registros de Actividad',
    language:        'Idioma',
    theme:           'Tema',
    dark:            'Oscuro',
    light:           'Claro',
    save_changes:    'Guardar Cambios',
    saving:          'Guardando…',
    saved:           'Guardado',
    full_name:       'Nombre completo',
    email:           'Correo electrónico',
    role:            'Rol',
    account_status:  'Estado de cuenta',
    active:          'Activo',
    inactive:        'Inactivo',
    all_actions:     'Todas las acciones',
    load_more:       'Cargar más',
    no_activity:     'Sin actividad registrada',
    filter_logs:     'Filtrar por acción…',
    action:          'Acción',
    resource:        'Recurso',
    status_col:      'Estado',
    date:            'Fecha',
    success:         'Exitoso',
    error:           'Error',
    spanish:         'Español',
    english:         'English',
    edit:            'Editar',
    cancel:          'Cancelar',
    model:           'Modelo',
    category:        'Categoría',
    availability:    'Disponibilidad',
    available:       'Disponible',
    beta:            'Beta',
    security:        'Seguridad',
    change_password: 'Cambio de contraseña',
    password_label:  'Contraseña',
    change_pw_btn:   'Cambiar contraseña',
    pw_code_hint:    'Se enviará un código de 6 dígitos a',
    pw_form_desc:    'Ingresa tu nueva contraseña. Después recibirás un código por correo para confirmar.',
    pw_placeholder:  'Nueva contraseña (mín. 8 caracteres)',
    send_code:       'Enviar código al correo',
    code_sent_to:    'Código enviado a',
    code_expires:    'Expira en 10 minutos.',
    six_digit_code:  'Código de 6 dígitos',
    confirm_change:  'Confirmar cambio',
    go_back:         'Volver',
    pw_updated:      'Contraseña actualizada correctamente.',
    pw_min_length:   'La contraseña debe tener al menos 8 caracteres',
    models_count:    'modelos',
    records_count:   'registros',
  },
  en: {
    configuration:   'Configuration',
    user_profile:    'User Profile',
    app_settings:    'App Settings',
    available_models:'Available Models',
    activity_logs:   'Activity Logs',
    language:        'Language',
    theme:           'Theme',
    dark:            'Dark',
    light:           'Light',
    save_changes:    'Save Changes',
    saving:          'Saving…',
    saved:           'Saved',
    full_name:       'Full name',
    email:           'Email address',
    role:            'Role',
    account_status:  'Account status',
    active:          'Active',
    inactive:        'Inactive',
    all_actions:     'All actions',
    load_more:       'Load more',
    no_activity:     'No activity recorded',
    filter_logs:     'Filter by action…',
    action:          'Action',
    resource:        'Resource',
    status_col:      'Status',
    date:            'Date',
    success:         'Success',
    error:           'Error',
    spanish:         'Español',
    english:         'English',
    edit:            'Edit',
    cancel:          'Cancel',
    model:           'Model',
    category:        'Category',
    availability:    'Availability',
    available:       'Available',
    beta:            'Beta',
    security:        'Security',
    change_password: 'Password change',
    password_label:  'Password',
    change_pw_btn:   'Change password',
    pw_code_hint:    'A 6-digit code will be sent to',
    pw_form_desc:    'Enter your new password. You will receive a confirmation code by email.',
    pw_placeholder:  'New password (min. 8 characters)',
    send_code:       'Send code to email',
    code_sent_to:    'Code sent to',
    code_expires:    'Expires in 10 minutes.',
    six_digit_code:  '6-digit code',
    confirm_change:  'Confirm change',
    go_back:         'Go back',
    pw_updated:      'Password updated successfully.',
    pw_min_length:   'Password must be at least 8 characters',
    models_count:    'models',
    records_count:   'records',
  },
} as const

type Key = keyof typeof T.en

interface LangCtx {
  lang: Lang
  t:    (k: string) => string
  setLang: (l: Lang) => void
}

const Ctx = createContext<LangCtx>({ lang: 'es', t: (k: string) => k, setLang: () => {} })

export function LanguageProvider({ children }: { children: React.ReactNode }) {
  const [lang, setLangState] = useState<Lang>('es')

  useEffect(() => {
    const saved = (localStorage.getItem('lang') as Lang | null) ?? 'es'
    setLangState(saved)
  }, [])

  const setLang = useCallback((l: Lang) => {
    setLangState(l)
    localStorage.setItem('lang', l)
  }, [])

  const t = useCallback((k: string) => (T[lang] as Record<string, string>)[k] ?? k, [lang])

  return <Ctx.Provider value={{ lang, t, setLang }}>{children}</Ctx.Provider>
}

export const useLanguage = () => useContext(Ctx)
