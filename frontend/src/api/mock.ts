// Mock transport: answers API calls with the synthetic fixtures in docs/contracts/examples.json.
// It mimics the contract's shapes and status codes so screens can be built before Resolve exists.
// It does NOT implement business rules — never copy logic from here into real UI code.
import fixtures from '@contracts/examples.json'
import type { RawResponse, Realm, RequestOptions } from './client'
import type {
  AgentCaseDetail,
  AgentSessionView,
  CaseQueue,
  CaseQueueRow,
  ConversationView,
  MessageView,
  SessionView,
  TurnResult,
} from './types'

type Examples = typeof fixtures.examples
function example<K extends keyof Examples>(key: K): unknown {
  return structuredClone(fixtures.examples[key].value)
}

const SESSION_TTL_MS = 30 * 60 * 1000
const STORAGE_KEY = 'hutch-resolve.mock-state'
const LATENCY_MS = 250

type MockState = {
  customer: SessionView | null
  agent: AgentSessionView | null
  conversations: Record<string, ConversationView>
  outage: boolean
}

function loadState(): MockState {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY)
    if (raw) return JSON.parse(raw) as MockState
  } catch {
    /* storage unavailable: fall through to a fresh state */
  }
  return { customer: null, agent: null, conversations: {}, outage: false }
}

const state = loadState()

function save() {
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(state))
  } catch {
    /* ignore */
  }
}

function freshExpiry() {
  return new Date(Date.now() + SESSION_TTL_MS).toISOString()
}

function isExpired(s: { expires_at: string } | null) {
  return !s || Date.parse(s.expires_at) <= Date.now()
}

function ok(body: unknown, status = 200): RawResponse {
  return { status, body }
}

function fail(status: number, code: string, message: string, retryable = false, details = {}): RawResponse {
  return {
    status,
    body: { error: { code, message: `Mock: ${message}`, retryable, request_id: crypto.randomUUID(), details } },
  }
}

// --- Agent fixtures -------------------------------------------------------

const detailA = example('agent_detail') as AgentCaseDetail
const queueD = (example('queue') as CaseQueue).items[0]

function queueRows(): CaseQueueRow[] {
  const rowA: CaseQueueRow = {
    case_id: detailA.case.id,
    line_alias: detailA.account.line_alias,
    complaint_type: detailA.case.complaint_type,
    evidence_state: detailA.case.investigation?.evidence_state ?? null,
    review_status: detailA.case.review_status,
    delivery_state: detailA.handoff?.delivery_state ?? null,
    updated_at: detailA.case.updated_at,
    version: detailA.case.version,
  }
  return [queueD, rowA]
}

function detailFor(id: string): AgentCaseDetail | null {
  if (id === detailA.case.id) return structuredClone(detailA)
  if (id === queueD.case_id) {
    // Fixture D has a queue row and an investigation but no full packet; assemble one for layout work.
    const d = structuredClone(detailA)
    const investigation = example('d_conflicting') as NonNullable<AgentCaseDetail['case']['investigation']>
    d.case = {
      ...d.case,
      id: queueD.case_id,
      review_status: queueD.review_status,
      version: queueD.version,
      status: 'REVIEW_REQUIRED',
      investigation,
    }
    d.account = { ...d.account, line_alias: queueD.line_alias, display_name: 'Synthetic customer D' }
    d.investigations = [investigation]
    d.proposals = []
    d.confirmations = []
    d.operations = []
    d.receipts = []
    d.handoff = example('crm_outage_handoff') as AgentCaseDetail['handoff']
    return d
  }
  return null
}

// --- Router ---------------------------------------------------------------

