import { useCallback, useEffect, useState } from 'react'
import { RefreshCw } from 'lucide-react'
import { useParams } from 'react-router'
import { agentApi } from '@/api/endpoints'
import type { AgentCaseDetail } from '@/api/types'
import { EmptyState, ErrorState, LoadingState } from '@/components/states'
import { DeliveryBadge, EvidenceBadge, ReviewBadge, StatusBadge } from '@/components/StatusBadge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { CalculationTable } from '@/components/evidence/CalculationTable'
import { formatDateTime, formatLkr, humanize } from '@/lib/format'
import { COMPLAINT_LABEL } from '@/lib/labels'


export function NoCaseSelected() {
  return <EmptyState title="Select a case" description="Choose a case from the queue to see its evidence and history." />
}

/**
 * Case packet shell (J-01). Shows server-provided facts only; review actions
 * (notes, start/close/reopen with expected_version) arrive in J-02.
 */
export function CaseDetailRoute() {
  const { caseId = '' } = useParams()
  // Keyed by case so switching cases starts from a clean state.
  return <CaseDetail key={caseId} caseId={caseId} />
}

function CaseDetail({ caseId }: { caseId: string }) {
  const [detail, setDetail] = useState<AgentCaseDetail | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [refreshing, setRefreshing] = useState(false)

  const load = useCallback(
    async (signal?: AbortSignal) => {
      try {
        setDetail(await agentApi.getCase(caseId, signal))
        setError(null)
      } catch (e) {
        if ((e as Error).name !== 'AbortError') setError(e)
      }
    },
    [caseId],
  )

  const refresh = async () => {
    setRefreshing(true)
    await load()
    setRefreshing(false)
  }

  useEffect(() => {
    const ctrl = new AbortController()
    void load(ctrl.signal)
    return () => ctrl.abort()
  }, [load])

  if (error != null && !detail) return <ErrorState title="Could not load this case" error={error} onRetry={() => void refresh()} />
  if (!detail) return <LoadingState label="Loading case…" rows={4} />

  const { case: c, account, handoff } = detail
  const inv = c.investigation

  return (
    <article className="mx-auto flex max-w-4xl flex-col gap-4 p-4 lg:p-6">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="font-mono text-sm text-muted-foreground">{account.line_alias}</p>
          <h2 className="text-xl font-bold tracking-tight">{COMPLAINT_LABEL[c.complaint_type] ?? humanize(c.complaint_type)}</h2>
          <p className="text-xs text-muted-foreground">
            Case <span className="font-mono">{c.id}</span> · v{c.version} · updated {formatDateTime(c.updated_at)}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <ReviewBadge status={c.review_status} />
          <EvidenceBadge state={inv?.evidence_state} />
          <DeliveryBadge state={handoff?.delivery_state} />
          <Button variant="outline" size="sm" onClick={() => void refresh()} disabled={refreshing} aria-label="Refresh case">
            <RefreshCw aria-hidden /> Refresh
          </Button>
        </div>
      </header>

      <div className="grid gap-4 md:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Account</CardTitle>
          </CardHeader>
          <CardContent className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
            <Field label="Customer" value={account.display_name} />
            <Field label="Region" value={humanize(account.region)} />
            <Field label="Status" value={humanize(account.status)} />
            {account.balances.map((b) => (
              <Field key={b.wallet} label={`${humanize(b.wallet)} balance`} value={formatLkr(b.amount_minor)} mono />
            ))}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Ticket</CardTitle>
          </CardHeader>
          <CardContent className="text-sm">
            {handoff ? (
              <div className="grid grid-cols-2 gap-x-4 gap-y-2">
                <Field label="Queue" value={humanize(handoff.queue)} />
                <Field label="Provider ticket" value={handoff.provider_ticket_id ?? 'Not yet assigned'} mono={!!handoff.provider_ticket_id} />
                <div className="col-span-2">
                  <Field label="Next step" value={handoff.next_step} />
                </div>
              </div>
            ) : (
              <p className="text-muted-foreground">No escalation has been delivered for this case.</p>
            )}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Investigation</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-4 text-sm">
          {!inv ? (
            <p className="text-muted-foreground">Not investigated yet.</p>
          ) : (
            <>
              <p className="text-xs text-muted-foreground">
                Revision {inv.revision} · window {formatDateTime(inv.window_start)} – {formatDateTime(inv.window_end)}
              </p>
              {inv.findings.length > 0 && (
                <ul className="flex flex-col gap-1.5">
                  {inv.findings.map((f) => (
                    <li key={f.code}>{f.text}</li>
                  ))}
                </ul>
              )}
              <Notes tone="danger" title="Conflicts" items={inv.conflicts} />
              <Notes tone="warning" title="Missing information" items={inv.missing} />
              <Notes tone="info" title="Needs human review because" items={inv.review_reasons} />
              {inv.calculations.map((calc) => (
                <CalculationTable key={calc.code} calc={calc} />
              ))}
            </>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Internal notes</CardTitle>
        </CardHeader>
        <CardContent className="text-sm">
          {detail.review_notes.length === 0 ? (
            <p className="text-muted-foreground">No internal notes yet.</p>
          ) : (
            <ul className="flex flex-col gap-3">
              {detail.review_notes.map((n) => (
                <li key={n.id} className="rounded-md bg-muted p-3">
                  <p>{n.note}</p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    {n.actor_id} · {formatDateTime(n.created_at)}
                  </p>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </article>
  )
}

function Field({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex flex-col">
      <span className="text-xs text-muted-foreground">{label}</span>
      <span className={mono ? 'font-mono' : undefined}>{value}</span>
    </div>
  )
}

function Notes({ title, items, tone }: { title: string; items: string[]; tone: 'danger' | 'warning' | 'info' }) {
  if (items.length === 0) return null
  return (
    <div className="flex flex-col gap-1.5">
      <StatusBadge tone={tone} className="self-start">
        {title}
      </StatusBadge>
      <ul className="list-disc pl-5">
        {items.map((t) => (
          <li key={t}>{t}</li>
        ))}
      </ul>
    </div>
  )
}
