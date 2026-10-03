import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react'
import { ArrowLeft, Mic, MicOff, PhoneOff, RefreshCw, Volume2 } from 'lucide-react'
import { API_MODE, newId } from '@/api/client'
import { customerApi } from '@/api/endpoints'
import { describeError, isApiError } from '@/api/errors'
import type { Decision, MessageRequest, OperationView, SessionView } from '@/api/types'
import { StatusBadge, type Tone } from '@/components/StatusBadge'
import { Button } from '@/components/ui/button'
import { useI18n } from '@/i18n/context'
import { cn } from '@/lib/utils'
import { VoiceCall, type CallState } from '@/voice/call'
import type { VoiceProposal, VoiceResolveResult } from '@/voice/contracts'
import { Bubble } from './ChatBubble'
import { ConfirmationCard } from './cards/ConfirmationCard'
import { OperationCard } from './cards/OperationTracker'

type Offer = {
  data: VoiceProposal
  /** The answer being sent, if any. */
  sending: false | Decision
  error: unknown
  retry: { decision: 'ACCEPT' | 'DECLINE'; body: MessageRequest } | null
}

const OPERATION_POLL_MS = 1000
const OPERATION_POLL_WINDOW_MS = 60_000
const OPERATION_REQUEST_TIMEOUT_MS = 5_000
const MAX_WATCHED_OPERATIONS = 20
const TERMINAL_OPERATION_STATUSES = new Set(['SUCCEEDED', 'FAILED', 'REVIEW_REQUIRED'])

function sameIds(left: string[], right: string[]) {
  return left.length === right.length && left.every((id, index) => id === right[index])
}

