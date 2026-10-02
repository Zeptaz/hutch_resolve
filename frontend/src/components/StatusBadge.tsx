import { cn } from '@/lib/utils'
import { useI18n } from '@/i18n/context'
import { humanize } from '@/lib/format'
import type { DeliveryState, EvidenceState, OperationStatus, ReviewStatus } from '@/api/types'
import { deliveryTone, evidenceTone, operationTone, reviewTone, type Tone } from './tones'

export type { Tone }

// Solid warning (dark text) because amber text on white fails contrast; others use AA-safe tinted styles.
const toneClasses: Record<Tone, string> = {
  neutral: 'bg-muted text-muted-foreground border-border',
  info: 'bg-info/10 text-info border-info/25',
  success: 'bg-success/10 text-success border-success/25',
  warning: 'bg-warning text-warning-foreground border-transparent',
  danger: 'bg-destructive/10 text-destructive border-destructive/25',
}

export function StatusBadge({ tone, children, className }: { tone: Tone; children: React.ReactNode; className?: string }) {
  return (
    <span
      className={cn(
        'inline-flex h-6 items-center gap-1 rounded-md border px-2 text-xs font-semibold whitespace-nowrap',
        toneClasses[tone],
        className,
      )}
    >
      {children}
    </span>
  )
}

function Missing({ label }: { label: string }) {
  return <StatusBadge tone="neutral">{label}</StatusBadge>
}

export function EvidenceBadge({ state }: { state: EvidenceState | null | undefined }) {
  const { t } = useI18n()
  if (!state) return <Missing label={t('evidence.none')} />
  return <StatusBadge tone={evidenceTone[state]}>{t('evidence.label', { state: t(`evidence.${state}`) })}</StatusBadge>
}

export function ReviewBadge({ status }: { status: ReviewStatus }) {
  return <StatusBadge tone={reviewTone[status]}>{humanize(status)}</StatusBadge>
}

export function DeliveryBadge({ state }: { state: DeliveryState | null | undefined }) {
  const { t } = useI18n()
  if (!state) return <Missing label={t('delivery.none')} />
  return <StatusBadge tone={deliveryTone[state]}>{t('delivery.label', { state: t(`delivery.${state}`) })}</StatusBadge>
}

export function OperationBadge({ status }: { status: OperationStatus }) {
  const { t } = useI18n()
  return <StatusBadge tone={operationTone[status]}>{t(`opStatus.${status}`)}</StatusBadge>
}
