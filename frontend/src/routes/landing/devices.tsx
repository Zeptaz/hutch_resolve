import { useEffect, useRef, useState } from 'react'
import { ChevronLeft, FileText, Mic, MicOff, PhoneOff, Search, Send, ShieldCheck, Sparkles } from 'lucide-react'
import { cn } from '@/lib/utils'

/*
 * Static illustrations of each system for the landing page. They are pictures, not the live apps:
 * nothing here starts a session or calls Resolve.
 */

export function PhoneFrame({ label, children, className }: { label: string; children: React.ReactNode; className?: string }) {
  return (
    <figure role="img" aria-label={label} className={cn('relative mx-auto w-[min(17.5rem,82vw)]', className)}>
      {/* Side buttons */}
      <span aria-hidden className="absolute top-24 -left-[3px] h-8 w-[3px] rounded-l bg-neutral-700" />
      <span aria-hidden className="absolute top-36 -left-[3px] h-12 w-[3px] rounded-l bg-neutral-700" />
      <span aria-hidden className="absolute top-32 -right-[3px] h-16 w-[3px] rounded-r bg-neutral-700" />
      <div className="aspect-[9/19] rounded-[2.75rem] bg-neutral-900 p-2.5 shadow-[0_30px_60px_-20px_rgb(0_0_0/0.35)] ring-1 ring-neutral-700">
        <div className="relative flex h-full flex-col overflow-hidden rounded-[2.2rem] bg-background">
          <div aria-hidden className="flex h-9 shrink-0 items-center justify-between px-6 pt-1 text-[10px] font-semibold">
            <span>9:41</span>
            <span className="absolute top-2 left-1/2 h-5 w-20 -translate-x-1/2 rounded-full bg-neutral-900" />
            <span className="flex items-center gap-1">
              <span className="flex items-end gap-px">
                {[3, 5, 7, 9].map((h) => <span key={h} className="w-[2px] rounded-sm bg-foreground" style={{ height: h }} />)}
              </span>
              <span className="ml-1 h-2.5 w-5 rounded-[3px] border border-foreground/70 p-px"><span className="block h-full w-3/4 rounded-[1px] bg-foreground" /></span>
            </span>
          </div>
          {children}
          <span aria-hidden className="absolute bottom-1.5 left-1/2 h-1 w-24 -translate-x-1/2 rounded-full bg-foreground/80" />
        </div>
      </div>
    </figure>
  )
}

export function DesktopFrame({ label, children, className }: { label: string; children: React.ReactNode; className?: string }) {
  return (
    <figure role="img" aria-label={label} className={cn('mx-auto w-full max-w-3xl', className)}>
      <div className="rounded-t-2xl bg-neutral-900 p-2 pb-2.5 shadow-[0_30px_60px_-20px_rgb(0_0_0/0.35)] ring-1 ring-neutral-700">
        <div className="overflow-hidden rounded-lg bg-background">
          <div aria-hidden className="flex items-center gap-3 border-b bg-muted/60 px-3 py-2">
            <span className="flex gap-1.5">
              <span className="size-2.5 rounded-full bg-[#ff5f57]" />
              <span className="size-2.5 rounded-full bg-[#febc2e]" />
              <span className="size-2.5 rounded-full bg-[#28c840]" />
            </span>
            <span className="mx-auto w-1/2 truncate rounded-md bg-background px-3 py-0.5 text-center text-[10px] text-muted-foreground">
              resolve.local/agent
            </span>
          </div>
          <ScaledScreen>{children}</ScaledScreen>
        </div>
      </div>
      <div aria-hidden className="relative mx-auto h-3 w-[108%] -translate-x-[3.7%] rounded-b-xl bg-gradient-to-b from-neutral-300 to-neutral-400 dark:from-neutral-600 dark:to-neutral-700">
        <span className="absolute top-0 left-1/2 h-1.5 w-20 -translate-x-1/2 rounded-b-md bg-neutral-400 dark:bg-neutral-800" />
      </div>
    </figure>
  )
}

const SCREEN_WIDTH = 760

/** Lays the screen out at a desktop width and scales it to fit, so it reads as a computer screen even on a phone. */
function ScaledScreen({ children }: { children: React.ReactNode }) {
  const ref = useRef<HTMLDivElement>(null)
  const [scale, setScale] = useState(1)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const observer = new ResizeObserver(([entry]) => setScale(entry.contentRect.width / SCREEN_WIDTH))
    observer.observe(el)
    return () => observer.disconnect()
  }, [])
  return (
    <div ref={ref} className="relative aspect-[16/10] overflow-hidden">
      <div className="absolute top-0 left-0 origin-top-left" style={{ width: SCREEN_WIDTH, height: SCREEN_WIDTH / 1.6, transform: `scale(${scale})` }}>
        {children}
      </div>
    </div>
  )
}

function MiniHeader({ right }: { right?: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between border-b px-3.5 py-2">
      <span className="text-[13px] font-bold tracking-tight">
        HUTCH <span className="text-primary">Resolve</span>
      </span>
      {right}
    </div>
  )
}

