import { useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router'
import { useNow } from './time'
import { Calculator, Database, Download, FileCheck2, History, ListChecks, MessagesSquare, Search, Ticket, UserRound } from 'lucide-react'
import type { AgentCaseDetail, Calculation, InvestigationResult, ProposalView, ReceiptView, SourceStatus } from '@/api/types'
import { CardFrame } from '@/components/CardFrame'
import { DeliveryBadge, OperationBadge, StatusBadge } from '@/components/StatusBadge'
import { deliveryTone, operationTone, type Tone } from '@/components/tones'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { downloadJson } from '@/lib/download'
import { formatCalcValue, formatDateTime, formatGb, formatLkr, humanize } from '@/lib/format'
import { cn } from '@/lib/utils'
import { CopyId } from './CopyId'
import { actionLabel, auditLabel, calcLabel, findingLabel, queueLabel, sourceLabel, syncLabel, termLabel } from './labels'

const SYNC_TONE: Record<string, Tone> = {
  NOT_APPLICABLE: 'neutral',
  PENDING: 'warning',
  UNKNOWN: 'warning',
  SYNCED: 'success',
  FAILED: 'danger',
  REVIEW_REQUIRED: 'danger',
}

const TAB_IDS = ['evidence', 'actions', 'conversation', 'receipts', 'history'] as const
type TabId = (typeof TAB_IDS)[number]

export function CaseTabs({ detail }: { detail: AgentCaseDetail }) {
  // The open tab lives in the URL (?tab=), so refresh and Back land on the same view.
  const [params, setParams] = useSearchParams()
  const requested = params.get('tab') as TabId | null
  const active: TabId = requested && TAB_IDS.includes(requested) ? requested : 'evidence'
  // Marks where the tab bar sits in the flow; the bar itself is sticky, so it can't be measured for this.
  const anchorRef = useRef<HTMLDivElement>(null)
  const stripRef = useRef<HTMLDivElement>(null)

  // On narrow screens the strip scrolls sideways; keep the open tab in view (also when opened from the URL).
  useEffect(() => {
    const strip = stripRef.current
    const tab = strip?.querySelector<HTMLElement>('[role=tab][data-state=active]')
    if (!strip || !tab) return
    const left = tab.offsetLeft - strip.offsetLeft
    if (left < strip.scrollLeft || left + tab.offsetWidth > strip.scrollLeft + strip.clientWidth) {
      strip.scrollTo({ left: Math.max(0, left - 16), behavior: 'smooth' })
    }
  }, [active])

  const select = (value: string) => {
    const anchor = anchorRef.current
    const scroller = anchor?.closest<HTMLElement>('[data-case-scroller]')
    // Was the reader already below the tab bar? Then the new tab should open right under it,
    // instead of wherever the old, longer tab happened to leave the scroll position.
    const anchored = anchor && scroller ? anchor.getBoundingClientRect().top - scroller.getBoundingClientRect().top <= 8 : false
    setParams(
      (p) => {
        const next = new URLSearchParams(p)
        if (value === 'evidence') next.delete('tab')
        else next.set('tab', value)
        return next
      },
      { replace: true, preventScrollReset: true },
    )
    if (anchored && anchor && scroller) {
      window.requestAnimationFrame(() => {
        const offset = anchor.getBoundingClientRect().top - scroller.getBoundingClientRect().top
        scroller.scrollTo({ top: scroller.scrollTop + offset, behavior: 'instant' })
      })
    }
  }

  const messages = detail.conversation.messages.length
  const tabs = [
    { value: 'evidence', label: 'Evidence', icon: <Search /> },
    { value: 'actions', label: 'Actions', icon: <ListChecks />, count: detail.proposals.length },
    { value: 'conversation', label: 'Conversation', icon: <MessagesSquare />, count: messages },
    { value: 'receipts', label: 'Receipts', icon: <FileCheck2 />, count: detail.receipts.length },
    { value: 'history', label: 'History', icon: <History />, count: detail.audit_events.length },
  ]
  return (
    <Tabs value={active} onValueChange={select} className="gap-4">
      {/* Stays in reach while reading a long tab. */}
      <div ref={anchorRef} aria-hidden className="-mb-4 h-0" />
      <div
        ref={stripRef}
        className="sticky top-0 z-20 -mx-1 overflow-x-auto bg-background/90 px-1 py-2 backdrop-blur [scrollbar-width:none] supports-[backdrop-filter]:bg-background/75 max-sm:[mask-image:linear-gradient(to_right,black_85%,transparent)]"
      >
        <TabsList className="h-10 w-max rounded-full bg-muted p-1">
          {tabs.map((t) => (
            <TabsTrigger key={t.value} value={t.value} className="h-8 rounded-full px-2.5 data-active:bg-card data-active:shadow-sm [&_svg]:hidden 2xl:[&_svg]:inline-block">
              {t.icon}
              {t.label}
              {t.count != null && t.count > 0 && <span className="rounded-full bg-foreground/10 px-1.5 text-[11px] font-semibold tabular-nums">{t.count}</span>}
            </TabsTrigger>
          ))}
        </TabsList>
      </div>
      <TabsContent value="evidence" className="min-h-[calc(100dvh-8rem)] animate-fade-in">
        <EvidenceTab detail={detail} />
      </TabsContent>
      <TabsContent value="actions" className="min-h-[calc(100dvh-8rem)] animate-fade-in">
        <ActionsTab detail={detail} />
      </TabsContent>
      <TabsContent value="conversation" className="min-h-[calc(100dvh-8rem)] animate-fade-in">
        <ConversationTab detail={detail} />
      </TabsContent>
      <TabsContent value="receipts" className="min-h-[calc(100dvh-8rem)] animate-fade-in">
        <ReceiptsTab receipts={detail.receipts} />
      </TabsContent>
      <TabsContent value="history" className="min-h-[calc(100dvh-8rem)] animate-fade-in">
        <HistoryTab detail={detail} />
      </TabsContent>
    </Tabs>
  )
}

// ---- Evidence ---------------------------------------------------------------------------------

function EvidenceTab({ detail }: { detail: AgentCaseDetail }) {
  const revisions = detail.investigations
  const [revision, setRevision] = useState<number | null>(null)
  const inv = revisions.find((r) => r.revision === revision) ?? revisions.at(-1) ?? detail.case.investigation ?? null
  if (!inv) return <Quiet>Resolve has not investigated this case yet, so there is no evidence to show.</Quiet>
  const latest = revisions.at(-1)?.revision

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
        <p className="text-muted-foreground">
          Records from <span className="font-medium text-foreground">{formatDateTime(inv.window_start)}</span> to{' '}
          <span className="font-medium text-foreground">{formatDateTime(inv.window_end)}</span>
        </p>
        {revisions.length > 1 && (
          <div role="radiogroup" aria-label="Investigation revision" className="flex gap-1 rounded-full bg-muted p-1 text-xs font-semibold">
            {revisions.map((r) => (
              <button
                key={r.revision}
                type="button"
                role="radio"
                aria-checked={r.revision === inv.revision}
                onClick={() => setRevision(r.revision)}
                className={cn('h-7 rounded-full px-3', r.revision === inv.revision ? 'bg-foreground text-background' : 'text-muted-foreground hover:text-foreground')}
              >
                Revision {r.revision}
                {r.revision === latest ? ' (latest)' : ''}
              </button>
            ))}
          </div>
        )}
      </div>

      {inv.findings.length > 0 && (
        <CardFrame icon={<ListChecks />} title="What Resolve found">
          <ul className="flex flex-col gap-3">
            {inv.findings.map((f) => (
              <li key={f.code} className="flex flex-col gap-0.5">
                <span className="font-semibold">{findingLabel(f.code)}</span>
                <span className="text-muted-foreground">{f.text}</span>
                {f.evidence_ids.length > 0 && <span className="text-xs text-muted-foreground">Based on {f.evidence_ids.length} {f.evidence_ids.length === 1 ? 'record' : 'records'}</span>}
              </li>
            ))}
          </ul>
        </CardFrame>
      )}

      {inv.calculations.map((calc) => (
        <CardFrame key={calc.code} icon={<Calculator />} title={calcLabel(calc.code)} tone={calc.delta ? 'danger' : calc.delta === 0 ? 'success' : undefined}>
          <CalcTable calc={calc} inv={inv} />
        </CardFrame>
      ))}

      <CardFrame icon={<Database />} title="Sources checked">
        <SourceList sources={inv.source_status} />
      </CardFrame>

      <CardFrame icon={<Database />} title={`Records (${inv.evidence.length})`}>
        <RecordList inv={inv} />
      </CardFrame>

      <AccountPanel detail={detail} />
    </div>
  )
}

