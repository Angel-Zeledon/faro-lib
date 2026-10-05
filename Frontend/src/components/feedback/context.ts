'use client'
import { createContext, useContext } from 'react'
import type { BugReportFacts } from '@/lib/bugReport'

/**
 * Kept apart from the dialog so the screens that offer "Send feedback"
 * (States, the error screen, the toast bridge) can import the hook without
 * importing the dialog, which itself imports States for its error wording.
 */
export type OpenFn = (facts?: BugReportFacts) => void

export const FeedbackContext = createContext<OpenFn>(() => {})

/** Opens the feedback dialog. Safe to call from any client component. */
export const useFeedback = (): OpenFn => useContext(FeedbackContext)