function Bubble({ mine, children, className }: { mine?: boolean; children: React.ReactNode; className?: string }) {
  return (
    <div className={cn('flex max-w-[86%] gap-1.5', mine ? 'ml-auto flex-row-reverse' : 'mr-auto', className)}>
      {!mine && (
        <span className="mt-auto grid size-5 shrink-0 place-items-center rounded-full bg-primary/12 text-primary">
          <Sparkles className="size-3" />
        </span>
      )}
      <p
        className={cn(
          'rounded-2xl px-3 py-2 text-[11px] leading-snug',
          mine ? 'rounded-br-md bg-primary text-primary-foreground' : 'rounded-bl-md bg-muted',
        )}
      >
        {children}
      </p>
    </div>
  )
}

export function ChatPreview() {
  return (
    <>
      <MiniHeader
        right={
          <span className="flex rounded-full bg-muted p-0.5 text-[9px] font-medium">
            <span className="rounded-full bg-background px-1.5 py-0.5 shadow-sm">EN</span>
            <span className="px-1.5 py-0.5 text-muted-foreground">සි</span>
            <span className="px-1.5 py-0.5 text-muted-foreground">த</span>
          </span>
        }
      />
      <div aria-hidden className="flex flex-1 flex-col gap-2.5 overflow-hidden px-3 py-3">
        <Bubble>Hi! I can look into balance, data, connection and value-added service issues. What's going on?</Bubble>
        <Bubble mine>I was charged Rs 99 for a service I never activated</Bubble>
        <Bubble>I found a daily Rs 99 charge for a horoscope service that started on 1 Oct. I can stop it for you.</Bubble>
        <div className="ml-6 rounded-2xl border bg-card p-2.5 shadow-sm">
          <p className="flex items-center gap-1.5 text-[11px] font-semibold">
            <ShieldCheck className="size-3.5 text-primary" /> Your confirmation is needed
          </p>
          <p className="mt-1.5 text-[9px] text-muted-foreground">Action</p>
          <p className="text-[11px] font-medium">Stop a subscription renewing</p>
          <div className="mt-2 flex gap-1.5">
            <span className="rounded-md bg-primary px-2 py-1 text-[10px] font-medium text-primary-foreground">Yes, go ahead</span>
            <span className="rounded-md border px-2 py-1 text-[10px] font-medium">No, leave it</span>
          </div>
        </div>
      </div>
      <div aria-hidden className="mx-3 mb-5 flex items-center gap-2 rounded-full border bg-card py-1.5 pr-1.5 pl-3">
        <span className="flex-1 text-[11px] text-muted-foreground">Type your message…</span>
        <span className="grid size-6 place-items-center rounded-full bg-primary text-primary-foreground">
          <Send className="size-3" />
        </span>
      </div>
    </>
  )
}

export function VoicePreview() {
  return (
    <>
      <MiniHeader
        right={<span className="rounded-full bg-success/12 px-2 py-0.5 text-[9px] font-semibold text-success">Listening</span>}
      />
      <div aria-hidden className="flex flex-1 flex-col gap-3 overflow-hidden px-3 py-3">
        <span className="flex items-center gap-1 text-[10px] font-medium text-muted-foreground">
          <ChevronLeft className="size-3" /> Back to chat
        </span>
        <div className="flex flex-1 flex-col items-center gap-3 rounded-3xl bg-muted/70 px-3 pt-5 pb-4 text-center">
          <p className="text-base font-bold">Talk to Resolve</p>
          <p className="-mt-2 text-[10px] text-muted-foreground">Your call continues this chat and uses the same case.</p>
          <div className="relative my-2 grid size-20 place-items-center">
            <span className="animate-splash-ring absolute inset-0 rounded-full border-2 border-primary/40" />
            <span className="animate-splash-ring absolute inset-0 rounded-full border-2 border-primary/25 [animation-delay:700ms]" />
            <span className="grid size-16 place-items-center rounded-full bg-card text-primary shadow-md">
              <Mic className="size-7" />
            </span>
          </div>
          <div className="flex h-5 items-center gap-[3px]">
            {[6, 12, 18, 10, 16, 8, 14, 6, 11].map((h, i) => (
              <span
                key={i}
                className="animate-typing-dot w-[3px] rounded-full bg-primary"
                style={{ height: h, animationDelay: `${i * 90}ms` }}
              />
            ))}
          </div>
          <div className="w-full space-y-2 text-left text-[11px] leading-snug">
            <p className="ml-auto w-fit max-w-[90%] rounded-2xl bg-primary px-3 py-1.5 text-primary-foreground">
              My data ran out but I still have a pack
            </p>
            <p className="w-fit max-w-[90%] rounded-2xl bg-card px-3 py-1.5">
              Your 5 GB pack is active until Friday. Let me check the connection on your line.
            </p>
          </div>
          <div className="mt-auto flex gap-2">
            <span className="flex items-center gap-1 rounded-lg border bg-background px-2.5 py-1.5 text-[10px] font-medium">
              <MicOff className="size-3" /> Mute
            </span>
            <span className="flex items-center gap-1 rounded-lg bg-destructive/10 px-2.5 py-1.5 text-[10px] font-medium text-destructive">
              <PhoneOff className="size-3" /> End call
            </span>
          </div>
        </div>
        <div className="h-3" />
      </div>
    </>
  )
}

