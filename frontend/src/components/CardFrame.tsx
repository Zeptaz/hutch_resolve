import type { ReactNode } from 'react'
import type { Tone } from '@/components/tones'
import { cn } from '@/lib/utils'

// Status cards get an edge in their state colour; neutral states (declined, expired) a quiet grey one.
const TONE_EDGE: Record<Tone, string> = {
  neutral: 'border-[1.5px] border-foreground/15',
  info: 'border-[1.5px] border-info/45',
  success: 'border-[1.5px] border-success/45',
  warning: 'border-[1.5px] border-warning/70',
  danger: 'border-[1.5px] border-destructive/45',
}

/** Soft panel tile. Pass `tone` only when the card shows a status, so its edge matches the badge. */
export function CardFrame({
  icon,
  title,
  aside,
  tone,
  children,
  className,
}: {
  icon: ReactNode
  title: string
  aside?: ReactNode
  tone?: Tone
  children: ReactNode
  className?: string
}) {
  return (
    <section
      className={cn('rounded-2xl bg-muted/70 text-card-foreground', tone ? TONE_EDGE[tone] : 'border-0', className)}
      aria-label={title}
    >
      <header className="flex items-center justify-between gap-2 px-4 pt-3 pb-1">
        <h3 className="flex items-center gap-2 text-sm font-semibold">
          <span aria-hidden className="text-muted-foreground [&_svg]:size-4">
            {icon}
          </span>
          {title}
        </h3>
        {aside}
      </header>
      <div className="px-4 pt-2 pb-3.5 text-sm">{children}</div>
    </section>
  )
}
