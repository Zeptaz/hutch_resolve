import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react'
import { Activity, ArrowLeft, FileText, Mic, MicOff, PhoneOff, ShieldCheck, Ticket, Volume2 } from 'lucide-react'
import { API_MODE, newId } from '@/api/client'
import { customerApi } from '@/api/endpoints'
import { describeError, isApiError } from '@/api/errors'
import type { OperationView, SessionView } from '@/api/types'
import { OperationBadge, StatusBadge } from '@/components/StatusBadge'
import { CardFrame } from '@/components/CardFrame'
import { operationTone } from '@/components/tones'
import { Button } from '@/components/ui/button'
import { hasMessage, useI18n } from '@/i18n/context'
import { formatTime, humanize } from '@/lib/format'
import { CALL_LIMIT_MS, VoiceCall, type CallState } from '@/voice/call'
import type { VoiceResolveResult } from '@/voice/contracts'
import { offerAfterVoiceResult, reconcileVoiceOffer, type OfferState } from '@/voice/offerState'
import { ReceiptDownloadButton } from './cards/ChatCards'

const OPERATION_POLL_MS = 1000
const OPERATION_POLL_WINDOW_MS = 60_000
const OPERATION_REQUEST_TIMEOUT_MS = 5_000
const MAX_WATCHED_OPERATIONS = 20
const TERMINAL_OPERATION_STATUSES = new Set(['SUCCEEDED', 'FAILED', 'REVIEW_REQUIRED'])

function sameIds(left: string[], right: string[]) {
  return left.length === right.length && left.every((id, index) => id === right[index])
}

/**
 * Customer call panel mounted by ChatShell; it shares the chat's session and conversation.
 * A call that ends (caller, time limit or Voice) stays on this screen with its results; only
 * "Back to chat" leaves it. `onCallEnd` lets the chat refresh its cases in the background.
 */
