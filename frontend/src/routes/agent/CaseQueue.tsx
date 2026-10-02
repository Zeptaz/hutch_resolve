import { useCallback, useEffect, useRef, useState } from 'react'
import { RefreshCw, Search, X } from 'lucide-react'
import { NavLink, useNavigate, useParams } from 'react-router'
import { agentApi } from '@/api/endpoints'
import type { CaseQueueRow, ComplaintType, DeliveryState, EvidenceState, QueueFilters, ReviewStatus } from '@/api/types'
import { EmptyState, ErrorState, LoadingState } from '@/components/states'
import { DeliveryBadge, EvidenceBadge, ReviewBadge } from '@/components/StatusBadge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { formatDateTime, formatTime, humanize } from '@/lib/format'
import { COMPLAINT_LABEL } from '@/lib/labels'
import { cn } from '@/lib/utils'
import { useQueueSignal } from './queueSignal'
import { relativeTime } from './time'

const POLL_MS = 5000
const PAGE = 25
const ALL = 'ALL'

const REVIEW_TABS: { value: ReviewStatus | undefined; label: string }[] = [
  { value: undefined, label: 'All' },
  { value: 'NEW', label: 'New' },
  { value: 'IN_REVIEW', label: 'In review' },
  { value: 'CLOSED', label: 'Closed' },
]
const COMPLAINT: ComplaintType[] = ['BALANCE_RECHARGE', 'DATA_DEPLETION', 'CONNECTIVITY', 'VAS_DISPUTE']
const EVIDENCE: EvidenceState[] = ['SUFFICIENT', 'PARTIAL', 'CONFLICTING']
const DELIVERY: DeliveryState[] = ['PENDING', 'DELIVERED', 'FAILED', 'REVIEW_REQUIRED']

/** Why a row deserves a second look. Strong colour is kept for these exceptions only. */
function attention(row: CaseQueueRow): { tone: 'danger' | 'warning'; reason: string } | null {
  if (row.review_status === 'CLOSED') return null
  if (row.evidence_state === 'CONFLICTING') return { tone: 'danger', reason: 'Evidence conflicts' }
  if (row.delivery_state === 'FAILED' || row.delivery_state === 'REVIEW_REQUIRED') return { tone: 'danger', reason: 'Ticket not delivered' }
  if (row.evidence_state === 'PARTIAL') return { tone: 'warning', reason: 'Evidence incomplete' }
  if (row.delivery_state === 'PENDING') return { tone: 'warning', reason: 'Ticket delivery pending' }
  return null
}

