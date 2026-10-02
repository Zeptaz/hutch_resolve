// Scripted customer journey for mock mode, built from contract fixture A (balance after recharge).
// It imitates the *shape* of Resolve/conversation responses so every chat state can be exercised.
// The branching here is a test script, not product logic — the real decisions come from the backend.
import type { RawResponse, RequestOptions } from './client'
import { COMPLAINT_LABEL } from '@/lib/labels'
import { example, fail, ok, randomHash, type MockState } from './mock-util'
import type {
  AccountView,
  Card,
  CaseView,
  ConversationView,
  EscalationRequest,
  Handoff,
  InvestigationResult,
  MessageView,
  OperationView,
  PendingQuestion,
  ProposalView,
  ReceiptView,
  SessionView,
  TurnInput,
  TurnResult,
} from './types'

const PROPOSAL_TTL_MS = 5 * 60 * 1000
const OP_PENDING_MS = 1200
const OP_RUNNING_MS = 2800

type TurnReply = Omit<TurnResult, 'message_id' | 'conversation_id' | 'conversation_version' | 'simulation'>

type MessageBody = { client_turn_id: string; expected_version: number; language: string; input: TurnInput }

export function handleCustomer(
  state: MockState,
  session: SessionView,
  method: string,
  path: string,
  opts: RequestOptions,
): RawResponse {
  if (method === 'POST' && path === '/conversations') {
    const lang = (opts.body as { language?: ConversationView['language'] })?.language ?? 'en'
    const conv: ConversationView = {
      ...example<ConversationView>('conversation'),
      id: crypto.randomUUID(),
      version: 1,
      language: lang,
      active_case_id: null,
      messages: [],
      cases: [],
      pending_question: null,
      pending_proposal: null,
      operation_ids: [],
      expires_at: session.expires_at,
    }
    state.conversations[conv.id] = conv
    return ok(conv, 201)
  }

  let m = path.match(/^\/conversations\/([^/]+)$/)
  if (m && method === 'GET') {
    const conv = state.conversations[m[1]]
    return conv ? ok(conv) : fail(404, 'RESOURCE_NOT_FOUND', 'conversation not found.')
  }

  m = path.match(/^\/conversations\/([^/]+)\/messages$/)
  if (m && method === 'POST') {
    const conv = state.conversations[m[1]]
    if (!conv) return fail(404, 'RESOURCE_NOT_FOUND', 'conversation not found.')
    return handleTurn(state, session, conv, opts.body as MessageBody)
  }

  m = path.match(/^\/operations\/([^/]+)$/)
  if (m && method === 'GET') {
    const entry = state.operations[m[1]]
    if (!entry) return fail(404, 'RESOURCE_NOT_FOUND', 'operation not found.')
    return ok(advanceOperation(state, entry))
  }

  m = path.match(/^\/cases\/([^/]+)$/)
  if (m && method === 'GET') {
    const c = state.cases[m[1]]
    return c ? ok(c) : fail(404, 'RESOURCE_NOT_FOUND', 'case not found.')
  }

  m = path.match(/^\/cases\/([^/]+)\/receipt$/)
  if (m && method === 'GET') {
    const c = state.cases[m[1]]
    if (!c?.receipt) return fail(404, 'RESOURCE_NOT_FOUND', 'no receipt has been issued for this case yet.')
    return ok(buildReceipt(state, c))
  }

  m = path.match(/^\/cases\/([^/]+)\/escalations$/)
  if (m && method === 'POST') {
    const c = state.cases[m[1]]
    if (!c) return fail(404, 'RESOURCE_NOT_FOUND', 'case not found.')
    const body = opts.body as EscalationRequest
    if (body.expected_version !== c.version) {
      return fail(409, 'STALE_VERSION', 'case changed.', false, { current_version: c.version })
    }
    const proposal: ProposalView = {
      ...example<ProposalView>('proposal'),
      id: crypto.randomUUID(),
      case_id: c.id,
      investigation_id: body.investigation_id,
      action_type: 'CREATE_REVIEW_TICKET',
      target_id: c.account_id,
      target_version: null,
      target_label: 'Billing review by a HUTCH agent',
      consequences: 'Creates a review ticket with the evidence above. A reviewer checks it; this does not issue a refund.',
      proposal_hash: randomHash(),
      expires_at: new Date(Date.now() + PROPOSAL_TTL_MS).toISOString(),
    }
    state.proposals[proposal.id] = proposal
    const conv = state.conversations[c.conversation_id]
    if (conv) conv.pending_proposal = proposal
    return ok(proposal, 201)
  }

  return fail(404, 'RESOURCE_NOT_FOUND', `no mock for ${method} ${path}.`)
}