export function VoiceShell({ session, conversationId, onClose, onCallEnd }: {
  session: SessionView
  conversationId: string
  onClose: () => void
  onCallEnd: () => void
}) {
  const { language } = useI18n()
  const call = useMemo(() => new VoiceCall(conversationId), [conversationId])
  const state = useSyncExternalStore(call.subscribe, call.getState, call.getState)
  const [caption, setCaption] = useState<{ user: string | null; reply: string | null }>({ user: null, reply: null })
  const [offer, setOffer] = useState<OfferState | null>(null)
  const [operations, setOperations] = useState<OperationView[]>([])
  const [watchIds, setWatchIds] = useState<string[]>([])
  const [trackingRetry, setTrackingRetry] = useState(0)
  const [trackingExhaustedKey, setTrackingExhaustedKey] = useState<string | null>(null)
  const lastPhase = useRef<CallState['phase']>('idle')
  const textDecisionPending = useRef(false)
  const canonicalRequestEpoch = useRef(0)

  const syncOperations = useCallback(async (additionalIds: string[] = []) => {
    const requestEpoch = ++canonicalRequestEpoch.current
    setTrackingExhaustedKey(null)
    setTrackingRetry((attempt) => attempt + 1)
    if (additionalIds.length) {
      setWatchIds((current) => {
        const next = [...new Set([...current, ...additionalIds])].slice(-MAX_WATCHED_OPERATIONS)
        return sameIds(current, next) ? current : next
      })
    }
    try {
      const conversation = await customerApi.getConversation(conversationId, AbortSignal.timeout(OPERATION_REQUEST_TIMEOUT_MS))
      const canonicalIds = [...conversation.operation_ids,
        ...conversation.messages.flatMap((message) => message.result?.operation_ids ?? [])]
      const next = [...new Set([...canonicalIds, ...additionalIds])].slice(-MAX_WATCHED_OPERATIONS)
      setWatchIds((current) => sameIds(current, next) ? current : next)
      if (requestEpoch === canonicalRequestEpoch.current && !textDecisionPending.current) {
        setOffer((current) => reconcileVoiceOffer(current, conversation.pending_proposal))
      }
    } catch {
      // Keep IDs already present in a successful Voice result and retry the canonical
      // refresh on the next Voice result without changing its external wire contract.
    }
  }, [conversationId])

  useEffect(() => {
    call.setHandlers({
      onGreeting: (text) => setCaption({ user: null, reply: text }),
      onTranscript: (text) => setCaption({ user: text, reply: null }),
      onResolveResult: (result: VoiceResolveResult) => {
        setCaption((previous) => ({ ...previous, reply: result.reply_text }))
        setOffer((current) => offerAfterVoiceResult(current, result.proposal))
        // operation_status is only a coarse Voice wire hint and does not contain IDs.
        // Resolve's scoped conversation is the source of truth, including older pending work.
        void syncOperations()
      },
    })
    return () => call.dispose()
  }, [call, syncOperations])

  useEffect(() => { void syncOperations() }, [syncOperations])

  useEffect(() => {
    if (state.phase === 'ended' && lastPhase.current !== 'ended' && !textDecisionPending.current && !state.error) onCallEnd()
    lastPhase.current = state.phase
  }, [state.phase, state.error, onCallEnd])

  useEffect(() => {
    if (!watchIds.length) return
    let cancelled = false
    let timer: number | undefined
    let attempts = 0
    const expiresAt = Date.now() + OPERATION_POLL_WINDOW_MS
    const watchKey = watchIds.join('|')
    const poll = async () => {
      if (cancelled) return
      if (attempts >= 60 || Date.now() >= expiresAt) {
        setTrackingExhaustedKey(watchKey)
        return
      }
      attempts++
      const requestTimeout = Math.max(1, Math.min(OPERATION_REQUEST_TIMEOUT_MS, expiresAt - Date.now()))
      const results = await Promise.allSettled(watchIds.map((id) =>
        customerApi.getOperation(id, AbortSignal.timeout(requestTimeout))))
      if (cancelled) return
      const values = results.flatMap((result) => result.status === 'fulfilled' ? [result.value] : [])
      setOperations((current) => {
        const latest = new Map(current.map((operation) => [operation.id, operation]))
        values.forEach((operation) => latest.set(operation.id, operation))
        return watchIds.flatMap((id) => latest.has(id) ? [latest.get(id)!] : [])
      })
      const allTerminal = results.length === watchIds.length && results.every((result) =>
        result.status === 'fulfilled' && TERMINAL_OPERATION_STATUSES.has(result.value.status))
      if (allTerminal) return
      if (attempts >= 60 || Date.now() >= expiresAt) {
        setTrackingExhaustedKey(watchKey)
        return
      }
      timer = window.setTimeout(() => void poll(), OPERATION_POLL_MS)
    }
    void poll()
    return () => { cancelled = true; if (timer !== undefined) window.clearTimeout(timer) }
  }, [watchIds, trackingRetry])

  const close = () => {
    if (textDecisionPending.current) return
    if (state.phase === 'live' || state.phase === 'connecting' || state.phase === 'requesting') call.stop()
    onCallEnd()
    onClose()
  }

  const answerByTap = async (decision: 'ACCEPT' | 'DECLINE') => {
    if (!offer || offer.sending || (state.phase === 'live' && caption.user !== null && caption.reply === null && !state.error) ||
        (offer.retry && offer.retry.decision !== decision)) return
    const current = offer
    // A tapped decision and a spoken turn must not race to claim the same conversation, so the
    // microphone is held muted while the decision is sent. The call itself stays connected.
    textDecisionPending.current = true
    const holdMic = call.getState().phase === 'live' && !call.getState().muted
    if (holdMic) call.setMuted(true)
    setOffer({ ...current, sending: true, error: null })
    let body = current.retry?.body
    try {
      if (!body) {
        const conversation = await customerApi.getConversation(conversationId)
        body = {
          client_turn_id: newId(), expected_version: conversation.version, language,
          input: { type: 'action_decision', proposal_id: current.data.id,
            proposal_hash: current.data.proposal_hash, decision },
        }
      }
      const result = await customerApi.sendMessage(conversationId, body)
      setCaption({ user: decision === 'ACCEPT' ? 'Yes, go ahead' : 'No, leave it', reply: result.reply_text })
      textDecisionPending.current = false
      setOffer((latest) => latest ? { ...latest, sending: false, error: null, retry: null } : null)
      await syncOperations(result.operation_ids)
    } catch (error) {
      // A timeout/network/5xx or busy claim can leave the outcome unknown. The next tap
      // resends exactly the same client_turn_id and body; a definite 4xx gets a fresh turn.
      const uncertain = !isApiError(error) || error.status === 0 || error.status >= 500 ||
        error.retryable || error.code === 'CONVERSATION_BUSY' || error.code === 'TURN_IN_PROGRESS'
      setOffer({ ...current, sending: false, error, retry: uncertain && body ? { decision, body } : null })
      textDecisionPending.current = false
    } finally {
      if (holdMic && call.getState().phase === 'live') call.setMuted(false)
    }
  }

  const expired = offer ? Date.parse(offer.data.expires_at) <= Date.now() : false
  const active = state.phase === 'live'
  // Playback can still report "thinking" after Resolve has returned. Only block
  // a decision while the caller's finalized turn has no Resolve reply yet.
  const voiceTurnPending = active && caption.user !== null && caption.reply === null && !state.error
  const visibleOperations = operations.filter((item) => watchIds.includes(item.id))
  const operationTrackingExhausted = trackingExhaustedKey === watchIds.join('|')

  if (session.role !== 'CUSTOMER') return (
    <section className="rounded-3xl bg-muted/70 p-6 text-center">
      <p className="font-medium">Sign in to a demo line before starting a call.</p>
      <Button variant="outline" className="mt-4" onClick={close}>Back to chat</Button>
    </section>
  )

  return (
    <section className="flex min-h-0 flex-1 flex-col gap-5 overflow-y-auto px-4 py-5" aria-label="Voice call">
      <div className="mx-auto flex w-full max-w-xl items-center justify-between">
        <Button variant="ghost" onClick={close}><ArrowLeft aria-hidden /> Back to chat</Button>
        <StatusBadge tone={active ? 'success' : state.phase === 'ended' ? 'neutral' : 'info'}>
          {active ? state.activity : state.phase}
        </StatusBadge>
      </div>

      <div className="mx-auto flex w-full max-w-md flex-col items-center gap-5 rounded-3xl bg-muted/70 p-6 text-center">
        <div className="space-y-1">
          <h1 className="text-2xl font-bold">Talk to Resolve</h1>
          <p className="text-sm text-muted-foreground">Your call continues this chat and uses the same case.</p>
        </div>
        {state.phase === 'idle' || state.phase === 'ended' ? (
          <Button className="size-24 rounded-full text-lg shadow-lg" onClick={() => void call.start()} aria-label="Start voice call">
            <Mic aria-hidden className="size-8" />
          </Button>
        ) : (
          <div className="grid size-24 place-items-center rounded-full bg-card text-primary shadow-md" aria-hidden>
            {state.activity === 'speaking' ? <Volume2 className="size-9 animate-pulse" /> : state.muted ? <MicOff className="size-9" /> : <Mic className="size-9" />}
          </div>
        )}
        <p className="text-xs text-muted-foreground" aria-live="polite">
          {state.phase === 'requesting' ? 'Requesting a secure call…' : state.phase === 'connecting' ? 'Connecting…' :
            active ? state.activity === 'listening' ? 'Listening' : state.activity === 'speaking' ? 'Speaking' : 'Thinking' :
            state.phase === 'ended' ? endedText(state.endReason) : 'Press the microphone to start.'}
        </p>
        {active && <p className="text-xs text-muted-foreground" aria-live="polite">
          {state.micActive ? 'Microphone is picking up your voice.' : state.activity === 'thinking' ?
            'Waiting for Resolve to finish the spoken reply.' : 'Microphone is on. Speak, then pause for a reply.'}
        </p>}
        {(caption.user || caption.reply) && <div className="w-full space-y-3 text-left" aria-live="polite">
          {caption.user && <p className="ml-auto w-fit max-w-[90%] rounded-2xl bg-primary px-4 py-2 text-primary-foreground">{caption.user}</p>}
          {caption.reply && <p className="w-fit max-w-[90%] rounded-2xl bg-card px-4 py-2">{caption.reply}</p>}
        </div>}
        {state.error && <p role="alert" className="text-sm text-destructive">
          {state.error.kind === 'grant' ? describeError(state.error.error) : state.error.kind === 'mic' ?
            'Microphone unavailable. Check browser permission or continue by text.' :
            state.error.code === 'speech_unavailable' ?
              'Spoken reply is unavailable. Read the reply on screen or continue by text.' :
            'Voice is unavailable. Continue by text.'}
        </p>}
        {active && <div className="flex gap-2">
          <Button variant="outline" aria-pressed={state.muted} onClick={() => call.setMuted(!state.muted)}>
            {state.muted ? <MicOff aria-hidden /> : <Mic aria-hidden />}{state.muted ? 'Unmute' : 'Mute'}
          </Button>
          <Button variant="destructive" onClick={() => call.stop()}><PhoneOff aria-hidden /> End call</Button>
        </div>}
      </div>

      {offer && <div className="mx-auto w-full max-w-md rounded-2xl border bg-card p-5">
        <h2 className="flex items-center gap-2 font-semibold"><ShieldCheck className="size-5" aria-hidden /> Confirm an action</h2>
        <dl className="mt-3 space-y-2 text-sm">
          <div><dt className="text-muted-foreground">Action</dt><dd>{humanize(offer.data.action_type)}</dd></div>
          <div><dt className="text-muted-foreground">Applies to</dt><dd>{offer.data.target_label}</dd></div>
          <div><dt className="text-muted-foreground">What it means</dt><dd>{offer.data.consequences}</dd></div>
        </dl>
        <p className="mt-3 text-xs text-muted-foreground">Valid until {formatTime(offer.data.expires_at)}</p>
        <p className="mt-2 text-sm" aria-live="polite">
          {expired ? 'This offer expired. Ask for a new one.' : 'Review the terms here and use the buttons to answer.'}
        </p>
        {!expired && <div className="mt-3 flex gap-2">
          <Button disabled={offer.sending || voiceTurnPending || offer.retry?.decision === 'DECLINE'} onClick={() => void answerByTap('ACCEPT')}>Yes, go ahead</Button>
          <Button variant="outline" disabled={offer.sending || voiceTurnPending || offer.retry?.decision === 'ACCEPT'} onClick={() => void answerByTap('DECLINE')}>No, leave it</Button>
        </div>}
        {offer.error && <p role="alert" className="mt-2 text-sm text-destructive">
          {describeError(offer.error)} {offer.retry ? 'The outcome is uncertain. Retry the same answer to check it safely.' : 'Check the latest chat before trying again.'}
        </p>}
      </div>}

      {watchIds.length > 0 && <div className="mx-auto w-full max-w-md space-y-2" aria-label="Action statuses">
        {visibleOperations.map((item) => <CallOperationCard key={item.id} operation={item} />)}
        {visibleOperations.length < watchIds.length && !operationTrackingExhausted &&
          <p role="status" className="rounded-2xl border bg-card p-4 text-sm">Checking action status…</p>}
        {operationTrackingExhausted && <div role="alert" className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border bg-card p-4 text-sm">
          <span>Status is unconfirmed. The action may still be processing.</span>
          <Button variant="outline" size="sm" onClick={() => {
            setTrackingExhaustedKey(null)
            setTrackingRetry((attempt) => attempt + 1)
          }}>Check again</Button>
        </div>}
      </div>}

      {API_MODE === 'mock' && <p className="mx-auto max-w-md text-center text-xs text-muted-foreground">
        Simulation: no microphone audio or real Voice provider is used. Use the buttons below to try a caller turn.
      </p>}
      {API_MODE === 'mock' && active && <MockTurnButtons />}
    </section>
  )
}

