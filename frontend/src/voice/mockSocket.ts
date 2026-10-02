import { newId } from '@/api/client'
import { customerApi } from '@/api/endpoints'
import type { VoiceProposal, VoiceServerMessage } from './contracts'
import type { VoiceSocket } from './socket'

/** Scripted browser transport for Vite mock mode; all case state stays in the shared mock API. */
class MockVoiceSocket implements VoiceSocket {
  binaryType: BinaryType = 'arraybuffer'
  readyState: number = WebSocket.CONNECTING
  onopen: (() => void) | null = null
  onmessage: ((event: { data: string | ArrayBuffer }) => void) | null = null
  onclose: ((event: { code: number; reason: string }) => void) | null = null
  onerror: (() => void) | null = null

  private readonly conversationId: string
  private reply: { id: string; proposal: VoiceProposal | null; complete: boolean; played: boolean } | null = null
  private presented: VoiceProposal | null = null
  private timers = new Set<number>()

  constructor(url: string) {
    this.conversationId = url.split('/').pop() ?? ''
    this.later(100, () => {
      this.readyState = WebSocket.OPEN
      this.onopen?.()
      this.emit({ type: 'ready', session_id: newId() })
      this.emit({ type: 'greeting', text: 'HUTCH Resolve demo. Tell me what you need help with.' })
    })
    this.later(120_000, () => this.end('session_limit'))
    mockVoice.active = this
  }

  send(data: string | ArrayBuffer) {
    if (typeof data !== 'string') return
    let message: { type?: string; response_id?: string; proposal_id?: string; proposal_hash?: string }
    try { message = JSON.parse(data) } catch { return }
    const reply = this.reply
    if (message.type === 'playback_complete') {
      const accepted = !!reply && reply.id === message.response_id && reply.complete && !reply.played
      if (accepted) reply!.played = true
      this.emit({ type: 'playback_ack', response_id: message.response_id ?? '', accepted })
    } else if (message.type === 'proposal_presented') {
      const accepted = !!reply && reply.id === message.response_id && reply.played &&
        reply.proposal?.id === message.proposal_id && reply.proposal.proposal_hash === message.proposal_hash
      if (accepted) this.presented = reply!.proposal
      this.emit({ type: 'proposal_ack', response_id: message.response_id ?? '', accepted })
    }
  }

  async say(text: string) {
    if (this.readyState !== WebSocket.OPEN || !text.trim()) return
    this.reply = null
    this.emit({ type: 'transcript', speaker: 'user', text, final: true })
    try {
      const conversation = await customerApi.getConversation(this.conversationId)
      const presented = this.presented
      this.presented = null
      const answer = /^\s*(yes|yeah|sure|go ahead|no|nope|decline)\b/i.exec(text)
      const input = presented && answer
        ? { type: 'action_decision' as const, proposal_id: presented.id, proposal_hash: presented.proposal_hash,
            decision: /^(no|nope|decline)/i.test(answer[1]) ? 'DECLINE' as const : 'ACCEPT' as const }
        : { type: 'text' as const, text }
      const result = await customerApi.sendMessage(this.conversationId, {
        client_turn_id: newId(), expected_version: conversation.version, language: conversation.language, input,
      })
      const confirmation = result.cards.find((card) => card.type === 'confirmation')
      const p = confirmation?.type === 'confirmation' ? confirmation.data : null
      const proposal: VoiceProposal | null = p ? {
        id: p.id, proposal_hash: p.proposal_hash, action_type: p.action_type,
        target_label: p.target_label, consequences: p.consequences, expires_at: p.expires_at,
      } : null
      const id = newId()
      this.reply = { id, proposal, complete: false, played: false }
      this.emit({ type: 'resolve_result', response_id: id, case_id: result.case_id, reply_text: result.reply_text,
        speech_text: result.reply_text, sensitive_audio: !!proposal || result.operation_ids.length > 0,
        pending_question: null, proposal, operation_status: result.operation_ids.length ? 'PENDING' : null,
        end_session: false })
      this.emit({ type: 'audio_start', response_id: id })
      this.onmessage?.({ data: tone(result.reply_text) })
      this.later(20, () => {
        if (this.reply?.id !== id) return
        this.reply.complete = true
        this.emit({ type: 'audio_end', response_id: id })
      })
    } catch {
      this.emit({ type: 'error', code: 'resolve_unavailable', message: 'Continue by text.' })
    }
  }

  interrupt() {
    const id = this.reply?.id ?? null
    this.reply = null
    this.presented = null
    this.emit({ type: 'interrupted', response_id: id })
  }

  close(code = 1000, reason = '') {
    if (this.readyState === WebSocket.CLOSED) return
    this.readyState = WebSocket.CLOSED
    for (const timer of this.timers) window.clearTimeout(timer)
    this.timers.clear()
    if (mockVoice.active === this) mockVoice.active = null
    this.onclose?.({ code, reason })
  }

  private end(reason: string) {
    this.emit({ type: 'ended', reason })
    this.close(1000, reason)
  }

  private emit(message: VoiceServerMessage) {
    if (this.readyState === WebSocket.OPEN) this.onmessage?.({ data: JSON.stringify(message) })
  }

  private later(delay: number, run: () => void) {
    const timer = window.setTimeout(() => { this.timers.delete(timer); run() }, delay)
    this.timers.add(timer)
  }
}

function tone(text: string): ArrayBuffer {
  const samples = new Int16Array(Math.round(24_000 * Math.min(5, Math.max(0.5, text.split(/\s+/).length * 0.2))))
  for (let i = 0; i < samples.length; i++) samples[i] = Math.sin(i * 2 * Math.PI * 220 / 24_000) * 900
  return samples.buffer
}

export const mockVoice = {
  active: null as MockVoiceSocket | null,
  say(text: string) { void this.active?.say(text) },
  interrupt() { this.active?.interrupt() },
}

export function openMockSocket(url: string): VoiceSocket {
  return new MockVoiceSocket(url)
}
