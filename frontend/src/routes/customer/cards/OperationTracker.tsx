import { useEffect, useRef, useState } from 'react'
import { Activity, RefreshCw } from 'lucide-react'
import { customerApi } from '@/api/endpoints'
import { describeError } from '@/api/errors'
import type { OperationView } from '@/api/types'
import { OperationBadge } from '@/components/StatusBadge'
import { Button } from '@/components/ui/button'
import { formatTime, humanize } from '@/lib/format'
import { CardFrame } from './ChatCards'

const POLL_MS = 1000
// UNKNOWN is deliberately not terminal: it stays visible and keeps polling until recovery resolves it.
const TERMINAL: OperationView['status'][] = ['SUCCEEDED', 'FAILED', 'REVIEW_REQUIRED']

const ACTION_LABEL: Record<OperationView['action_type'], string> = {
  DEACTIVATE_VAS: 'Stopping subscription renewal',
  SEND_SETTINGS_INSTRUCTIONS: 'Sending settings instructions',
  CREATE_REVIEW_TICKET: 'Creating review ticket',
}

const STATUS_TEXT: Record<OperationView['status'], string> = {
  PENDING: 'Queued — not done yet.',
  RUNNING: 'In progress — not done yet.',
  SUCCEEDED: 'Done. The change was confirmed by the provider.',
  FAILED: 'This did not go through. Nothing was changed.',
  UNKNOWN: 'We are still checking whether this went through. Please don’t ask again yet.',
  REVIEW_REQUIRED: 'We could not confirm the result automatically. A person will check it.',
}

const SUCCESS_TEXT: Record<OperationView['action_type'], string> = {
  DEACTIVATE_VAS: 'Done. The provider confirmed the subscription will not renew.',
  SEND_SETTINGS_INSTRUCTIONS: 'Done. The settings instructions were sent.',
  CREATE_REVIEW_TICKET: 'Done. Your review ticket was created.',
}

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
      title={op ? ACTION_LABEL[op.action_type] ?? humanize(op.action_type) : 'Checking progress…'}
      aside={op && <OperationBadge status={op.status} />}
    >
      <div aria-live="polite">
        {op ? (
          <>
            <p>{op.status === 'SUCCEEDED' ? SUCCESS_TEXT[op.action_type] ?? STATUS_TEXT.SUCCEEDED : STATUS_TEXT[op.status]}</p>
            <p className="mt-1 text-muted-foreground">{op.next_step}</p>
            {op.outcome.provider_ticket_id && (
              <p className="mt-2 text-xs">
                Ticket number <span className="font-mono">{op.outcome.provider_ticket_id}</span>
              </p>
            )}
            <p className="mt-2 text-[11px] text-muted-foreground">Last updated {formatTime(op.updated_at)}</p>
          </>
        ) : (
          !error && <p className="text-muted-foreground">Checking the latest status…</p>
        )}
      </div>
      {error != null && (
        <div role="alert" className="mt-2 flex flex-wrap items-center gap-2 text-xs text-destructive">
          Could not check progress: {describeError(error)}
          <Button variant="outline" size="xs" onClick={() => setAttempt((n) => n + 1)}>
            <RefreshCw aria-hidden /> Check again
          </Button>
        </div>
      )}
    </CardFrame>
  )
}
