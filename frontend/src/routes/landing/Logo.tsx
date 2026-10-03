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

/** "HUTCH Resolve" with the o of Resolve drawn as the SIM tick. Screen readers hear the plain name. */
export function ResolveWordmark({ className }: { className?: string }) {
  return (
    <span className={cn('inline-block whitespace-nowrap', className)}>
      <span className="sr-only">HUTCH Resolve</span>
      <span aria-hidden>
        HUTCH{' '}
        <span className="text-primary">
          Res
          <SimTick className="mx-[0.03em] inline-block size-[0.6em] -translate-y-[0.02em] align-baseline" />
          lve
        </span>
      </span>
    </span>
  )
}
