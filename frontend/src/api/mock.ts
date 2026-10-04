// Mock transport: answers API calls with the synthetic fixtures in docs/contracts/examples.json.
// It mimics the contract's shapes and status codes so screens can be built before Resolve exists.
// It does NOT implement business rules — never copy logic from here into real UI code.
import type { RawResponse, Realm, RequestOptions } from './client'
import { handleCustomer } from './mock-customer'
import { emptyState, example, fail, isExpired, ok, type MockState } from './mock-util'
import type { AgentCaseDetail, AgentSessionView, AuditEvent, CaseQueue, CaseQueueRow, ReviewRequest, ReviewResult, SessionView } from './types'

const SESSION_TTL_MS = 30 * 60 * 1000
const STORAGE_KEY = 'hutch-resolve.mock-state.v2'
const LATENCY_MS = 250

function loadState(): MockState {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY)
    if (raw) return { ...emptyState(), ...(JSON.parse(raw) as Partial<MockState>) }
  } catch {
    /* storage unavailable: fall through to a fresh state */
  }
  return emptyState()
}

const state = loadState()
let failNextLogout = false
let loseNextReviewResponse = false

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

function clearCustomerData() {
  state.conversations = {}
  state.cases = {}
  state.proposals = {}
  state.operations = {}
  state.receiptRevisions = {}
}

// --- Agent fixtures -------------------------------------------------------

const detailA = example<AgentCaseDetail>('agent_detail')
const queueD = example<CaseQueue>('queue').items[0]

function queueRows(): CaseQueueRow[] {
  const currentA = detailFor(detailA.case.id)!
  const rowA: CaseQueueRow = {
    case_id: currentA.case.id,
    line_alias: currentA.account.line_alias,
    complaint_type: currentA.case.complaint_type,
    evidence_state: currentA.case.investigation?.evidence_state ?? null,
    classification: currentA.case.investigation?.outcome?.classification ?? null,
    review_status: currentA.case.review_status,
    delivery_state: currentA.handoff?.delivery_state ?? null,
    updated_at: currentA.case.updated_at,
    version: currentA.case.version,
  }
  const currentD = detailFor(queueD.case_id)!
  return [{ ...queueD, review_status: currentD.case.review_status, version: currentD.case.version, updated_at: currentD.case.updated_at }, rowA]
}

function detailFor(id: string): AgentCaseDetail | null {
  if (state.agentDetails[id]) return state.agentDetails[id]
  if (id === detailA.case.id) return (state.agentDetails[id] = structuredClone(detailA))
  if (id === queueD.case_id) {
    // Fixture D has a queue row and an investigation but no full packet; assemble one for layout work.
    const d = structuredClone(detailA)
    const investigation = example<NonNullable<AgentCaseDetail['case']['investigation']>>('d_conflicting')
    d.case = {
      ...d.case,
      id: queueD.case_id,
      review_status: queueD.review_status,
      version: queueD.version,
      status: 'REVIEW_REQUIRED',
      investigation,
    }
    const observed = investigation.calculations[0]?.observed
    d.account = {
      ...d.account,
      line_alias: queueD.line_alias,
      display_name: 'Synthetic customer D',
      balances: d.account.balances.map((b) => (observed == null ? b : { ...b, amount_minor: observed })),
    }
    d.investigations = [investigation]
    d.proposals = []
    d.confirmations = []
    d.operations = []
    d.receipts = []
    d.handoff = example<AgentCaseDetail['handoff']>('crm_outage_handoff')
    return (state.agentDetails[id] = d)
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
      state.customer = { ...example<SessionView>('guest_session'), expires_at: freshExpiry() }
      return ok(state.customer, 201)
    }
    if (method === 'POST' && path === '/demo/sessions') {
      state.customer = { ...example<SessionView>('customer_session'), expires_at: freshExpiry() }
      clearCustomerData()
      return ok(state.customer)
    }
    if (method === 'DELETE' && path === '/session') {
      state.customer = null
      clearCustomerData()
      return ok(null, 204)
    }
    if (isExpired(state.customer)) return fail(401, 'SESSION_EXPIRED', 'session expired.')
    return handleCustomer(state, state.customer!, method, path, opts)
  }

  // realm === 'agent'
  if (method === 'GET' && path === '/agent/session') {
    if (isExpired(state.agent)) return fail(401, state.agent ? 'SESSION_EXPIRED' : 'UNAUTHENTICATED', 'no agent session.')
    return ok(state.agent)
  }
  if (method === 'POST' && path === '/agent/sessions') {
    state.agent = { ...example<AgentSessionView>('agent_session'), expires_at: freshExpiry() }
    return ok(state.agent)
  }
  if (method === 'DELETE' && path === '/agent/session') {
    if (failNextLogout) {
      failNextLogout = false
      return fail(503, 'DEPENDENCY_UNAVAILABLE', 'simulated logout failure.', true)
    }
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
  const reviewMatch = path.match(/^\/agent\/cases\/([^/]+)\/review$/)
  if (method === 'PATCH' && reviewMatch) return mockReview(reviewMatch[1], opts)
  return fail(404, 'RESOURCE_NOT_FOUND', `no mock for ${method} ${path}.`)
}

