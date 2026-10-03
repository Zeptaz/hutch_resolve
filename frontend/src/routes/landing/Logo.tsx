import { cn } from '@/lib/utils'

/**
 * The Resolve mark: a SIM card's cut-corner tile with a tick, meaning "your line, sorted".
 * Drawn on a 40-unit grid; the same shape is public/favicon.svg.
 */
export function SimTick({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 40 40" aria-hidden className={className}>
      <path fill="currentColor" d="M9 2h17l12 12v17a7 7 0 0 1-7 7H9a7 7 0 0 1-7-7V9a7 7 0 0 1 7-7z" />
      <path fill="none" stroke="#fff" strokeWidth="4.6" strokeLinecap="round" strokeLinejoin="round" d="M11.5 21.5l5.5 5.5 11.5-12" />
    </svg>
  )
}

/** A round tick, drawn to stand in for the o of Resolve. */
export function RoundTick({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 40 40" aria-hidden className={className}>
      <circle cx="20" cy="20" r="19" fill="currentColor" />
      <path fill="none" stroke="#fff" strokeWidth="5" strokeLinecap="round" strokeLinejoin="round" d="M11.5 20.5l5.6 5.6L28.5 14.5" />
    </svg>
  )
}

/** The SIM tick, then "HUTCH Resolve" with a round tick as the o. Screen readers hear the plain name. */
export function ResolveWordmark({ className }: { className?: string }) {
  return (
    <span className={cn('inline-flex items-center gap-[0.4em] whitespace-nowrap', className)}>
      <SimTick className="size-[1.3em] shrink-0 text-primary" />
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
