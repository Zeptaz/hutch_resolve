import { API_MODE, newId, request } from '@/api/client'
import type { VoiceProposal, VoiceResolveResult, VoiceServerMessage, VoiceSessionGrant } from './contracts'
import { CallAudio, MicError, type MicErrorKind } from './audio'
import { openVoiceSocket, VOICE_PROTOCOL, type VoiceSocket } from './socket'

/** Voice caps a call at 120 s (HUTCH_VOICE_MAX_SESSION_SECONDS). */
export const CALL_LIMIT_MS = 120_000

/** The server permanently records an expired grant key, so a later user retry must rotate it. */
export function shouldRotateGrantKey(error: unknown): boolean {
  return error instanceof Error && 'code' in error &&
    (error as { code?: string }).code === 'VOICE_GRANT_EXPIRED'
}

/** Return the key a repeated request must use after this failure. */
export function grantKeyAfterFailure(key: string, error: unknown): string | null {
  return shouldRotateGrantKey(error) ? null : key
}

/** Async work from a prior attempt must not mutate or tear down the currently active call. */
export function isCurrentCallAttempt(active: object | null, attempt: object): boolean {
  return active === attempt
}

export type CallPhase = 'idle' | 'requesting' | 'connecting' | 'live' | 'ended'
export type CallActivity = 'listening' | 'thinking' | 'speaking'

/**
 * reading: being spoken, not yet acknowledged.
 * awaiting: Voice acknowledged the presentation, so a spoken yes/no on the next turn can count.
 * text-only: could not be presented by voice (speech check failed or ack refused); answer by text.
 * interrupted: the caller talked over it before it finished.
 */
export type ProposalStatus = 'reading' | 'awaiting' | 'text-only' | 'interrupted'

export type CallError =
  | { kind: 'mic'; mic: MicErrorKind }
  | { kind: 'grant'; error: unknown }
  | { kind: 'voice'; code: string }

export type CallHandlers = {
  /** The caller's finalized words. */
  onTranscript?: (text: string) => void
  /** Voice's greeting when the call opens. */
  onGreeting?: (text: string) => void
  /** Resolve's reply; the page refreshes cards from the conversation. */
  onResolveResult?: (result: VoiceResolveResult) => void
  /** Sensitive speech failed verification; show this verified text instead. */
  onFallback?: (responseId: string, text: string) => void
}

export type CallState = {
  phase: CallPhase
  activity: CallActivity
  muted: boolean
  proposal: { data: VoiceProposal; responseId: string; status: ProposalStatus } | null
  liveAt: number | null
  endReason: string | null
  error: CallError | null
}

const INITIAL: CallState = {
  phase: 'idle',
  activity: 'listening',
  muted: false,
  proposal: null,
  liveAt: null,
  endReason: null,
  error: null,
}

type AudioPort = Pick<CallAudio,
  'onFrame' | 'onDrained' | 'onPlayingChange' | 'startMic' | 'play' | 'beginReply' |
  'endReply' | 'flush' | 'close' | 'micLevel' | 'speakerLevel'>

/** Mock mode exercises the protocol without requesting a microphone or AudioContext. */
class MockAudio implements AudioPort {
  onFrame: (pcm: ArrayBuffer) => void = () => {}
  onDrained: (responseId: string) => void = () => {}
  onPlayingChange: (playing: boolean) => void = () => {}
  readonly micLevel = 0
  readonly speakerLevel = 0
  private current: string | null = null

  async startMic() {}
  beginReply(id: string) { this.current = id }
  play(_chunk: ArrayBuffer) {}
  endReply(id: string) {
    if (this.current !== id) return
    this.current = null
    queueMicrotask(() => this.onDrained(id))
  }
  flush() { this.current = null }
  close() { this.flush() }
}

/**
 * One customer voice call: mic -> Voice WebSocket -> speaker, following protocol zeptaz-hutch-v2.
 *
 * Rules (docs/hutch-resolve-contract.md):
 * - Send playback_complete only after a reply's audio has fully drained, then proposal_presented
 *   only after an accepted playback_ack. Nothing is acknowledged for interrupted or fallback replies.
 * - On `interrupted`, discard queued audio and any pending acknowledgement.
 * - Resolve decides; the browser only reports what was actually played.
 */
export class VoiceCall {
  private state: CallState = INITIAL
  private listeners = new Set<() => void>()
  private audio: AudioPort | null = null
  private socket: VoiceSocket | null = null
  private reply: { id: string; proposal: VoiceProposal | null; fallback: boolean } | null = null
  private awaitingPlaybackAck: string | null = null
  private playbackAttempts = 0
  private playbackRetryTimer = 0
  private limitTimer = 0
  private grantRequestKey: string | null = null

  private handlers: CallHandlers = {}

  private readonly conversationId: string

  constructor(conversationId: string) {
    this.conversationId = conversationId
  }

  setHandlers(handlers: CallHandlers) {
    this.handlers = handlers
  }

