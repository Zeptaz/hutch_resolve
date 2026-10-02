import type { ReactNode } from 'react'
import { useState } from 'react'
import { Calculator, CheckCircle2, Clock, Download, ExternalLink, FileText, ListOrdered, Search, Ticket, UserRound } from 'lucide-react'
import { customerApi } from '@/api/endpoints'
import { describeError } from '@/api/errors'
import type { Card, CardOf, Citation } from '@/api/types'
import { CalculationTable } from '@/components/evidence/CalculationTable'
import { DeliveryBadge, StatusBadge } from '@/components/StatusBadge'
import { deliveryTone, type Tone } from '@/components/tones'
import { Button } from '@/components/ui/button'
import { hasMessage, useI18n, type Translate } from '@/i18n/context'
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

// Status cards get an edge in their state colour; neutral states (declined, expired) a quiet grey one.
const TONE_EDGE: Record<Tone, string> = {
  neutral: 'border-[1.5px] border-foreground/15',
  info: 'border-[1.5px] border-info/45',
  success: 'border-[1.5px] border-success/45',
  warning: 'border-[1.5px] border-warning/70',
  danger: 'border-[1.5px] border-destructive/45',
}

/** Soft panel tile. Pass `tone` only when the card shows a status, so its edge matches the badge. */
export function CardFrame({
  icon,
  title,
  aside,
  tone,
  children,
  className,
}: {
  icon: ReactNode
  title: string
  aside?: ReactNode
  tone?: Tone
  children: ReactNode
  className?: string
}) {
  return (
    <section
      className={cn('rounded-2xl bg-muted/70 text-card-foreground', tone ? TONE_EDGE[tone] : 'border-0', className)}
      aria-label={title}
    >
      <header className="flex items-center justify-between gap-2 px-4 pt-3 pb-1">
        <h3 className="flex items-center gap-2 text-sm font-semibold">
          <span aria-hidden className="text-muted-foreground [&_svg]:size-4">
            {icon}
          </span>
          {title}
        </h3>
        {aside}
      </header>
      <div className="px-4 pt-2 pb-3.5 text-sm">{children}</div>
    </section>
  )
}