// --- Turns ------------------------------------------------------------------

function handleTurn(state: MockState, session: SessionView, conv: ConversationView, body: MessageBody): RawResponse {
  const replay = conv.messages.find((msg) => msg.client_turn_id === body.client_turn_id && msg.speaker === 'ASSISTANT')
  if (replay?.result) return ok(replay.result)
  if (body.expected_version !== conv.version) {
    return fail(409, 'STALE_VERSION', 'stale conversation version.', false, { current_version: conv.version })
  }

  const input = body.input
  let reply: TurnReply

  if (input.type === 'action_decision') {
    const res = decide(state, conv, input)
    if ('error' in res) return res.error
    reply = res.reply
  } else if (session.role === 'GUEST') {
    reply = plain(
      'I can answer general questions about HUTCH prepaid services. To look into your own balance, data or ' +
        'subscriptions I need to know which line is yours. (Mock: use “Mock controls → Continue as demo line A”.)',
    )
  } else if (input.type === 'case_selection') {
    conv.active_case_id = input.case_id
    reply = { ...plain('Okay, we are now looking at that case.'), case_id: input.case_id }
  } else if (input.type === 'complaint_details') {
    reply = investigate(state, conv)
  } else if (input.type === 'category_selection') {
    reply =
      input.complaint_type === 'BALANCE_RECHARGE'
        ? ask('COMPLAINT_DETAILS', 'When did this happen, and how much did you recharge?', ['complaint_details', 'text'])
        : plain('Mock: only the balance-after-recharge journey (fixture A) is scripted. Try “Balance or recharge”.')
  } else if (/balance|recharge|lkr|charge|money/i.test(input.text)) {
    reply = investigate(state, conv)
  } else {
    reply = ask('COMPLAINT_CATEGORY', 'Which of these best describes the problem?', ['category_selection', 'text'])
  }

  return ok(record(conv, body, reply))
}

function record(conv: ConversationView, body: MessageBody, reply: TurnReply) {
  const now = new Date().toISOString()
  const result: TurnResult = {
    ...reply,
    message_id: crypto.randomUUID(),
    conversation_id: conv.id,
    conversation_version: conv.version + 1,
    simulation: true,
  }
  const userMsg: MessageView = {
    id: crypto.randomUUID(),
    client_turn_id: body.client_turn_id,
    speaker: 'USER',
    body: describeInput(body.input, conv),
    created_at: now,
    result: null,
  }
  const botMsg: MessageView = {
    id: result.message_id,
    client_turn_id: body.client_turn_id,
    speaker: 'ASSISTANT',
    body: result.reply_text,
    created_at: now,
    result,
  }
  conv.messages.push(userMsg, botMsg)
  conv.version += 1
  conv.pending_question = result.pending_question
  conv.operation_ids = [...new Set([...conv.operation_ids, ...result.operation_ids])]
  return result
}

function describeInput(input: TurnInput, conv: ConversationView): string {
  switch (input.type) {
    case 'text':
      return input.text
    case 'category_selection':
      return labelFor(input.complaint_type)
    case 'complaint_details':
      return `${labelFor(input.complaint_type)}: details sent`
    case 'case_selection':
      return `Switch to case ${conv.cases.find((c) => c.id === input.case_id) ? labelFor(conv.cases.find((c) => c.id === input.case_id)!.complaint_type) : ''}`.trim()
    case 'action_decision':
      return input.decision === 'ACCEPT' ? 'Yes, go ahead.' : 'No, don’t make that change.'
  }
}

