import type { ReactNode } from 'react'
import { agentApi, customerApi } from '@/api/endpoints'
import { AgentSessionContext, CustomerSessionContext } from './context'
import { useRealmSession } from './useRealmSession'

export function CustomerSessionProvider({ children }: { children: ReactNode }) {
  const value = useRealmSession('customer', customerApi.getSession, customerApi.logout)
  return <CustomerSessionContext value={value}>{children}</CustomerSessionContext>
}

export function AgentSessionProvider({ children }: { children: ReactNode }) {
  const value = useRealmSession('agent', agentApi.getSession, agentApi.logout)
  return <AgentSessionContext value={value}>{children}</AgentSessionContext>
}