/** Server arithmetic shown verbatim; nothing is added up here. */
function CalcTable({ calc, inv }: { calc: Calculation; inv: InvestigationResult }) {
  const fmt = (n: number | null | undefined) => (n == null ? 'Unavailable' : formatCalcValue(calc.unit, n))
  const when = new Map(inv.evidence.map((e) => [e.id, e.observed_at]))
  return (
    <table className="w-full text-sm">
      <caption className="sr-only">{calcLabel(calc.code)}</caption>
      <tbody className="[&_td]:py-1.5">
        <tr>
          <td className="text-muted-foreground">Opening</td>
          <td className="hidden sm:table-cell" />
          <td className="text-right font-mono whitespace-nowrap">{fmt(calc.opening)}</td>
        </tr>
        {calc.terms.map((t) => (
          <tr key={t.evidence_id} className="border-t border-foreground/5">
            <td>{termLabel(t.label)}</td>
            <td className="hidden text-xs text-muted-foreground sm:table-cell">{when.get(t.evidence_id) ? formatDateTime(when.get(t.evidence_id)!) : ''}</td>
            <td className="text-right font-mono whitespace-nowrap">{fmt(t.value)}</td>
          </tr>
        ))}
      </tbody>
      <tfoot className="border-t-2 border-foreground/10 font-semibold [&_td]:py-1.5">
        <tr>
          <td>Expected</td>
          <td className="hidden sm:table-cell" />
          <td className="text-right font-mono whitespace-nowrap">{fmt(calc.expected)}</td>
        </tr>
        <tr>
          <td>Recorded</td>
          <td className="hidden sm:table-cell" />
          <td className="text-right font-mono whitespace-nowrap">{fmt(calc.observed)}</td>
        </tr>
        <tr>
          <td>Difference</td>
          <td className="hidden sm:table-cell" />
          <td className={cn('text-right font-mono whitespace-nowrap', calc.delta ? 'text-destructive' : calc.delta === 0 ? 'text-success' : undefined)}>{fmt(calc.delta)}</td>
        </tr>
      </tfoot>
    </table>
  )
}

