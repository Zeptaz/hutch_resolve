import type { MessageRequest, ProposalView } from '@/api/types'
import type { VoiceProposal } from './contracts'

export type OfferState = {
  data: VoiceProposal
  sending: boolean
  error: unknown
  retry: { decision: 'ACCEPT' | 'DECLINE'; body: MessageRequest } | null
}

function offerFromProposal(data: VoiceProposal): OfferState {
  return { data, sending: false, error: null, retry: null }
}

/** A Voice reply can omit an offer that is still pending in Resolve. */
export function offerAfterVoiceResult(current: OfferState | null, proposal: VoiceProposal | null): OfferState | null {
  if (!proposal) return current
  if (current?.data.id === proposal.id && current.data.proposal_hash === proposal.proposal_hash) return current
  return offerFromProposal(proposal)
}

/** The scoped conversation is authoritative for whether an offer remains open. */
export function reconcileVoiceOffer(current: OfferState | null, pending: ProposalView | null): OfferState | null {
  if (!pending) return null
  if (current?.data.id === pending.id && current.data.proposal_hash === pending.proposal_hash) return current
  return offerFromProposal({
    id: pending.id, proposal_hash: pending.proposal_hash, action_type: pending.action_type,
    target_label: pending.target_label, consequences: pending.consequences,
    expires_at: pending.expires_at, package_terms: pending.package_terms,
  })
}
