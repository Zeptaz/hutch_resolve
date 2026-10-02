// Shared helpers for the mock transport. Mock-only; never import from UI code.
import fixtures from '@contracts/examples.json'
import type { RawResponse } from './client'
import type { AgentSessionView, CaseView, ConversationView, OperationView, ProposalView, SessionView } from './types'

type Examples = typeof fixtures.examples

export function example<T = unknown, K extends keyof Examples = keyof Examples>(key: K): T {
  return structuredClone(fixtures.examples[key].value) as T
}

export type MockOperation = { op: OperationView; createdMs: number }

export type MockState = {
  customer: SessionView | null
  agent: AgentSessionView | null
  conversations: Record<string, ConversationView>
  cases: Record<string, CaseView>
  proposals: Record<string, ProposalView & { decided?: boolean }>
  operations: Record<string, MockOperation>
  receiptRevisions: Record<string, number>
  outage: boolean
}

export function emptyState(): MockState {
  return {
    customer: null,
    agent: null,
    conversations: {},
    cases: {},
    proposals: {},
    operations: {},
    receiptRevisions: {},
    outage: false,
  }
}

export function ok(body: unknown, status = 200): RawResponse {
  return { status, body }
}

export function fail(status: number, code: string, message: string, retryable = false, details = {}): RawResponse {
  return {
    status,
    body: { error: { code, message: `Mock: ${message}`, retryable, request_id: crypto.randomUUID(), details } },
  }
}

export function isExpired(s: { expires_at: string } | null) {
  return !s || Date.parse(s.expires_at) <= Date.now()
}

export function randomHash() {
  return Array.from(crypto.getRandomValues(new Uint8Array(32)), (b) => b.toString(16).padStart(2, '0')).join('')
}
