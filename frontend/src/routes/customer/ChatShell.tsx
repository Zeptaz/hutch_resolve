import { useCallback, useEffect, useRef, useState } from 'react'
import { Bot, FolderOpen, SendHorizontal } from 'lucide-react'
import { newId } from '@/api/client'
import { customerApi } from '@/api/endpoints'
import { describeError, isApiError } from '@/api/errors'
import type { ComplaintType, ConversationView, Decision, Language, OperationView, ProposalView, SessionView, TurnInput } from '@/api/types'
import { BrandMark } from '@/components/BrandMark'
import { ErrorState, LoadingState } from '@/components/states'
import { StatusBadge } from '@/components/StatusBadge'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogTitle, DialogTrigger } from '@/components/ui/dialog'
import { Textarea } from '@/components/ui/textarea'
import { useI18n, type Translate } from '@/i18n/context'
import { formatTime } from '@/lib/format'
import { cn } from '@/lib/utils'
import { CasePanel, CasePanelBody, type CasePanelProps } from './CasePanel'
import { ChatCard, CitationList } from './cards/ChatCards'
import { ConfirmationCard, type ProposalState } from './cards/ConfirmationCard'
import { OperationTracker } from './cards/OperationTracker'
import { QuestionPrompt } from './QuestionPrompt'

// Each language is named in its own script so it is recognisable whatever the current UI language.
const LANGUAGES: { value: Language; short: string; label: string }[] = [
  { value: 'en', short: 'EN', label: 'English' },
  { value: 'si', short: 'සි', label: 'සිංහල' },
  { value: 'ta', short: 'த', label: 'தமிழ்' },
]

const MAX_TEXT = 4000

/** A turn that failed to send. Retrying reuses its client_turn_id so the server can de-duplicate. */
type FailedTurn = { clientTurnId: string; input: TurnInput; label: string; error: unknown }

/** Errors where resending the same turn can't help; the user needs fresh state or a new request. */
const NOT_RETRYABLE = new Set(['STALE_VERSION', 'PROPOSAL_EXPIRED', 'PROPOSAL_INVALIDATED', 'VALIDATION_ERROR', 'ACTION_NOT_ALLOWED'])

