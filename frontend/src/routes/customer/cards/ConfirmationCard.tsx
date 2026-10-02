import { useEffect, useState } from 'react'
import { ShieldCheck, Timer } from 'lucide-react'
import type { Decision, ProposalView } from '@/api/types'
import { StatusBadge, type Tone } from '@/components/StatusBadge'
import { Button } from '@/components/ui/button'
import { hasMessage, useI18n, type Translate } from '@/i18n/context'
import { formatTime, humanize } from '@/lib/format'
import { CardFrame } from './ChatCards'


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
  const { t } = useI18n()
  const expiresMs = Date.parse(proposal.expires_at)
  const now = useNow(state.kind === 'open' || state.kind === 'submitting')
  const remaining = Math.max(0, expiresMs - now)
  const expired = remaining === 0 && (state.kind === 'open' || state.kind === 'submitting')
  const actionable = state.kind === 'open' && !expired
  const badge = stateBadge(t, state, expired)

  return (
    <CardFrame
      icon={<ShieldCheck />}
      title={
        state.kind === 'open' || state.kind === 'submitting'
          ? expired
            ? t('confirm.expiredTitle')
            : t('confirm.needed')
          : state.kind === 'decided'
            ? t('confirm.decision')
            : t('confirm.suggested')
      }
      tone={badge.tone}
      aside={<StatusBadge tone={badge.tone}>{badge.label}</StatusBadge>}
    >
      <dl className="flex flex-col gap-2">
        <div>
          <dt className="text-xs text-muted-foreground">{t('confirm.action')}</dt>
          <dd className="font-medium">{hasMessage(`action.${proposal.action_type}`) ? t(`action.${proposal.action_type}`) : humanize(proposal.action_type)}</dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">{t('confirm.appliesTo')}</dt>
          <dd>{proposal.target_label}</dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">{t('confirm.means')}</dt>
          <dd>{proposal.consequences}</dd>
        </div>
      </dl>

      {(state.kind === 'open' || state.kind === 'submitting') && (
        <p className="mt-3 flex items-center gap-1.5 text-xs text-muted-foreground">
          <Timer aria-hidden className="size-3.5" />
          {expired
            ? t('confirm.expiredNote')
            : t('confirm.validUntil', { time: formatTime(proposal.expires_at), left: formatRemaining(remaining) })}
        </p>
      )}

      {(state.kind === 'open' || state.kind === 'submitting') && !expired && (
        <div className="mt-3 flex flex-wrap gap-2" role="group" aria-label={t('confirm.group')}>
          <Button onClick={() => onDecide('ACCEPT')} disabled={!actionable}>
            {state.kind === 'submitting' && state.decision === 'ACCEPT' ? t('confirm.sending') : t('confirm.yes')}
          </Button>
          <Button variant="outline" onClick={() => onDecide('DECLINE')} disabled={!actionable}>
            {state.kind === 'submitting' && state.decision === 'DECLINE' ? t('confirm.sending') : t('confirm.no')}
          </Button>
        </div>
      )}
      <p className="mt-3 text-[11px] text-muted-foreground">{t('confirm.simNote')}</p>
    </CardFrame>
  )
}

function stateBadge(t: Translate, state: ProposalState, expired: boolean): { tone: Tone; label: string } {
  if (state.kind === 'decided') {
    return state.decision === 'ACCEPT' ? { tone: 'info', label: t('confirm.accepted') } : { tone: 'neutral', label: t('confirm.declined') }
  }
  if (state.kind === 'closed') return { tone: 'neutral', label: t('confirm.closed') }
  if (expired) return { tone: 'neutral', label: t('confirm.expired') }
  return { tone: 'warning', label: t('confirm.waiting') }
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
