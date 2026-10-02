import { AlertTriangle, Clock, RefreshCw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { describeError, isApiError } from '@/api/errors'
import { useI18n } from '@/i18n/context'

export function LoadingState({ label, rows = 3 }: { label?: string; rows?: number }) {
  const { t } = useI18n()
  return (
    <div role="status" aria-live="polite" className="flex flex-col gap-3 p-6">
      <span className="sr-only">{label ?? t('state.loading')}</span>
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton key={i} className="h-12 w-full" />
      ))}
    </div>
  )
}

export function ErrorState({ error, onRetry, title }: { error: unknown; onRetry?: () => void; title?: string }) {
  const { t, language } = useI18n()
  const ref = isApiError(error) ? error.requestId : null
  return (
    <div role="alert" className="flex flex-col items-center gap-3 p-8 text-center">
      <AlertTriangle className="size-8 text-destructive" aria-hidden />
      <div>
        <p className="font-semibold">{title ?? t('state.errorTitle')}</p>
        <p className="text-sm text-muted-foreground">{describeError(error, language === 'en' ? undefined : t)}</p>
        {ref && <p className="mt-1 font-mono text-xs text-muted-foreground">{t('state.reference', { id: ref })}</p>}
      </div>
      {onRetry && (
        <Button variant="outline" onClick={onRetry}>
          <RefreshCw aria-hidden /> {t('state.retry')}
        </Button>
      )}
    </div>
  )
}

export function ExpiredState({ message, actionLabel, onAction }: { message: string; actionLabel: string; onAction: () => void }) {
  const { t } = useI18n()
  return (
    <div role="alert" className="flex flex-col items-center gap-3 p-8 text-center">
      <Clock className="size-8 text-muted-foreground" aria-hidden />
      <div>
        <p className="font-semibold">{t('state.sessionEnded')}</p>
        <p className="text-sm text-muted-foreground">{message}</p>
      </div>
      <Button onClick={onAction}>{actionLabel}</Button>
    </div>
  )
}

export function EmptyState({ title, description }: { title: string; description?: string }) {
  return (
    <div className="flex flex-col items-center gap-1 p-8 text-center">
      <p className="font-semibold">{title}</p>
      {description && <p className="text-sm text-muted-foreground">{description}</p>}
    </div>
  )
}
