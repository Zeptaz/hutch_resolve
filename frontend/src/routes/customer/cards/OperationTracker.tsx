import { useEffect, useRef, useState } from 'react'
import { Activity, RefreshCw } from 'lucide-react'
import { customerApi } from '@/api/endpoints'
import { describeError } from '@/api/errors'
import type { OperationView } from '@/api/types'
import { OperationBadge } from '@/components/StatusBadge'
import { operationTone } from '@/components/tones'
import { Button } from '@/components/ui/button'
import { hasMessage, useI18n } from '@/i18n/context'
import { formatTime, humanize } from '@/lib/format'
import { CardFrame } from './ChatCards'

const POLL_MS = 1000
// UNKNOWN is deliberately not terminal: it stays visible and keeps polling until recovery resolves it.
const TERMINAL: OperationView['status'][] = ['SUCCEEDED', 'FAILED', 'REVIEW_REQUIRED']

/** Polls an operation every second until it reaches a terminal state or the component unmounts. */
export function OperationTracker({
  operationId,
  onUpdate,
  onSettled,
}: {
  operationId: string
  onUpdate?: (op: OperationView) => void
  onSettled?: (op: OperationView) => void
}) {
  const { t } = useI18n()
  const [op, setOp] = useState<OperationView | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [attempt, setAttempt] = useState(0)
  const onSettledRef = useRef(onSettled)
  const onUpdateRef = useRef(onUpdate)
  useEffect(() => {
    onSettledRef.current = onSettled
    onUpdateRef.current = onUpdate
  })

  useEffect(() => {
    const ctrl = new AbortController()
    let timer: number | undefined
    const tick = async () => {
      try {
        const next = await customerApi.getOperation(operationId, ctrl.signal)
        setOp(next)
        setError(null)
        onUpdateRef.current?.(next)
        if (TERMINAL.includes(next.status)) {
          onSettledRef.current?.(next)
          return
        }
      } catch (e) {
        if ((e as Error).name === 'AbortError') return
        setError(e)
        return // stop polling on error; the user can resume manually
      }
      timer = window.setTimeout(tick, POLL_MS)
    }
    void tick()
    return () => {
      ctrl.abort()
      window.clearTimeout(timer)
    }
  }, [operationId, attempt])

  return (
    <CardFrame
      icon={<Activity />}
      title={op ? (hasMessage(`op.${op.action_type}`) ? t(`op.${op.action_type}`) : humanize(op.action_type)) : t('op.checking')}
      tone={op ? operationTone[op.status] : undefined}
      aside={op && <OperationBadge status={op.status} />}
    >
      <div aria-live="polite">
        {op ? (
          <>
            <p>
              {op.status === 'SUCCEEDED' && hasMessage(`op.done.${op.action_type}`)
                ? t(`op.done.${op.action_type}`)
                : t(`op.${op.status}`)}
            </p>
            <p className="mt-1 text-muted-foreground">{op.next_step}</p>
            {op.outcome.provider_ticket_id && (
              <p className="mt-2 text-xs">
                {t('card.ticketNumber')} <span className="font-mono">{op.outcome.provider_ticket_id}</span>
              </p>
            )}
            <p className="mt-2 text-[11px] text-muted-foreground">{t('op.lastUpdated', { time: formatTime(op.updated_at) })}</p>
          </>
        ) : (
          !error && <p className="text-muted-foreground">{t('op.checkingLatest')}</p>
        )}
      </div>
      {error != null && (
        <div role="alert" className="mt-2 flex flex-wrap items-center gap-2 text-xs text-destructive">
          {t('op.couldNotCheck', { reason: describeError(error, t) })}
          <Button variant="outline" size="xs" onClick={() => setAttempt((n) => n + 1)}>
            <RefreshCw aria-hidden /> {t('op.checkAgain')}
          </Button>
        </div>
      )}
    </CardFrame>
  )
}
