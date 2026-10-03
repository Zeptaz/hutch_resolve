import { request, newId } from './client'
import type {
  AgentCaseDetail,
  AgentSessionView,
  CaseQueue,
  CaseView,
  ConversationView,
  EscalationRequest,
  Language,
  LoginRequest,
  MessageRequest,
  OperationView,
  ProposalView,
  QueueFilters,
  ReceiptView,
  ReviewRequest,
  ReviewResult,
  SessionView,
  TurnResult,
  VoiceSessionGrant,
} from './types'

// Thin typed wrappers over docs/contracts/openapi.json. Business rules live in Resolve, not here.

export const customerApi = {
  getSession: () => request<SessionView>('customer', 'GET', '/session'),
  createGuestSession: () => request<SessionView>('customer', 'POST', '/sessions/anonymous', { body: {} }),
  login: (body: LoginRequest) => request<SessionView>('customer', 'POST', '/demo/sessions', { body }),
  logout: () => request<void>('customer', 'DELETE', '/session'),

  createConversation: (language: Language, idempotencyKey = newId()) =>
    request<ConversationView>('customer', 'POST', '/conversations', { body: { language }, idempotencyKey }),
  getConversation: (id: string, signal?: AbortSignal) =>
    request<ConversationView>('customer', 'GET', `/conversations/${id}`, { signal }),
  /** Reuse body.client_turn_id when retrying the same turn; generate a new one for a new turn. */
  sendMessage: (conversationId: string, body: MessageRequest) =>
    request<TurnResult>('customer', 'POST', `/conversations/${conversationId}/messages`, { body }),
  resumeTurn: (conversationId: string, turnId: string, signal?: AbortSignal) =>
    request<TurnResult>('customer', 'POST', `/conversations/${conversationId}/turns/${turnId}/resume`, { body: {}, signal }),
  createVoiceSession: (conversationId: string) =>
    request<VoiceSessionGrant>('customer', 'POST', `/conversations/${conversationId}/voice-sessions`, { body: {} }),

  getCase: (id: string) => request<CaseView>('customer', 'GET', `/cases/${id}`),
  getOperation: (id: string, signal?: AbortSignal) =>
    request<OperationView>('customer', 'GET', `/operations/${id}`, { signal }),
  getReceipt: (caseId: string) => request<ReceiptView>('customer', 'GET', `/cases/${caseId}/receipt`),
  /** Returns a CREATE_REVIEW_TICKET proposal; the customer still confirms it like any other action. */
  requestReview: (caseId: string, body: EscalationRequest, idempotencyKey = newId()) =>
    request<ProposalView>('customer', 'POST', `/cases/${caseId}/escalations`, { body, idempotencyKey }),
}

export const agentApi = {
  getSession: () => request<AgentSessionView>('agent', 'GET', '/agent/session'),
  login: (body: LoginRequest) => request<AgentSessionView>('agent', 'POST', '/agent/sessions', { body }),
  logout: () => request<void>('agent', 'DELETE', '/agent/session'),

  listCases: (filters: QueueFilters = {}, signal?: AbortSignal) =>
    request<CaseQueue>('agent', 'GET', '/agent/cases', { query: filters, signal }),
  getCase: (id: string, signal?: AbortSignal) =>
    request<AgentCaseDetail>('agent', 'GET', `/agent/cases/${id}`, { signal }),
  updateReview: (caseId: string, body: ReviewRequest, idempotencyKey: string) =>
    request<ReviewResult>('agent', 'PATCH', `/agent/cases/${caseId}/review`, { body, idempotencyKey }),
}
