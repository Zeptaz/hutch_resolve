import { useEffect, useRef, useState } from 'react'
import { AlertTriangle, ClipboardCheck, Lock } from 'lucide-react'
import { newId } from '@/api/client'
import { agentApi } from '@/api/endpoints'
import { describeError, isApiError } from '@/api/errors'
import type { AgentCaseDetail, Disposition, ReviewRequest, ReviewResult } from '@/api/types'
import { CardFrame } from '@/components/CardFrame'
import { ReviewBadge, StatusBadge } from '@/components/StatusBadge'
import { reviewTone } from '@/components/tones'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { formatDateTime } from '@/lib/format'
import { cn } from '@/lib/utils'
import { DISPOSITION, DISPOSITION_HINT, dispositionLabel, syncLabel } from './labels'
import { useQueueSignal } from './queueSignal'

type Mode = 'note' | 'close' | 'reopen'
type Draft = { mode: Mode; text: string; disposition: Disposition | null; baseVersion: number | null }

const MAX = 2000
const EMPTY: Draft = { mode: 'note', text: '', disposition: null, baseVersion: null }
const storageKey = (caseId: string) => `hutch-resolve.review-draft.${caseId}`

// Drafts survive a refresh or an accidental click on another case. Per browser tab only.
function loadDraft(caseId: string): Draft {
  try {
    const raw = sessionStorage.getItem(storageKey(caseId))
    if (raw) return { ...EMPTY, ...(JSON.parse(raw) as Partial<Draft>) }
  } catch {
    /* storage unavailable */
  }
  return EMPTY
}
function saveDraft(caseId: string, d: Draft) {
  try {
    if (!d.text && d.mode === 'note') sessionStorage.removeItem(storageKey(caseId))
    else sessionStorage.setItem(storageKey(caseId), JSON.stringify(d))
  } catch {
    /* storage unavailable */
  }
}

type Feedback = { kind: 'saved'; result: ReviewResult } | { kind: 'conflict'; message: string } | { kind: 'error'; message: string }

const SUBMIT_LABEL: Record<Mode, string> = { note: 'Add note', close: 'Close review', reopen: 'Reopen review' }

/**
 * Internal review: notes and status changes, versioned against the case.
 * Only transitions Resolve allows are offered; the server still decides.
 */
