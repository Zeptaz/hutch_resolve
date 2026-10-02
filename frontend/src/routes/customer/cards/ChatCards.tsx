import type { ReactNode } from 'react'
import { useState } from 'react'
import { Calculator, CheckCircle2, Clock, Download, ExternalLink, FileText, ListOrdered, Search, Ticket, UserRound } from 'lucide-react'
import { customerApi } from '@/api/endpoints'
import { describeError } from '@/api/errors'
import type { Card, CardOf, Citation } from '@/api/types'
import { CalculationTable } from '@/components/evidence/CalculationTable'
import { DeliveryBadge, StatusBadge } from '@/components/StatusBadge'
import { Button } from '@/components/ui/button'
import { formatDateTime, formatGb, formatLkr, humanize } from '@/lib/format'
import { cn } from '@/lib/utils'
import { downloadJson } from './download'

/** Card renderers for TurnResult.cards. Fixed variants only — model output is never rendered as HTML. */
export function ChatCard({ card, renderConfirmation }: { card: Card; renderConfirmation: (c: CardOf<'confirmation'>) => ReactNode }) {
  switch (card.type) {
    case 'account':
      return <AccountCard data={card.data} />
    case 'timeline':
      return <TimelineCard data={card.data} />
    case 'calculation':
      return <CalculationCard data={card.data} />
    case 'finding':
      return <FindingCard data={card.data} />
    case 'confirmation':
      return renderConfirmation(card)
    case 'ticket':
      return <TicketCard data={card.data} />
    case 'receipt':
      return <ReceiptCard data={card.data} />
    default:
      return null
  }
}

export function CardFrame({
  icon,
  title,
  aside,
  children,
  className,
}: {
  icon: ReactNode
  title: string
  aside?: ReactNode
  children: ReactNode
  className?: string
}) {
  return (
    <section className={cn('rounded-xl border bg-card text-card-foreground shadow-xs', className)} aria-label={title}>
      <header className="flex items-center justify-between gap-2 border-b px-4 py-2.5">
        <h3 className="flex items-center gap-2 text-sm font-semibold">
          <span aria-hidden className="text-muted-foreground [&_svg]:size-4">
            {icon}
          </span>
          {title}
        </h3>
        {aside}
      </header>
      <div className="px-4 py-3 text-sm">{children}</div>
    </section>
  )
}

function AccountCard({ data }: { data: CardOf<'account'>['data'] }) {
  return (
    <CardFrame icon={<UserRound />} title="Your line" aside={<span className="font-mono text-xs text-muted-foreground">{data.line_alias}</span>}>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-2">
        {data.balances.map((b) => (
          <div key={b.wallet}>
            <dt className="text-xs text-muted-foreground">{humanize(b.wallet)} balance</dt>
            <dd className="font-mono font-medium">{formatLkr(b.amount_minor)}</dd>
            <dd className="text-[11px] text-muted-foreground">as of {formatDateTime(b.as_of)}</dd>
          </div>
        ))}
        <div>
          <dt className="text-xs text-muted-foreground">Status</dt>
          <dd>{humanize(data.status)}</dd>
        </div>
      </dl>
      {data.subscriptions.length > 0 && (
        <ul className="mt-3 flex flex-col gap-1.5 border-t pt-3">
          {data.subscriptions.map((s) => (
            <li key={s.id} className="flex items-center justify-between gap-2">
              <span>
                {s.name} <span className="text-xs text-muted-foreground">({s.kind === 'VAS' ? 'value-added service' : 'package'})</span>
              </span>
              <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
                {s.remaining_bytes != null && <span className="font-mono">{formatGb(s.remaining_bytes)} left</span>}
                {s.renewal && <StatusBadge tone="info">Renews</StatusBadge>}
                <StatusBadge tone="neutral">{humanize(s.status)}</StatusBadge>
              </span>
            </li>
          ))}
        </ul>
      )}
      <SourceNote sources={data.source_status} />
    </CardFrame>
  )
}

function SourceNote({ sources }: { sources: CardOf<'account'>['data']['source_status'] }) {
  if (sources.length === 0) return null
  const incomplete = sources.filter((s) => !s.complete)
  return (
    <p className={cn('mt-3 text-[11px]', incomplete.length ? 'text-destructive' : 'text-muted-foreground')}>
      {incomplete.length
        ? `Some records may be missing: ${incomplete.map((s) => s.source).join(', ')} was not fully read.`
        : `Checked ${sources.map((s) => `${s.source} (${formatDateTime(s.fetched_at)})`).join(', ')}.`}
    </p>
  )
}