function SourceList({ sources }: { sources: SourceStatus[] }) {
  if (sources.length === 0) return <p className="text-muted-foreground">No source status was recorded.</p>
  return (
    <ul className="flex flex-col divide-y divide-foreground/5">
      {sources.map((s) => (
        <li key={`${s.source}-${s.source_version}`} className="flex flex-wrap items-center justify-between gap-2 py-2">
          <div className="flex flex-col">
            <span className="font-medium">{sourceLabel(s.source)}</span>
            <span className="text-xs text-muted-foreground">
              {s.complete_through ? `Complete through ${formatDateTime(s.complete_through)}` : 'No completeness time given'} · fetched {formatDateTime(s.fetched_at)}
            </span>
            {s.warnings.map((w) => (
              <span key={w} className="text-xs font-medium text-warning-foreground">
                ⚠ {humanize(w)}
              </span>
            ))}
          </div>
          <StatusBadge tone={s.complete ? 'success' : 'warning'}>{s.complete ? 'Complete' : 'Incomplete'}</StatusBadge>
        </li>
      ))}
    </ul>
  )
}

function formatValue(value: string | number | boolean | null | undefined, unit: string | null | undefined) {
  if (value == null) return '—'
  if (typeof value !== 'number') return String(value)
  if (unit === 'LKR_MINOR') return formatLkr(value)
  if (unit === 'BYTES') return formatGb(value)
  return value.toLocaleString('en-LK')
}

