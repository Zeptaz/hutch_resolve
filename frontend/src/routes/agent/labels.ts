import { humanize } from '@/lib/format'

// Agent-facing wording for Resolve codes. Unknown codes fall back to a readable form of the code,
// so a new backend code never breaks the page; it just reads less naturally until added here.

const REASON: Record<string, string> = {
  BALANCE_SNAPSHOT_DOES_NOT_MATCH_POSTINGS: 'The recorded balance does not match the postings',
  RECHARGE_RECORD_MISSING: 'No recharge record was found',
  OPENING_SNAPSHOT_MISSING: 'The opening balance snapshot is missing',
  VAS_ACTIVATION_EVIDENCE_MISSING: 'No record of the customer activating the service',
}

const FINDING: Record<string, string> = {
  LEDGER_CONFLICT: 'Ledger conflict',
  LEDGER_INCOMPLETE: 'Ledger incomplete',
  LEDGER_RECONCILED: 'Ledger reconciles',
  PAYMENT_CAPTURED_FULFILMENT_PENDING: 'Payment taken, recharge not fulfilled',
  QUOTA_RECONCILED: 'Data quota reconciles',
  RECHARGE_FULFILLED_AND_CREDITED: 'Recharge fulfilled and credited',
  RECHARGE_RECORD_NOT_FOUND: 'Recharge record not found',
  VAS_ACTIVATION_UNVERIFIED: 'Service activation not verified',
}

const SOURCE: Record<string, string> = {
  CHARGING_LEDGER: 'Charging ledger',
  QUOTA_LEDGER: 'Data quota ledger',
  RECHARGE_FULFILMENT: 'Recharge fulfilment',
  PRODUCT_VAS: 'Value-added services',
  CUSTOMER_REGISTRY: 'Customer registry',
  SERVICE_STATUS: 'Network service status',
}

const CALC: Record<string, string> = {
  BALANCE_LEDGER_RECONCILIATION: 'Balance reconciliation',
  QUOTA_BUCKET_RECONCILIATION: 'Data quota reconciliation',
}

const CASE_STATUS: Record<string, string> = {
  OPEN: 'Open',
  AWAITING_CUSTOMER: 'Waiting for customer',
  ACTION_PENDING: 'Action in progress',
  REVIEW_REQUIRED: 'Needs human review',
  RESOLVED: 'Resolved',
}

const ACTION: Record<string, string> = {
  DEACTIVATE_VAS: 'Stop subscription renewal',
  SEND_SETTINGS_INSTRUCTIONS: 'Send settings instructions',
  CREATE_REVIEW_TICKET: 'Create review ticket',
  ACTIVATE_PACKAGE: 'Activate package',
}

const QUEUE: Record<string, string> = { BILLING_REVIEW: 'Billing review', TECHNICAL_SUPPORT: 'Technical support' }

export const DISPOSITION: Record<string, string> = {
  REVIEW_COMPLETE: 'Review complete',
  NEEDS_OPERATOR_FOLLOWUP: 'Needs operator follow-up',
  CUSTOMER_WITHDREW: 'Customer withdrew',
}

export const DISPOSITION_HINT: Record<string, string> = {
  REVIEW_COMPLETE: 'You checked the evidence and nothing more is needed.',
  NEEDS_OPERATOR_FOLLOWUP: 'Someone outside this tool must act, for example the recharge operator.',
  CUSTOMER_WITHDREW: 'The customer no longer wants this looked at.',
}

const SYNC: Record<string, string> = {
  NOT_APPLICABLE: 'No ticket to sync',
  PENDING: 'Sync pending',
  UNKNOWN: 'Sync unconfirmed',
  SYNCED: 'Synced to ticket',
  FAILED: 'Sync failed',
  REVIEW_REQUIRED: 'Sync needs checking',
}

const TERM: Record<string, string> = {
  RECHARGE: 'Recharge',
  PACKAGE_RENEWAL: 'Package renewal',
  VAS_CHARGE: 'Value-added service charge',
  RATED_USAGE: 'Rated usage',
  OUT_OF_BUNDLE_USAGE: 'Out-of-bundle usage',
  CONSUME: 'Data used',
}

const pick = (map: Record<string, string>) => (code: string | null | undefined) => (code ? (map[code] ?? humanize(code)) : '')

export const reasonLabel = pick(REASON)
export const findingLabel = pick(FINDING)
export const sourceLabel = pick(SOURCE)
export const calcLabel = pick(CALC)
export const caseStatusLabel = pick(CASE_STATUS)
export const actionLabel = pick(ACTION)
export const queueLabel = pick(QUEUE)
export const dispositionLabel = pick(DISPOSITION)
export const syncLabel = pick(SYNC)
export const termLabel = pick(TERM)

/** One line per audit event, written for a reviewer scanning history. */
export function auditLabel(type: string, d: Record<string, unknown>): string {
  const s = (k: string) => (typeof d[k] === 'string' ? (d[k] as string) : '')
  switch (type) {
    case 'CASE_CREATED':
      return 'Case opened from the conversation'
    case 'INVESTIGATION_COMPLETED':
      return `Investigation revision ${String(d.revision ?? '')} finished: evidence ${humanize(s('evidence_state')).toLowerCase()}`
    case 'ACTION_PROPOSED':
      return `Offered: ${actionLabel(s('action_type'))}`
    case 'ACTION_CONFIRMED':
      return s('decision') === 'ACCEPT' ? 'Customer accepted the offer' : 'Customer declined the offer'
    case 'OPERATION_CHANGED':
      return `Action ${humanize(s('status')).toLowerCase()}${d.attempt ? ` (attempt ${String(d.attempt)})` : ''}`
    case 'REVIEW_UPDATED': {
      const from = s('from')
      const to = s('to')
      const disp = s('disposition')
      const change = from === to ? `Note added (${humanize(to).toLowerCase()})` : `Review ${humanize(from).toLowerCase()} → ${humanize(to).toLowerCase()}`
      return disp ? `${change}: ${dispositionLabel(disp).toLowerCase()}` : change
    }
    case 'REVIEW_SYNC_CHANGED':
      return `Ticket sync: ${syncLabel(s('state')).toLowerCase()}`
    default:
      return humanize(type)
  }
}
