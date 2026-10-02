import { createContext, useContext } from 'react'
import type { AgentSessionView, SessionView } from '@/api/types'
import type { RealmSession } from './useRealmSession'

export const CustomerSessionContext = createContext<RealmSession<SessionView> | null>(null)
export const AgentSessionContext = createContext<RealmSession<AgentSessionView> | null>(null)

export function useCustomerSession() {
  const ctx = useContext(CustomerSessionContext)
  if (!ctx) throw new Error('useCustomerSession must be used inside CustomerSessionProvider')
  return ctx
}

export function useAgentSession() {
  const ctx = useContext(AgentSessionContext)
  if (!ctx) throw new Error('useAgentSession must be used inside AgentSessionProvider')
  return ctx
}
