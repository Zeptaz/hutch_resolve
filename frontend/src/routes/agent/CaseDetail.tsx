import { useCallback, useEffect, useState } from 'react'
import { AlertTriangle, ArrowLeft, CheckCircle2, CircleHelp, RefreshCw } from 'lucide-react'
import { Link, useParams } from 'react-router'
import { agentApi } from '@/api/endpoints'
import type { AgentCaseDetail, Calculation, EvidenceState } from '@/api/types'
import { CardFrame } from '@/components/CardFrame'
import { ErrorState, LoadingState } from '@/components/states'
import { ReviewBadge, StatusBadge } from '@/components/StatusBadge'
import { deliveryTone, evidenceTone } from '@/components/tones'
import { Button } from '@/components/ui/button'
import { formatCalcValue, formatDateTime, formatLkr, formatTime, humanize } from '@/lib/format'
import { COMPLAINT_LABEL } from '@/lib/labels'
import { cn } from '@/lib/utils'
import { CaseTabs } from './CaseTabs'
import { CopyId } from './CopyId'
import { caseStatusLabel, reasonLabel } from './labels'
import { CLASSIFICATION_HEADLINE, CLASSIFICATION_LABEL, classificationTone, OutcomeSummary } from './OutcomeSummary'
import { ReviewPanel } from './ReviewPanel'

const POLL_MS = 5000

export function NoCaseSelected() {
  return (
    <div className="grid h-full place-items-center p-8">
      <div className="flex max-w-xs flex-col items-center gap-2 text-center">
        <p className="font-semibold">Pick a case from the queue</p>
        <p className="text-sm text-muted-foreground">
          Tinted cases need attention first. Use <kbd className="font-mono">j</kbd> and <kbd className="font-mono">k</kbd> to move through them.
        </p>
      </div>
    </div>
  )
}

export function CaseDetailRoute() {
  const { caseId = '' } = useParams()
  // Keyed by case so switching cases starts from a clean state.
  return <CaseDetail key={caseId} caseId={caseId} />
}

function CaseDetail({ caseId }: { caseId: string }) {
  const [detail, setDetail] = useState<AgentCaseDetail | null>(null)
  const [loadedAt, setLoadedAt] = useState<string | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [refreshing, setRefreshing] = useState(false)

  const load = useCallback(
    async (signal?: AbortSignal) => {
      try {
        setDetail(await agentApi.getCase(caseId, signal))
        setLoadedAt(new Date().toISOString())
        setError(null)
      } catch (e) {
        if ((e as Error).name !== 'AbortError') setError(e)
      }
    },
    [caseId],
  )

  const refresh = useCallback(async () => {
    setRefreshing(true)
    await load()
    setRefreshing(false)
  }, [load])

  // Live while visible, so a ticket sync or another agent's note shows up without a reload.
  useEffect(() => {
    const ctrl = new AbortController()
    void load(ctrl.signal)
    const tick = () => {
      if (document.visibilityState === 'visible') void load(ctrl.signal)
    }
    const id = window.setInterval(tick, POLL_MS)
    document.addEventListener('visibilitychange', tick)
    return () => {
      ctrl.abort()
      window.clearInterval(id)
      document.removeEventListener('visibilitychange', tick)
    }
  }, [load])

  if (error != null && !detail) return <ErrorState title="Could not load this case" error={error} onRetry={() => void refresh()} />
  if (!detail) return <LoadingState label="Loading case…" rows={4} />

  return (
    <article className="mx-auto flex max-w-6xl flex-col gap-5 p-4 lg:p-6">
      <Link to="/agent" className="inline-flex items-center gap-1 self-start text-sm font-medium text-muted-foreground hover:text-foreground lg:hidden">
        <ArrowLeft className="size-4" aria-hidden /> Queue
      </Link>
      <div className="animate-rise-in">
        <CaseHeader detail={detail} loadedAt={loadedAt} refreshing={refreshing} onRefresh={() => void refresh()} stale={error != null} />
      </div>
      <div className="grid gap-5 [grid-template-areas:'why'_'review'_'tabs'] xl:grid-cols-[minmax(0,1fr)_22rem] xl:[grid-template-areas:'why_review'_'tabs_review']">
        <div className="animate-rise-in [grid-area:why]" style={{ '--i': 1 } as React.CSSProperties}>
          <WhyHere detail={detail} />
        </div>
        <div className="animate-rise-in [grid-area:review] xl:sticky xl:top-4 xl:self-start" style={{ '--i': 2 } as React.CSSProperties}>
          <ReviewPanel detail={detail} onChanged={() => void refresh()} />
        </div>
        <div className="min-w-0 animate-rise-in [grid-area:tabs]" style={{ '--i': 3 } as React.CSSProperties}>
          <CaseTabs detail={detail} />
        </div>
      </div>
    </article>
  )
}

