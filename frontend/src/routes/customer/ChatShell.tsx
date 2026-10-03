import { useCallback, useEffect, useRef, useState } from 'react'
import { ArrowDown, Bot, FolderOpen, Phone, SendHorizontal } from 'lucide-react'
import { newId } from '@/api/client'
import { customerApi } from '@/api/endpoints'
import { describeError, isApiError } from '@/api/errors'
import type { ComplaintType, ConversationView, Decision, Language, LoginRequest, OperationView, ProposalView, SessionView, TurnInput } from '@/api/types'
import { BrandMark } from '@/components/BrandMark'
import { ErrorState, LoadingState } from '@/components/states'
import { StatusBadge } from '@/components/StatusBadge'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogTitle, DialogTrigger } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { useI18n, type Translate } from '@/i18n/context'
import { formatTime } from '@/lib/format'
import { cn } from '@/lib/utils'
import { CasePanel, CasePanelBody, type CasePanelProps } from './CasePanel'
import { ChatCard, CitationList } from './cards/ChatCards'
import { ConfirmationCard, type ProposalState } from './cards/ConfirmationCard'
import { OperationTracker } from './cards/OperationTracker'
import { QuestionPrompt } from './QuestionPrompt'
import { VoiceShell } from './VoiceShell'

// Each language is named in its own script so it is recognisable whatever the current UI language.
const LANGUAGES: { value: Language; short: string; label: string }[] = [
  { value: 'en', short: 'EN', label: 'English' },
  { value: 'si', short: 'සි', label: 'සිංහල' },
  { value: 'ta', short: 'த', label: 'தமிழ்' },
]

const MAX_TEXT = 4000
const RECOVERY_TIMEOUT_MS = 30_000
const RECOVERY_REQUEST_TIMEOUT_MS = 5_000
const RECOVERY_MAX_POLLS = 32
const CONVERSATION_READ_TIMEOUT_MS = 5_000
// How much unread thread below the fold before "More below" shows.
const MORE_BELOW_PX = 48

/** A turn that failed to send. Retrying reuses its client_turn_id so the server can de-duplicate. */
type FailedTurn = { clientTurnId: string; input: TurnInput; label: string; error: unknown }

/** A failed request may have reached Resolve. Keep the turn locked until its same ID is replayed. */
function hasUncertainOutcome(error: unknown) {
  return !isApiError(error) || error.status === 0 || error.status >= 500 || error.retryable ||
    error.code === 'CONVERSATION_BUSY' || error.code === 'TURN_IN_PROGRESS'
}

