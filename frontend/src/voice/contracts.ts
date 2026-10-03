import type { components } from '@/api/schema'

type Schemas = components['schemas']

export type VoiceSessionGrant = Schemas['VoiceSessionGrant']
export type VoiceProposal = Schemas['Proposal']

/** Browser wire messages from the external Zeptaz Voice v2 runtime. */
export type VoiceResolveResult = {
  type: 'resolve_result'
  response_id: string
  case_id: string | null
  reply_text: string
  pending_question: string | null
  proposal: VoiceProposal | null
  operation_status: string | null
  end_session: boolean
}

export type VoiceServerMessage =
  | { type: 'ready'; session_id: string }
  | { type: 'greeting'; text: string }
  | { type: 'transcript'; speaker: 'user' | 'assistant'; text: string; final: boolean; response_id?: string }
  | VoiceResolveResult
  | { type: 'audio_start' | 'audio_end'; response_id: string }
  | { type: 'playback_ack' | 'proposal_ack'; response_id: string; accepted: boolean }
  | { type: 'interrupted'; response_id: string | null }
  | { type: 'error'; code: string; message?: string }
  | { type: 'ended'; reason: string }