function labelFor(t: string) {
  return COMPLAINT_LABEL[t as keyof typeof COMPLAINT_LABEL] ?? t
}

function syncSummary(state: MockState, c: CaseView) {
  for (const conv of Object.values(state.conversations)) {
    const s = conv.cases.find((x) => x.id === c.id)
    if (s) s.status = c.status
  }
}

function plain(text: string): TurnReply {
  return { case_id: null, reply_text: text, cards: [], citations: [], pending_question: null, operation_ids: [] }
}

function ask(code: string, text: string, allowed: PendingQuestion['allowed_input_types']): TurnReply {
  return { ...plain(text), pending_question: { code, text, allowed_input_types: allowed } }
}

/** Fixture A: reconciled balance, plus an eligible VAS deactivation offered for confirmation. */
function investigate(state: MockState, conv: ConversationView): TurnReply {
  const base = example<CaseView>('case')
  const inv = example<InvestigationResult>('a_sufficient')
  const caseView: CaseView = { ...base, conversation_id: conv.id, investigation: inv, receipt: null, operation_ids: [] }
  state.cases[caseView.id] = caseView
  conv.active_case_id = caseView.id
  if (!conv.cases.some((c) => c.id === caseView.id)) {
    conv.cases.push({ id: caseView.id, complaint_type: caseView.complaint_type, status: caseView.status })
  }

  const proposal: ProposalView = {
    ...example<ProposalView>('proposal'),
    id: crypto.randomUUID(),
    case_id: caseView.id,
    proposal_hash: randomHash(),
    expires_at: new Date(Date.now() + PROPOSAL_TTL_MS).toISOString(),
  }
  state.proposals[proposal.id] = proposal
  conv.pending_proposal = proposal

  const calc = inv.calculations[0]
  const cards: Card[] = [
    { type: 'account', data: example<AccountView>('account') },
    {
      type: 'timeline',
      data: {
        items: calc.terms.map((t, i) => {
          const at = new Date(Date.parse(inv.window_start) + (i + 1) * 45 * 60 * 1000).toISOString()
          return {
            evidence_id: t.evidence_id,
            occurred_at: at,
            recorded_at: at,
            label: t.label,
            amount_minor: t.value,
            bytes: null,
          }
        }),
      },
    },
    { type: 'calculation', data: calc },
    ...inv.findings.map((f): Card => ({ type: 'finding', data: f })),
    { type: 'confirmation', data: proposal },
  ]
  return {
    case_id: caseView.id,
    reply_text:
      'I checked your recharge and the charges after it. They add up exactly to your current balance of LKR 420. ' +
      'Part of it was a recurring “Synthetic video alerts” subscription — I can stop it renewing if you want.',
    cards,
    citations: [],
    pending_question: null,
    operation_ids: [],
  }
}

