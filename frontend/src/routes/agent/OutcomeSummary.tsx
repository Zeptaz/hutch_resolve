import type { InvestigationOutcome } from '@/api/types'
import type { Tone } from '@/components/tones'
import { formatLkr, formatTime, humanize } from '@/lib/format'
import { cn } from '@/lib/utils'

type Classification = InvestigationOutcome['classification']
type Line = InvestigationOutcome['breakdown'][number]
type Item = Line['items'][number]

export const classificationTone: Record<Classification, Tone> = {
  EXPLAINED: 'success',
  PARTIALLY_EXPLAINED: 'warning',
  UNEXPLAINED: 'danger',
  INSUFFICIENT_EVIDENCE: 'warning',
}

export const CLASSIFICATION_LABEL: Record<Classification, string> = {
  EXPLAINED: 'Explained',
  PARTIALLY_EXPLAINED: 'Partly explained',
  UNEXPLAINED: 'Unexplained',
  INSUFFICIENT_EVIDENCE: 'Not enough evidence',
}

export const CLASSIFICATION_HEADLINE: Record<Classification, string> = {
  EXPLAINED: 'Every deduction is backed by a record, and the balance matches. Nothing needs fixing unless the customer disputes a record itself.',
  PARTIALLY_EXPLAINED: 'Records explain part of the money. The unexplained amount below has no record behind it.',
  UNEXPLAINED: 'Resolve found a specific fault in the records, such as a duplicate charge or a top-up that was paid but not credited.',
  INSUFFICIENT_EVIDENCE: 'The balance history is incomplete, so Resolve could not rebuild what happened. Nothing was estimated.',
}

const CATEGORY_LABEL: Record<string, string> = {
  RECHARGES: 'Recharges', PROMOTIONS: 'Promotions', REFUNDS: 'Refunds', PACKAGES: 'Packages', VAS: 'Value-added services',
  CALLS: 'Calls', SMS: 'SMS', DATA: 'Data', TRANSFERS: 'Balance transfers', FEES: 'Fees', USAGE: 'Usage (not itemised)', OTHER: 'Other',
}

const ANOMALY_LABEL: Record<string, string> = {
  BALANCE_GAP: 'Balance moved with no record',
  DUPLICATE_CHARGE: 'Duplicate charge',
  RECHARGE_NOT_CREDITED: 'Top-up paid but not credited',
  VAS_CONSENT_UNVERIFIED: 'No subscription record for a VAS charge',
  REVERSAL_MISMATCH: 'Refund does not match its charge',
}

const amount = (minor: number | null | undefined) => (minor == null ? 'Unavailable' : formatLkr(minor))

function itemText(item: Item) {
  const parts: string[] = []
  if (item.event_kind === 'VOICE_CALL') parts.push(`Call to ${item.counterparty ?? 'unknown'}${item.duration_seconds ? `, ${Math.max(1, Math.round(item.duration_seconds / 60))} min` : ''}`)
  else if (item.event_kind === 'SMS') parts.push(`SMS${item.counterparty ? ` to ${item.counterparty}` : ''}`)
  else if (item.event_kind === 'DATA_SESSION') parts.push('Data session')
  else parts.push(item.product_name ?? humanize(item.kind))
  if (item.reverses_reference) parts.push(`refund of ${item.reverses_reference}`)
  if (item.reversed) parts.push('refunded later')
  if (item.itemisation === 'INCOMPLETE') parts.push('itemisation incomplete')
  return parts.join(' · ')
}

type Group = { key: string; items: Item[] }

/** Runs of the same thing (e.g. 10 SMS to one number) read as one row; Resolve's items are kept, only listed together. */
function grouped(items: Item[]): Group[] {
  const groups: Group[] = []
  for (const item of items) {
    const key = [item.event_kind, item.counterparty, item.product_name, item.kind, item.reverses_reference, item.reversed].join('|')
    const last = groups.at(-1)
    if (last && last.key === key && item.event_kind) last.items.push(item)
    else groups.push({ key, items: [item] })
  }
  return groups
}

function groupText(group: Group) {
  const [first] = group.items
  if (group.items.length === 1) return itemText(first)
  const noun = first.event_kind === 'SMS' ? 'SMS' : first.event_kind === 'VOICE_CALL' ? 'calls' : 'items'
  return `${group.items.length} ${noun}${first.counterparty ? ` to ${first.counterparty}` : ''}`
}

function groupTime(group: Group) {
  const times = group.items.map((i) => i.occurred_at).filter((t): t is string => !!t)
  if (!times.length) return null
  const first = formatTime(times[0])
  const last = formatTime(times[times.length - 1])
  return first === last ? first : `${first}–${last}`
}

