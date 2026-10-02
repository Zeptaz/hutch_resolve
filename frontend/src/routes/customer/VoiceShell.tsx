import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react'
import { Activity, ArrowLeft, Mic, MicOff, PhoneOff, ShieldCheck, Volume2 } from 'lucide-react'
import { API_MODE, newId } from '@/api/client'
import { customerApi } from '@/api/endpoints'
import { describeError, isApiError } from '@/api/errors'
import type { MessageRequest, OperationView, SessionView } from '@/api/types'
import { OperationBadge, StatusBadge } from '@/components/StatusBadge'
import { Button } from '@/components/ui/button'
import { useI18n } from '@/i18n/context'
import { formatTime, humanize } from '@/lib/format'
import { VoiceCall, type CallState } from '@/voice/call'
import type { VoiceProposal, VoiceResolveResult } from '@/voice/contracts'

type Offer = {
  data: VoiceProposal
  sending: boolean
  error: unknown
  retry: { decision: 'ACCEPT' | 'DECLINE'; body: MessageRequest } | null
}

/** Customer call panel mounted by ChatShell; it shares the chat's session and conversation. */
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
  const [offer, setOffer] = useState<Offer | null>(null)
  const [operation, setOperation] = useState<OperationView | null>(null)
  const [watchId, setWatchId] = useState<string | null>(null)
  const lastPhase = useRef<CallState['phase']>('idle')
  const textDecisionPending = useRef(false)

  useEffect(() => {
    call.setHandlers({
      onGreeting: (text) => setCaption({ user: null, reply: text }),
      onTranscript: (text) => setCaption({ user: text, reply: null }),
      onResolveResult: (result: VoiceResolveResult) => {
        setCaption((previous) => ({ ...previous, reply: result.reply_text }))
        setOffer(result.proposal ? { data: result.proposal, sending: false, error: null, retry: null } : null)
        if (result.operation_status) {
          void customerApi.getConversation(conversationId).then((conversation) => {
            const id = conversation.messages.findLast((message) => message.result?.operation_ids.length)?.result?.operation_ids.at(-1)
            if (id) setWatchId(id)
          }).catch(() => {})
        }
      },
      onFallback: (_responseId, text) => setCaption((previous) => ({ ...previous, reply: text })),
    })
    return () => call.dispose()
  }, [call, conversationId])

  useEffect(() => {
    if (state.phase === 'ended' && lastPhase.current !== 'ended' && !textDecisionPending.current) onCallEnd()
    lastPhase.current = state.phase
  }, [state.phase, onCallEnd])

  useEffect(() => {
    if (!watchId) return
    let cancelled = false
    let timer = 0
    let attempts = 0
    const poll = async () => {
      try {
        const result = await customerApi.getOperation(watchId)
        if (cancelled) return
        setOperation(result)
        if (['SUCCEEDED', 'FAILED', 'REVIEW_REQUIRED'].includes(result.status) || attempts++ >= 60) return
      } catch {
        if (cancelled || attempts++ >= 60) return
      }
      timer = window.setTimeout(() => void poll(), 1000)
    }
    void poll()
    return () => { cancelled = true; window.clearTimeout(timer) }
  }, [watchId])

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
      setOffer(null)
      if (result.operation_ids.length) setWatchId(result.operation_ids.at(-1) ?? null)
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

  const proposalStatus = state.proposal?.data.id === offer?.data.id ? state.proposal.status : 'text-only'
  const byTap = state.phase !== 'live' || proposalStatus === 'text-only' || proposalStatus === 'interrupted'
  const expired = offer ? Date.parse(offer.data.expires_at) <= Date.now() : false
  const active = state.phase === 'live'

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
            state.phase === 'ended' ? 'Call ended. Continue by text or call again.' : 'Press the microphone to start.'}
        </p>
        {(caption.user || caption.reply) && <div className="w-full space-y-3 text-left" aria-live="polite">
          {caption.user && <p className="ml-auto w-fit max-w-[90%] rounded-2xl bg-primary px-4 py-2 text-primary-foreground">{caption.user}</p>}
          {caption.reply && <p className="w-fit max-w-[90%] rounded-2xl bg-card px-4 py-2">{caption.reply}</p>}
        </div>}
        {state.error && <p role="alert" className="text-sm text-destructive">
          {state.error.kind === 'grant' ? describeError(state.error.error) : state.error.kind === 'mic' ?
            'Microphone unavailable. Check browser permission or continue by text.' :
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
          {expired ? 'This offer expired. Ask for a new one.' : proposalStatus === 'reading' ?
            'The offer is being read aloud.' : proposalStatus === 'awaiting' ?
            'The offer was read. Say yes or no.' : 'Answer here by text if you want to proceed.'}
        </p>
        {byTap && !expired && <div className="mt-3 flex gap-2">
          <Button disabled={offer.sending || offer.retry?.decision === 'DECLINE'} onClick={() => void answerByTap('ACCEPT')}>Yes, go ahead</Button>
          <Button variant="outline" disabled={offer.sending || offer.retry?.decision === 'ACCEPT'} onClick={() => void answerByTap('DECLINE')}>No, leave it</Button>
        </div>}
        {offer.error && <p role="alert" className="mt-2 text-sm text-destructive">
          {describeError(offer.error)} {offer.retry ? 'The outcome is uncertain. Retry the same answer to check it safely.' : 'Check the latest chat before trying again.'}
        </p>}
      </div>}

      {operation && <div className="mx-auto flex w-full max-w-md items-center gap-3 rounded-2xl border bg-card p-4">
        <Activity className="size-5" aria-hidden /><span className="flex-1 text-sm">Action status</span>
        <OperationBadge status={operation.status} />
      </div>}

      {API_MODE === 'mock' && <p className="mx-auto max-w-md text-center text-xs text-muted-foreground">
        Simulation: no microphone audio or real Voice provider is used. Use the buttons below to try a caller turn.
      </p>}
      {API_MODE === 'mock' && active && <MockTurnButtons />}
    </section>
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