/** Every record behind the findings, with the raw source payload one click away (agent-only). */
function RecordList({ inv }: { inv: InvestigationResult }) {
  if (inv.evidence.length === 0) return <p className="text-muted-foreground">No records were saved with this investigation.</p>
  return (
    <ul className="flex flex-col divide-y divide-foreground/5">
      {inv.evidence.map((e) => {
        const kind = typeof e.source_payload?.kind === 'string' ? e.source_payload.kind : null
        return (
          <li key={e.id} className="py-2">
            <details className="group">
              <summary className="flex cursor-pointer list-none flex-wrap items-center justify-between gap-2 rounded outline-none focus-visible:ring-2 focus-visible:ring-ring">
                <span className="flex flex-col">
                  <span className="font-medium">
                    {sourceLabel(e.source)}
                    {kind ? ` · ${termLabel(kind)}` : ''}
                  </span>
                  <span className="text-xs text-muted-foreground">Observed {formatDateTime(e.observed_at)}</span>
                </span>
                <span className="flex items-center gap-3">
                  <span className="font-mono">{formatValue(e.value, e.unit)}</span>
                  <span className="text-xs text-muted-foreground group-open:hidden">Details</span>
                  <span className="hidden text-xs text-muted-foreground group-open:inline">Hide</span>
                </span>
              </summary>
              <dl className="mt-2 grid animate-expand grid-cols-[auto_1fr] gap-x-3 gap-y-1 rounded-lg bg-card p-3 text-xs">
                <dt className="text-muted-foreground">Record</dt>
                <dd className="font-mono break-all">{e.source_record_id}</dd>
                <dt className="text-muted-foreground">Version</dt>
                <dd className="font-mono break-all">{e.source_version}</dd>
                <dt className="text-muted-foreground">Fetched</dt>
                <dd>{formatDateTime(e.fetched_at)}</dd>
                <dt className="text-muted-foreground">Payload</dt>
                <dd>
                  <pre className="overflow-x-auto font-mono text-[11px] whitespace-pre-wrap">{JSON.stringify(e.source_payload, null, 2)}</pre>
                </dd>
              </dl>
            </details>
          </li>
        )
      })}
    </ul>
  )
}

