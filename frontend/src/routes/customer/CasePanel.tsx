import { useEffect, useState } from 'react'
import { customerApi } from '@/api/endpoints'
import type { CaseView, ConversationView } from '@/api/types'
import { EvidenceBadge, StatusBadge, type Tone } from '@/components/StatusBadge'
import { humanize } from '@/lib/format'
import { cn } from '@/lib/utils'
import { ReceiptDownloadButton } from './cards/ChatCards'

const CASE_STATUS: Record<string, { label: string; tone: Tone }> = {
  OPEN: { label: 'Open', tone: 'info' },
  AWAITING_CUSTOMER: { label: 'Waiting for you', tone: 'warning' },
  ACTION_PENDING: { label: 'Action in progress', tone: 'warning' },
  REVIEW_REQUIRED: { label: 'With a reviewer', tone: 'info' },
  RESOLVED: { label: 'Resolved', tone: 'success' },
}

/** Case context beside the chat. Every status shown here comes from Resolve. */
export function CasePanel({ conversation, refreshKey }: { conversation: ConversationView | null; refreshKey: number }) {
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

  return (
    <aside aria-label="Your cases" className="hidden w-80 shrink-0 flex-col gap-4 overflow-y-auto border-l bg-sidebar p-4 lg:flex">
      <h2 className="text-sm font-semibold">Your cases</h2>
      {!conversation || conversation.cases.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          When we look into an issue, the case and the evidence we checked will appear here.
        </p>
      ) : (
        <ul className="flex flex-col gap-2">
          {conversation.cases.map((c) => {
            const status = CASE_STATUS[c.status] ?? { label: humanize(c.status), tone: 'neutral' as Tone }
            return (
              <li
                key={c.id}
                className={cn('rounded-lg border bg-card p-3 text-sm', c.id === activeId && 'border-primary/50 ring-1 ring-primary/30')}
              >
                <p className="font-medium">{humanize(c.complaint_type)}</p>
                <StatusBadge tone={status.tone} className="mt-1.5">
                  {status.label}
                </StatusBadge>
              </li>
            )
          })}
        </ul>
      )}

      {shown && (
        <section aria-label="Current case" className="flex flex-col gap-3 border-t pt-4 text-sm">
          <h3 className="font-semibold">Current case</h3>
          <div className="flex flex-wrap gap-1.5">
            <StatusBadge tone={(CASE_STATUS[shown.status] ?? { tone: 'neutral' }).tone}>
              {(CASE_STATUS[shown.status] ?? { label: humanize(shown.status) }).label}
            </StatusBadge>
            <EvidenceBadge state={shown.investigation?.evidence_state} />
          </div>
          {shown.investigation && shown.investigation.missing.length > 0 && (
            <p className="text-xs text-muted-foreground">Missing: {shown.investigation.missing.join('; ')}</p>
          )}
          {shown.receipt ? (
            <ReceiptDownloadButton caseId={shown.id} />
          ) : (
            <p className="text-xs text-muted-foreground">A receipt will be available once a decision is recorded.</p>
          )}
          <p className="font-mono text-[11px] text-muted-foreground">Case {shown.id.slice(-8)}</p>
        </section>
      )}
    </aside>
  )
}
