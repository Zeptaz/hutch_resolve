// Mock transport: answers API calls with the synthetic fixtures in docs/contracts/examples.json.
// It mimics the contract's shapes and status codes so screens can be built before Resolve exists.
// It does NOT implement business rules — never copy logic from here into real UI code.
import type { RawResponse, Realm, RequestOptions } from './client'
import { handleCustomer } from './mock-customer'
import { emptyState, example, fail, isExpired, ok, type MockState } from './mock-util'
import type { AgentCaseDetail, AgentSessionView, CaseQueue, CaseQueueRow, ReviewNote, ReviewRequest, ReviewResult, ReviewStatus, SessionView } from './types'

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

// Review changes made in mock mode (memory only; a reload starts over). Mirrors the contract's
// version check and allowed transitions so the conflict and validation UI can be exercised.
const reviews: Record<string, { status: ReviewStatus; version: number; notes: ReviewNote[]; updated_at: string }> = {}

function withReview<T extends { case_id?: string; review_status: ReviewStatus; version: number; updated_at: string }>(id: string, row: T): T {
  const r = reviews[id]
  return r ? { ...row, review_status: r.status, version: r.version, updated_at: r.updated_at } : row
}

function patchReview(id: string, body: ReviewRequest, current: { review_status: ReviewStatus; version: number }): RawResponse {
  const base = reviews[id] ?? { status: current.review_status, version: current.version, notes: [], updated_at: '' }
  if (body.expected_version !== base.version) return fail(409, 'STALE_VERSION', 'Case changed; reload before updating review.')
  const to = body.review_status ?? base.status
  const allowed: Record<ReviewStatus, ReviewStatus[]> = { NEW: ['NEW', 'IN_REVIEW', 'CLOSED'], IN_REVIEW: ['IN_REVIEW', 'CLOSED'], CLOSED: ['CLOSED', 'IN_REVIEW'] }
  if (!allowed[base.status].includes(to)) return fail(409, 'INVALID_REVIEW_TRANSITION', 'Review status transition is not allowed.')
  if (to === 'CLOSED' && (!body.disposition || !body.note)) return fail(422, 'VALIDATION_ERROR', 'Closing review requires disposition and explanatory note.')
  if (base.status === 'CLOSED' && to === 'IN_REVIEW' && !body.reopen_reason) return fail(422, 'VALIDATION_ERROR', 'Reopening review requires a reason.')
  const now = new Date().toISOString()
  const text = body.note ?? body.reopen_reason
  const note: ReviewNote | null = text ? { id: crypto.randomUUID(), actor_id: state.agent?.principal_id ?? 'agent', note: text, created_at: now, visibility: 'INTERNAL' } : null
  reviews[id] = { status: to, version: base.version + 1, notes: note ? [...base.notes, note] : base.notes, updated_at: now }
  return ok({ case_id: id, version: base.version + 1, review_status: to, disposition: body.disposition ?? null, note, review_sync_state: 'NOT_APPLICABLE', updated_at: now } satisfies ReviewResult)
}

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
  return [withReview(queueD.case_id, queueD), withReview(rowA.case_id, rowA)]
}

function detailFor(id: string): AgentCaseDetail | null {
  const d = baseDetailFor(id)
  const r = reviews[id]
  if (!d || !r) return d
  d.case = { ...d.case, review_status: r.status, version: r.version, updated_at: r.updated_at }
  d.review_notes = [...d.review_notes, ...r.notes]
  return d
}

function baseDetailFor(id: string): AgentCaseDetail | null {
  if (id === detailA.case.id) return structuredClone(detailA)
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
  if (method === 'PATCH' && reviewMatch) {
    const d = detailFor(reviewMatch[1])
    return d ? patchReview(reviewMatch[1], opts.body as ReviewRequest, d.case) : fail(404, 'RESOURCE_NOT_FOUND', 'case not found.')
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