function AccountPanel({ detail }: { detail: AgentCaseDetail }) {
  const a = detail.account
  return (
    <CardFrame icon={<UserRound />} title="Customer account">
      <div className="flex flex-col gap-3">
        <dl className="grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-4">
          <Field label="Customer" value={a.display_name} />
          <Field label="Line" value={a.line_alias} mono />
          <Field label="Region" value={humanize(a.region)} />
          <Field label="Status" value={humanize(a.status)} />
          {a.balances.map((b) => (
            <Field key={b.wallet} label={`${humanize(b.wallet)} balance`} value={formatLkr(b.amount_minor)} hint={b.as_of ? `as of ${formatDateTime(b.as_of)}` : undefined} mono />
          ))}
        </dl>
        {a.subscriptions.length > 0 && (
          <ul className="flex flex-col gap-1.5">
            {a.subscriptions.map((s) => (
              <li key={s.id} className="flex flex-wrap items-center justify-between gap-2 rounded-lg bg-card px-3 py-2">
                <span>
                  <span className="font-medium">{s.name}</span>
                  <span className="text-xs text-muted-foreground">
                    {' '}
                    · {s.kind === 'VAS' ? 'Value-added service' : 'Package'}
                    {s.remaining_bytes != null ? ` · ${formatGb(s.remaining_bytes)} left` : ''}
                    {s.expires_at ? ` · ends ${formatDateTime(s.expires_at)}` : ''}
                  </span>
                </span>
                <span className="flex gap-1.5">
                  <StatusBadge tone={s.status === 'ACTIVE' ? 'success' : 'neutral'}>{humanize(s.status)}</StatusBadge>
                  <StatusBadge tone={s.renewal ? 'info' : 'neutral'}>{s.renewal ? 'Renews' : 'No renewal'}</StatusBadge>
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </CardFrame>
  )
}

// ---- Actions and ticket -----------------------------------------------------------------------

function ActionsTab({ detail }: { detail: AgentCaseDetail }) {
  const now = useNow(15_000)
  const h = detail.handoff
  return (
    <div className="flex flex-col gap-4">
      <CardFrame icon={<Ticket />} title="Review ticket" tone={h ? deliveryTone[h.delivery_state] : undefined}>
        {h ? (
          <div className="flex flex-col gap-3">
            <dl className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-3">
              <Field label="Team" value={queueLabel(h.queue)} />
              <div className="flex flex-col gap-1">
                <dt className="text-xs text-muted-foreground">Delivery to the ticket system</dt>
                <dd>
                  <DeliveryBadge state={h.delivery_state} />
                </dd>
              </div>
              <div className="flex flex-col gap-1">
                <dt className="text-xs text-muted-foreground">Your review on the ticket</dt>
                <dd>
                  <StatusBadge tone={SYNC_TONE[h.review_sync_state] ?? 'neutral'}>{syncLabel(h.review_sync_state)}</StatusBadge>
                </dd>
              </div>
              <div className="col-span-2 flex flex-col sm:col-span-3">
                <dt className="text-xs text-muted-foreground">Ticket ID</dt>
                <dd className="font-mono text-sm break-all">{h.provider_ticket_id ?? 'Not assigned yet. The ticket system has not confirmed it.'}</dd>
              </div>
            </dl>
            <p className="text-xs text-muted-foreground">
              Your notes and status are copied to this ticket. The ticket's own status is set by the ticket team, not from here.
            </p>
          </div>
        ) : (
          <p className="text-muted-foreground">No review ticket. The customer has not confirmed a request for human review.</p>
        )}
      </CardFrame>

      {detail.proposals.length === 0 ? (
        <Quiet>Resolve has not offered the customer any action on this case.</Quiet>
      ) : (
        detail.proposals.map((p) => <ActionCard key={p.id} proposal={p} detail={detail} now={now} />)
      )}
    </div>
  )
}

function ActionCard({ proposal: p, detail, now }: { proposal: ProposalView; detail: AgentCaseDetail; now: number }) {
  const decision = detail.confirmations.find((c) => c.proposal_id === p.id)
  const op = detail.operations.find((o) => o.proposal_id === p.id)
  const expired = !decision && Date.parse(p.expires_at) < now
  const tone: Tone | undefined = op ? operationTone[op.status] : decision?.decision === 'DECLINE' || expired ? 'neutral' : 'warning'
  return (
    <CardFrame
      icon={<ListChecks />}
      title={actionLabel(p.action_type)}
      tone={tone}
      aside={op ? <OperationBadge status={op.status} /> : <StatusBadge tone="neutral">{decision ? (decision.decision === 'ACCEPT' ? 'Accepted' : 'Declined') : expired ? 'Expired' : 'Awaiting customer'}</StatusBadge>}
    >
      <ol className="flex flex-col gap-3 border-l-2 border-foreground/10 pl-4">
        <Step title="Offered to the customer" when={null}>
          <span className="font-medium">{p.target_label}</span> · {p.consequences}
          <span className="block text-xs text-muted-foreground">Offer valid until {formatDateTime(p.expires_at)}</span>
        </Step>
        {decision ? (
          <Step title={decision.decision === 'ACCEPT' ? 'Customer accepted' : 'Customer declined'} when={decision.created_at}>
            By {decision.channel === 'VOICE' ? 'voice' : decision.channel === 'AGENT' ? 'agent' : 'chat'}
          </Step>
        ) : (
          <Step title={expired ? 'Offer expired without an answer' : 'Waiting for the customer to answer'} when={null} />
        )}
        {op && (
          <Step title={`Action ${humanize(op.status).toLowerCase()}`} when={op.updated_at}>
            {op.outcome.message ?? op.next_step}
            {op.outcome.provider_ticket_id && <span className="block font-mono text-xs break-all text-muted-foreground">Ticket {op.outcome.provider_ticket_id}</span>}
          </Step>
        )}
      </ol>
    </CardFrame>
  )
}

function Step({ title, when, children }: { title: string; when: string | null; children?: React.ReactNode }) {
  return (
    <li className="relative">
      <span aria-hidden className="absolute top-1.5 -left-[1.36rem] size-2.5 rounded-full border-2 border-muted bg-foreground/40" />
      <p className="font-semibold">
        {title}
        {when && <span className="ml-2 text-xs font-normal text-muted-foreground">{formatDateTime(when)}</span>}
      </p>
      {children && <div className="text-muted-foreground">{children}</div>}
    </li>
  )
}

// ---- Conversation -----------------------------------------------------------------------------

function ConversationTab({ detail }: { detail: AgentCaseDetail }) {
  const conv = detail.conversation
  if (conv.messages.length === 0) {
    return <Quiet>No messages were stored for this conversation. Cases opened outside the chat have no transcript.</Quiet>
  }
  return (
    <div className="flex flex-col gap-3">
      <p className="text-xs text-muted-foreground">
        Language: {conv.language === 'si' ? 'Sinhala' : conv.language === 'ta' ? 'Tamil' : 'English'}
        {conv.cases.length > 1 && ` · this conversation has ${conv.cases.length} cases`}
      </p>
      <ol className="flex flex-col gap-2 rounded-2xl bg-muted/70 p-4">
        {conv.messages.map((m) => (
          <li key={m.id} className={cn('flex flex-col gap-0.5', m.speaker === 'USER' ? 'items-end' : 'items-start')}>
            <div
              className={cn(
                'max-w-[85%] rounded-2xl px-4 py-2.5 text-sm whitespace-pre-wrap',
                m.speaker === 'USER' ? 'rounded-br-md bg-primary text-primary-foreground' : 'rounded-bl-md bg-card',
              )}
            >
              <span className="sr-only">{m.speaker === 'USER' ? 'Customer: ' : 'Resolve: '}</span>
              {m.body}
            </div>
            <span className="px-1 text-[11px] text-muted-foreground">
              {m.speaker === 'USER' ? 'Customer' : 'Resolve'} · {formatDateTime(m.created_at)}
            </span>
          </li>
        ))}
      </ol>
    </div>
  )
}

// ---- Receipts ---------------------------------------------------------------------------------

function ReceiptsTab({ receipts }: { receipts: ReceiptView[] }) {
  if (receipts.length === 0) return <Quiet>No receipt yet. Resolve issues one when an action finishes.</Quiet>
  return (
    <div className="flex flex-col gap-4">
      {[...receipts].reverse().map((r) => (
        <CardFrame
          key={r.id}
          icon={<FileCheck2 />}
          title={`Receipt revision ${r.revision}`}
          aside={
            <button
              type="button"
              onClick={() => downloadJson(r, `resolve-receipt-${r.case_id.slice(0, 8)}-r${r.revision}.json`)}
              className="inline-flex items-center gap-1 text-xs font-semibold text-foreground hover:underline"
            >
              <Download className="size-3.5" aria-hidden /> JSON
            </button>
          }
        >
          <dl className="grid grid-cols-2 gap-x-4 gap-y-3">
            <Field label="Issued" value={formatDateTime(r.issued_at)} />
            <Field label="Actions" value={r.actions.map((a) => `${actionLabel(a.action_type)}: ${humanize(a.operation_status ?? (a.decision ?? 'offered')).toLowerCase()}`).join('; ') || 'None'} />
            <div className="col-span-2">
              <Field label="Next step" value={r.next_step} />
            </div>
            <div className="col-span-2 flex flex-col">
              <dt className="text-xs text-muted-foreground">SHA-256 digest (integrity check, not a signature)</dt>
              <dd className="font-mono text-xs break-all">
                {r.digest_sha256} <CopyId label="Digest" value={r.digest_sha256} />
              </dd>
            </div>
          </dl>
        </CardFrame>
      ))}
    </div>
  )
}

// ---- History ----------------------------------------------------------------------------------

function HistoryTab({ detail }: { detail: AgentCaseDetail }) {
  const events = [...detail.audit_events].reverse()
  if (events.length === 0) return <Quiet>No history recorded yet.</Quiet>
  return (
    <ol className="flex flex-col gap-0 rounded-2xl bg-muted/70 p-4">
      {events.map((e, i) => (
        <li key={e.id} className="relative flex gap-3 pb-4 last:pb-0">
          {i < events.length - 1 && <span aria-hidden className="absolute top-3 left-[5px] h-full w-0.5 bg-foreground/10" />}
          <span aria-hidden className={cn('relative mt-1.5 size-3 shrink-0 rounded-full', e.event_type.startsWith('REVIEW') ? 'bg-primary' : 'bg-foreground/30')} />
          <div className="flex flex-col">
            <span className="text-sm font-medium">{auditLabel(e.event_type, e.details)}</span>
            <span className="text-xs text-muted-foreground">
              {formatDateTime(e.created_at)} · {e.actor_id ?? 'Resolve (automatic)'}
            </span>
          </div>
        </li>
      ))}
    </ol>
  )
}

// ---- Shared bits ------------------------------------------------------------------------------

function Field({ label, value, mono, hint }: { label: string; value: string; mono?: boolean; hint?: string }) {
  return (
    <div className="flex min-w-0 flex-col">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className={cn('break-words', mono && 'font-mono')}>{value}</dd>
      {hint && <dd className="text-[11px] text-muted-foreground">{hint}</dd>}
    </div>
  )
}

function Quiet({ children }: { children: React.ReactNode }) {
  return <p className="rounded-2xl bg-muted/70 px-4 py-6 text-center text-sm text-muted-foreground">{children}</p>
}