export function ChatShell({ session }: { session: SessionView }) {
  const { language, setLanguage, t } = useI18n()
  const [conversation, setConversation] = useState<ConversationView | null>(null)
  const [loadError, setLoadError] = useState<unknown>(null)
  const [attempt, setAttempt] = useState(0)
  const [draft, setDraft] = useState('')
  const [sending, setSending] = useState(false)
  const [failed, setFailed] = useState<FailedTurn | null>(null)
  const [decisions, setDecisions] = useState<Record<string, ProposalState>>({})
  const [caseRefresh, setCaseRefresh] = useState(0)
  const [casesOpen, setCasesOpen] = useState(false)
  // The category the customer last picked, so a follow-up details form starts on it.
  const [lastCategory, setLastCategory] = useState<ComplaintType | null>(null)
  const createKey = useRef(newId())
  // Language at the moment the conversation is opened; later changes go with each turn instead.
  const openingLanguage = useRef(language)
  const scrollRef = useRef<HTMLDivElement>(null)

  // Reopen this session's conversation after a page refresh, otherwise create one.
  // The same Idempotency-Key makes a retried create safe.
  useEffect(() => {
    let cancelled = false
    const open = async () => {
      const savedId = readSavedConversation(session.id)
      if (savedId) {
        try {
          return await customerApi.getConversation(savedId)
        } catch (e) {
          if (!(isApiError(e) && e.status === 404)) throw e
        }
      }
      const created = await customerApi.createConversation(openingLanguage.current, createKey.current)
      saveConversation(session.id, created.id)
      return created
    }
    open()
      .then((c) => {
        if (cancelled) return
        setConversation(c)
      })
      .catch((e) => !cancelled && setLoadError(e))
    return () => {
      cancelled = true
    }
  }, [session.id, attempt])

  // New reply: bring its start into view so long card stacks are read from the top.
  // Typing indicator / failed turn: show the bottom of the thread.
  const lastMessageId = conversation?.messages.at(-1)?.id
  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    // Wait a frame so cards have laid out before measuring.
    const frame = requestAnimationFrame(() => {
      const latest = lastMessageId ? el.querySelector<HTMLElement>(`[data-message-id="${lastMessageId}"]`) : null
      const top = latest && !sending && !failed ? latest.offsetTop - 16 : el.scrollHeight
      el.scrollTo({ top, behavior: prefersSmoothScroll() ? 'smooth' : 'auto' })
    })
    return () => cancelAnimationFrame(frame)
  }, [lastMessageId, sending, failed])

  const reload = useCallback(async (id: string) => {
    try {
      setConversation(await customerApi.getConversation(id))
    } catch {
      /* keep the current view; the next action will surface any error */
    }
  }, [])

  const sendTurn = useCallback(
    async (input: TurnInput, label: string, clientTurnId: string) => {
      if (!conversation) return false
      setSending(true)
      setFailed(null)
      try {
        await customerApi.sendMessage(conversation.id, {
          client_turn_id: clientTurnId,
          expected_version: conversation.version,
          language,
          input,
        })
        // Fetch the canonical conversation rather than patching state locally.
        setConversation(await customerApi.getConversation(conversation.id))
        setCaseRefresh((n) => n + 1)
        return true
      } catch (e) {
        // Anything that says our view is out of date: reload, then let the user decide what to do.
        if (isApiError(e) && (e.status === 409 || e.status === 422)) await reload(conversation.id)
        setFailed({ clientTurnId, input, label, error: e })
        return false
      } finally {
        setSending(false)
      }
    },
    [conversation, language, reload],
  )

  const submitText = () => {
    const text = draft.trim()
    if (!text || sending || !conversation) return // keep the draft until the chat is ready
    setDraft('')
    void sendTurn({ type: 'text', text }, text, newId())
  }

  const decide = async (proposal: ProposalView, decision: Decision) => {
    if (sending) return
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
    if (sending) return
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

  const renderProposal = (p: ProposalView) => (
    <ConfirmationCard key={p.id} proposal={p} state={proposalState(p)} onDecide={(d) => void decide(p, d)} />
  )

  const panelProps: CasePanelProps = {
    conversation,
    refreshKey: caseRefresh,
    disabled: sending,
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

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <header className="flex items-center justify-between gap-3 border-b px-4 py-3">
        <BrandMark subtitle={t('brand.subtitle')} />
        <div className="flex items-center gap-2">
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
          <div ref={scrollRef} className="relative flex-1 overflow-y-auto" aria-live="polite">
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
                    return (
                      <div key={m.id} data-message-id={m.id} className="flex flex-col gap-2">
                        <Bubble speaker={m.speaker} time={m.created_at}>
                          {m.body}
                        </Bubble>
                        {result && hasExtras && (
                          <div className="flex max-w-xl flex-col gap-2 sm:ml-9">
                            {result.cards.map((card, i) => (
                              <ChatCard key={`${m.id}-${i}`} card={card} renderConfirmation={(c) => renderProposal(c.data)} />
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
                  {sending && <Typing />}
                  {failed && <FailedTurnNotice failed={failed} onRetry={retryFailed} t={t} />}
                </>
              )}
            </div>
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
                disabled={!textAllowed}
              />
              <Button type="submit" size="icon-lg" aria-label={t('chat.send')} disabled={!conversation || sending || !draft.trim()}>
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

function prefersSmoothScroll() {
  return document.visibilityState === 'visible' && !window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

function FailedTurnNotice({ failed, onRetry, t }: { failed: FailedTurn; onRetry: () => void; t: Translate }) {
  const code = isApiError(failed.error) ? failed.error.code : null
  const canRetry = !code || !NOT_RETRYABLE.has(code)
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

function Bubble({ speaker, time, children }: { speaker: 'USER' | 'ASSISTANT'; time?: string; children: React.ReactNode }) {
  const { t } = useI18n()
  const mine = speaker === 'USER'
  return (
    <div className={cn('flex max-w-[85%] gap-2', mine ? 'ml-auto flex-row-reverse' : 'mr-auto')}>
      {!mine && (
        <span aria-hidden className="mt-1 grid size-7 shrink-0 place-items-center rounded-full bg-accent text-accent-foreground">
          <Bot className="size-4" />
        </span>
      )}
      <div className={cn('flex flex-col gap-1', mine && 'items-end')}>
        <span className="sr-only">{mine ? t('chat.youSaid') : t('chat.assistantSaid')}</span>
        <div
          className={cn(
            'rounded-2xl px-4 py-2.5 text-sm leading-relaxed whitespace-pre-wrap',
            mine ? 'rounded-br-md bg-primary text-primary-foreground' : 'rounded-bl-md bg-muted',
          )}
        >
          {children}
        </div>
        {time && <time className="text-[11px] text-muted-foreground">{formatTime(time)}</time>}
      </div>
    </div>
  )
}

function Typing() {
  const { t } = useI18n()
  return (
    <div role="status" className="mr-auto flex items-center gap-1 rounded-2xl bg-muted px-4 py-3">
      <span className="sr-only">{t('chat.typing')}</span>
      {[0, 150, 300].map((d) => (
        <span key={d} className="size-1.5 animate-bounce rounded-full bg-muted-foreground" style={{ animationDelay: `${d}ms` }} />
      ))}
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
