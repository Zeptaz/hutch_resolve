import { useEffect, useState } from 'react'
import { ShieldCheck, Timer } from 'lucide-react'
import type { Decision, ProposalView } from '@/api/types'
import { StatusBadge } from '@/components/StatusBadge'
import { Button } from '@/components/ui/button'
import { formatTime, humanize } from '@/lib/format'
import { CardFrame } from './ChatCards'

const ACTION_TITLE: Record<ProposalView['action_type'], string> = {
  DEACTIVATE_VAS: 'Stop a subscription renewing',
  SEND_SETTINGS_INSTRUCTIONS: 'Send settings instructions',
  CREATE_REVIEW_TICKET: 'Ask for a human review',
}

export type ProposalState =
  | { kind: 'open' }
  | { kind: 'submitting'; decision: Decision }
  | { kind: 'decided'; decision: Decision }
  | { kind: 'closed' } // no longer the conversation's pending proposal (decided elsewhere, replaced or invalidated)

/**
 * Explicit confirmation for a server proposal. Shows exact target, consequences and expiry.
 * Nothing is preselected or autofocused, and nothing is ever submitted automatically.
 */
export function ConfirmationCard({
  proposal,
  state,
  onDecide,
}: {
  proposal: ProposalView
  state: ProposalState
  onDecide: (decision: Decision) => void
}) {
  const expiresMs = Date.parse(proposal.expires_at)
  const now = useNow(state.kind === 'open' || state.kind === 'submitting')
  const remaining = Math.max(0, expiresMs - now)
  const expired = remaining === 0 && (state.kind === 'open' || state.kind === 'submitting')
  const actionable = state.kind === 'open' && !expired

  return (
    <CardFrame
      icon={<ShieldCheck />}
      title="Your confirmation is needed"
      className={actionable ? 'border-primary/40 ring-1 ring-primary/20' : undefined}
      aside={<StateBadge state={state} expired={expired} />}
    >
      <dl className="flex flex-col gap-2">
        <div>
          <dt className="text-xs text-muted-foreground">Action</dt>
          <dd className="font-medium">{ACTION_TITLE[proposal.action_type] ?? humanize(proposal.action_type)}</dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">Applies to</dt>
          <dd>{proposal.target_label}</dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">What this means</dt>
          <dd>{proposal.consequences}</dd>
        </div>
      </dl>

      {(state.kind === 'open' || state.kind === 'submitting') && (
        <p className="mt-3 flex items-center gap-1.5 text-xs text-muted-foreground">
          <Timer aria-hidden className="size-3.5" />
          {expired ? (
            'This offer has expired. Ask again if you still want it.'
          ) : (
            <>
              Offer valid until {formatTime(proposal.expires_at)} ({formatRemaining(remaining)} left)
            </>
          )}
        </p>
      )}

      {(state.kind === 'open' || state.kind === 'submitting') && !expired && (
        <div className="mt-3 flex flex-wrap gap-2" role="group" aria-label="Confirm or decline this action">
          <Button onClick={() => onDecide('ACCEPT')} disabled={!actionable}>
            {state.kind === 'submitting' && state.decision === 'ACCEPT' ? 'Sending…' : 'Yes, go ahead'}
          </Button>
          <Button variant="outline" onClick={() => onDecide('DECLINE')} disabled={!actionable}>
            {state.kind === 'submitting' && state.decision === 'DECLINE' ? 'Sending…' : 'No, thanks'}
          </Button>
        </div>
      )}
      <p className="mt-3 text-[11px] text-muted-foreground">Simulation — no real account is changed.</p>
    </CardFrame>
  )
}

function StateBadge({ state, expired }: { state: ProposalState; expired: boolean }) {
  if (state.kind === 'decided') {
    return state.decision === 'ACCEPT' ? <StatusBadge tone="info">You accepted</StatusBadge> : <StatusBadge tone="neutral">You declined</StatusBadge>
  }
  if (state.kind === 'closed') return <StatusBadge tone="neutral">No longer open</StatusBadge>
  if (expired) return <StatusBadge tone="neutral">Expired</StatusBadge>
  return <StatusBadge tone="warning">Waiting for you</StatusBadge>
}

function formatRemaining(ms: number) {
  const s = Math.ceil(ms / 1000)
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

/** Current time, re-rendering every second while active. */
function useNow(active: boolean) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!active) return
    const id = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(id)
  }, [active])
  return now
}