export function ChatShell({ session, onDemoLogin }: {
  session: SessionView
  onDemoLogin: (credentials: LoginRequest, conversationId: string) => Promise<void>
}) {
  const { language, setLanguage, t } = useI18n()
  const [conversation, setConversation] = useState<ConversationView | null>(null)
  const [loadError, setLoadError] = useState<unknown>(null)
  const [attempt, setAttempt] = useState(0)
  const [draft, setDraft] = useState('')
  const [sending, setSending] = useState(false)
  // The customer's own words, shown straight away while the turn is in flight.
  const [outgoing, setOutgoing] = useState<string | null>(null)
  // Messages already there when the chat opened; only ones after this animate in.
  const [openedWith, setOpenedWith] = useState<ReadonlySet<string> | null>(null)
  const [failed, setFailed] = useState<FailedTurn | null>(null)
  const [decisions, setDecisions] = useState<Record<string, ProposalState>>({})
  const [caseRefresh, setCaseRefresh] = useState(0)
  const [casesOpen, setCasesOpen] = useState(false)
  const [voiceOpen, setVoiceOpen] = useState(false)
  const [loginOpen, setLoginOpen] = useState(false)
  const [loginIdentity, setLoginIdentity] = useState('')
  const [loginCredential, setLoginCredential] = useState('')
  const [loginBusy, setLoginBusy] = useState(false)
  const [loginError, setLoginError] = useState<unknown>(null)
  // The category the customer last picked, so a follow-up details form starts on it.
  const [lastCategory, setLastCategory] = useState<ComplaintType | null>(null)
  const createKey = useRef(newId())
  // Language at the moment the conversation is opened; later changes go with each turn instead.
  const openingLanguage = useRef(language)
  const scrollRef = useRef<HTMLDivElement>(null)
  // True when there is unread thread below the visible area.
  const [moreBelow, setMoreBelow] = useState(false)
  const checkMoreBelow = useCallback(() => {
    const el = scrollRef.current
    if (el) setMoreBelow(el.scrollHeight - el.scrollTop - el.clientHeight > MORE_BELOW_PX)
  }, [])

  // Reopen this session's conversation after a page refresh, otherwise create one.
  // The same Idempotency-Key makes a retried create safe.
  useEffect(() => {
    let cancelled = false
    const open = async () => {
      const savedId = readSavedConversation(session.id)
      let conversation: ConversationView
      if (savedId) {
        try {
          conversation = await customerApi.getConversation(savedId, AbortSignal.timeout(CONVERSATION_READ_TIMEOUT_MS))
        } catch (e) {
          if (!(isApiError(e) && e.status === 404)) throw e
          conversation = await customerApi.createConversation(openingLanguage.current, createKey.current)
          saveConversation(session.id, conversation.id)
        }
      } else {
        conversation = await customerApi.createConversation(openingLanguage.current, createKey.current)
        saveConversation(session.id, conversation.id)
      }
      // Resolve exposes abandoned text turns by ID; resume from the persisted
      // input after expiry instead of making a reload strand the conversation.
      // Every request and the whole recovery window are bounded so a broken
      // retry_after or an unavailable API cannot spin or hold the page forever.
      const recoveryDeadline = Date.now() + RECOVERY_TIMEOUT_MS
      for (let poll = 0; conversation.pending_turn && poll < RECOVERY_MAX_POLLS && Date.now() < recoveryDeadline; poll++) {
        if (conversation.pending_turn.state === 'IN_PROGRESS') {
          const retryAt = Date.parse(conversation.pending_turn.retry_after)
          const now = Date.now()
          const suggestedDelay = Number.isFinite(retryAt) && retryAt > now ? retryAt - now : 500
          const delay = Math.min(1000, Math.max(100, suggestedDelay), recoveryDeadline - now)
          await new Promise((resolve) => window.setTimeout(resolve, delay))
          if (Date.now() >= recoveryDeadline) break
          conversation = await customerApi.getConversation(conversation.id, recoverySignal(recoveryDeadline))
          continue
        }
        try {
          await customerApi.resumeTurn(conversation.id, conversation.pending_turn.turn_id, recoverySignal(recoveryDeadline))
        } catch (error) {
          const outcomeMayBeUnknown = !isApiError(error) || error.status === 0 || error.status >= 500 || error.retryable
          const leaseStillActive = isApiError(error) && error.code === 'TURN_IN_PROGRESS'
          if (!outcomeMayBeUnknown && !leaseStillActive) throw error
        }
        conversation = await customerApi.getConversation(conversation.id, recoverySignal(recoveryDeadline))
      }
      if (conversation.pending_turn) throw new Error('A previous message is still recovering. Retry loading the conversation.')
      return conversation
    }
    open()
      .then((c) => {
        if (cancelled) return
        setConversation(c)
        setOpenedWith(new Set(c.messages.map((m) => m.id)))
      })
      .catch((e) => !cancelled && setLoadError(e))
    return () => {
      cancelled = true
    }
  }, [session.id, attempt])

  // New reply that fits on screen: show all of it, down to any buttons under it.
  // Longer reply: start at its top so the text is read before its cards; "More below" leads on.
  // Typing indicator / failed turn: show the bottom of the thread.
  const lastMessageId = conversation?.messages.at(-1)?.id
  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    // Wait a frame so cards have laid out before measuring.
    const frame = requestAnimationFrame(() => {
      const latest = lastMessageId ? el.querySelector<HTMLElement>(`[data-message-id="${lastMessageId}"]`) : null
      const replyStart = latest && !sending && !failed ? latest.offsetTop - 16 : null
      const fits = replyStart != null && el.scrollHeight - replyStart <= el.clientHeight
      const top = replyStart == null || fits ? el.scrollHeight : replyStart
      el.scrollTo({ top, behavior: prefersSmoothScroll() ? 'smooth' : 'auto' })
      checkMoreBelow()
    })
    return () => cancelAnimationFrame(frame)
  }, [lastMessageId, sending, failed, checkMoreBelow])

  const reload = useCallback(async (id: string) => {
    try {
      setConversation(await customerApi.getConversation(id, AbortSignal.timeout(CONVERSATION_READ_TIMEOUT_MS)))
    } catch {
      /* keep the current view; the next action will surface any error */
    }
  }, [])

  const sendTurn = useCallback(
    async (input: TurnInput, label: string, clientTurnId: string) => {
      if (!conversation) return false
      setSending(true)
      setOutgoing(label)
      setFailed(null)
      try {
        await customerApi.sendMessage(conversation.id, {
          client_turn_id: clientTurnId,
          expected_version: conversation.version,
          language,
          input,
        })
        // Fetch the canonical conversation rather than patching state locally.
        setConversation(await customerApi.getConversation(conversation.id, AbortSignal.timeout(CONVERSATION_READ_TIMEOUT_MS)))
        setCaseRefresh((n) => n + 1)
        return true
      } catch (e) {
        // Anything that says our view is out of date: reload, then let the user decide what to do.
        if (isApiError(e) && (e.status === 409 || e.status === 422)) await reload(conversation.id)
        setFailed({ clientTurnId, input, label, error: e })
        return false
      } finally {
        setSending(false)
        setOutgoing(null)
      }
    },
    [conversation, language, reload],
  )

  const submitText = () => {
    const text = draft.trim()
    if (!text || sending || turnOutcomeUncertain || !conversation) return // keep the draft until the chat is ready
    setDraft('')
    void sendTurn({ type: 'text', text }, text, newId())
  }

  const decide = async (proposal: ProposalView, decision: Decision) => {
    if (sending || turnOutcomeUncertain) return
    setDecisions((d) => ({ ...d, [proposal.id]: { kind: 'submitting', decision } }))
    const okSent = await sendTurn(
      { type: 'action_decision', proposal_id: proposal.id, proposal_hash: proposal.proposal_hash, decision },
      decision === 'ACCEPT' ? t('confirm.yes') : t('confirm.no'),
      newId(),
    )
    setDecisions((d) => {
      const next = { ...d }
      if (okSent) next[proposal.id] = { kind: 'decided', decision }
      else delete next[proposal.id] // back to whatever the server says is pending
      return next
    })
  }

  const answer = (input: TurnInput, label: string) => {
    if (input.type === 'category_selection') setLastCategory(input.complaint_type)
    if (sending || turnOutcomeUncertain) return
    void sendTurn(input, label, newId())
  }

  // Accepting is the only way an operation is created, so an operation means its proposal was accepted.
  const markAcceptedFromOperation = useCallback((op: OperationView) => {
    setDecisions((d) => (d[op.proposal_id]?.kind === 'decided' ? d : { ...d, [op.proposal_id]: { kind: 'decided', decision: 'ACCEPT' } }))
  }, [])

  const retryFailed = () => {
    if (!failed) return
    void sendTurn(failed.input, failed.label, failed.clientTurnId)
  }

  const proposalState = (p: ProposalView): ProposalState => {
    const local = decisions[p.id]
    if (local) return local
    return conversation?.pending_proposal?.id === p.id ? { kind: 'open' } : { kind: 'closed' }
  }
  const turnOutcomeUncertain = failed !== null && hasUncertainOutcome(failed.error)

  const renderProposal = (p: ProposalView) => (
    <ConfirmationCard key={p.id} proposal={p} state={proposalState(p)} disabled={turnOutcomeUncertain} onDecide={(d) => void decide(p, d)} />
  )

  const panelProps: CasePanelProps = {
    conversation,
    refreshKey: caseRefresh,
    disabled: sending || turnOutcomeUncertain,
    onSelectCase: (id, label) => {
      setCasesOpen(false)
      answer({ type: 'case_selection', case_id: id }, t('chat.switchTo', { label }))
    },
    onReviewRequested: () => {
      setCasesOpen(false)
      setCaseRefresh((n) => n + 1)
      if (conversation) void reload(conversation.id)
    },
  }

  const textAllowed = !conversation?.pending_question || conversation.pending_question.allowed_input_types.includes('text')

  // A pending proposal that didn't arrive inside a message (e.g. a human-review request) is shown at the end.
  const pending = conversation?.pending_proposal
  const pendingShownInline =
    !!pending &&
    !!conversation?.messages.some((m) => m.result?.cards.some((c) => c.type === 'confirmation' && c.data.id === pending.id))

  if (voiceOpen && conversation && session.role === 'CUSTOMER') {
    const returnToChat = () => {
      setVoiceOpen(false)
      setCaseRefresh((n) => n + 1)
      void reload(conversation.id)
    }
    return <VoiceShell session={session} conversationId={conversation.id} onClose={returnToChat} onCallEnd={returnToChat} />
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <header className="flex items-center justify-between gap-3 border-b px-4 py-3">
        <BrandMark subtitle={t('brand.subtitle')} />
        <div className="flex items-center gap-2">
          {session.role === 'CUSTOMER' && (
            <Button variant="outline" size="sm" onClick={() => setVoiceOpen(true)} disabled={!conversation || turnOutcomeUncertain}>
              <Phone aria-hidden /> {t('chat.call')}
            </Button>
          )}
          {session.role === 'GUEST' && (
            <Dialog open={loginOpen} onOpenChange={setLoginOpen}>
              <DialogTrigger asChild><Button variant="outline" size="sm">{t('chat.demoSignIn')}</Button></DialogTrigger>
              <DialogContent>
                <DialogTitle>{t('chat.demoSignInTitle')}</DialogTitle>
                <DialogDescription>{t('chat.demoSignInDescription')}</DialogDescription>
                <form className="flex flex-col gap-3" onSubmit={async (event) => {
                  event.preventDefault()
                  if (!conversation || loginBusy) return
                  setLoginBusy(true)
                  setLoginError(null)
                  try {
                    await onDemoLogin({ demo_identity: loginIdentity.trim(), credential: loginCredential }, conversation.id)
                    setLoginCredential('')
                    setLoginOpen(false)
                  } catch (error) {
                    setLoginError(error)
                  } finally {
                    setLoginBusy(false)
                  }
                }}>
                  <Input aria-label={t('chat.demoIdentity')} placeholder={t('chat.demoIdentity')} autoComplete="username" required value={loginIdentity} onChange={(event) => setLoginIdentity(event.target.value)} />
                  <Input aria-label={t('chat.demoCredential')} placeholder={t('chat.demoCredential')} autoComplete="current-password" type="password" required value={loginCredential} onChange={(event) => setLoginCredential(event.target.value)} />
                  {loginError != null && <p role="alert" className="text-sm text-destructive">{describeError(loginError, t)}</p>}
                  <Button type="submit" disabled={loginBusy || !conversation}>{loginBusy ? t('chat.signingIn') : t('chat.signIn')}</Button>
                </form>
              </DialogContent>
            </Dialog>
          )}
          <StatusBadge tone={session.role === 'GUEST' ? 'neutral' : 'info'} className="hidden sm:inline-flex">
            {session.role === 'GUEST' ? t('chat.guest') : t('chat.demoLine')}
          </StatusBadge>
          <Dialog open={casesOpen} onOpenChange={setCasesOpen}>
            <DialogTrigger asChild>
              <Button variant="outline" size="sm" className="lg:hidden">
                <FolderOpen aria-hidden /> <span className="sr-only sm:not-sr-only">{t('chat.cases')}</span>
                {!!conversation?.cases.length && (
                  <span className="rounded-full bg-primary px-1.5 text-[11px] leading-4 text-primary-foreground">
                    {conversation.cases.length}
                  </span>
                )}
              </Button>
            </DialogTrigger>
            <DialogContent className="max-h-[85dvh] overflow-y-auto">
              <DialogTitle className="sr-only">{t('panel.yourCases')}</DialogTitle>
              <DialogDescription className="sr-only">{t('chat.casesDescription')}</DialogDescription>
              <div className="flex flex-col gap-4">
                <CasePanelBody {...panelProps} />
              </div>
            </DialogContent>
          </Dialog>
          <LanguageToggle value={language} onChange={setLanguage} label={t('lang.label')} />
        </div>
      </header>

      <div className="flex min-h-0 flex-1">
        <main className="flex min-w-0 flex-1 flex-col">
          <div className="relative flex min-h-0 flex-1 flex-col">
            <div ref={scrollRef} onScroll={checkMoreBelow} className="relative flex-1 overflow-y-auto" aria-live="polite">
              <div className="mx-auto flex w-full max-w-2xl flex-col gap-3 px-4 py-6">
                {loadError ? (
                  <ErrorState
                    title={t('chat.openFailed')}
                    error={loadError}
                    onRetry={() => {
                      setLoadError(null)
                      setAttempt((n) => n + 1)
                    }}
                  />
                ) : !conversation ? (
                  <LoadingState label={t('chat.opening')} rows={2} />
                ) : (
                  <>
                    <Welcome />
                    {conversation.messages.map((m) => {
                      const result = m.speaker === 'ASSISTANT' ? m.result : null
                      const hasExtras = !!result && (result.cards.length > 0 || result.citations.length > 0 || result.operation_ids.length > 0)
                      // Replies animate in; the customer's own message already did while it was sending.
                      const animate = m.speaker === 'ASSISTANT' && !!openedWith && !openedWith.has(m.id)
                      const enter = (i: number) =>
                        animate ? { className: 'animate-bubble-in', style: { animationDelay: `${180 + i * 110}ms` } } : {}
                      return (
                        <div key={m.id} data-message-id={m.id} className="flex flex-col gap-2">
                          <Bubble speaker={m.speaker} time={m.created_at} animate={animate}>
                            {m.body}
                          </Bubble>
                          {result && hasExtras && (
                            <div className="flex max-w-xl flex-col gap-2 sm:ml-9">
                              {result.cards.map((card, i) => (
                                <div key={`${m.id}-${i}`} {...enter(i)}>
                                  <ChatCard card={card} renderConfirmation={(c) => renderProposal(c.data)} />
                                </div>
                              ))}
                              {result.operation_ids.map((id) => (
                                <OperationTracker
                                  key={id}
                                  operationId={id}
                                  onUpdate={markAcceptedFromOperation}
                                  onSettled={() => {
                                    setCaseRefresh((n) => n + 1)
                                    void reload(conversation.id)
                                  }}
                                />
                              ))}
                              <CitationList citations={result.citations} />
                            </div>
                          )}
                        </div>
                      )
                    })}
                    {pending && !pendingShownInline && <div className="max-w-xl sm:ml-9">{renderProposal(pending)}</div>}
                    {conversation.pending_question && !sending && (
                      <div className="max-w-xl sm:ml-9">
                        <QuestionPrompt
                          question={conversation.pending_question}
                          defaultComplaint={lastCategory}
                          disabled={sending}
                          onAnswer={answer}
                        />
                      </div>
                    )}
                    {outgoing != null && (
                      <Bubble speaker="USER" animate pending>
                        {outgoing}
                      </Bubble>
                    )}
                    {sending && <Typing />}
                    {failed && <FailedTurnNotice failed={failed} onRetry={retryFailed} t={t} />}
                  </>
                )}
              </div>
            </div>

            {moreBelow && (
              <Button
                size="sm"
                variant="outline"
                onClick={() => {
                  const el = scrollRef.current
                  el?.scrollTo({ top: el.scrollHeight, behavior: prefersSmoothScroll() ? 'smooth' : 'auto' })
                }}
                className="absolute bottom-3 left-1/2 -translate-x-1/2 animate-bubble-in rounded-full bg-background shadow-md"
              >
                <ArrowDown aria-hidden /> {t('chat.moreBelow')}
              </Button>
            )}
          </div>

          <form
            className="border-t bg-background px-4 py-3"
            onSubmit={(e) => {
              e.preventDefault()
              submitText()
            }}
          >
            <div className="mx-auto flex w-full max-w-2xl items-end gap-2">
              <Textarea
                value={draft}
                onChange={(e) => setDraft(e.target.value.slice(0, MAX_TEXT))}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault()
                    submitText()
                  }
                }}
                placeholder={textAllowed ? t('chat.placeholder') : t('chat.placeholderOptions')}
                aria-label={t('chat.message')}
                rows={1}
                className="max-h-40 min-h-11 resize-none rounded-xl"
                disabled={!textAllowed || turnOutcomeUncertain}
              />
              <Button type="submit" size="icon-lg" aria-label={t('chat.send')} disabled={!conversation || sending || turnOutcomeUncertain || !draft.trim()}>
                <SendHorizontal aria-hidden />
              </Button>
            </div>
          </form>
        </main>

        <CasePanel {...panelProps} />
      </div>
    </div>
  )
}