function CaseHeader({
  detail,
  loadedAt,
  refreshing,
  onRefresh,
  stale,
}: {
  detail: AgentCaseDetail
  loadedAt: string | null
  refreshing: boolean
  onRefresh: () => void
  stale: boolean
}) {
  const { case: c, account, handoff } = detail
  const main = account.balances.find((b) => b.wallet === 'MAIN') ?? account.balances[0]
  return (
    <header className="flex flex-col gap-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-0 flex-col gap-1">
          <p className="text-sm text-muted-foreground">
            <span className="font-mono font-semibold text-foreground">{account.line_alias}</span> · {account.display_name} · {humanize(account.region)}
            {main && <> · balance {formatLkr(main.amount_minor)}</>}
          </p>
          <h2 className="text-2xl font-bold tracking-tight">{COMPLAINT_LABEL[c.complaint_type] ?? humanize(c.complaint_type)}</h2>
          <p className="flex flex-wrap items-center gap-x-2 text-xs text-muted-foreground">
            <span>
              Opened {formatDateTime(c.created_at)} · last activity {formatDateTime(c.updated_at)} · version {c.version}
            </span>
            <CopyId label="Case ID" value={c.id} />
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className={cn('text-xs', stale ? 'text-destructive' : 'text-muted-foreground')} aria-live="polite">
            {stale ? 'Could not refresh' : loadedAt ? `Live · ${formatTime(loadedAt)}` : ''}
          </span>
          <Button variant="outline" size="sm" onClick={onRefresh} disabled={refreshing} aria-label="Refresh case">
            <RefreshCw aria-hidden className={cn(refreshing && 'animate-spin')} /> Refresh
          </Button>
        </div>
      </div>
      {/* Four separate states, each with its own name, so none is mistaken for another. */}
      <dl className="flex flex-wrap gap-x-5 gap-y-2 text-xs">
        <State label="Case">
          <StatusBadge key={c.status} className="animate-pop" tone={c.status === 'REVIEW_REQUIRED' ? 'danger' : c.status === 'RESOLVED' ? 'success' : 'info'}>{caseStatusLabel(c.status)}</StatusBadge>
        </State>
        <State label="Review">
          <span key={c.review_status} className="inline-flex animate-pop">
            <ReviewBadge status={c.review_status} />
          </span>
        </State>
        <State label="Evidence">
          {c.investigation ? (
            <StatusBadge tone={evidenceTone[c.investigation.evidence_state]}>{humanize(c.investigation.evidence_state)}</StatusBadge>
          ) : (
            <StatusBadge tone="neutral">Not checked</StatusBadge>
          )}
        </State>
        {c.investigation?.outcome && (
          <State label="Finding">
            <StatusBadge tone={classificationTone[c.investigation.outcome.classification]}>
              {CLASSIFICATION_LABEL[c.investigation.outcome.classification]}
            </StatusBadge>
          </State>
        )}
        <State label="Ticket">
          {handoff ? <StatusBadge key={handoff.delivery_state} className="animate-pop" tone={deliveryTone[handoff.delivery_state]}>{humanize(handoff.delivery_state)}</StatusBadge> : <StatusBadge tone="neutral">No ticket</StatusBadge>}
        </State>
      </dl>
    </header>
  )
}

