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

/** Require sustained speech before interrupting, with a higher bar during playback. */
export class SpeechActivityDetector {
  private noiseFloor = 0.003
  private active = false
  private speechFrames = 0
  private quietFrames = 0

  observe(level: number, replyPlaying: boolean): 'start' | 'end' | null {
    if (!this.active) {
      const threshold = Math.min(0.05, Math.max(0.018, this.noiseFloor * 2.8 + 0.009, replyPlaying ? 0.03 : 0))
      if (level >= threshold) {
        this.speechFrames++
        if (this.speechFrames >= 3) {
          this.active = true
          this.speechFrames = 0
          this.quietFrames = 0
          return 'start'
        }
      } else {
        this.speechFrames = 0
        // Playback echo is not the room baseline; learning it would suppress
        // the caller's quieter next turn after the speakers stop.
        if (!replyPlaying) this.noiseFloor = Math.min(0.02, this.noiseFloor * 0.9 + level * 0.1)
      }
      return null
    }
    const endThreshold = Math.max(0.009, this.noiseFloor * 1.5 + 0.004)
    if (level < endThreshold) this.quietFrames++
    else this.quietFrames = 0
    if (this.quietFrames >= 7) {
      this.active = false
      this.quietFrames = 0
      this.speechFrames = 0
      return 'end'
    }
    return null
  }

  reset() {
    this.active = false
    this.speechFrames = 0
    this.quietFrames = 0
  }
}

/** Keep the UI in speaking state through short PCM gaps within one reply. */
export function playbackActivity(playing: boolean, replyAudioEnded: boolean): CallActivity | null {
  if (playing) return 'speaking'
  return replyAudioEnded ? 'listening' : null
}

export type CallPhase = 'idle' | 'requesting' | 'connecting' | 'live' | 'ended'
export type CallActivity = 'listening' | 'thinking' | 'speaking'

/**
 * Offers use the displayed buttons because streamed model speech cannot verify
 * that every proposal term was heard correctly.
 */
export type ProposalStatus = 'text-only' | 'interrupted'

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
}

export type CallState = {
  phase: CallPhase
  activity: CallActivity
  muted: boolean
  micActive: boolean
  proposal: { data: VoiceProposal; responseId: string; status: ProposalStatus } | null
  liveAt: number | null
  endReason: string | null
  error: CallError | null
}

const INITIAL: CallState = {
  phase: 'idle',
  activity: 'listening',
  muted: false,
  micActive: false,
  proposal: null,
  liveAt: null,
  endReason: null,
  error: null,
}

type AudioPort = Pick<CallAudio,
  'onFrame' | 'onDrained' | 'onPlayingChange' | 'startMic' | 'play' | 'playGreeting' | 'beginReply' |
  'endReply' | 'flush' | 'close' | 'micLevel' | 'speakerLevel'>

/** Mock mode exercises the protocol without requesting a microphone or AudioContext. */
class MockAudio implements AudioPort {
  onFrame: (pcm: ArrayBuffer, level: number) => void = () => {}
  onDrained: (responseId: string) => void = () => {}
  onPlayingChange: (playing: boolean) => void = () => {}
  readonly micLevel = 0
  readonly speakerLevel = 0
  private current: string | null = null

  async startMic() {}
  async playGreeting(_wav: ArrayBuffer, _onEnded: () => void) {}
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
 * One customer voice call: mic -> Voice WebSocket -> speaker, following protocol zeptaz-hutch-v3.
 *
 * Rules (docs/hutch-resolve-contract.md):
 * - Send playback_complete only after a reply's audio has fully drained.
 * - Proposal confirmation remains a displayed button action.
 * - On `interrupted`, discard queued audio and any pending acknowledgement.
 * - Resolve decides; the browser only reports what was actually played.
 */
export class VoiceCall {
  private state: CallState = INITIAL
  private listeners = new Set<() => void>()
  private audio: AudioPort | null = null
  private socket: VoiceSocket | null = null
  private reply: { id: string; audioEnded: boolean; playbackStarted: boolean; playbackDrained: boolean } | null = null
  private awaitingPlaybackAck: string | null = null
  private playbackAttempts = 0
  private playbackRetryTimer = 0
  private limitTimer = 0
  private grantRequestKey: string | null = null
  private greetingPlaying = false
  private readonly speechDetector = new SpeechActivityDetector()
  private segmentId = 0
  private micStreaming = false

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

