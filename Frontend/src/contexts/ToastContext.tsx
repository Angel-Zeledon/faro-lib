'use client'
import { createContext, useContext, useState, useCallback, useRef } from 'react'

export type ToastType = 'success' | 'error' | 'info'

export interface ToastItem {
  id: string
  title: string
  message: string
  type: ToastType
  exiting?: boolean
}

interface ToastCtx {
  toasts: ToastItem[]
  addToast: (title: string, message: string, type?: ToastType) => void
  dismiss: (id: string) => void
}

const ToastContext = createContext<ToastCtx>({ toasts: [], addToast: () => {}, dismiss: () => {} })

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([])
  const timers = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map())

  const dismiss = useCallback((id: string) => {
    setToasts(prev => prev.map(t => t.id === id ? { ...t, exiting: true } : t))
    setTimeout(() => setToasts(prev => prev.filter(t => t.id !== id)), 320)
    const t = timers.current.get(id)
    if (t) { clearTimeout(t); timers.current.delete(id) }
  }, [])

  const addToast = useCallback((title: string, message: string, type: ToastType = 'info') => {
    const id = `t_${Date.now()}_${Math.random().toString(36).slice(2)}`
    setToasts(prev => [...prev.slice(-4), { id, title, message, type }])
    timers.current.set(id, setTimeout(() => dismiss(id), 4500))
  }, [dismiss])

  return (
    <ToastContext.Provider value={{ toasts, addToast, dismiss }}>
      {children}
    </ToastContext.Provider>
  )
}

export const useToast = () => useContext(ToastContext)
