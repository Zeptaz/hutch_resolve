import { useCallback, useEffect, useState } from 'react'
import { RefreshCw, Search } from 'lucide-react'
import { NavLink } from 'react-router'
import { agentApi } from '@/api/endpoints'
import type { CaseQueue as CaseQueueData, ComplaintType, EvidenceState, QueueFilters, ReviewStatus } from '@/api/types'
import { ErrorState, EmptyState, LoadingState } from '@/components/states'
import { DeliveryBadge, EvidenceBadge, ReviewBadge } from '@/components/StatusBadge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { formatDateTime, humanize } from '@/lib/format'
import { cn } from '@/lib/utils'

const POLL_MS = 5000
const ALL = 'ALL'

const REVIEW: ReviewStatus[] = ['NEW', 'IN_REVIEW', 'CLOSED']
const COMPLAINT: ComplaintType[] = ['BALANCE_RECHARGE', 'DATA_DEPLETION', 'CONNECTIVITY', 'VAS_DISPUTE']
const EVIDENCE: EvidenceState[] = ['SUFFICIENT', 'PARTIAL', 'CONFLICTING']

/** Review queue. Filtering/search happen server-side; this only renders what the API returns. */
export function CaseQueue() {
  const [filters, setFilters] = useState<QueueFilters>({})
  const [searchDraft, setSearchDraft] = useState('')
  const [data, setData] = useState<CaseQueueData | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [refreshing, setRefreshing] = useState(false)

  const load = useCallback(
    async (signal?: AbortSignal) => {
      try {
        const res = await agentApi.listCases(filters, signal)
        setData(res)
        setError(null)
      } catch (e) {
        if ((e as Error).name !== 'AbortError') setError(e)
      }
    },
    [filters],
  )

  const refresh = async () => {
    setRefreshing(true)
    await load()
    setRefreshing(false)
  }

  // Load on filter change, then poll every 5s only while the tab is visible.
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

  const setFilter = <K extends keyof QueueFilters>(key: K, value: string) =>
    setFilters((f) => ({ ...f, [key]: value === ALL ? undefined : value }))

  return (
    <aside aria-label="Review queue" className="flex min-h-0 flex-col border-b lg:w-[34rem] lg:border-r lg:border-b-0">
      <div className="flex flex-col gap-2 border-b p-3">
        <div className="flex items-center justify-between">
          <h1 className="text-base font-semibold">Review queue</h1>
          <Button variant="ghost" size="sm" onClick={() => void refresh()} disabled={refreshing} aria-label="Refresh queue">
            <RefreshCw aria-hidden className={cn(refreshing && 'animate-spin')} /> Refresh
          </Button>
        </div>
        <form
          role="search"
          className="relative"
          onSubmit={(e) => {
            e.preventDefault()
            setFilters((f) => ({ ...f, search: searchDraft.trim() || undefined }))
          }}
        >
          <Search aria-hidden className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={searchDraft}
            onChange={(e) => setSearchDraft(e.target.value)}
            maxLength={128}
            placeholder="Case ID or exact line (SIM-LK-…), then Enter"
            aria-label="Search cases"
            className="pl-8"
          />
        </form>
        <div className="grid grid-cols-3 gap-2">
          <FilterSelect label="Review" value={filters.review_status} options={REVIEW} onChange={(v) => setFilter('review_status', v)} />
          <FilterSelect label="Complaint" value={filters.complaint_type} options={COMPLAINT} onChange={(v) => setFilter('complaint_type', v)} />
          <FilterSelect label="Evidence" value={filters.evidence_state} options={EVIDENCE} onChange={(v) => setFilter('evidence_state', v)} />
        </div>
      </div>

      <div className="relative min-h-0 flex-1 overflow-y-auto">
        {error != null && !data ? (
          <ErrorState title="Could not load the queue" error={error} onRetry={() => void refresh()} />
        ) : !data ? (
          <LoadingState label="Loading queue…" />
        ) : data.items.length === 0 ? (
          <EmptyState title="No cases match" description="Try clearing filters or search." />
        ) : (
          <ul className="divide-y">
            {error != null && (
              <li role="alert" className="bg-destructive/5 px-3 py-2 text-xs text-destructive">
                Refresh failed — showing the last loaded queue.
              </li>
            )}
            {data.items.map((row) => (
              <li key={row.case_id}>
                <NavLink
                  to={`cases/${row.case_id}`}
                  className={({ isActive }) =>
                    cn(
                      'flex flex-col gap-2 px-3 py-3 outline-none hover:bg-muted/60 focus-visible:bg-muted',
                      isActive && 'bg-accent hover:bg-accent',
                    )
                  }
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-mono text-sm font-medium">{row.line_alias}</span>
                    <time className="text-xs text-muted-foreground">{formatDateTime(row.updated_at)}</time>
                  </div>
                  <span className="text-sm">{humanize(row.complaint_type)}</span>
                  <div className="flex flex-wrap gap-1.5">
                    <ReviewBadge status={row.review_status} />
                    <EvidenceBadge state={row.evidence_state} />
                    <DeliveryBadge state={row.delivery_state} />
                  </div>
                </NavLink>
              </li>
            ))}
          </ul>
        )}
      </div>
    </aside>
  )
}

function FilterSelect({
  label,
  value,
  options,
  onChange,
}: {
  label: string
  value: string | undefined
  options: string[]
  onChange: (v: string) => void
}) {
  return (
    <Select value={value ?? ALL} onValueChange={onChange}>
      <SelectTrigger size="sm" aria-label={`${label} filter`} className="w-full">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value={ALL}>{label}: all</SelectItem>
        {options.map((o) => (
          <SelectItem key={o} value={o}>
            {humanize(o)}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}