  private handleFrame(pcm: ArrayBuffer, level: number) {
    const micActive = !this.greetingPlaying && !this.state.muted && level >= 0.012
    if (micActive !== this.state.micActive) this.set({ micActive })
    if (this.state.phase !== 'live' || this.state.muted || this.greetingPlaying ||
        this.socket?.readyState !== WebSocket.OPEN) return
    const activity = this.speechDetector.observe(level, Boolean(this.reply?.playbackStarted && !this.reply.playbackDrained))
    if (activity === 'start') {
      this.segmentId++
      this.send({ type: 'input_activity_start', segment_id: this.segmentId })
      if (this.reply) {
        this.audio?.flush()
        this.reply = null
        this.awaitingPlaybackAck = null
        window.clearTimeout(this.playbackRetryTimer)
        this.set({ activity: 'listening' })
      }
    } else if (activity === 'end') {
      this.send({ type: 'input_activity_end', segment_id: this.segmentId })
    }
    this.socket.send(pcm)
    this.micStreaming = true
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
    audio.onFrame = (pcm, level) => this.handleFrame(pcm, level)
    audio.onDrained = (id) => this.drained(id)
    audio.onPlayingChange = (playing) => this.handlePlaybackChange(playing)

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
    if (muted && !this.state.muted && this.micStreaming) {
      this.send({ type: 'input_audio_end' })
      this.micStreaming = false
      this.speechDetector.reset()
    }
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
        void this.playGreeting()
        break
      case 'transcript':
        // Only the caller's finalized turn is shown; assistant text comes from Resolve's reply_text.
        if (msg.speaker === 'user' && msg.final && msg.text.trim()) {
          this.handlers.onTranscript?.(msg.text.trim())
          this.set({ activity: 'thinking' })
        }
        break
      case 'resolve_result': {
        this.greetingPlaying = false
        this.audio?.flush()
        this.awaitingPlaybackAck = null
        window.clearTimeout(this.playbackRetryTimer)
        this.playbackAttempts = 0
        this.reply = { id: msg.response_id, audioEnded: false, playbackStarted: false, playbackDrained: false }
        this.set({
          activity: 'thinking',
          proposal: msg.proposal ? { data: msg.proposal, responseId: msg.response_id, status: 'text-only' } : null,
        })
        this.handlers.onResolveResult?.(msg)
        break
      }
      case 'audio_start':
        if (this.reply?.id === msg.response_id) {
          this.audio?.beginReply(msg.response_id)
          this.set({ activity: 'speaking' })
          if (this.state.error?.kind === 'voice' && this.state.error.code === 'speech_unavailable') this.set({ error: null })
        }
        break
      case 'audio_end':
        if (this.reply?.id === msg.response_id) this.reply.audioEnded = true
        this.audio?.endReply(msg.response_id)
        break
      case 'playback_ack':
        if (msg.response_id !== this.awaitingPlaybackAck) break
        if (!msg.accepted && this.reply?.id === msg.response_id &&
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
        if (!msg.accepted) this.updateProposal(msg.response_id, 'text-only')
        break
      case 'proposal_ack':
        this.updateProposal(msg.response_id, 'text-only')
        break
      case 'interrupted': {
        this.greetingPlaying = false
        this.audio?.flush()
        this.awaitingPlaybackAck = null
        window.clearTimeout(this.playbackRetryTimer)
        this.playbackAttempts = 0
        const p = this.state.proposal
        if (p && (msg.response_id === null || msg.response_id === p.responseId)) {
          this.set({ proposal: { ...p, status: 'interrupted' } })
        }
        this.reply = null
        this.set({ activity: 'listening' })
        break
      }
      case 'error':
        if (msg.code === 'voice_session_ending' || msg.code.startsWith('invalid_')) break // informational
        if (msg.code === 'speech_unavailable') this.set({ activity: 'listening' })
        this.set({ error: { kind: 'voice', code: msg.code } })
        break
      case 'ended':
        this.finish(msg.reason)
        break
    }
  }

  private handlePlaybackChange(playing: boolean) {
    if (this.state.phase !== 'live') return
    if (playing && this.reply) this.reply.playbackStarted = true
    const next = playbackActivity(playing, this.reply?.audioEnded ?? true)
    if (next && this.state.activity !== next) this.set({ activity: next })
  }

  /** The reply's audio has fully played: report it, and nothing else. */
  private drained(id: string) {
    if (this.reply?.id !== id || this.state.phase !== 'live') return
    this.reply.audioEnded = true
    this.reply.playbackDrained = true
    if (this.state.activity !== 'listening') this.set({ activity: 'listening' })
    this.awaitingPlaybackAck = id
    this.playbackAttempts = 1
    this.send({ type: 'playback_complete', response_id: id })
  }

  private async playGreeting() {
    if (API_MODE !== 'live' || this.state.phase !== 'live') return
    const audio = this.audio
    if (!audio) return
    this.greetingPlaying = true
    try {
      const response = await fetch(`${import.meta.env.BASE_URL}audio/hutch-greeting.wav`)
      if (!response.ok) throw new Error('greeting_audio_unavailable')
      const wav = await response.arrayBuffer()
      if (this.audio !== audio || !this.greetingPlaying || this.state.phase !== 'live') return
      await audio.playGreeting(wav, () => { this.greetingPlaying = false })
    } catch {
      // The text greeting remains visible; call audio can still work normally.
      this.greetingPlaying = false
    }
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
      proposal: p && p.status !== 'text-only' ? { ...p, status: 'text-only' } : p,
    })
  }

  private teardown() {
    window.clearTimeout(this.limitTimer)
    this.greetingPlaying = false
    const socket = this.socket
    this.socket = null
    if (socket) socket.onclose = null
    this.audio?.close()
    this.audio = null
    this.reply = null
    this.awaitingPlaybackAck = null
    window.clearTimeout(this.playbackRetryTimer)
    this.playbackAttempts = 0
    this.speechDetector.reset()
    this.segmentId = 0
    this.micStreaming = false
  }
}