/** Customer call panel mounted by ChatShell; it shares the chat's session and conversation. */
export function VoiceShell({ session, conversationId, onClose, onCallEnd, onResult }: {
  session: SessionView
  conversationId: string
  onClose: () => void
  onCallEnd: () => void
  /** Resolve answered a spoken turn; the case panel beside the call can refresh. */
  onResult?: () => void
}) {
  const { language, t } = useI18n()
  const call = useMemo(() => new VoiceCall(conversationId), [conversationId])
  const state = useSyncExternalStore(call.subscribe, call.getState, call.getState)
  const [caption, setCaption] = useState<{ user: string | null; reply: string | null }>({ user: null, reply: null })
  const [offer, setOffer] = useState<Offer | null>(null)
  const [operations, setOperations] = useState<OperationView[]>([])
  const [watchIds, setWatchIds] = useState<string[]>([])
  const [trackingRetry, setTrackingRetry] = useState(0)
  const [trackingExhaustedKey, setTrackingExhaustedKey] = useState<string | null>(null)
  const lastPhase = useRef<CallState['phase']>('idle')
  const textDecisionPending = useRef(false)
  const onResultRef = useRef(onResult)
  useEffect(() => {
    onResultRef.current = onResult
  })

  const syncOperations = useCallback(async (additionalIds: string[] = []) => {
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
        setOffer(result.proposal ? { data: result.proposal, sending: false, error: null, retry: null } : null)
        // operation_status is only a coarse Voice wire hint and does not contain IDs.
        // Resolve's scoped conversation is the source of truth, including older pending work.
        void syncOperations()
        onResultRef.current?.()
      },
      onFallback: (_responseId, text) => setCaption((previous) => ({ ...previous, reply: text })),
    })
    return () => call.dispose()
  }, [call, syncOperations])

  useEffect(() => {
    if (state.phase === 'ended' && lastPhase.current !== 'ended' && !textDecisionPending.current) onCallEnd()
    lastPhase.current = state.phase
  }, [state.phase, onCallEnd])

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
    if (!offer || offer.sending || (offer.retry && offer.retry.decision !== decision)) return
    const current = offer
    // A text decision and live Voice turn must not race to claim the same conversation.
    textDecisionPending.current = true
    if (state.phase === 'live' || state.phase === 'connecting' || state.phase === 'requesting') call.stop()
    setOffer({ ...current, sending: decision, error: null })
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
      setCaption({ user: decision === 'ACCEPT' ? t('confirm.yes') : t('confirm.no'), reply: result.reply_text })
      setOffer(null)
      void syncOperations(result.operation_ids)
      textDecisionPending.current = false
      onCallEnd()
    } catch (error) {
      // A timeout/network/5xx or busy claim can leave the outcome unknown. The next tap
      // resends exactly the same client_turn_id and body; a definite 4xx gets a fresh turn.
      const uncertain = !isApiError(error) || error.status === 0 || error.status >= 500 ||
        error.retryable || error.code === 'CONVERSATION_BUSY' || error.code === 'TURN_IN_PROGRESS'
      setOffer({ ...current, sending: false, error, retry: uncertain && body ? { decision, body } : null })
      textDecisionPending.current = false
    }
  }

  // Voice tracks the offer it read aloud; any other offer can only be answered by tap.
  const proposalStatus = state.proposal && state.proposal.data.id === offer?.data.id ? state.proposal.status : 'text-only'
  const byTap = state.phase !== 'live' || proposalStatus === 'text-only' || proposalStatus === 'interrupted'
  const live = state.phase === 'live'
  const connecting = state.phase === 'requesting' || state.phase === 'connecting'
  const visibleOperations = operations.filter((item) => watchIds.includes(item.id))
  const operationTrackingExhausted = trackingExhaustedKey === watchIds.join('|')

  if (session.role !== 'CUSTOMER') return (
    <main className="flex min-w-0 flex-1 flex-col items-center px-4 py-6">
      <div className="flex w-full max-w-md flex-col items-center gap-4 rounded-2xl bg-muted/70 p-6 text-center">
        <p className="font-medium">{t('voice.signInFirst')}</p>
        <Button variant="outline" onClick={close}><ArrowLeft aria-hidden /> {t('voice.back')}</Button>
      </div>
    </main>
  )

  const badge: { tone: Tone; label: string } = live
    ? state.muted
      ? { tone: 'warning', label: t('voice.state.muted') }
      : { tone: 'success', label: t(`voice.state.${state.activity}`) }
    : connecting
      ? { tone: 'info', label: t('voice.state.connecting') }
      : { tone: 'neutral', label: t(state.phase === 'ended' ? 'voice.state.ended' : 'voice.state.idle') }
  const hint = state.phase === 'live'
    ? t(state.muted ? 'voice.hint.muted' : `voice.hint.${state.activity}`)
    : t(`voice.hint.${state.phase}`)
  const offerHint = proposalStatus === 'reading' ? t('voice.offer.reading')
    : proposalStatus === 'awaiting' ? t('voice.offer.awaiting') : t('voice.offer.tap')

  return (
    <main className="flex min-w-0 flex-1 flex-col" aria-label={t('voice.region')}>
      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto flex w-full max-w-2xl flex-col gap-3 px-4 py-6">
          <div className="flex items-center justify-between gap-2">
            <Button variant="ghost" size="sm" onClick={close}><ArrowLeft aria-hidden /> {t('voice.back')}</Button>
            <StatusBadge tone={badge.tone}>{badge.label}</StatusBadge>
          </div>

          <section className="flex animate-bubble-in flex-col items-center gap-4 rounded-2xl bg-muted/70 px-4 py-7 text-center">
            <div className="space-y-1">
              <h1 className="text-xl font-semibold">{t('voice.title')}</h1>
              <p className="text-sm text-muted-foreground">{t('voice.subtitle')}</p>
            </div>
            {state.phase === 'idle' || state.phase === 'ended' ? (
              <Button className="size-20 rounded-full shadow-md [&_svg:not([class*='size-'])]:size-8" onClick={() => void call.start()} aria-label={t('voice.start')}>
                <Mic aria-hidden />
              </Button>
            ) : (
              <span
                aria-hidden
                className={cn(
                  'grid size-20 place-items-center rounded-full bg-accent text-accent-foreground',
                  (connecting || (live && state.activity === 'thinking')) && 'animate-thinking-ring',
                )}
              >
                {live && state.activity === 'speaking' ? <Volume2 className="size-8 animate-pulse" /> : state.muted ? <MicOff className="size-8" /> : <Mic className="size-8" />}
              </span>
            )}
            <p className="text-xs text-muted-foreground" aria-live="polite">{hint}</p>
            {state.error && (
              <p role="alert" className="text-sm text-destructive">
                {state.error.kind === 'grant' ? describeError(state.error.error, t) : t(state.error.kind === 'mic' ? 'voice.err.mic' : 'voice.err.unavailable')}
              </p>
            )}
            {live && (
              <div className="flex gap-2">
                <Button variant="outline" aria-pressed={state.muted} onClick={() => call.setMuted(!state.muted)}>
                  {state.muted ? <MicOff aria-hidden /> : <Mic aria-hidden />}
                  {t(state.muted ? 'voice.unmute' : 'voice.mute')}
                </Button>
                <Button variant="destructive" onClick={() => call.stop()}><PhoneOff aria-hidden /> {t('voice.end')}</Button>
              </div>
            )}
          </section>

          {(caption.user || caption.reply) && (
            <div className="flex flex-col gap-3" aria-live="polite">
              {caption.user && <Bubble key={`u:${caption.user}`} speaker="USER" animate>{caption.user}</Bubble>}
              {caption.reply && <Bubble key={`r:${caption.reply}`} speaker="ASSISTANT" animate>{caption.reply}</Bubble>}
            </div>
          )}

          {offer && (
            <div className="flex animate-bubble-in flex-col gap-2">
              <ConfirmationCard
                proposal={offer.data}
                state={offer.sending ? { kind: 'submitting', decision: offer.sending } : { kind: 'open' }}
                disabled={!byTap}
                lockedTo={offer.retry?.decision}
                onDecide={(decision) => void answerByTap(decision)}
              />
              <p className="text-xs text-muted-foreground" aria-live="polite">{offerHint}</p>
              {offer.error != null && (
                <p role="alert" className="text-xs text-destructive">
                  {describeError(offer.error, t)} {t(offer.retry ? 'voice.offer.uncertain' : 'voice.offer.checkChat')}
                </p>
              )}
            </div>
          )}

          {watchIds.length > 0 && (
            <div className="flex flex-col gap-3">
              {visibleOperations.map((op) => (
                <OperationCard key={op.id} op={op} className="animate-bubble-in" />
              ))}
              {visibleOperations.length < watchIds.length && !operationTrackingExhausted && (
                <p role="status" className="rounded-2xl bg-muted/70 px-4 py-3 text-sm text-muted-foreground">{t('voice.ops.checking')}</p>
              )}
              {operationTrackingExhausted && (
                <div role="alert" className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border-[1.5px] border-warning/70 bg-muted/70 px-4 py-3 text-sm">
                  <span>{t('voice.ops.unconfirmed')}</span>
                  <Button variant="outline" size="sm" onClick={() => {
                    setTrackingExhaustedKey(null)
                    setTrackingRetry((attempt) => attempt + 1)
                  }}><RefreshCw aria-hidden /> {t('op.checkAgain')}</Button>
                </div>
              )}
            </div>
          )}

          {API_MODE === 'mock' && <p className="text-center text-xs text-muted-foreground">
            Simulation: no microphone audio or real Voice provider is used. Use the buttons below to try a caller turn.
          </p>}
          {API_MODE === 'mock' && live && <MockTurnButtons />}
        </div>
      </div>
    </main>
  )
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
