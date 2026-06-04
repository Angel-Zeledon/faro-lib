'use client'
import { useState, useEffect } from 'react'
import { getSessions } from '@/lib/api'
import type { SessionInfo } from '@/lib/types'

export interface AutoSessionResult {
  sessionId:         string
  setSessionId:      (id: string) => void
  currentSession:    SessionInfo | undefined
  completedSessions: SessionInfo[]
  loading:           boolean
}

export function useAutoSession(): AutoSessionResult {
  const [sessions,   setSessions]   = useState<SessionInfo[]>([])
  const [sessionId,  setSessionId]  = useState('')
  const [loading,    setLoading]    = useState(true)

  useEffect(() => {
    getSessions()
      .then(list => {
        setSessions(list)
        const completed = list
          .filter(s => s.status === 'COMPLETED')
          .sort((a, b) => b.updated_at.localeCompare(a.updated_at))
        if (completed.length && !sessionId) {
          setSessionId(completed[0].session_id)
        }
      })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])  // eslint-disable-line

  const currentSession    = sessions.find(s => s.session_id === sessionId)
  const completedSessions = sessions
    .filter(s => s.status === 'COMPLETED')
    .sort((a, b) => b.updated_at.localeCompare(a.updated_at))

  return { sessionId, setSessionId, currentSession, completedSessions, loading }
}