function decide(
  state: MockState,
  conv: ConversationView,
  input: Extract<TurnInput, { type: 'action_decision' }>,
): { error: RawResponse } | { reply: TurnReply } {
  const p = state.proposals[input.proposal_id]
  if (!p || p.proposal_hash !== input.proposal_hash || p.decided) {
    return { error: fail(409, 'PROPOSAL_INVALIDATED', 'this proposal is no longer valid.') }
  }
  if (Date.parse(p.expires_at) <= Date.now()) {
    return { error: fail(422, 'PROPOSAL_EXPIRED', 'this proposal expired. Ask again for a new one.') }
  }
  p.decided = true
  conv.pending_proposal = null
  const c = state.cases[p.case_id]

  if (input.decision === 'DECLINE') {
    issueReceipt(state, c)
    return { reply: { ...plain('Okay — I have not changed anything.'), case_id: p.case_id } }
  }

  const now = new Date().toISOString()
  const op: OperationView = {
    ...example<OperationView>('succeeded_operation'),
    id: crypto.randomUUID(),
    case_id: p.case_id,
    proposal_id: p.id,
    action_type: p.action_type,
    status: 'PENDING',
    created_at: now,
    updated_at: now,
    provider_operation_id: null,
    outcome: { code: null, message: null, actual_target_status: null, provider_ticket_id: null },
    next_step: 'Waiting for the provider to confirm.',
  }
  state.operations[op.id] = { op, createdMs: Date.now() }
  if (c) {
    c.status = 'ACTION_PENDING'
    c.operation_ids = [...c.operation_ids, op.id]
    syncSummary(state, c)
  }

  const isTicket = p.action_type === 'CREATE_REVIEW_TICKET'
  const cards: Card[] = isTicket
    ? [{ type: 'ticket', data: { ...example<Handoff>('crm_outage_handoff'), reference: crypto.randomUUID(), delivery_state: 'PENDING' } }]
    : []
  return {
    reply: {
      ...plain(
        isTicket
          ? 'Thanks — I have asked for a human review. You can track it below.'
          : 'Thanks — I have asked the system to stop the subscription renewing. I will confirm once it is done.',
      ),
      case_id: p.case_id,
      cards,
      operation_ids: [op.id],
    },
  }
}

function advanceOperation(state: MockState, entry: { op: OperationView; createdMs: number }): OperationView {
  const elapsed = Date.now() - entry.createdMs
  const { op } = entry
  if (op.status === 'SUCCEEDED') return op
  if (elapsed < OP_PENDING_MS) return op
  op.updated_at = new Date().toISOString()
  if (elapsed < OP_RUNNING_MS) {
    op.status = 'RUNNING'
    return op
  }
  op.status = 'SUCCEEDED'
  op.provider_operation_id = `SIM-OP-${op.id.slice(0, 8)}`
  if (op.action_type === 'CREATE_REVIEW_TICKET') {
    op.outcome = { code: 'TICKET_CREATED', message: 'Review ticket created.', actual_target_status: null, provider_ticket_id: `SIM-TKT-${op.id.slice(0, 6)}` }
    op.next_step = 'A reviewer will look at your case. You will be contacted through your usual channel.'
  } else {
    op.outcome = { code: 'DEACTIVATED', message: 'Subscription renewal stopped.', actual_target_status: 'INACTIVE', provider_ticket_id: null }
    op.next_step = 'The subscription will not renew. Past charges are not refunded by this action.'
  }
  const c = state.cases[op.case_id]
  if (c) {
    c.status = 'RESOLVED'
    issueReceipt(state, c)
    syncSummary(state, c)
  }
  return op
}

function issueReceipt(state: MockState, c: CaseView | undefined) {
  if (!c) return
  const rev = (state.receiptRevisions[c.id] ?? 0) + 1
  state.receiptRevisions[c.id] = rev
  c.receipt = { id: c.receipt?.id ?? crypto.randomUUID(), revision: rev }
  c.version += 1
}

function buildReceipt(state: MockState, c: CaseView): ReceiptView {
  const base = example<ReceiptView>('receipt')
  const proposals = Object.values(state.proposals).filter((p) => p.case_id === c.id)
  return {
    ...base,
    id: c.receipt!.id,
    case_id: c.id,
    revision: c.receipt!.revision,
    issued_at: new Date().toISOString(),
    actions: proposals.map((p) => {
      const op = Object.values(state.operations).find((o) => o.op.proposal_id === p.id)?.op
      return {
        proposal_id: p.id,
        action_type: p.action_type,
        requested: !!p.decided,
        decision: p.decided ? (op ? 'ACCEPT' : 'DECLINE') : null,
        operation_id: op?.id ?? null,
        operation_status: op?.status ?? null,
        completed: op?.status === 'SUCCEEDED',
      }
    }),
  }
}
