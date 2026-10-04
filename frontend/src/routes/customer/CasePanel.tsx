import { useEffect, useState } from 'react'
import { UserRoundSearch } from 'lucide-react'
import { newId } from '@/api/client'
import { customerApi } from '@/api/endpoints'
import { describeError } from '@/api/errors'
import type { CaseView, ConversationView } from '@/api/types'
import { EvidenceBadge, StatusBadge, type Tone } from '@/components/StatusBadge'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { hasMessage, useI18n, type Translate } from '@/i18n/context'
import { humanize } from '@/lib/format'
import { cn } from '@/lib/utils'
import { ReceiptDownloadButton } from './cards/ChatCards'
import { RecordsSection, type ChatRecord, type RecordFocus } from './Records'

const CASE_TONE: Record<string, Tone> = {
  OPEN: 'info',
  AWAITING_CUSTOMER: 'warning',
  ACTION_PENDING: 'warning',
  REVIEW_REQUIRED: 'info',
  RESOLVED: 'success',
}

function caseStatus(t: Translate, status: string) {
  const key = `caseStatus.${status}`
  return { label: hasMessage(key) ? t(key) : humanize(status), tone: CASE_TONE[status] ?? ('neutral' as Tone) }
}

export type CasePanelProps = {
  /** The chat's records (checks, findings, decisions, progress, receipts), newest first. */
  records: ChatRecord[]
  recordFocus: RecordFocus | null
  conversation: ConversationView | null
  refreshKey: number
  disabled: boolean
  onSelectCase: (caseId: string, label: string) => void
  /** Called after Resolve returns a review-ticket proposal; the chat then shows it for confirmation. */
  onReviewRequested: () => void
}

/** Desktop: case context beside the chat. */
export function CasePanel(props: CasePanelProps) {
  return (
    <aside aria-label="Records and cases" className="relative hidden w-96 shrink-0 flex-col gap-4 overflow-y-auto border-l bg-sidebar p-4 lg:flex">
      <CasePanelBody {...props} />
    </aside>
  )
}

/** Case list, current case, receipt and human review. Every status shown here comes from Resolve. */
export function CasePanelBody({ records, recordFocus, conversation, refreshKey, disabled, onSelectCase, onReviewRequested }: CasePanelProps) {
  const { t } = useI18n()
  const activeId = conversation?.active_case_id ?? null
  const [active, setActive] = useState<CaseView | null>(null)

  useEffect(() => {
    if (!activeId) return
    let cancelled = false
    customerApi
      .getCase(activeId)
      .then((c) => !cancelled && setActive(c))
      .catch(() => {
        /* panel is supplementary; the chat shows errors that matter */
      })
    return () => {
      cancelled = true
    }
  }, [activeId, refreshKey])

  const shown = active && active.id === activeId ? active : null
  const reviewPending = conversation?.pending_proposal?.action_type === 'CREATE_REVIEW_TICKET'

  return (
    <>
      <RecordsSection records={records} focus={recordFocus} />
      <h2 className="border-t pt-4 text-sm font-semibold">{t('panel.yourCases')}</h2>
      {!conversation || conversation.cases.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          {t('panel.empty')}
        </p>
      ) : (
        <ul className="flex flex-col gap-2">
          {conversation.cases.map((c) => {
            const status = caseStatus(t, c.status)
            const isActive = c.id === activeId
            const label = t(`complaint.${c.complaint_type}`)
            return (
              <li key={c.id}>
                <button
                  type="button"
                  disabled={isActive || disabled}
                  aria-current={isActive ? 'true' : undefined}
                  onClick={() => onSelectCase(c.id, label)}
                  className={cn(
                    'w-full rounded-lg border bg-card p-3 text-left text-sm transition-colors enabled:hover:bg-muted disabled:cursor-default',
                    isActive && 'border-primary/50 ring-1 ring-primary/30',
                  )}
                >
                  <p className="font-medium">{label}</p>
                  <span className="mt-1.5 flex items-center justify-between gap-2">
                    <StatusBadge tone={status.tone}>{status.label}</StatusBadge>
                    {!isActive && <span className="text-xs text-muted-foreground">{t('panel.switch')}</span>}
                  </span>
                </button>
              </li>
            )
          })}
        </ul>
      )}

      {shown && (
        <section aria-label="Current case" className="flex flex-col gap-3 border-t pt-4 text-sm">
          <h3 className="font-semibold">{t('panel.current')}</h3>
          <div className="flex flex-wrap gap-1.5">
            <StatusBadge tone={caseStatus(t, shown.status).tone}>{caseStatus(t, shown.status).label}</StatusBadge>
            <EvidenceBadge state={shown.investigation?.evidence_state} />
          </div>
          {shown.investigation && shown.investigation.missing.length > 0 && (
            <p className="text-xs text-muted-foreground">
              {t('panel.notAvailable', { items: shown.investigation.missing.map((m) => humanize(m).toLowerCase()).join('; ') })}
            </p>
          )}
          {shown.receipt ? (
            <ReceiptDownloadButton caseId={shown.id} />
          ) : (
            <p className="text-xs text-muted-foreground">{t('panel.receiptLater')}</p>
          )}
          {shown.investigation && (
            <ReviewRequest caseView={shown} pending={reviewPending} disabled={disabled} onRequested={onReviewRequested} />
          )}
          <p className="font-mono text-[11px] text-muted-foreground">{t('panel.case', { id: shown.id.slice(-8) })}</p>
        </section>
      )}
    </>
  )
}