function route(realm: Realm, method: string, path: string, opts: RequestOptions): RawResponse {
  if (state.outage && !path.includes('session')) {
    return fail(503, 'DEPENDENCY_UNAVAILABLE', 'simulated outage.', true)
  }

  if (realm === 'customer') {
    if (method === 'GET' && path === '/session') {
      if (isExpired(state.customer)) return fail(401, state.customer ? 'SESSION_EXPIRED' : 'UNAUTHENTICATED', 'no session.')
      return ok(state.customer)
    }
    if (method === 'POST' && path === '/sessions/anonymous') {
      state.customer = { ...(example('guest_session') as SessionView), expires_at: freshExpiry() }
      return ok(state.customer, 201)
    }
    if (method === 'POST' && path === '/demo/sessions') {
      state.customer = { ...(example('customer_session') as SessionView), expires_at: freshExpiry() }
      return ok(state.customer)
    }
    if (method === 'DELETE' && path === '/session') {
      state.customer = null
      state.conversations = {}
      return ok(null, 204)
    }

    if (isExpired(state.customer)) return fail(401, 'SESSION_EXPIRED', 'session expired.')
    const session = state.customer!

    if (method === 'POST' && path === '/conversations') {
      const lang = (opts.body as { language?: ConversationView['language'] })?.language ?? 'en'
      const base = example('conversation') as ConversationView
      // Guests only get public FAQ access, so they start with an empty conversation and no cases.
      const conv: ConversationView =
        session.role === 'GUEST'
          ? { ...base, id: crypto.randomUUID(), version: 1, language: lang, active_case_id: null, messages: [], cases: [], pending_question: null, pending_proposal: null }
          : { ...base, language: lang }
      conv.expires_at = session.expires_at
      state.conversations[conv.id] = conv
      return ok(conv, 201)
    }
    const convMatch = path.match(/^\/conversations\/([^/]+)(\/messages)?$/)
    if (convMatch) {
      const conv = state.conversations[convMatch[1]]
      if (!conv) return fail(404, 'RESOURCE_NOT_FOUND', 'conversation not found.')
      if (method === 'GET' && !convMatch[2]) return ok(conv)
      if (method === 'POST' && convMatch[2]) {
        const body = opts.body as { client_turn_id: string; expected_version: number; input: { type: string; text?: string } }
        const replay = conv.messages.find((m) => m.client_turn_id === body.client_turn_id && m.speaker === 'ASSISTANT')
        if (replay?.result) return ok(replay.result)
        if (body.expected_version !== conv.version) {
          return fail(409, 'STALE_VERSION', 'stale conversation version.', false, { current_version: conv.version })
        }
        const now = new Date().toISOString()
        const userMsg: MessageView = {
          id: crypto.randomUUID(),
          client_turn_id: body.client_turn_id,
          speaker: 'USER',
          body: body.input.text ?? `[${body.input.type}]`,
          created_at: now,
          result: null,
        }
        const result = example('turn_result') as TurnResult
        const assistantId = crypto.randomUUID()
        result.message_id = assistantId
        result.conversation_id = conv.id
        result.conversation_version = conv.version + 1
        const assistantMsg: MessageView = {
          id: assistantId,
          client_turn_id: body.client_turn_id,
          speaker: 'ASSISTANT',
          body: result.reply_text,
          created_at: now,
          result,
        }
        conv.messages.push(userMsg, assistantMsg)
        conv.version += 1
        return ok(result)
      }
    }
    return fail(404, 'RESOURCE_NOT_FOUND', `no mock for ${method} ${path}.`)
  }

  // realm === 'agent'
  if (method === 'GET' && path === '/agent/session') {
    if (isExpired(state.agent)) return fail(401, state.agent ? 'SESSION_EXPIRED' : 'UNAUTHENTICATED', 'no agent session.')
    return ok(state.agent)
  }
  if (method === 'POST' && path === '/agent/sessions') {
    state.agent = { ...(example('agent_session') as AgentSessionView), expires_at: freshExpiry() }
    return ok(state.agent)
  }
  if (method === 'DELETE' && path === '/agent/session') {
    state.agent = null
    return ok(null, 204)
  }
  if (isExpired(state.agent)) return fail(401, 'SESSION_EXPIRED', 'agent session expired.')

  if (method === 'GET' && path === '/agent/cases') {
    const q = (opts.query ?? {}) as Record<string, string | undefined>
    const items = queueRows().filter(
      (r) =>
        (!q.review_status || r.review_status === q.review_status) &&
        (!q.complaint_type || r.complaint_type === q.complaint_type) &&
        (!q.evidence_state || r.evidence_state === q.evidence_state) &&
        (!q.delivery_state || r.delivery_state === q.delivery_state) &&
        (!q.search || r.case_id === q.search || r.line_alias === q.search),
    )
    return ok({ items, next_cursor: null } satisfies CaseQueue)
  }
  const caseMatch = path.match(/^\/agent\/cases\/([^/]+)$/)
  if (method === 'GET' && caseMatch) {
    const d = detailFor(caseMatch[1])
    return d ? ok(d) : fail(404, 'RESOURCE_NOT_FOUND', 'case not found.')
  }
  return fail(404, 'RESOURCE_NOT_FOUND', `no mock for ${method} ${path}.`)
}

export async function mockTransport(
  realm: Realm,
  method: 'GET' | 'POST' | 'PATCH' | 'DELETE',
  path: string,
  opts: RequestOptions,
): Promise<RawResponse> {
  await new Promise((r) => setTimeout(r, LATENCY_MS))
  if (opts.signal?.aborted) throw new DOMException('Aborted', 'AbortError')
  const res = route(realm, method, path, opts)
  save()
  return structuredClone(res)
}

/** Dev-only switches for exercising loading / error / expiry states. */
export const mockControls = {
  expireSession(realm: Realm) {
    const s = state[realm]
    if (s) s.expires_at = new Date(Date.now() - 1000).toISOString()
    save()
  },
  setOutage(on: boolean) {
    state.outage = on
    save()
  },
  get outage() {
    return state.outage
  },
  reset() {
    state.customer = null
    state.agent = null
    state.conversations = {}
    state.outage = false
    save()
  },
}
