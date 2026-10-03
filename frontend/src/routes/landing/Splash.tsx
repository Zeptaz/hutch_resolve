import { useEffect, useState } from 'react'
import { LayoutDashboard, MessageCircle, MessageSquareText, Mic } from 'lucide-react'
import { cn } from '@/lib/utils'

const SEEN_KEY = 'hutch-resolve.intro-seen'
const HOLD_MS = 1500
const FADE_MS = 450

/** True when the intro already played in this tab, so going back to the landing page skips it. */
function introSeen() {
  try {
    return sessionStorage.getItem(SEEN_KEY) === '1'
  } catch {
    return false
  }
}

function markIntroSeen() {
  try {
    sessionStorage.setItem(SEEN_KEY, '1')
  } catch {
    // Storage may be unavailable; the intro then plays on each visit.
  }
}

/** Brief branded intro that fades into the landing page. Plays once per tab; short for reduced motion. */
export function Splash() {
  const [phase, setPhase] = useState<'shown' | 'leaving' | 'gone'>(() => (introSeen() ? 'gone' : 'shown'))

  useEffect(() => {
    if (introSeen()) return
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    const hold = reduced ? 300 : HOLD_MS
    const leave = window.setTimeout(() => setPhase('leaving'), hold)
    const done = window.setTimeout(() => {
      markIntroSeen()
      setPhase('gone')
    }, hold + FADE_MS)
    return () => {
      window.clearTimeout(leave)
      window.clearTimeout(done)
    }
  }, [])

  if (phase === 'gone') return null

  return (
    <div
      role="status"
      aria-label="Loading HUTCH Resolve"
      className={cn(
        'fixed inset-0 z-50 grid place-items-center bg-[#f5f2ec] transition-opacity duration-[450ms] ease-out',
        phase === 'leaving' && 'pointer-events-none opacity-0',
      )}
    >
      <div aria-hidden className="pointer-events-none absolute inset-0 bg-[radial-gradient(40rem_28rem_at_50%_45%,color-mix(in_oklch,var(--primary)_14%,transparent),transparent)]" />
      <div className="relative flex flex-col items-center gap-6">
        <div className="relative grid size-20 place-items-center">
          <span className="animate-splash-ring absolute inset-0 rounded-full border-2 border-primary/50" />
          <span className="animate-splash-ring absolute inset-0 rounded-full border-2 border-primary/30 [animation-delay:500ms]" />
          <span className="animate-pop grid size-16 place-items-center rounded-2xl bg-primary text-primary-foreground shadow-lg">
            <MessageSquareText className="size-8" strokeWidth={2.2} aria-hidden />
          </span>
        </div>
        <p className="animate-rise-in text-3xl font-medium tracking-[-0.03em] text-[#111114] [--i:4]">
          HUTCH <span className="text-primary">Resolve</span>
        </p>
        <div className="flex items-center gap-3 text-muted-foreground">
          {[MessageCircle, Mic, LayoutDashboard].map((Icon, i) => (
            <span
              key={i}
              className="animate-pop grid size-9 place-items-center rounded-xl bg-white shadow-sm"
              style={{ animationDelay: `${450 + i * 140}ms` }}
            >
              <Icon className="size-4" aria-hidden />
            </span>
          ))}
        </div>
        <div className="h-1 w-40 overflow-hidden rounded-full bg-black/[0.07]">
          <div className="animate-splash-bar h-full rounded-full bg-primary" />
        </div>
      </div>
    </div>
  )
}