function AccountCard({ data }: { data: CardOf<'account'>['data'] }) {
  const { t } = useI18n()
  return (
    <CardFrame icon={<UserRound />} title={t('card.yourLine')} aside={<span className="font-mono text-xs text-muted-foreground">{data.line_alias}</span>}>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-2">
        {data.balances.map((b) => (
          <div key={b.wallet}>
            <dt className="text-xs text-muted-foreground">
              {b.wallet === 'MAIN' ? t('card.mainBalance') : `${humanize(b.wallet)} ${t('card.balance')}`}
            </dt>
            <dd className="font-mono font-medium">{formatLkr(b.amount_minor)}</dd>
            <dd className="text-[11px] text-muted-foreground">as of {formatDateTime(b.as_of)}</dd>
          </div>
        ))}
        <div>
          <dt className="text-xs text-muted-foreground">{t('card.status')}</dt>
          <dd>{statusLabel(t, data.status)}</dd>
        </div>
      </dl>
      {data.subscriptions.length > 0 && (
        <ul className="mt-3 flex flex-col gap-1.5 border-t pt-3">
          {data.subscriptions.map((s) => (
            <li key={s.id} className="flex items-center justify-between gap-2">
              <span>
                {s.name} <span className="text-xs text-muted-foreground">({s.kind === 'VAS' ? t('card.vas') : t('card.package')})</span>
              </span>
              <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
                {s.remaining_bytes != null && <span className="font-mono">{t('card.left', { amount: formatGb(s.remaining_bytes) })}</span>}
                {s.renewal && <StatusBadge tone="info">{t('card.renews')}</StatusBadge>}
                <StatusBadge tone="neutral">{statusLabel(t, s.status)}</StatusBadge>
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
  const { t } = useI18n()
  if (sources.length === 0) return null
  const incomplete = sources.filter((s) => !s.complete)
  return (
    <p className={cn('mt-3 text-[11px]', incomplete.length ? 'text-destructive' : 'text-muted-foreground')}>
      {incomplete.length
        ? t('card.sourcesIncomplete', { sources: incomplete.map((s) => s.source).join(', ') })
        : t('card.sourcesChecked', { sources: sources.map((s) => `${s.source} (${formatDateTime(s.fetched_at)})`).join(', ') })}
    </p>
  )
}

function TimelineCard({ data }: { data: CardOf<'timeline'>['data'] }) {
  const { t } = useI18n()
  return (
    <CardFrame icon={<ListOrdered />} title={t('card.whatHappened')}>
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
                    {' · '}
                    {t('card.recordedAt', { time: formatDateTime(item.recorded_at) })}
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
  const { t } = useI18n()
  const matched = data.delta === 0
  const tone: Tone = data.delta == null ? 'warning' : matched ? 'success' : 'danger'
  return (
    <CardFrame
      icon={<Calculator />}
      tone={tone}
      title={data.unit === 'BYTES' ? t('card.dataCheck') : t('card.balanceCheck')}
      aside={
        data.delta == null ? (
          <StatusBadge tone="warning">{t('card.incomplete')}</StatusBadge>
        ) : matched ? (
          <StatusBadge tone="success">{t('card.addsUp')}</StatusBadge>
        ) : (
          <StatusBadge tone="danger">{t('card.doesNotAddUp')}</StatusBadge>
        )
      }
    >
      <CalculationTable calc={data} title={false} />
    </CardFrame>
  )
}

function FindingCard({ data }: { data: CardOf<'finding'>['data'] }) {
  const { t } = useI18n()
  return (
    <CardFrame icon={<Search />} title={t('card.found')}>
      <p>{data.text}</p>
      <p className="mt-2 text-xs text-muted-foreground">
        {data.evidence_ids.length === 0
          ? t('card.noRecord')
          : data.evidence_ids.length === 1
            ? t('card.basedOne')
            : t('card.basedMany', { count: data.evidence_ids.length })}
      </p>
    </CardFrame>
  )
}

function TicketCard({ data }: { data: CardOf<'ticket'>['data'] }) {
  const { t } = useI18n()
  return (
    <CardFrame
      icon={<Ticket />}
      title={t('card.reviewRequest')}
      tone={data.delivery_state ? deliveryTone[data.delivery_state] : undefined}
      aside={<DeliveryBadge state={data.delivery_state} />}
    >
      <dl className="grid grid-cols-2 gap-x-4 gap-y-2">
        <div>
          <dt className="text-xs text-muted-foreground">{t('card.team')}</dt>
          <dd>{humanize(data.queue)}</dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">{t('card.ticketNumber')}</dt>
          <dd className="font-mono">{data.provider_ticket_id ?? t('card.notAssigned')}</dd>
        </div>
        <div className="col-span-2">
          <dt className="text-xs text-muted-foreground">{t('card.reference')}</dt>
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
  const { t } = useI18n()
  return (
    <CardFrame icon={<FileText />} title={t('card.receipt')} aside={<span className="text-xs text-muted-foreground">{t('card.revision', { n: data.revision })}</span>}>
      <p className="mb-3 text-muted-foreground">{t('card.receiptBody')}</p>
      <ReceiptDownloadButton caseId={data.case_id} />
    </CardFrame>
  )
}

export function ReceiptDownloadButton({ caseId, size = 'sm' }: { caseId: string; size?: 'sm' | 'default' }) {
  const { t } = useI18n()
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
        <Download aria-hidden /> {busy ? t('common.preparing') : t('receipt.download')}
      </Button>
      {error != null && (
        <p role="alert" className="text-xs text-destructive">
          {describeError(error, t)}
        </p>
      )}
    </div>
  )
}

export function CitationList({ citations }: { citations: Citation[] }) {
  const { t } = useI18n()
  if (citations.length === 0) return null
  return (
    <ul aria-label={t('card.sources')} className="flex flex-wrap gap-1.5">
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
            <span className="sr-only">{t('card.newTab')}</span>
          </a>
        </li>
      ))}
    </ul>
  )
}

function statusLabel(t: Translate, status: string) {
  const key = `status.${status}`
  return hasMessage(key) ? t(key) : humanize(status)
}
