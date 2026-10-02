import { cn } from '@/lib/utils'

/** Text-only product wordmark — deliberately not the HUTCH logo. */
export function BrandMark({ subtitle, className }: { subtitle?: string; className?: string }) {
  return (
    <div className={cn('flex flex-col leading-tight', className)}>
      <span className="text-base font-bold tracking-tight whitespace-nowrap">
        HUTCH <span className="text-primary">Resolve</span>
      </span>
      {subtitle && <span className="hidden text-xs text-muted-foreground sm:block">{subtitle}</span>}
    </div>
  )
}
