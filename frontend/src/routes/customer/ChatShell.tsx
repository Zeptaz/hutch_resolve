import { useCallback, useEffect, useRef, useState } from 'react'
import { Bot, SendHorizontal } from 'lucide-react'
import { newId } from '@/api/client'
import { customerApi } from '@/api/endpoints'
import { describeError, isApiError } from '@/api/errors'
import type { ConversationView, Language, SessionView } from '@/api/types'
import { BrandMark } from '@/components/BrandMark'
import { ErrorState, LoadingState } from '@/components/states'
import { StatusBadge } from '@/components/StatusBadge'
import { Button } from '@/components/ui/button'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { formatTime, humanize } from '@/lib/format'
import { cn } from '@/lib/utils'

const LANGUAGES: { value: Language; label: string }[] = [
  { value: 'en', label: 'English' },
  { value: 'si', label: 'සිංහල' },
  { value: 'ta', label: 'தமிழ்' },
]

const MAX_TEXT = 4000

/** A turn that failed to send; retrying reuses its client_turn_id so the server can de-duplicate. */
type FailedTurn = { clientTurnId: string; text: string; error: unknown }

export function ChatShell({ session }: { session: SessionView }) {
  const [language, setLanguage] = useState<Language>('en')
  const [conversation, setConversation] = useState<ConversationView | null>(null)
  const [loadError, setLoadError] = useState<unknown>(null)
  const [attempt, setAttempt] = useState(0)
  const [draft, setDraft] = useState('')
  const [sending, setSending] = useState(false)
  const [failed, setFailed] = useState<FailedTurn | null>(null)
  const createKey = useRef(newId())
  const scrollRef = useRef<HTMLDivElement>(null)

  // Create the conversation once per session; the same Idempotency-Key makes a retry safe.
  useEffect(() => {
    let cancelled = false
    customerApi
      .createConversation('en', createKey.current)
      .then((c) => {
        if (cancelled) return
        setConversation(c)
        setLanguage(c.language)
      })
      .catch((e) => !cancelled && setLoadError(e))
    return () => {
      cancelled = true
    }
  }, [session.id, attempt])

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [conversation?.messages.length, sending])

  const send = useCallback(
    async (text: string, clientTurnId: string) => {
      if (!conversation) return
      setSending(true)
      setFailed(null)
      try {
        await customerApi.sendMessage(conversation.id, {
          client_turn_id: clientTurnId,
          expected_version: conversation.version,
          language,
          input: { type: 'text', text },
        })
        // Fetch the canonical conversation rather than patching state locally.
        setConversation(await customerApi.getConversation(conversation.id))
      } catch (e) {
        if (isApiError(e) && e.code === 'STALE_VERSION') {
          // Someone else advanced the conversation: reload, and let the user decide whether to resend.
          setConversation(await customerApi.getConversation(conversation.id).catch(() => conversation))
        }
        setFailed({ clientTurnId, text, error: e })
      } finally {
        setSending(false)
      }
    },
    [conversation, language],
  )

  const submit = () => {
    const text = draft.trim()
    if (!text || sending) return
    setDraft('')
    void send(text, newId())
  }

  const retryFailed = () => {
    if (!failed) return
    // Same turn → same ID; a stale-version failure is a new turn against the reloaded conversation.
    const sameTurn = !(isApiError(failed.error) && failed.error.code === 'STALE_VERSION')
    void send(failed.text, sameTurn ? failed.clientTurnId : newId())
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <header className="flex items-center justify-between gap-3 border-b px-4 py-3">
        <BrandMark subtitle="Customer support" />
        <div className="flex items-center gap-2">
          <StatusBadge tone={session.role === 'GUEST' ? 'neutral' : 'info'}>
            {session.role === 'GUEST' ? 'Guest' : 'Signed in'}
          </StatusBadge>
          <Select value={language} onValueChange={(v) => setLanguage(v as Language)}>
            <SelectTrigger size="sm" aria-label="Language" className="w-28">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {LANGUAGES.map((l) => (
                <SelectItem key={l.value} value={l.value}>
                  {l.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </header>

      <div className="flex min-h-0 flex-1">
        <main className="flex min-w-0 flex-1 flex-col">
          <div ref={scrollRef} className="flex-1 overflow-y-auto" aria-live="polite">
            <div className="mx-auto flex w-full max-w-2xl flex-col gap-3 px-4 py-6">
              {loadError ? (
                <ErrorState title="Could not open the chat" error={loadError} onRetry={() => { setLoadError(null); setAttempt((n) => n + 1) }} />
              ) : !conversation ? (
                <LoadingState label="Opening chat…" rows={2} />
              ) : (
                <>
                  <Welcome />
                  {conversation.messages.map((m) => (
                    <Bubble key={m.id} speaker={m.speaker} time={m.created_at}>
                      {m.body}
                    </Bubble>
                  ))}
                  {sending && <Typing />}
                  {failed && (
                    <div role="alert" className="ml-auto flex max-w-[85%] flex-col items-end gap-1.5">
                      <div className="rounded-2xl rounded-br-md border border-destructive/30 bg-destructive/5 px-4 py-2.5 text-sm">
                        {failed.text}
                      </div>
                      <p className="text-xs text-destructive">
                        Not sent — {describeError(failed.error)}{' '}
                        <button className="font-semibold underline underline-offset-2" onClick={retryFailed}>
                          Retry
                        </button>
                      </p>
                    </div>
                  )}
                </>
              )}
            </div>
          </div>

          <form
            className="border-t bg-background px-4 py-3"
            onSubmit={(e) => {
              e.preventDefault()
              submit()
            }}
          >
            <div className="mx-auto flex w-full max-w-2xl items-end gap-2">
              <Textarea
                value={draft}
                onChange={(e) => setDraft(e.target.value.slice(0, MAX_TEXT))}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault()
                    submit()
                  }
                }}
                placeholder="Describe your issue — e.g. “I recharged LKR 1000 but my balance is LKR 420”"
                aria-label="Message"
                rows={1}
                className="max-h-40 min-h-11 resize-none rounded-xl"
                disabled={!conversation}
              />
              <Button type="submit" size="icon-lg" aria-label="Send" disabled={!conversation || sending || !draft.trim()}>
                <SendHorizontal aria-hidden />
              </Button>
            </div>
          </form>
        </main>

        <CasePanel conversation={conversation} />
      </div>
    </div>
  )
}