// Only the conversation ID is kept (per tab, per session) — never credentials or message content.
const conversationKey = (sessionId: string) => `hutch-resolve.conversation.${sessionId}`

function readSavedConversation(sessionId: string): string | null {
  try {
    return sessionStorage.getItem(conversationKey(sessionId))
  } catch {
    return null
  }
}

function saveConversation(sessionId: string, conversationId: string) {
  try {
    sessionStorage.setItem(conversationKey(sessionId), conversationId)
  } catch {
    /* storage unavailable: a refresh will start a new conversation */
  }
}

function recoverySignal(deadline: number) {
  const remaining = deadline - Date.now()
  if (remaining <= 0) throw new Error('Conversation recovery timed out.')
  return AbortSignal.timeout(Math.min(RECOVERY_REQUEST_TIMEOUT_MS, remaining))
}

function prefersSmoothScroll() {
  return document.visibilityState === 'visible' && !window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

function FailedTurnNotice({ failed, onRetry, t }: { failed: FailedTurn; onRetry: () => void; t: Translate }) {
  const code = isApiError(failed.error) ? failed.error.code : null
  const canRetry = hasUncertainOutcome(failed.error)
  const reason =
    code === 'PROPOSAL_EXPIRED'
      ? t('chat.err.proposalExpired')
      : code === 'PROPOSAL_INVALIDATED'
        ? t('chat.err.proposalInvalidated')
        : code === 'STALE_VERSION'
          ? t('chat.err.stale')
          : describeError(failed.error, t)
  return (
    <div role="alert" className="ml-auto flex max-w-[85%] flex-col items-end gap-1.5">
      <div className="rounded-2xl rounded-br-md border border-destructive/30 bg-destructive/5 px-4 py-2.5 text-sm">{failed.label}</div>
      <p className="text-right text-xs text-destructive">
        {t('chat.notSent', { reason })}{' '}
        {canRetry && (
          <button className="font-semibold underline underline-offset-2" onClick={onRetry}>
            {t('chat.retry')}
          </button>
        )}
      </p>
    </div>
  )
}

function Welcome() {
  const { t } = useI18n()
  return <Bubble speaker="ASSISTANT">{t('chat.welcome')}</Bubble>
}

function Bubble({
  speaker,
  time,
  animate,
  pending,
  children,
}: {
  speaker: 'USER' | 'ASSISTANT'
  time?: string
  /** Rise in from the speaker's corner (new messages only). */
  animate?: boolean
  /** The customer's message while it is still being sent. */
  pending?: boolean
  children: React.ReactNode
}) {
  const { t } = useI18n()
  const mine = speaker === 'USER'
  return (
    <div
      className={cn(
        'flex max-w-[85%] gap-2',
        mine ? 'ml-auto origin-bottom-right flex-row-reverse' : 'mr-auto origin-bottom-left',
        animate && 'animate-bubble-in',
      )}
    >
      {!mine && <BotAvatar />}
      <div className={cn('flex flex-col gap-1', mine && 'items-end')}>
        <span className="sr-only">{mine ? t('chat.youSaid') : t('chat.assistantSaid')}</span>
        <div
          className={cn(
            'rounded-2xl px-4 py-2.5 text-sm leading-relaxed whitespace-pre-wrap transition-opacity',
            mine ? 'rounded-br-md bg-primary text-primary-foreground' : 'rounded-bl-md bg-muted',
            pending && 'opacity-80',
          )}
        >
          {children}
        </div>
        {pending ? (
          <span className="text-[11px] text-muted-foreground">{t('chat.sending')}</span>
        ) : (
          time && <time className="text-[11px] text-muted-foreground">{formatTime(time)}</time>
        )}
      </div>
    </div>
  )
}

function BotAvatar({ thinking }: { thinking?: boolean }) {
  return (
    <span
      aria-hidden
      className={cn(
        'mt-1 grid size-7 shrink-0 place-items-center rounded-full bg-accent text-accent-foreground',
        thinking && 'animate-thinking-ring',
      )}
    >
      <Bot className="size-4" />
    </span>
  )
}

const STILL_WORKING_MS = 3500

/** Resolve is working on a reply. After a few seconds a short note says it hasn't stalled. */
function Typing() {
  const { t } = useI18n()
  const [slow, setSlow] = useState(false)
  useEffect(() => {
    const id = window.setTimeout(() => setSlow(true), STILL_WORKING_MS)
    return () => window.clearTimeout(id)
  }, [])
  return (
    <div role="status" className="mr-auto flex origin-bottom-left animate-bubble-in gap-2">
      <BotAvatar thinking />
      <div className="flex flex-col gap-1">
        <span className="sr-only">{t('chat.typing')}</span>
        <div className="flex h-10 items-center gap-1.5 rounded-2xl rounded-bl-md bg-muted px-4">
          {[0, 160, 320].map((d) => (
            <span key={d} className="size-2 animate-typing-dot rounded-full bg-muted-foreground" style={{ animationDelay: `${d}ms` }} />
          ))}
        </div>
        {slow && <span className="animate-bubble-in text-[11px] text-muted-foreground">{t('chat.stillWorking')}</span>}
      </div>
    </div>
  )
}

/** Segmented EN | සි | த switch. A radio group: arrow keys move between languages, Tab leaves the group. */
function LanguageToggle({ value, onChange, label }: { value: Language; onChange: (l: Language) => void; label: string }) {
  const move = (e: React.KeyboardEvent<HTMLDivElement>) => {
    const step = e.key === 'ArrowRight' || e.key === 'ArrowDown' ? 1 : e.key === 'ArrowLeft' || e.key === 'ArrowUp' ? -1 : 0
    if (!step) return
    e.preventDefault()
    const i = LANGUAGES.findIndex((l) => l.value === value)
    const next = LANGUAGES[(i + step + LANGUAGES.length) % LANGUAGES.length]
    onChange(next.value)
    e.currentTarget.querySelector<HTMLButtonElement>(`[data-lang="${next.value}"]`)?.focus()
  }
  return (
    <div role="radiogroup" aria-label={label} onKeyDown={move} className="flex h-7 items-center rounded-full bg-muted p-0.5">
      {LANGUAGES.map((l) => {
        const selected = l.value === value
        return (
          <button
            key={l.value}
            type="button"
            role="radio"
            aria-checked={selected}
            aria-label={l.label}
            title={l.label}
            lang={l.value}
            data-lang={l.value}
            tabIndex={selected ? 0 : -1}
            onClick={() => onChange(l.value)}
            className={cn(
              'h-6 min-w-8 rounded-full px-2 text-xs font-semibold transition-colors outline-none focus-visible:ring-2 focus-visible:ring-ring/60',
              selected ? 'bg-foreground text-background' : 'text-muted-foreground hover:text-foreground',
            )}
          >
            {l.short}
          </button>
        )
      })}
    </div>
  )
}