export function ReviewPanel({ detail, onChanged }: { detail: AgentCaseDetail; onChanged: () => void }) {
  const { refreshQueue } = useQueueSignal()
  const c = detail.case
  const [draft, setDraftState] = useState<Draft>(() => loadDraft(c.id))
  const [sending, setSending] = useState(false)
  const [feedback, setFeedback] = useState<Feedback | null>(null)
  const keyRef = useRef<{ body: string; key: string } | null>(null)
  const textRef = useRef<HTMLTextAreaElement>(null)

  const setDraft = (patch: Partial<Draft>) =>
    setDraftState((d) => {
      // The version the agent was looking at when they started; sending against it catches edits by others.
      const next = { ...d, ...patch, baseVersion: d.baseVersion ?? c.version }
      saveDraft(c.id, next)
      return next
    })
  const reset = () => {
    saveDraft(c.id, EMPTY)
    setDraftState(EMPTY)
  }

  const choose = (mode: Mode) => {
    setDraft({ mode })
    setFeedback(null)
    window.requestAnimationFrame(() => textRef.current?.focus())
  }

  // A mode the current status no longer allows (someone else moved it) falls back to a note.
  const allowed: Mode[] = c.review_status === 'CLOSED' ? ['reopen'] : ['close']
  const canStart = c.review_status === 'NEW'
  const mode: Mode = draft.mode !== 'note' && !allowed.includes(draft.mode) ? 'note' : draft.mode
  const text = draft.text.trim()
  const changedSince = draft.baseVersion != null && draft.baseVersion !== c.version && (text.length > 0 || mode !== 'note')

  const problem =
    mode === 'close' && !draft.disposition
      ? 'Choose an outcome.'
      : (mode === 'note' || mode === 'close') && !text
        ? mode === 'close'
          ? 'Explain the outcome in a note.'
          : null
        : mode === 'reopen' && !text
          ? 'Say why the review is reopening.'
          : null
  const canSend = !sending && !problem && (mode !== 'note' || text.length > 0) && draft.text.length <= MAX

  // `start` moves NEW to IN_REVIEW in one click, carrying any note already typed.
  const send = async (start = false) => {
    const body: ReviewRequest = { expected_version: draft.baseVersion ?? c.version }
    if (start) body.review_status = 'IN_REVIEW'
    if (mode === 'close') Object.assign(body, { review_status: 'CLOSED', disposition: draft.disposition })
    if (mode === 'reopen') Object.assign(body, { review_status: 'IN_REVIEW', reopen_reason: text })
    else if (text) body.note = text
    // Same request, same key: a retry after a dropped connection can't double-post.
    const serial = JSON.stringify(body)
    if (keyRef.current?.body !== serial) keyRef.current = { body: serial, key: newId() }

    setSending(true)
    setFeedback(null)
    try {
      const result = await agentApi.updateReview(c.id, body, keyRef.current.key)
      keyRef.current = null
      reset()
      setFeedback({ kind: 'saved', result })
      onChanged()
      refreshQueue()
    } catch (e) {
      if (isApiError(e) && e.status === 409) {
        setFeedback({
          kind: 'conflict',
          message:
            e.code === 'STALE_VERSION'
              ? 'Someone changed this case while you were writing. The latest version is loaded. Your draft is kept: check the changes, then send again.'
              : 'The review status was changed by someone else, so this step is no longer allowed. The latest version is loaded and your draft is kept.',
        })
        onChanged()
      } else if (isApiError(e) && e.status === 422) {
        setFeedback({ kind: 'error', message: e.message || describeError(e) })
      } else {
        setFeedback({ kind: 'error', message: `Not sent: ${describeError(e)} Your draft is kept, so you can try again.` })
      }
    } finally {
      setSending(false)
    }
  }

  // Clear "Saved" after a while so it doesn't describe an older state.
  useEffect(() => {
    if (feedback?.kind !== 'saved') return
    const t = window.setTimeout(() => setFeedback(null), 8000)
    return () => window.clearTimeout(t)
  }, [feedback])

  const lastClose = [...detail.audit_events].reverse().find((e) => e.event_type === 'REVIEW_UPDATED' && e.details.to === 'CLOSED')
  const notes = [...detail.review_notes].reverse()

  return (
    <CardFrame icon={<ClipboardCheck />} title="Your review" tone={reviewTone[c.review_status]} aside={<ReviewBadge status={c.review_status} />}>
      <div className="flex flex-col gap-4">
        <p className="text-muted-foreground">
          {c.review_status === 'NEW' && 'Nobody has picked this up yet.'}
          {c.review_status === 'IN_REVIEW' && 'Under review. Close it with an outcome when you are done.'}
          {c.review_status === 'CLOSED' &&
            `Closed${typeof lastClose?.details.disposition === 'string' ? `: ${dispositionLabel(lastClose.details.disposition).toLowerCase()}` : ''}. Reopen it if something new comes up.`}
        </p>

        <div className="flex flex-wrap gap-2">
          {canStart && (
            <Button size="sm" onClick={() => void send(true)} disabled={sending || mode !== 'note' || draft.text.length > MAX || changedSince}>
              Start review
            </Button>
          )}
          {allowed.map((m) => (
            <Button
              key={m}
              size="sm"
              variant="outline"
              onClick={() => choose(mode === m ? 'note' : m)}
              aria-pressed={mode === m}
              className={cn(mode === m && 'border-foreground bg-foreground/5')}
            >
              {m === 'close' ? (mode === m ? 'Cancel closing' : 'Close…') : mode === m ? 'Cancel reopening' : 'Reopen…'}
            </Button>
          ))}
        </div>

        <form
          className="flex flex-col gap-3"
          onSubmit={(e) => {
            e.preventDefault()
            if (canSend) void send()
          }}
        >
          {mode === 'close' && (
            <fieldset className="flex flex-col gap-1.5">
              <legend className="mb-1.5 text-xs font-semibold">Outcome</legend>
              {(Object.keys(DISPOSITION) as Disposition[]).map((d) => (
                <label
                  key={d}
                  className={cn(
                    'flex cursor-pointer gap-2 rounded-lg bg-card px-3 py-2 ring-1 ring-transparent has-focus-visible:ring-ring',
                    draft.disposition === d && 'ring-foreground/40',
                  )}
                >
                  <input type="radio" name="disposition" value={d} checked={draft.disposition === d} onChange={() => setDraft({ disposition: d })} className="mt-1 accent-primary" />
                  <span className="flex flex-col">
                    <span className="font-medium">{DISPOSITION[d]}</span>
                    <span className="text-xs text-muted-foreground">{DISPOSITION_HINT[d]}</span>
                  </span>
                </label>
              ))}
            </fieldset>
          )}

          <div className="flex flex-col gap-1.5">
            <label htmlFor={`note-${c.id}`} className="flex items-center justify-between text-xs font-semibold">
              <span>
                {mode === 'reopen' ? 'Reason for reopening' : mode === 'close' ? 'Closing note' : 'Internal note'}
              </span>
              <span className="inline-flex items-center gap-1 font-normal text-muted-foreground">
                <Lock className="size-3" aria-hidden /> Never shown to the customer
              </span>
            </label>
            <Textarea
              id={`note-${c.id}`}
              ref={textRef}
              value={draft.text}
              onChange={(e) => setDraft({ text: e.target.value })}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && (e.metaKey || e.ctrlKey) && canSend) {
                  e.preventDefault()
                  void send()
                }
              }}
              rows={mode === 'note' ? 3 : 4}
              maxLength={MAX}
              placeholder={
                mode === 'reopen'
                  ? 'e.g. Customer sent a new bank statement.'
                  : mode === 'close'
                    ? 'What you checked and what happens next.'
                    : 'What you checked, what you found, what is next.'
              }
              className="resize-y bg-card"
              aria-describedby={`note-help-${c.id}`}
            />
            <p id={`note-help-${c.id}`} className="flex justify-between text-[11px] text-muted-foreground">
              <span>{problem && (draft.text || draft.disposition || mode !== 'note') ? problem : 'Ctrl/⌘ + Enter to send'}</span>
              <span className={cn(draft.text.length > MAX - 100 && 'text-warning-foreground')}>
                {draft.text.length}/{MAX}
              </span>
            </p>
          </div>

          {mode === 'close' && <p className="text-xs text-muted-foreground">Closing the review changes no account, evidence or ticket status.</p>}

          {changedSince && feedback?.kind !== 'conflict' && (
            <Notice tone="warning">
              This case changed after you started (version {draft.baseVersion} → {c.version}). Check the update before sending.{' '}
              <button type="button" className="font-semibold underline" onClick={() => setDraftState((d) => ({ ...d, baseVersion: c.version }))}>
                I've checked it
              </button>
            </Notice>
          )}
          {feedback?.kind === 'conflict' && (
            <Notice tone="danger">
              {feedback.message}{' '}
              <button
                type="button"
                className="font-semibold underline"
                onClick={() => {
                  setDraftState((d) => ({ ...d, baseVersion: c.version }))
                  setFeedback(null)
                }}
              >
                I've checked it
              </button>
            </Notice>
          )}
          {feedback?.kind === 'error' && <Notice tone="danger">{feedback.message}</Notice>}
          <p aria-live="polite" className="sr-only">
            {feedback?.kind === 'saved' ? 'Saved.' : ''}
          </p>
          {feedback?.kind === 'saved' && (
            <p className="flex flex-wrap items-center gap-2 text-xs text-success">
              Saved as version {feedback.result.version}.
              {feedback.result.review_sync_state !== 'NOT_APPLICABLE' && (
                <StatusBadge tone={feedback.result.review_sync_state === 'SYNCED' ? 'success' : 'warning'}>{syncLabel(feedback.result.review_sync_state)}</StatusBadge>
              )}
            </p>
          )}

          <div className="flex items-center gap-2">
            <Button type="submit" disabled={!canSend || (changedSince && feedback?.kind !== 'error')} variant={mode === 'close' ? 'default' : mode === 'note' ? 'outline' : 'default'}>
              {sending ? 'Saving…' : SUBMIT_LABEL[mode]}
            </Button>
            {(mode !== 'note' || draft.text) && (
              <Button type="button" variant="ghost" size="sm" onClick={reset} disabled={sending}>
                Discard
              </Button>
            )}
          </div>
        </form>

        <section aria-label="Internal notes" className="flex flex-col gap-2 border-t border-foreground/10 pt-3">
          <h4 className="text-xs font-semibold text-muted-foreground">Notes ({notes.length})</h4>
          {notes.length === 0 ? (
            <p className="text-xs text-muted-foreground">No notes yet.</p>
          ) : (
            <ol className="flex max-h-80 flex-col gap-2 overflow-y-auto">
              {notes.map((n) => (
                <li key={n.id} className="rounded-xl bg-card px-3 py-2">
                  <p className="whitespace-pre-wrap">{n.note}</p>
                  <p className="mt-1 text-[11px] text-muted-foreground">
                    {n.actor_id} · {formatDateTime(n.created_at)}
                  </p>
                </li>
              ))}
            </ol>
          )}
        </section>
      </div>
    </CardFrame>
  )
}

function Notice({ tone, children }: { tone: 'warning' | 'danger'; children: React.ReactNode }) {
  return (
    <div role="alert" className={cn('flex gap-2 rounded-lg px-3 py-2 text-xs', tone === 'danger' ? 'bg-destructive/10 text-destructive' : 'bg-warning/25 text-foreground')}>
      <AlertTriangle className="mt-0.5 size-3.5 shrink-0" aria-hidden />
      <span>{children}</span>
    </div>
  )
}