  subscribe = (fn: () => void) => {
    this.listeners.add(fn)
    return () => {
      this.listeners.delete(fn)
    }
  }
  getState = () => this.state
  get levels() {
    return { mic: this.state.muted ? 0 : (this.audio?.micLevel ?? 0), speaker: this.audio?.speakerLevel ?? 0 }
  }

  private set(patch: Partial<CallState>) {
    this.state = { ...this.state, ...patch }
    this.listeners.forEach((fn) => fn())
  }

  /** Must run from a click handler: the AudioContext is created synchronously here. */
  async start() {
    if (this.state.phase === 'requesting' || this.state.phase === 'connecting' || this.state.phase === 'live') return
    this.set({ ...INITIAL, phase: 'requesting' })
    try {
      this.audio = API_MODE === 'mock' ? new MockAudio() : new CallAudio()
    } catch {
      return this.fail({ kind: 'mic', mic: 'unsupported' })
    }
    const audio = this.audio
    audio.onFrame = (pcm) => {
      if (this.state.phase === 'live' && !this.state.muted && this.socket?.readyState === WebSocket.OPEN) this.socket.send(pcm)
    }
    audio.onDrained = (id) => this.drained(id)
    audio.onPlayingChange = (playing) => {
      if (this.state.phase !== 'live') return
      if (playing) this.set({ activity: 'speaking' })
      else if (this.state.activity === 'speaking') this.set({ activity: 'listening' })
    }

    // Ask for the microphone before the grant: the grant is only valid for about a minute.
    // Mock mode simulates the caller with buttons, so it never opens the microphone.
    if (API_MODE === 'live') {
      try {
        await audio.startMic()
      } catch (e) {
        if (!isCurrentCallAttempt(this.audio, audio)) return
        return this.fail({ kind: 'mic', mic: e instanceof MicError ? e.kind : 'denied' })
      }
    }
    if (!isCurrentCallAttempt(this.audio, audio)) return // ended while the permission prompt was open

    let grant
    const requestKey = this.grantRequestKey ??= newId()
    try {
      grant = await request<VoiceSessionGrant>('customer', 'POST', `/conversations/${this.conversationId}/voice-sessions`, {
        body: {},
        idempotencyKey: requestKey,
      })
    } catch (error) {
      if (!isCurrentCallAttempt(this.audio, audio)) return
      // Expired keys cannot create another grant. Unknown outcomes must replay the same key
      // so the server can return the recorded result without duplicating provider work.
      if (this.grantRequestKey === requestKey) this.grantRequestKey = grantKeyAfterFailure(requestKey, error)
      return this.fail({ kind: 'grant', error })
    }
    if (!isCurrentCallAttempt(this.audio, audio)) return
    if (this.grantRequestKey === requestKey) this.grantRequestKey = null

    this.set({ phase: 'connecting' })
    let socket: VoiceSocket
    try {
      socket = await openVoiceSocket(grant)
    } catch {
      if (!isCurrentCallAttempt(this.audio, audio)) return
      return this.fail({ kind: 'voice', code: 'voice_connection_failed' })
    }
    if (!isCurrentCallAttempt(this.audio, audio)) {
      socket.close(1000, 'call_cancelled')
      return
    }
    this.socket = socket
    socket.onmessage = (e) => {
      if (typeof e.data === 'string') {
        try {
          const message = JSON.parse(e.data) as VoiceServerMessage
          if (message && typeof message.type === 'string') this.handle(message)
        } catch {
          this.set({ error: { kind: 'voice', code: 'invalid_voice_message' } })
        }
      }
      else if (this.reply && this.state.phase === 'live') audio.play(e.data)
    }
    socket.onclose = () => {
      if (this.socket === socket && this.state.phase !== 'ended') this.finish(this.state.phase === 'live' ? 'disconnected' : null, { kind: 'voice', code: 'voice_provider_unavailable' })
    }
    socket.onerror = () => {
      /* onclose follows and reports it */
    }
    if (socket instanceof WebSocket) {
      socket.onopen = () => {
        if (socket.protocol !== VOICE_PROTOCOL) socket.close(1002, 'protocol')
      }
    }
  }

  stop() {
    const socket = this.socket
    this.finish('user') // detaches onclose first, so the close below isn't reported as a drop
    socket?.close(1000, 'caller_ended')
  }

  setMuted(muted: boolean) {
    this.set({ muted })
  }

  dispose() {
    this.listeners.clear()
    this.handlers = {}
    const socket = this.socket
    this.teardown()
    socket?.close(1000, 'page_closed')
  }