function Welcome() {
  return (
    <Bubble speaker="ASSISTANT">
      Hi! I can look into balance, data, connection and value-added service issues on your prepaid line. What&apos;s
      going on?
    </Bubble>
  )
}

function Bubble({ speaker, time, children }: { speaker: 'USER' | 'ASSISTANT'; time?: string; children: React.ReactNode }) {
  const mine = speaker === 'USER'
  return (
    <div className={cn('flex max-w-[85%] gap-2', mine ? 'ml-auto flex-row-reverse' : 'mr-auto')}>
      {!mine && (
        <span aria-hidden className="mt-1 grid size-7 shrink-0 place-items-center rounded-full bg-accent text-accent-foreground">
          <Bot className="size-4" />
        </span>
      )}
      <div className={cn('flex flex-col gap-1', mine && 'items-end')}>
        <span className="sr-only">{mine ? 'You said' : 'Assistant said'}</span>
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
  return (
    <div role="status" className="mr-auto flex items-center gap-1 rounded-2xl bg-muted px-4 py-3">
      <span className="sr-only">Assistant is replying</span>
      {[0, 150, 300].map((d) => (
        <span key={d} className="size-1.5 animate-bounce rounded-full bg-muted-foreground" style={{ animationDelay: `${d}ms` }} />
      ))}
    </div>
  )
}

/** Side panel for case context. Evidence, calculation and confirmation cards arrive in J-02. */
function CasePanel({ conversation }: { conversation: ConversationView | null }) {
  return (
    <aside aria-label="Your cases" className="hidden w-80 shrink-0 flex-col gap-3 overflow-y-auto border-l bg-sidebar p-4 lg:flex">
      <h2 className="text-sm font-semibold">Your cases</h2>
      {!conversation || conversation.cases.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          When we look into an issue, the case and the evidence we checked will appear here.
        </p>
      ) : (
        <ul className="flex flex-col gap-2">
          {conversation.cases.map((c) => (
            <li
              key={c.id}
              className={cn(
                'rounded-lg border bg-card p-3 text-sm',
                c.id === conversation.active_case_id && 'border-primary/50 ring-1 ring-primary/30',
              )}
            >
              <p className="font-medium">{humanize(c.complaint_type)}</p>
              <p className="text-xs text-muted-foreground">{humanize(c.status)}</p>
            </li>
          ))}
        </ul>
      )}
    </aside>
  )
}
