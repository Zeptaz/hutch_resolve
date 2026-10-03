import { cn } from '@/lib/utils'

/** A round tick, drawn to stand in for the o of Resolve. The same shape is public/favicon.svg. */
export function RoundTick({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 40 40" aria-hidden className={className}>
      <circle cx="20" cy="20" r="19" fill="currentColor" />
      <path fill="none" stroke="#fff" strokeWidth="5" strokeLinecap="round" strokeLinejoin="round" d="M11.5 20.5l5.6 5.6L28.5 14.5" />
    </svg>
  )
}

/** "HUTCH Resolve" with a round tick as the o. Screen readers hear the plain name. */
export function ResolveWordmark({ className }: { className?: string }) {
  return (
    <span className={cn('inline-block whitespace-nowrap', className)}>
      <span className="sr-only">HUTCH Resolve</span>
      <span aria-hidden>
        HUTCH{' '}
        <span className="text-primary">
          Res
          <RoundTick className="mx-[0.03em] inline-block size-[0.58em] -translate-y-[0.01em] align-baseline" />
          lve
        </span>
      </span>
    </span>
  )
}