function State({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-2">
      <dt className="font-medium text-muted-foreground">{label}</dt>
      <dd>{children}</dd>
    </div>
  )
}


const HEADLINE: Record<EvidenceState, string> = {
  CONFLICTING: 'Records disagree, so Resolve will not change the account on its own. A person has to decide which record is right.',
  PARTIAL: 'Some records were missing, so Resolve could not reach a firm answer.',
  SUFFICIENT: 'The records are complete and add up.',
}

/** The answer first: why a person is looking at this, and the one number that matters. */
function WhyHere({ detail }: { detail: AgentCaseDetail }) {
  const inv = detail.case.investigation
  const state = inv?.evidence_state
  const conflicts = new Set(inv?.conflicts ?? [])
  const missing = new Set(inv?.missing ?? [])
  const reasons = [...new Set([...(inv?.review_reasons ?? []), ...conflicts, ...missing])]
  // Only an accepted review offer counts; accepting another action (e.g. a renewal stop) does not.
  const reviewOffers = new Set(detail.proposals.filter((p) => p.action_type === 'CREATE_REVIEW_TICKET').map((p) => p.id))
  const acceptedReview = detail.confirmations.some((c) => c.decision === 'ACCEPT' && reviewOffers.has(c.proposal_id))
  const calc = inv?.calculations[0]
  const outcome = inv?.outcome ?? null

  return (
    <CardFrame
      icon={state === 'SUFFICIENT' ? <CheckCircle2 /> : state ? <AlertTriangle /> : <CircleHelp />}
      title="Why this case is here"
      tone={state ? evidenceTone[state] : undefined}
    >
      <div className="flex flex-col gap-4">
        <p className="text-base leading-snug text-balance">
          {outcome ? CLASSIFICATION_HEADLINE[outcome.classification] : state ? HEADLINE[state] : 'Resolve has not investigated this case yet.'}
          {acceptedReview && ' The customer accepted a human review.'}
        </p>
        {reasons.length > 0 && (
          <ul className="flex flex-col gap-1.5">
            {reasons.map((r) => (
              <li key={r} className="flex items-start gap-2">
                <span aria-hidden className={cn('mt-1.5 size-2 shrink-0 rounded-full', conflicts.has(r) ? 'bg-destructive' : missing.has(r) ? 'bg-warning' : 'bg-info')} />
                <span>
                  {reasonLabel(r)}
                  <span className="sr-only">{conflicts.has(r) ? ' (conflict)' : missing.has(r) ? ' (missing)' : ''}</span>
                </span>
              </li>
            ))}
          </ul>
        )}
        {outcome ? <OutcomeSummary outcome={outcome} /> : calc && <KeyNumbers calc={calc} />}
      </div>
    </CardFrame>
  )
}

function KeyNumbers({ calc }: { calc: Calculation }) {
  const fmt = (n: number | null | undefined) => (n == null ? 'Unavailable' : formatCalcValue(calc.unit, n))
  const off = calc.delta != null && calc.delta !== 0
  return (
    <dl className="grid gap-2 rounded-xl bg-card p-3 sm:grid-cols-3">
      <Num label="Expected" value={fmt(calc.expected)} />
      <Num label="Recorded" value={fmt(calc.observed)} />
      <Num label="Difference" value={fmt(calc.delta)} className={off ? 'text-destructive' : calc.delta === 0 ? 'text-success' : undefined} />
    </dl>
  )
}

function Num({ label, value, className }: { label: string; value: string; className?: string }) {
  return (
    <div className="flex min-w-0 items-baseline justify-between gap-3 sm:flex-col sm:justify-start sm:gap-0">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className={cn('font-mono text-base font-semibold whitespace-nowrap sm:text-lg', className)}>{value}</dd>
    </div>
  )
}