const QUEUE = [
  { line: 'SIM-LK-0004', title: 'VAS dispute', status: 'Review required', tone: 'warning', tinted: true },
  { line: 'SIM-LK-0002', title: 'Reversal mismatch', status: 'Escalated', tone: 'destructive', tinted: true },
  { line: 'SIM-LK-0006', title: 'No signal in area', status: 'In review', tone: 'info', tinted: false },
  { line: 'SIM-LK-0001', title: 'Balance deduction', status: 'Closed', tone: 'neutral', tinted: false },
] as const

const TONE: Record<(typeof QUEUE)[number]['tone'], string> = {
  warning: 'bg-warning/20 text-[color-mix(in_oklch,var(--warning),black_45%)]',
  destructive: 'bg-destructive/10 text-destructive',
  info: 'bg-info/10 text-info',
  neutral: 'bg-muted text-muted-foreground',
}

export function DashboardPreview() {
  return (
    <div aria-hidden className="flex h-full flex-col text-[12px]">
      <div className="flex items-center justify-between border-b px-3 py-1.5">
        <span className="text-xs font-bold tracking-tight">
          HUTCH <span className="text-primary">Resolve</span>
          <span className="ml-2 font-medium text-muted-foreground">Agent</span>
        </span>
        <span className="flex items-center gap-2">
          <span className="flex items-center gap-1 rounded-md border px-2 py-0.5 text-muted-foreground">
            <Search className="size-3" /> Search cases
          </span>
          <span className="grid size-5 place-items-center rounded-full bg-primary/12 text-[9px] font-bold text-primary">AD</span>
        </span>
      </div>
      <div className="flex min-h-0 flex-1">
        <div className="flex w-[38%] flex-col gap-1.5 border-r bg-sidebar p-2">
          <p className="px-1 font-semibold">Queue <span className="font-normal text-muted-foreground">· 4 cases</span></p>
          {QUEUE.map((c, i) => (
            <div
              key={c.line}
              className={cn(
                'rounded-lg border bg-card p-1.5',
                c.tinted && 'border-primary/25 bg-accent',
                i === 0 && 'ring-2 ring-primary/40',
              )}
            >
              <p className="truncate font-semibold">{c.title}</p>
              <div className="mt-0.5 flex items-center justify-between gap-1">
                <span className="truncate font-mono text-[9px] text-muted-foreground">{c.line}</span>
                <span className={cn('shrink-0 rounded-full px-1.5 text-[8px] font-semibold', TONE[c.tone])}>{c.status}</span>
              </div>
            </div>
          ))}
        </div>
        <div className="flex min-w-0 flex-1 flex-col gap-2 p-2.5">
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0">
              <p className="truncate text-xs font-bold">VAS dispute · SIM-LK-0004</p>
              <p className="truncate text-muted-foreground">Customer disputes a daily horoscope charge</p>
            </div>
            <span className="shrink-0 rounded-full bg-warning/20 px-2 py-0.5 text-[9px] font-semibold text-[color-mix(in_oklch,var(--warning),black_45%)]">Review required</span>
          </div>
          <div className="flex gap-3 border-b text-muted-foreground">
            <span className="border-b-2 border-primary pb-1 font-semibold text-foreground">Overview</span>
            <span className="pb-1">Transcript</span>
            <span className="pb-1">Evidence</span>
            <span className="pb-1">Actions</span>
          </div>
          <div className="rounded-lg border bg-accent/60 p-2">
            <p className="font-semibold">Why this case is here</p>
            <p className="mt-0.5 text-muted-foreground">The reversal did not match the charges on the line, so a person needs to check it.</p>
          </div>
          <div className="grid grid-cols-2 gap-2">
            <div className="rounded-lg border p-2">
              <p className="text-muted-foreground">Ticket</p>
              <p className="flex items-center gap-1 font-semibold"><FileText className="size-3" /> Waiting on us</p>
            </div>
            <div className="rounded-lg border p-2">
              <p className="text-muted-foreground">Evidence</p>
              <p className="font-semibold">3 charges · 1 reversal</p>
            </div>
          </div>
          <div className="space-y-1.5 rounded-lg border p-2">
            <p className="font-semibold">Latest in the transcript</p>
            <p className="ml-auto w-fit max-w-[80%] rounded-xl rounded-br-sm bg-primary px-2.5 py-1 text-primary-foreground">I never signed up for this horoscope service</p>
            <p className="w-fit max-w-[80%] rounded-xl rounded-bl-sm bg-muted px-2.5 py-1">I’ve passed this to a person with your charges attached. You’ll get an update on the ticket.</p>
          </div>
          <div className="mt-auto flex items-center justify-between gap-2 rounded-lg border p-2">
            <span className="truncate text-muted-foreground">Review outcome and note</span>
            <span className="shrink-0 rounded-md bg-primary px-2 py-1 font-medium text-primary-foreground">Close review</span>
          </div>
        </div>
      </div>
    </div>
  )
}