/** A tapped decision's result inside the call: its progress, then the review or Trust Receipt once Resolve settles it. */
function CallOperationCard({ operation }: { operation: OperationView }) {
  const { t } = useI18n()
  const review = operation.action_type === 'CREATE_REVIEW_TICKET' || operation.status === 'REVIEW_REQUIRED'
  const settled = TERMINAL_OPERATION_STATUSES.has(operation.status)
  const title = operation.action_type === 'CREATE_REVIEW_TICKET'
    ? settled && operation.status !== 'FAILED' ? 'Sent for review' : 'Sending for review'
    : t(`op.${operation.action_type}`)
  const status = operation.status === 'SUCCEEDED' && hasMessage(`op.done.${operation.action_type}`)
    ? t(`op.done.${operation.action_type}`) : t(`op.${operation.status}`)
  return (
    <CardFrame icon={review ? <Ticket /> : <Activity />} title={title} tone={operationTone[operation.status]}
      aside={<OperationBadge status={operation.status} />}>
      <div className="space-y-1 text-sm" aria-live="polite">
        <p>{status}</p>
        {operation.next_step && <p className="text-muted-foreground">{operation.next_step}</p>}
        {operation.outcome.provider_ticket_id && <p className="text-xs">
          Ticket number <span className="font-mono">{operation.outcome.provider_ticket_id}</span>
        </p>}
      </div>
      {settled && <div className="mt-3 space-y-2 border-t pt-3">
        <p className="flex items-center gap-2 text-sm font-semibold">
          <FileText className="size-4 text-muted-foreground" aria-hidden />{review ? 'Review receipt' : 'Trust Receipt'}
        </p>
        <p className="text-xs text-muted-foreground">
          {review ? 'A record of what was checked and handed to the review team. A person will follow up on this case.'
            : 'A record of what was checked and what changed on your line, with a tamper-evident digest.'}
          {' '}It is also saved with this case in the chat.
        </p>
        <ReceiptDownloadButton caseId={operation.case_id} />
      </div>}
    </CardFrame>
  )
}

function endedText(reason: string | null) {
  if (reason === 'session_limit') return `The call reached its ${CALL_LIMIT_MS / 60_000}-minute limit. Your results are below; call again or continue by text.`
  return 'Call ended. Your results are below; call again or continue by text.'
}

function MockTurnButtons() {
  const say = (text: string) => void import('@/voice/mockSocket').then(({ mockVoice }) => mockVoice.say(text))
  return <div className="mx-auto flex max-w-md flex-wrap justify-center gap-2" aria-label="Mock caller controls">
    <Button variant="outline" onClick={() => say('I recharged LKR 1000, but my balance is LKR 420')}>Try a balance issue</Button>
    <Button variant="outline" onClick={() => say('Yes, go ahead')}>Say yes</Button>
    <Button variant="outline" onClick={() => say('No, leave it')}>Say no</Button>
    <Button variant="outline" onClick={() => void import('@/voice/mockSocket').then(({ mockVoice }) => mockVoice.interrupt())}>Interrupt</Button>
  </div>
}