/** Review queue. Filtering, search and order are server-side; this only renders what the API returns. */
export function CaseQueue() {
  const { caseId } = useParams()
  const navigate = useNavigate()
  const { tick } = useQueueSignal()
  const [filters, setFiltersState] = useState<QueueFilters>({})
  const [searchDraft, setSearchDraft] = useState('')
  const [rows, setRows] = useState<CaseQueueRow[] | null>(null)
  const [cursor, setCursor] = useState<string | null>(null)
  const [pages, setPages] = useState(1)
  const [loadedAt, setLoadedAt] = useState<string | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState<'refresh' | 'more' | null>(null)
  const searchRef = useRef<HTMLInputElement>(null)
  // New filters start again from the first page.
  const setFilters = (next: QueueFilters | ((f: QueueFilters) => QueueFilters)) => {
    setFiltersState(next)
    setRows(null)
    setPages(1)
    setCursor(null)
  }
  const listRef = useRef<HTMLUListElement>(null)

  // Reload the first page. Older pages the agent already opened stay below it, refreshed by ID.
  const load = useCallback(
    async (signal?: AbortSignal) => {
      try {
        const res = await agentApi.listCases({ ...filters, limit: PAGE }, signal)
        setRows((prev) => {
          if (!prev || pages === 1) return res.items
          const fresh = new Map(res.items.map((r) => [r.case_id, r]))
          return [...res.items, ...prev.filter((r) => !fresh.has(r.case_id))]
        })
        if (pages === 1) setCursor(res.next_cursor)
        setLoadedAt(new Date().toISOString())
        setError(null)
      } catch (e) {
        if ((e as Error).name !== 'AbortError') setError(e)
      }
    },
    [filters, pages],
  )

  const refresh = async () => {
    setBusy('refresh')
    await load()
    setBusy(null)
  }

  const loadMore = async () => {
    if (!cursor) return
    setBusy('more')
    try {
      const res = await agentApi.listCases({ ...filters, limit: PAGE, cursor })
      setRows((prev) => [...(prev ?? []), ...res.items.filter((r) => !prev?.some((p) => p.case_id === r.case_id))])
      setCursor(res.next_cursor)
      setPages((n) => n + 1)
    } catch (e) {
      setError(e)
    } finally {
      setBusy(null)
    }
  }

  // Load now, after a review change elsewhere, and every 5s while the tab is visible.
  useEffect(() => {
    const ctrl = new AbortController()
    void load(ctrl.signal)
    const tickFn = () => {
      if (document.visibilityState === 'visible') void load(ctrl.signal)
    }
    const id = window.setInterval(tickFn, POLL_MS)
    document.addEventListener('visibilitychange', tickFn)
    return () => {
      ctrl.abort()
      window.clearInterval(id)
      document.removeEventListener('visibilitychange', tickFn)
    }
  }, [load, tick])

  // "/" jumps to search; j / k step through cases from anywhere outside a text field.
  const move = useCallback(
    (step: 1 | -1) => {
      if (!rows?.length) return
      const i = rows.findIndex((r) => r.case_id === caseId)
      const next = rows[Math.min(rows.length - 1, Math.max(0, i === -1 ? 0 : i + step))]
      navigate(`/agent/cases/${next.case_id}`)
      window.requestAnimationFrame(() => listRef.current?.querySelector<HTMLElement>(`[data-case="${next.case_id}"]`)?.focus())
    },
    [rows, caseId, navigate],
  )
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement
      if (el.closest('input, textarea, select, [contenteditable], [role="dialog"], [role="listbox"]') || e.metaKey || e.ctrlKey || e.altKey) return
      // Only from the queue (or nothing focused): a stray letter typed at the case panel must never switch cases.
      const fromQueue = el === document.body || el.closest('[aria-label="Review queue"]') != null
      if (!fromQueue) return
      if (e.key === '/') {
        e.preventDefault()
        searchRef.current?.focus()
      } else if (e.key === 'j') move(1)
      else if (e.key === 'k') move(-1)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [move])

  const setFilter = <K extends keyof QueueFilters>(key: K, value: QueueFilters[K] | undefined) =>
    setFilters((f) => ({ ...f, [key]: value || undefined }))
  const chips = (
    [
      ['complaint_type', filters.complaint_type && (COMPLAINT_LABEL[filters.complaint_type] ?? humanize(filters.complaint_type))],
      ['evidence_state', filters.evidence_state && `Evidence: ${humanize(filters.evidence_state).toLowerCase()}`],
      ['delivery_state', filters.delivery_state && `Ticket: ${humanize(filters.delivery_state).toLowerCase()}`],
      ['search', filters.search && `“${filters.search}”`],
    ] as const
  ).filter(([, label]) => label)
  const clearAll = () => {
    setFilters((f) => ({ review_status: f.review_status }))
    setSearchDraft('')
  }

  return (
    <aside aria-label="Review queue" className="flex min-h-0 flex-1 flex-col">
      <div className="flex flex-col gap-3 px-4 pt-4 pb-3">
        <div className="flex items-center justify-between gap-2">
          <div>
            <h1 className="text-lg font-bold tracking-tight">Review queue</h1>
            <p className="text-xs text-muted-foreground" aria-live="polite">
              {loadedAt ? `Updated ${formatTime(loadedAt)} · refreshes every 5 s` : 'Loading…'}
            </p>
          </div>
          <Button variant="ghost" size="icon-sm" onClick={() => void refresh()} disabled={busy === 'refresh'} aria-label="Refresh queue" title="Refresh queue">
            <RefreshCw aria-hidden className={cn(busy === 'refresh' && 'animate-spin')} />
          </Button>
        </div>

        <div role="radiogroup" aria-label="Review status" className="grid grid-cols-4 rounded-full bg-muted p-1 text-xs font-semibold">
          {REVIEW_TABS.map((tab) => {
            const active = filters.review_status === tab.value
            return (
              <button
                key={tab.label}
                type="button"
                role="radio"
                aria-checked={active}
                onClick={() => setFilter('review_status', tab.value)}
                className={cn(
                  'h-7 rounded-full transition-colors outline-none focus-visible:ring-2 focus-visible:ring-ring',
                  active ? 'bg-foreground text-background' : 'text-muted-foreground hover:text-foreground',
                )}
              >
                {tab.label}
              </button>
            )
          })}
        </div>

        <form
          role="search"
          className="relative"
          onSubmit={(e) => {
            e.preventDefault()
            setFilter('search', searchDraft.trim() || undefined)
          }}
        >
          <Search aria-hidden className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            ref={searchRef}
            value={searchDraft}
            onChange={(e) => setSearchDraft(e.target.value)}
            maxLength={128}
            placeholder="Case ID or line (SIM-LK-0004)"
            aria-label="Search by exact case ID or line"
            className="h-9 rounded-full border-0 bg-muted pr-12 pl-9"
          />
          <kbd className="pointer-events-none absolute top-1/2 right-3 hidden -translate-y-1/2 rounded border bg-card px-1.5 font-mono text-[10px] text-muted-foreground sm:block">
            /
          </kbd>
        </form>

        <div className="grid grid-cols-3 gap-2">
          <FilterSelect label="Issue" value={filters.complaint_type} options={COMPLAINT} format={(v) => COMPLAINT_LABEL[v as ComplaintType] ?? humanize(v)} onChange={(v) => setFilter('complaint_type', v as ComplaintType)} />
          <FilterSelect label="Evidence" value={filters.evidence_state} options={EVIDENCE} onChange={(v) => setFilter('evidence_state', v as EvidenceState)} />
          <FilterSelect label="Ticket" value={filters.delivery_state} options={DELIVERY} onChange={(v) => setFilter('delivery_state', v as DeliveryState)} />
        </div>

        {chips.length > 0 && (
          <div className="flex flex-wrap items-center gap-1.5">
            {chips.map(([key, label]) => (
              <span key={key} className="inline-flex h-7 items-center gap-1 rounded-full bg-foreground/90 pr-1 pl-3 text-xs font-medium text-background">
                {label}
                <button
                  type="button"
                  onClick={() => {
                    setFilter(key, undefined)
                    if (key === 'search') setSearchDraft('')
                  }}
                  className="grid size-5 place-items-center rounded-full hover:bg-background/20"
                  aria-label={`Remove filter ${label}`}
                >
                  <X className="size-3" aria-hidden />
                </button>
              </span>
            ))}
            <button type="button" onClick={clearAll} className="px-1 text-xs font-medium text-muted-foreground underline-offset-2 hover:underline">
              Clear all
            </button>
          </div>
        )}
      </div>

      <div className="relative min-h-0 flex-1 overflow-y-auto px-2 pb-4">
        {error != null && !rows ? (
          <ErrorState title="Could not load the queue" error={error} onRetry={() => void refresh()} />
        ) : !rows ? (
          <LoadingState label="Loading queue…" />
        ) : rows.length === 0 ? (
          <div className="flex flex-col items-center">
            <EmptyState
              title={chips.length || filters.review_status ? 'No cases match' : 'Nothing needs review'}
              description={chips.length || filters.review_status ? 'Try another status, or clear the filters.' : 'Cases appear here when Resolve needs a person or a customer asks for one.'}
            />
            {(chips.length > 0 || filters.review_status) && (
              <Button variant="outline" size="sm" onClick={() => setFilters({})}>
                Show all cases
              </Button>
            )}
          </div>
        ) : (
          <>
            <p className="px-2 pb-2 text-xs text-muted-foreground">
              {rows.length}
              {cursor ? '+' : ''} {rows.length === 1 ? 'case' : 'cases'} · newest activity first
            </p>
            {error != null && (
              <p role="alert" className="mx-2 mb-2 rounded-lg bg-destructive/10 px-3 py-2 text-xs text-destructive">
                Refresh failed. Showing the last loaded queue.
              </p>
            )}
            <ul ref={listRef} className="flex flex-col gap-1.5" aria-label="Cases">
              {rows.map((row) => (
                <QueueRow key={row.case_id} row={row} selected={row.case_id === caseId} onStep={move} />
              ))}
            </ul>
            {cursor && (
              <div className="flex justify-center pt-3">
                <Button variant="outline" size="sm" onClick={() => void loadMore()} disabled={busy === 'more'}>
                  {busy === 'more' ? 'Loading…' : 'Load older cases'}
                </Button>
              </div>
            )}
            <p className="hidden px-2 pt-4 text-center text-[11px] text-muted-foreground lg:block">
              <kbd className="font-mono">j</kbd> / <kbd className="font-mono">k</kbd> next and previous case · <kbd className="font-mono">/</kbd> search
            </p>
          </>
        )}
      </div>
    </aside>
  )
}