/** Resolve's reconstruction as it computed it: claim vs records, anomalies, then every line. Totals come from Resolve; only a run of identical items (e.g. 10 SMS to one number) is added up for its display row. */
export function OutcomeSummary({ outcome }: { outcome: InvestigationOutcome }) {
  const unexplained = outcome.unexplained_minor ?? 0
  return (
    <div className="flex flex-col gap-3">
      <dl className="grid gap-2 rounded-xl bg-card p-3 sm:grid-cols-3">
        <Figure label="Customer says" value={outcome.claimed_minor == null ? 'No amount given' : amount(outcome.claimed_minor)} />
        <Figure label="Explained by records" value={amount(outcome.explained_minor)} className={outcome.explained_minor ? 'text-success' : undefined} />
        <Figure label="Unexplained" value={amount(outcome.unexplained_minor)} className={unexplained ? 'text-destructive' : 'text-success'} />
      </dl>
      {outcome.anomalies.length > 0 && (
        <ul className="flex flex-col gap-1.5">
          {outcome.anomalies.map((a, i) => (
            <li key={`${a.code}-${i}`} className="flex items-start gap-2">
              <span aria-hidden className="mt-1.5 size-2 shrink-0 rounded-full bg-destructive" />
              <span>
                {ANOMALY_LABEL[a.code] ?? humanize(a.code)}
                {a.amount_minor != null && <span className="font-mono"> · {formatLkr(a.amount_minor)}</span>}
                {a.product_name && <> · {a.product_name}</>}
                {a.reference && <span className="font-mono text-muted-foreground"> · {a.reference}</span>}
                {a.occurred_at && <span className="text-muted-foreground"> · {formatTime(a.occurred_at)}</span>}
              </span>
            </li>
          ))}
        </ul>
      )}
      {outcome.breakdown.length > 0 && (
        <div className="overflow-hidden rounded-xl border">
          <table className="w-full text-sm">
            <caption className="sr-only">Reconstructed balance</caption>
            <tbody>
              <Row label="Opening balance" value={amount(outcome.opening_minor)} muted />
              {outcome.breakdown.map((line) => (
                <LineRows key={line.category} line={line} />
              ))}
              <Row label="Expected from records" value={amount(outcome.expected_minor)} strong />
              <Row label="Recorded balance" value={amount(outcome.observed_minor)} strong />
            </tbody>
          </table>
        </div>
      )}
      {outcome.history.length > 0 && (
        <p className="text-sm text-muted-foreground">
          Earlier tickets:{' '}
          {outcome.history.map((h) => `${h.case_ref} (${humanize(h.status)}${h.resolution ? `: ${h.resolution}` : ''})`).join('; ')}
        </p>
      )}
    </div>
  )
}

function LineRows({ line }: { line: Line }) {
  const sign = line.direction === 'CREDIT' ? '+' : '−'
  return (
    <>
      <tr className="border-t">
        <th scope="row" className="px-3 py-1.5 text-left font-medium">
          {CATEGORY_LABEL[line.category] ?? humanize(line.category)}
          <span className="font-normal text-muted-foreground"> · {line.count}</span>
        </th>
        <td className={cn('px-3 py-1.5 text-right font-mono', line.direction === 'CREDIT' && 'text-success')}>
          {sign}
          {formatLkr(line.amount_minor)}
        </td>
      </tr>
      {line.items.length > 1 || line.items[0]?.event_kind || line.items[0]?.product_name
        ? grouped(line.items).map((group, i) => (
            <tr key={`${group.key}-${i}`} className="text-xs text-muted-foreground">
              <td className="py-0.5 pr-3 pl-6">
                {groupText(group)}
                {groupTime(group) && <> · {groupTime(group)}</>}
              </td>
              <td className="px-3 py-0.5 text-right font-mono">
                {formatLkr(group.items.reduce((total, item) => total + Math.abs(item.amount_minor), 0))}
              </td>
            </tr>
          ))
        : null}
    </>
  )
}

function Row({ label, value, muted, strong }: { label: string; value: string; muted?: boolean; strong?: boolean }) {
  return (
    <tr className={cn('border-t', strong && 'bg-muted/40 font-semibold', muted && 'text-muted-foreground')}>
      <th scope="row" className="px-3 py-1.5 text-left font-[inherit]">
        {label}
      </th>
      <td className="px-3 py-1.5 text-right font-mono">{value}</td>
    </tr>
  )
}

function Figure({ label, value, className }: { label: string; value: string; className?: string }) {
  return (
    <div className="flex min-w-0 items-baseline justify-between gap-3 sm:flex-col sm:justify-start sm:gap-0">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className={cn('font-mono text-base font-semibold whitespace-nowrap sm:text-lg', className)}>{value}</dd>
    </div>
  )
}