  private handle(msg: VoiceServerMessage) {
    switch (msg.type) {
      case 'ready':
        window.clearTimeout(this.limitTimer)
        this.limitTimer = window.setTimeout(() => this.stop(), CALL_LIMIT_MS + 5_000) // Voice should end it first
        this.set({ phase: 'live', activity: 'listening', liveAt: Date.now() })
        break
      case 'greeting':
        this.handlers.onGreeting?.(msg.text)
        break
      case 'transcript':
        // Only the caller's finalized turn is shown; assistant text comes from Resolve's reply_text.
        if (msg.speaker === 'user' && msg.final && msg.text.trim()) {
          this.handlers.onTranscript?.(msg.text.trim())
          this.set({ activity: 'thinking' })
        }
        break
      case 'resolve_result': {
        this.audio?.flush()
        this.awaitingPlaybackAck = null
        window.clearTimeout(this.playbackRetryTimer)
        this.playbackAttempts = 0
        this.reply = { id: msg.response_id, proposal: msg.proposal, fallback: false }
        this.set({
          activity: 'listening',
          proposal: msg.proposal ? { data: msg.proposal, responseId: msg.response_id, status: msg.sensitive_audio ? 'reading' : 'text-only' } : null,
        })
        this.handlers.onResolveResult?.(msg)
        break
      }
      case 'audio_start':
        if (this.reply?.id === msg.response_id) this.audio?.beginReply(msg.response_id)
        break
      case 'audio_end':
        this.audio?.endReply(msg.response_id)
        break
      case 'audio_fallback':
        if (this.reply?.id === msg.response_id) {
          this.reply.fallback = true
          this.handlers.onFallback?.(msg.response_id, msg.text)
          this.updateProposal(msg.response_id, 'text-only')
        }
        break
      case 'playback_ack':
        if (msg.response_id !== this.awaitingPlaybackAck) break
        if (!msg.accepted && this.reply?.id === msg.response_id && !this.reply.fallback &&
            this.state.phase === 'live' && this.playbackAttempts < 2) {
          // The Voice server can observe the acknowledgement before it has finished marking
          // audio_end complete. One bounded retry is safe: playback has already drained.
          this.playbackRetryTimer = window.setTimeout(() => {
            if (this.awaitingPlaybackAck !== msg.response_id || this.reply?.id !== msg.response_id) return
            this.playbackAttempts++
            this.send({ type: 'playback_complete', response_id: msg.response_id })
          }, 150)
          break
        }
        this.awaitingPlaybackAck = null
        window.clearTimeout(this.playbackRetryTimer)
        if (this.reply?.id === msg.response_id && this.reply.proposal && msg.accepted) {
          this.send({ type: 'proposal_presented', response_id: msg.response_id, proposal_id: this.reply.proposal.id, proposal_hash: this.reply.proposal.proposal_hash })
        } else if (!msg.accepted) {
          this.updateProposal(msg.response_id, 'text-only')
        }
        break
      case 'proposal_ack':
        this.updateProposal(msg.response_id, msg.accepted ? 'awaiting' : 'text-only')
        break
      case 'interrupted': {
        this.audio?.flush()
        this.awaitingPlaybackAck = null
        window.clearTimeout(this.playbackRetryTimer)
        this.playbackAttempts = 0
        const p = this.state.proposal
        if (p && (msg.response_id === null || msg.response_id === p.responseId) && (p.status === 'reading' || p.status === 'awaiting')) {
          this.set({ proposal: { ...p, status: 'interrupted' } })
        }
        this.reply = null
        this.set({ activity: 'listening' })
        break
      }
      case 'error':
        if (msg.code === 'voice_session_ending' || msg.code.startsWith('invalid_')) break // informational
        this.set({ error: { kind: 'voice', code: msg.code } })
        break
      case 'ended':
        this.finish(msg.reason)
        break
    }
  }

  /** The reply's audio has fully played: report it, and nothing else. */
  private drained(id: string) {
    if (this.reply?.id !== id || this.reply.fallback || this.state.phase !== 'live') return
    this.awaitingPlaybackAck = id
    this.playbackAttempts = 1
    this.send({ type: 'playback_complete', response_id: id })
  }

  private updateProposal(responseId: string, status: ProposalStatus) {
    const p = this.state.proposal
    if (p && p.responseId === responseId) this.set({ proposal: { ...p, status } })
  }

  private send(msg: object) {
    if (this.socket?.readyState === WebSocket.OPEN) this.socket.send(JSON.stringify(msg))
  }

  private fail(error: CallError) {
    this.teardown()
    this.set({ phase: 'idle', error })
  }

  private finish(reason: string | null, error?: CallError) {
    this.teardown()
    const p = this.state.proposal
    this.set({
      phase: 'ended',
      endReason: reason,
      error: reason ? this.state.error : (error ?? this.state.error),
      // A spoken offer can't be answered once the call is over; it stays answerable by text.
      proposal: p && p.status !== 'text-only' ? { ...p, status: 'text-only' } : p,
    })
  }

  private teardown() {
    window.clearTimeout(this.limitTimer)
    const socket = this.socket
    this.socket = null
    if (socket) socket.onclose = null
    this.audio?.close()
    this.audio = null
    this.reply = null
    this.awaitingPlaybackAck = null
    window.clearTimeout(this.playbackRetryTimer)
    this.playbackAttempts = 0
  }
}