function mockReview(caseId: string, opts: RequestOptions): RawResponse {
  const detail = detailFor(caseId)
  if (!detail) return fail(404, 'RESOURCE_NOT_FOUND', 'case not found.')
  const body = opts.body as ReviewRequest
  const note = body.note?.trim() || undefined
  const reopenReason = body.reopen_reason?.trim() || undefined
  const fingerprint = JSON.stringify({ caseId, body })
  const key = opts.idempotencyKey ?? ''
  if (!key) return fail(422, 'VALIDATION_ERROR', 'Idempotency-Key is required.')
  const replay = state.reviewReplays[key]
  if (replay) return replay.fingerprint === fingerprint ? ok(replay.result) : fail(409, 'IDEMPOTENCY_CONFLICT', 'key was reused with a changed request.')
  if (body.expected_version !== detail.case.version) return fail(409, 'STALE_VERSION', 'case changed; reload before updating review.')
  const oldStatus = detail.case.review_status
  const status = body.review_status ?? oldStatus
  if (oldStatus === 'CLOSED' && status === 'NEW') return fail(409, 'INVALID_REVIEW_TRANSITION', 'review transition is not allowed.')
  if (oldStatus === 'IN_REVIEW' && status === 'NEW') return fail(409, 'INVALID_REVIEW_TRANSITION', 'review transition is not allowed.')
  if (!note && !body.review_status) return fail(422, 'VALIDATION_ERROR', 'note or status is required.')
  if (status === 'CLOSED' && (!note || !body.disposition)) return fail(422, 'VALIDATION_ERROR', 'closing requires a note and disposition.')
  if (oldStatus === 'CLOSED' && status === 'IN_REVIEW' && !reopenReason) return fail(422, 'VALIDATION_ERROR', 'reopening requires a reason.')
  const now = new Date().toISOString()
  const storedNote = note ?? reopenReason ?? `Review status changed to ${status}.`
  const entry = { id: crypto.randomUUID(), actor_id: 'mock-agent', note: storedNote, created_at: now, visibility: 'INTERNAL' as const }
  detail.case.version++
  detail.case.review_status = status
  detail.case.updated_at = now
  detail.review_notes.push(entry)
  const event: AuditEvent = {
    id: crypto.randomUUID(),
    event_type: 'REVIEW_UPDATED',
    actor_id: state.agent?.principal_id ?? 'mock-agent',
    created_at: now,
    details: { from: oldStatus, to: status, version: detail.case.version, disposition: body.disposition ?? null },
  }
  detail.audit_events.push(event)
  const result: ReviewResult = {
    case_id: caseId,
    version: detail.case.version,
    review_status: status,
    disposition: body.disposition ?? null,
    note: note || reopenReason ? entry : null,
    review_sync_state: detail.handoff?.delivery_state === 'DELIVERED' && detail.handoff.provider_ticket_id ? 'PENDING' : 'NOT_APPLICABLE',
    updated_at: now,
  }
  state.reviewReplays[key] = { fingerprint, result }
  return ok(result)
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
  if (loseNextReviewResponse && realm === 'agent' && method === 'PATCH' && path.includes('/review')) {
    loseNextReviewResponse = false
    return fail(503, 'DEPENDENCY_UNAVAILABLE', 'simulated lost review response after commit.', true)
  }
  return structuredClone(res)
}

/** Dev-only switches for exercising loading / error / expiry states. */
export const mockControls = {
  /** Fail the next agent logout without revoking its synthetic session. */
  failNextLogout() {
    failNextLogout = true
  },
  /** Commit the next review mutation but lose its response to exercise idempotent retry. */
  loseNextReviewResponse() {
    loseNextReviewResponse = true
  },
  /** Simulates a concurrent agent review so the stale-version draft path can be exercised. */
  reviewElsewhere(caseId: string, note = 'Another agent added a note.') {
    const detail = detailFor(caseId)
    if (!detail) throw new Error('Mock case does not exist')
    const result = mockReview(caseId, {
      body: { expected_version: detail.case.version, note },
      idempotencyKey: crypto.randomUUID(),
    })
    if (result.status !== 200) throw new Error(`Mock concurrent review failed: ${result.status}`)
    save()
  },
  expireSession(realm: Realm) {
    const s = state[realm]
    if (s) s.expires_at = new Date(Date.now() - 1000).toISOString()
    save()
  },
  /** Stand-in for the undecided guest→customer upgrade: become synthetic customer A. */
  continueAsDemoLine() {
    state.customer = { ...example<SessionView>('customer_session'), expires_at: freshExpiry() }
    clearCustomerData()
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
    Object.assign(state, emptyState())
    save()
  },
}