function QueueRow({ row, selected, onStep }: { row: CaseQueueRow; selected: boolean; onStep: (step: 1 | -1) => void }) {
  const flag = attention(row)
  return (
    <li>
      <NavLink
        to={`/agent/cases/${row.case_id}`}
        data-case={row.case_id}
        aria-current={selected ? 'page' : undefined}
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown') {
            e.preventDefault()
            onStep(1)
          } else if (e.key === 'ArrowUp') {
            e.preventDefault()
            onStep(-1)
          }
        }}
        className={cn(
          'relative flex flex-col gap-2 overflow-hidden rounded-xl px-4 py-3 outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring',
          selected ? 'bg-muted ring-1 ring-foreground/15' : 'hover:bg-muted/60',
        )}
      >
        {flag && (
          <span aria-hidden className={cn('absolute inset-y-2 left-0 w-1 rounded-r-full', flag.tone === 'danger' ? 'bg-destructive' : 'bg-warning')} />
        )}
        <div className="flex items-baseline justify-between gap-2">
          <span className="font-mono text-sm font-semibold">{row.line_alias}</span>
          <time dateTime={row.updated_at} title={formatDateTime(row.updated_at)} className="text-xs text-muted-foreground">
            {relativeTime(row.updated_at)}
          </time>
        </div>
        <div className="flex items-center justify-between gap-2">
          <span className="text-sm">{COMPLAINT_LABEL[row.complaint_type] ?? humanize(row.complaint_type)}</span>
          {flag && <span className={cn('text-xs font-semibold', flag.tone === 'danger' ? 'text-destructive' : 'text-warning-foreground')}>{flag.reason}</span>}
        </div>
        <div className="flex flex-wrap gap-1.5">
          <ReviewBadge status={row.review_status} />
          <EvidenceBadge state={row.evidence_state} />
          <DeliveryBadge state={row.delivery_state} />
        </div>
      </NavLink>
    </li>
  )
}

function FilterSelect({
  label,
  value,
  options,
  onChange,
  format = humanize,
}: {
  label: string
  value: string | undefined
  options: string[]
  onChange: (v: string | undefined) => void
  format?: (v: string) => string
}) {
  return (
    <Select value={value ?? ALL} onValueChange={(v) => onChange(v === ALL ? undefined : v)}>
      <SelectTrigger size="sm" aria-label={`${label} filter`} className={cn('w-full min-w-0 rounded-full border-0 bg-muted px-3 text-xs', value && 'bg-foreground/10 font-semibold')}>
        <SelectValue>{value ? format(value) : label}</SelectValue>
      </SelectTrigger>
      <SelectContent>
        <SelectItem value={ALL}>Any {label.toLowerCase()}</SelectItem>
        {options.map((o) => (
          <SelectItem key={o} value={o}>
            {format(o)}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}
