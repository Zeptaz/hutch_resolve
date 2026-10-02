import { request, newId } from './client'
import type {
  AgentCaseDetail,
  AgentSessionView,
  CaseQueue,
  ConversationView,
  Language,
  LoginRequest,
  MessageRequest,
  QueueFilters,
  SessionView,
  TurnResult,
} from './types'

// Thin typed wrappers over docs/contracts/openapi.json. Business rules live in Resolve, not here.

export const customerApi = {
  getSession: () => request<SessionView>('customer', 'GET', '/session'),
  createGuestSession: () => request<SessionView>('customer', 'POST', '/sessions/anonymous', { body: {} }),
  login: (body: LoginRequest) => request<SessionView>('customer', 'POST', '/demo/sessions', { body }),
  logout: () => request<void>('customer', 'DELETE', '/session'),

  createConversation: (language: Language, idempotencyKey = newId()) =>
    request<ConversationView>('customer', 'POST', '/conversations', { body: { language }, idempotencyKey }),
  getConversation: (id: string) => request<ConversationView>('customer', 'GET', `/conversations/${id}`),
  /** Reuse body.client_turn_id when retrying the same turn; generate a new one for a new turn. */
  sendMessage: (conversationId: string, body: MessageRequest) =>
    request<TurnResult>('customer', 'POST', `/conversations/${conversationId}/messages`, { body }),
}

export const agentApi = {
  getSession: () => request<AgentSessionView>('agent', 'GET', '/agent/session'),
  login: (body: LoginRequest) => request<AgentSessionView>('agent', 'POST', '/agent/sessions', { body }),
  logout: () => request<void>('agent', 'DELETE', '/agent/session'),

  listCases: (filters: QueueFilters = {}, signal?: AbortSignal) =>
    request<CaseQueue>('agent', 'GET', '/agent/cases', { query: filters, signal }),
  getCase: (id: string, signal?: AbortSignal) =>
    request<AgentCaseDetail>('agent', 'GET', `/agent/cases/${id}`, { signal }),
}