function TimelineCard({ data }: { data: CardOf<'timeline'>['data'] }) {
  return (
    <CardFrame icon={<ListOrdered />} title="What happened">
      <ol className="relative flex flex-col gap-3 border-l pl-4">
        {data.items.map((item) => {
          const recordedDiffers = item.recorded_at !== item.occurred_at
          return (
            <li key={item.evidence_id} className="relative">
              <span aria-hidden className="absolute top-1.5 -left-[21px] size-2.5 rounded-full border-2 border-background bg-primary" />
              <div className="flex items-baseline justify-between gap-3">
                <span>{item.label}</span>
                {item.amount_minor != null && (
                  <span className={cn('font-mono', item.amount_minor < 0 ? 'text-foreground' : 'text-success')}>
                    {item.amount_minor > 0 ? '+' : ''}
                    {formatLkr(item.amount_minor)}
                  </span>
                )}
                {item.bytes != null && <span className="font-mono">{formatGb(item.bytes)}</span>}
              </div>
              <p className="text-xs text-muted-foreground">
                <time dateTime={item.occurred_at}>{formatDateTime(item.occurred_at)}</time>
                {recordedDiffers && (
                  <>
                    {' · recorded '}
                    <time dateTime={item.recorded_at}>{formatDateTime(item.recorded_at)}</time>
                  </>
                )}
              </p>
            </li>
          )
        })}
      </ol>
    </CardFrame>
  )
}

function CalculationCard({ data }: { data: CardOf<'calculation'>['data'] }) {
  const matched = data.delta === 0
  return (
    <CardFrame
      icon={<Calculator />}
      title={data.unit === 'BYTES' ? 'Data usage check' : 'Balance check'}
      aside={
        data.delta == null ? (
          <StatusBadge tone="warning">Incomplete</StatusBadge>
        ) : matched ? (
          <StatusBadge tone="success">Adds up</StatusBadge>
        ) : (
          <StatusBadge tone="danger">Doesn’t add up</StatusBadge>
        )
      }
    >
      <CalculationTable calc={data} title={false} />
    </CardFrame>
  )
}

function FindingCard({ data }: { data: CardOf<'finding'>['data'] }) {
  return (
    <CardFrame icon={<Search />} title="What we found">
      <p>{data.text}</p>
      <p className="mt-2 text-xs text-muted-foreground">
        {data.evidence_ids.length === 0
          ? 'No matching record was found in our systems.'
          : `Based on ${data.evidence_ids.length} record${data.evidence_ids.length === 1 ? '' : 's'} from our systems.`}
      </p>
    </CardFrame>
  )
}

function TicketCard({ data }: { data: CardOf<'ticket'>['data'] }) {
  return (
    <CardFrame icon={<Ticket />} title="Review request" aside={<DeliveryBadge state={data.delivery_state} />}>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-2">
        <div>
          <dt className="text-xs text-muted-foreground">Team</dt>
          <dd>{humanize(data.queue)}</dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">Ticket number</dt>
          <dd className="font-mono">{data.provider_ticket_id ?? 'Not assigned yet'}</dd>
        </div>
        <div className="col-span-2">
          <dt className="text-xs text-muted-foreground">Reference</dt>
          <dd className="font-mono text-xs break-all">{data.reference}</dd>
        </div>
      </dl>
      <p className="mt-3 flex items-start gap-1.5 text-muted-foreground">
        <Clock aria-hidden className="mt-0.5 size-3.5 shrink-0" /> {data.next_step}
      </p>
    </CardFrame>
  )
}

function ReceiptCard({ data }: { data: CardOf<'receipt'>['data'] }) {
  return (
    <CardFrame icon={<FileText />} title="Receipt" aside={<span className="text-xs text-muted-foreground">Revision {data.revision}</span>}>
      <p className="mb-3 text-muted-foreground">A record of what we checked and any actions taken on this case.</p>
      <ReceiptDownloadButton caseId={data.case_id} />
    </CardFrame>
  )
}

export function ReceiptDownloadButton({ caseId, size = 'sm' }: { caseId: string; size?: 'sm' | 'default' }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const download = async () => {
    setBusy(true)
    setError(null)
    try {
      const receipt = await customerApi.getReceipt(caseId)
      downloadJson(receipt, `hutch-resolve-receipt-${receipt.case_id.slice(-8)}-r${receipt.revision}.json`)
    } catch (e) {
      setError(e)
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="flex flex-col gap-1">
      <Button variant="outline" size={size} onClick={() => void download()} disabled={busy} className="w-fit">
        <Download aria-hidden /> {busy ? 'Preparing…' : 'Download receipt (JSON)'}
      </Button>
      {error != null && (
        <p role="alert" className="text-xs text-destructive">
          {describeError(error)}
        </p>
      )}
    </div>
  )
}

export function CitationList({ citations }: { citations: Citation[] }) {
  if (citations.length === 0) return null
  return (
    <ul aria-label="Sources" className="flex flex-wrap gap-1.5">
      {citations.map((c) => (
        <li key={`${c.article_id}-${c.version}`}>
          <a
            href={c.url}
            target="_blank"
            rel="noreferrer noopener"
            className="inline-flex items-center gap-1 rounded-md border bg-background px-2 py-1 text-xs hover:bg-muted"
          >
            {c.scope === 'PUBLIC' ? <CheckCircle2 aria-hidden className="size-3 text-success" /> : null}
            {c.title}
            <ExternalLink aria-hidden className="size-3 text-muted-foreground" />
            <span className="sr-only">(opens in a new tab)</span>
          </a>
        </li>
      ))}
    </ul>
  )
}