const MAX_REASON = 2000

/**
 * Ask Resolve for a human review. Resolve returns a CREATE_REVIEW_TICKET proposal, which the customer
 * confirms like any other action — nothing is sent to a reviewer from this form alone.
 */
function ReviewRequest({
  caseView,
  pending,
  disabled,
  onRequested,
}: {
  caseView: CaseView
  pending: boolean
  disabled: boolean
  onRequested: () => void
}) {
  const { t } = useI18n()
  const [open, setOpen] = useState(false)
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  // One key per attempt at this request, so a retry after a network failure can't create two proposals.
  const [requestKey, setRequestKey] = useState(newId)

  if (pending) {
    return (
      <p className="rounded-md bg-muted p-2.5 text-xs">
        {t('review.pending')}
      </p>
    )
  }

  if (!open) {
    return (
      <Button variant="outline" size="sm" className="w-fit" disabled={disabled} onClick={() => setOpen(true)}>
        <UserRoundSearch aria-hidden /> {t('action.CREATE_REVIEW_TICKET')}
      </Button>
    )
  }

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    const text = reason.trim()
    if (!text || !caseView.investigation) return
    setBusy(true)
    setError(null)
    try {
      await customerApi.requestReview(
        caseView.id,
        { expected_version: caseView.version, investigation_id: caseView.investigation.id, reason: text },
        requestKey,
      )
      setOpen(false)
      setReason('')
      setRequestKey(newId())
      onRequested()
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-2 rounded-lg border bg-card p-3">
      <Label htmlFor="review-reason">{t('review.question')}</Label>
      <Textarea
        id="review-reason"
        rows={3}
        maxLength={MAX_REASON}
        required
        value={reason}
        onChange={(e) => {
          setReason(e.target.value)
          setRequestKey(newId()) // a changed request is a new request
        }}
        placeholder={t('review.placeholder')}
      />
      <p className="text-[11px] text-muted-foreground">
        {t('review.note')}
      </p>
      {error != null && (
        <p role="alert" className="text-xs text-destructive">
          {describeError(error, t)}
        </p>
      )}
      <div className="flex gap-2">
        <Button type="submit" size="sm" disabled={busy || disabled || !reason.trim()}>
          {busy ? t('common.preparing') : t('review.submit')}
        </Button>
        <Button type="button" size="sm" variant="ghost" onClick={() => setOpen(false)} disabled={busy}>
          {t('common.cancel')}
        </Button>
      </div>
    </form>
  )
}
