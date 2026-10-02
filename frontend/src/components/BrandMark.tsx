import { cn } from '@/lib/utils'

/** Generic product wordmark — deliberately not the HUTCH logo. */
export function BrandMark({ subtitle, className }: { subtitle?: string; className?: string }) {
  return (
    <div className={cn('flex items-center gap-2.5', className)}>
      <span aria-hidden className="grid size-8 place-items-center rounded-lg bg-primary text-sm font-extrabold text-primary-foreground">
        R
      </span>
      <span className="flex flex-col leading-tight">
        <span className="text-sm font-bold tracking-tight whitespace-nowrap">HUTCH Resolve</span>
        {subtitle && <span className="hidden text-xs text-muted-foreground sm:block">{subtitle}</span>}
      </span>
    </div>
  )
}
