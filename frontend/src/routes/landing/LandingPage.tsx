import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router'
import {
  Activity,
  ArrowRight,
  BadgeCheck,
  ChevronDown,
  ChevronRight,
  CircleCheck,
  FileText,
  Languages,
  MessageSquareText,
  Mic,
  PhoneCall,
  ReceiptText,
  SearchCheck,
  ShieldCheck,
  Signal,
  UserRoundCheck,
  Wallet,
  Wifi,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import { Bubble, ChatPreview, DashboardPreview, DesktopFrame, HeroScreen, PhoneFrame, VoicePreview } from './devices'
import { Splash } from './Splash'

/*
 * Landing page in the spirit of a product site: warm sand hero, gradient feature panels, a sand bento,
 * a dark block for the dashboard, an FAQ and an orange call to action. Every preview is a picture.
 */

const SAND = 'bg-[#f5f2ec]'
const PANEL = 'bg-[#f6f3ee]'
const INK = 'bg-[#111114]'

const NAV = [
  { href: '#chat', label: 'Chat' },
  { href: '#voice', label: 'Voice' },
  { href: '#dashboard', label: 'Dashboard' },
  { href: '#engine', label: 'How it works' },
  { href: '#faq', label: 'FAQ' },
]

export default function LandingPage() {
  return (
    <div className="min-h-dvh overflow-x-clip bg-white text-[#111114] [&_h1]:tracking-[-0.035em] [&_h2]:tracking-[-0.03em]">
      <Splash />
      <SiteHeader />
      <main>
        <Hero />
        <TopicStrip />
        <Engine />
        <Systems />
        <Bento />
        <DashboardBlock />
        <Faq />
        <CallToAction />
      </main>
      <SiteFooter />
    </div>
  )
}

/* ---------- shared bits ---------- */

function LogoMark({ className }: { className?: string }) {
  return (
    <span aria-hidden className={cn('grid size-7 place-items-center rounded-lg bg-primary text-primary-foreground shadow-sm', className)}>
      <MessageSquareText className="size-4" strokeWidth={2.4} />
    </span>
  )
}

function Wordmark({ className }: { className?: string }) {
  return (
    <span className={cn('flex items-center gap-2 text-lg font-semibold tracking-tight', className)}>
      <LogoMark />
      <span>
        HUTCH <span className="text-primary">Resolve</span>
      </span>
    </span>
  )
}

function Eyebrow({ children, className }: { children: React.ReactNode; className?: string }) {
  return <p className={cn('text-[11px] font-semibold tracking-[0.22em] text-neutral-500 uppercase', className)}>{children}</p>
}

function Tag({ children }: { children: React.ReactNode }) {
  return <span className="inline-block rounded-md border border-neutral-200 bg-neutral-50 px-2.5 py-1 text-xs font-medium text-neutral-600">{children}</span>
}

function PillLink({ to, children, tone = 'dark', className }: { to: string; children: React.ReactNode; tone?: 'dark' | 'soft' | 'white' | 'brand'; className?: string }) {
  return (
    <Link
      to={to}
      className={cn(
        'inline-flex h-11 items-center justify-center gap-2 rounded-full px-5 text-sm font-medium transition-all hover:-translate-y-0.5 focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none',
        tone === 'dark' && 'bg-[#111114] text-white hover:bg-black hover:shadow-lg',
        tone === 'soft' && 'bg-black/[0.06] text-[#111114] hover:bg-black/10',
        tone === 'white' && 'bg-white text-[#111114] shadow-sm hover:shadow-lg',
        tone === 'brand' && 'bg-primary text-primary-foreground hover:shadow-lg hover:shadow-primary/30',
        className,
      )}
    >
      {children}
    </Link>
  )
}

/** Fades content up the first time it scrolls into view. */
function Reveal({ children, className, delay = 0 }: { children: React.ReactNode; className?: string; delay?: number }) {
  const ref = useRef<HTMLDivElement>(null)
  const [shown, setShown] = useState(
    () => window.matchMedia('(prefers-reduced-motion: reduce)').matches || !('IntersectionObserver' in window),
  )
  useEffect(() => {
    const el = ref.current
    if (!el || shown) return
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setShown(true)
          observer.disconnect()
        }
      },
      { rootMargin: '0px 0px -10% 0px' },
    )
    observer.observe(el)
    return () => observer.disconnect()
  }, [shown])
  return (
    <div
      ref={ref}
      style={{ transitionDelay: `${delay}ms` }}
      className={cn('transition-all duration-700 ease-[cubic-bezier(0.2,0.8,0.2,1)]', shown ? 'translate-y-0 opacity-100' : 'translate-y-6 opacity-0', className)}
    >
      {children}
    </div>
  )
}

/* ---------- navbar ---------- */

function SiteHeader() {
  const [scrolled, setScrolled] = useState(false)
  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 8)
    onScroll()
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [])
  return (
    <header className={cn('sticky top-0 z-40 transition-colors duration-300', scrolled ? 'bg-white/85 shadow-[0_1px_0_rgb(0_0_0/0.06)] backdrop-blur-md' : SAND)}>
      <div className="mx-auto flex h-16 max-w-6xl items-center justify-between gap-4 px-4 sm:px-6">
        <a href="#top" aria-label="HUTCH Resolve, back to top">
          <Wordmark />
        </a>
        <nav aria-label="Sections" className="hidden items-center gap-7 md:flex">
          {NAV.map((n) => (
            <a key={n.href} href={n.href} className="text-sm text-neutral-600 transition-colors hover:text-primary">
              {n.label}
            </a>
          ))}
        </nav>
        <PillLink to="/chat" className="h-9 px-4">
          Try the chat
        </PillLink>
      </div>
    </header>
  )
}

/* ---------- hero ---------- */

function Hero() {
  return (
    <section id="top" className={cn('relative overflow-hidden', SAND)}>
      <div className="mx-auto grid max-w-6xl items-center gap-10 px-4 pt-10 pb-8 sm:px-6 lg:grid-cols-[1.05fr_1fr] lg:pt-16 lg:pb-4">
        <div className="max-w-xl">
          <span className="animate-rise-in inline-flex items-center gap-2 rounded-full bg-white px-3 py-1 text-xs font-medium text-neutral-600 shadow-[0_1px_2px_rgb(0_0_0/0.06)]">
            <span className="relative flex size-2">
              <span className="absolute inset-0 animate-ping rounded-full bg-primary/60" />
              <span className="relative size-2 rounded-full bg-primary" />
            </span>
            Chat · Voice · Agent dashboard
          </span>
          <h1 className="animate-rise-in mt-6 text-[2.6rem] leading-[1.05] font-medium text-balance [--i:1] sm:text-6xl">
            Fixing your prepaid line is now in your hand.
          </h1>
          <p className="animate-rise-in mt-5 max-w-md text-[15px] leading-relaxed text-neutral-600 [--i:2]">
            Say hello to support that checks your line before it answers, asks before it changes anything, and brings in a person when it should.
          </p>
          <div className="animate-rise-in mt-8 flex flex-wrap gap-3 [--i:3]">
            <PillLink to="/chat">Start a chat</PillLink>
            <PillLink to="/chat?call=1" tone="soft">
              <Mic className="size-4" aria-hidden /> Call Resolve
            </PillLink>
          </div>
        </div>

        <div className="relative mx-auto flex h-[30rem] w-full max-w-[34rem] items-end justify-center sm:h-[36rem]">
          {/* The orange sun behind the phone, fading out at the bottom. */}
          <div
            aria-hidden
            className="absolute top-6 left-1/2 aspect-square w-[min(30rem,92vw)] -translate-x-[52%] rounded-full bg-[radial-gradient(circle_at_35%_35%,#ff6a2b,#ec4100_45%,#f4a07a_70%,transparent_78%)] opacity-95 [mask-image:linear-gradient(to_bottom,black_55%,transparent_92%)]"
          />
          <div className="animate-float relative z-10 mb-[-4.5rem] rotate-[4deg]">
            <PhoneFrame label="A customer chat with Resolve on a phone" width="w-[15.5rem] sm:w-[17rem]">
              <HeroScreen />
            </PhoneFrame>
          </div>
          <FloatChip className="top-[34%] left-0 sm:left-2 [animation-delay:-2s]">
            <span className="grid size-7 place-items-center rounded-full bg-success/12 text-success"><CircleCheck className="size-4" /></span>
            <span><span className="block text-[11px] text-neutral-500">Daily charge</span>Stopped</span>
          </FloatChip>
          <FloatChip className="top-[10%] right-0 sm:right-4 [animation-delay:-4s]">
            <span className="grid size-7 place-items-center rounded-full bg-primary/12 text-primary"><Mic className="size-4" /></span>
            <span><span className="block text-[11px] text-neutral-500">Voice</span>Listening…</span>
          </FloatChip>
        </div>
      </div>
    </section>
  )
}

function FloatChip({ className, children }: { className?: string; children: React.ReactNode }) {
  return (
    <div aria-hidden className={cn('animate-float absolute z-20 flex items-center gap-2.5 rounded-2xl bg-white/95 py-2 pr-4 pl-2 text-sm font-semibold shadow-[0_12px_30px_-10px_rgb(0_0_0/0.25)] backdrop-blur', className)}>
      {children}
    </div>
  )
}

/* ---------- topic strip (in place of a logo wall) ---------- */

const TOPICS = [
  { icon: Wallet, label: 'Balance' },
  { icon: Wifi, label: 'Data packs' },
  { icon: Signal, label: 'Connection' },
  { icon: ReceiptText, label: 'Value-added services' },
  { icon: Languages, label: 'English' },
  { label: 'සිංහල' },
  { label: 'தமிழ்' },
  { icon: PhoneCall, label: 'Voice calls' },
  { icon: UserRoundCheck, label: 'Human review' },
]

function TopicStrip() {
  const row = (
    <ul className="flex shrink-0 items-center gap-12 pr-12">
      {TOPICS.map((t) => (
        <li key={t.label} className="flex items-center gap-2 text-xl font-semibold whitespace-nowrap text-neutral-400 sm:text-2xl">
          {t.icon && <t.icon className="size-5 sm:size-6" aria-hidden />}
          {t.label}
        </li>
      ))}
    </ul>
  )
  return (
    <section aria-label="What Resolve helps with" className={cn('relative z-10 border-b border-black/5 py-8', SAND)}>
      <div className="flex overflow-hidden [mask-image:linear-gradient(to_right,transparent,black_10%,black_90%,transparent)]">
        <div className="animate-marquee flex">
          {row}
          <div aria-hidden className="flex">{row}</div>
        </div>
      </div>
    </section>
  )
}

/* ---------- the engine: cycling list with a live panel ---------- */

const STEPS = [
  {
    icon: SearchCheck,
    title: 'Understands the issue',
    text: 'Balance, data, connection or value-added service problems, described in English, Sinhala or Tamil.',
  },
  {
    icon: BadgeCheck,
    title: 'Checks the line first',
    text: 'Looks at the line’s own records before it answers, and shows what it found instead of guessing.',
  },
  {
    icon: ShieldCheck,
    title: 'Asks before acting',
    text: 'Every change comes with its exact target and effect, and runs only after the customer says yes.',
  },
  {
    icon: UserRoundCheck,
    title: 'Hands over to a person',
    text: 'Anything it cannot settle safely goes to an agent, with the full case and a review ticket.',
  },
] as const

const STEP_MS = 4500

function Engine() {
  const [active, setActive] = useState(0)
  const [paused, setPaused] = useState(false)
  useEffect(() => {
    if (paused || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
    const id = window.setTimeout(() => setActive((a) => (a + 1) % STEPS.length), STEP_MS)
    return () => window.clearTimeout(id)
  }, [active, paused])

  return (
    <section id="engine" aria-labelledby="engine-title" className="scroll-mt-16 py-20 sm:py-28">
      <div className="mx-auto max-w-6xl px-4 sm:px-6">
        <Reveal className="text-center">
          <Eyebrow>The Resolve engine</Eyebrow>
          <h2 id="engine-title" className="mt-3 text-3xl font-medium sm:text-5xl">One careful engine behind every answer</h2>
        </Reveal>
        <div className="mt-12 grid items-stretch gap-8 lg:mt-16 lg:grid-cols-[2fr_3fr]" onMouseEnter={() => setPaused(true)} onMouseLeave={() => setPaused(false)}>
          <div role="tablist" aria-label="How Resolve works" aria-orientation="vertical" className="flex flex-col gap-2">
            {STEPS.map((s, i) => {
              const on = i === active
              return (
                <button
                  key={s.title}
                  role="tab"
                  id={`step-${i}`}
                  aria-selected={on}
                  aria-controls="step-panel"
                  onClick={() => setActive(i)}
                  onFocus={() => setPaused(true)}
                  onBlur={() => setPaused(false)}
                  className={cn(
                    'relative overflow-hidden rounded-r-xl border-l-2 px-5 py-4 text-left transition-colors duration-300 focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none',
                    on ? cn('border-primary', PANEL) : 'border-transparent hover:bg-neutral-50',
                  )}
                >
                  <span className="flex items-center gap-2.5 font-semibold">
                    <span className={cn('grid size-7 place-items-center rounded-lg transition-colors', on ? 'bg-primary text-primary-foreground' : 'bg-neutral-100 text-neutral-500')}>
                      <s.icon className="size-4" aria-hidden />
                    </span>
                    {s.title}
                  </span>
                  <span className={cn('grid transition-[grid-template-rows,opacity] duration-500', on ? 'grid-rows-[1fr] opacity-100' : 'grid-rows-[0fr] opacity-60 lg:grid-rows-[1fr]')}>
                    <span className="overflow-hidden">
                      <span className="block pt-2 pl-[2.375rem] text-sm leading-relaxed text-neutral-500">{s.text}</span>
                    </span>
                  </span>
                  {on && !paused && (
                    <span aria-hidden className="absolute bottom-0 left-0 h-0.5 bg-primary/40" style={{ animation: `splash-bar ${STEP_MS}ms linear both` }} />
                  )}
                </button>
              )
            })}
          </div>
          <div
            id="step-panel"
            role="tabpanel"
            aria-labelledby={`step-${active}`}
            className={cn('relative grid min-h-[22rem] place-items-center overflow-hidden rounded-3xl p-6 sm:p-10', PANEL)}
          >
            <div aria-hidden className="absolute inset-0 bg-[repeating-linear-gradient(135deg,rgb(0_0_0/0.025)_0_1px,transparent_1px_14px)]" />
            <div key={active} className="animate-rise-in relative w-full max-w-sm">
              <StepScene step={active} />
            </div>
          </div>
        </div>
      </div>
    </section>
  )
}

function SceneCard({ children, className }: { children: React.ReactNode; className?: string }) {
  return <div className={cn('rounded-2xl bg-white p-4 text-left text-sm shadow-[0_10px_30px_-12px_rgb(0_0_0/0.18)]', className)}>{children}</div>
}

function StepScene({ step }: { step: number }) {
  if (step === 0)
    return (
      <div aria-hidden className="flex flex-col gap-3">
        <SceneCard className="flex flex-col gap-2">
          <Bubble mine>මගේ data pack එක ඉවරයි කියනවා</Bubble>
          <Bubble mine>என் balance குறைந்துவிட்டது</Bubble>
          <Bubble mine>My calls keep dropping at home</Bubble>
        </SceneCard>
        <SceneCard className="ml-8 flex flex-wrap gap-1.5">
          {['Data', 'Balance', 'Connection'].map((t) => (
            <span key={t} className="rounded-full bg-primary/10 px-2.5 py-1 text-xs font-semibold text-primary">{t}</span>
          ))}
          <span className="rounded-full bg-neutral-100 px-2.5 py-1 text-xs font-medium text-neutral-500">සි · த · EN</span>
        </SceneCard>
      </div>
    )
  if (step === 1)
    return (
      <SceneCard className="space-y-3">
        <p className="flex items-center gap-2 font-semibold"><FileText className="size-4 text-primary" aria-hidden /> What Resolve found</p>
        {[
          ['1 Oct', 'Horoscope service', '− Rs 99'],
          ['2 Oct', 'Horoscope service', '− Rs 99'],
          ['3 Oct', 'Horoscope service', '− Rs 99'],
        ].map(([d, w, a]) => (
          <div key={d} className="flex items-center justify-between rounded-lg bg-neutral-50 px-3 py-2 text-xs">
            <span className="text-neutral-500">{d}</span>
            <span className="flex-1 px-3 font-medium">{w}</span>
            <span className="font-mono">{a}</span>
          </div>
        ))}
        <p className="text-xs text-neutral-500">From the line’s simulated charge records.</p>
      </SceneCard>
    )
  if (step === 2)
    return (
      <SceneCard className="space-y-3">
        <p className="flex items-center gap-2 font-semibold"><ShieldCheck className="size-4 text-primary" aria-hidden /> Your confirmation is needed</p>
        <dl className="space-y-2 text-xs">
          <div><dt className="text-neutral-500">Action</dt><dd className="font-medium">Stop a subscription renewing</dd></div>
          <div><dt className="text-neutral-500">Applies to</dt><dd>Horoscope daily · SIM-LK-0004</dd></div>
          <div><dt className="text-neutral-500">What it means</dt><dd>No more daily Rs 99 charges for this service.</dd></div>
        </dl>
        <div className="flex gap-2">
          <span className="rounded-lg bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground">Yes, go ahead</span>
          <span className="rounded-lg border px-3 py-1.5 text-xs font-medium">No, leave it</span>
        </div>
      </SceneCard>
    )
  return (
    <div aria-hidden className="flex flex-col gap-3">
      <SceneCard className="flex items-center gap-3">
        <span className="grid size-9 place-items-center rounded-full bg-warning/25 text-[color-mix(in_oklch,var(--warning),black_45%)]"><UserRoundCheck className="size-4" /></span>
        <span className="flex-1">
          <span className="block font-semibold">Human review requested</span>
          <span className="text-xs text-neutral-500">The reversal did not match the charges.</span>
        </span>
      </SceneCard>
      <SceneCard className="ml-8 space-y-1.5">
        <p className="text-xs text-neutral-500">Review ticket</p>
        <p className="font-semibold">Waiting on us</p>
        <p className="text-xs text-neutral-500">Transcript, evidence and actions attached</p>
      </SceneCard>
    </div>
  )
}

/* ---------- chat and voice rows ---------- */

function Systems() {
  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-24 px-4 pb-24 sm:gap-32 sm:px-6 sm:pb-32">
      <FeatureRow
        id="chat"
        tag="Customer chat"
        title="Support that checks before it answers"
        text="Customers describe the problem in their own words. Resolve looks at their line, explains what it found and offers a fix they can accept or decline, with live status for every action."
        cta={{ to: '/chat', label: 'Open the chat' }}
        gradient="from-[#ff7a45] via-[#ffc6ad] to-white"
        device={<PhoneFrame label="Preview of the customer chat on a phone" width="w-[15rem] sm:w-[16.5rem]"><ChatPreview /></PhoneFrame>}
      />
      <FeatureRow
        id="voice"
        tag="Voice agent"
        title="Say it out loud, keep the same case"
        text="A call picks up where the chat left off. Customers speak, read live captions, and answer offers by voice or with a tap. Sign in to a demo line when asked."
        cta={{ to: '/chat?call=1', label: 'Start a voice call' }}
        gradient="from-[#7c6cf2] via-[#c8c0fb] to-white"
        device={<PhoneFrame label="Preview of a voice call on a phone" width="w-[15rem] sm:w-[16.5rem]"><VoicePreview /></PhoneFrame>}
        flip
      />
    </div>
  )
}

function FeatureRow({
  id,
  tag,
  title,
  text,
  cta,
  gradient,
  device,
  flip,
}: {
  id: string
  tag: string
  title: string
  text: string
  cta: { to: string; label: string }
  gradient: string
  device: React.ReactNode
  flip?: boolean
}) {
  return (
    <section id={id} aria-labelledby={`${id}-title`} className="grid scroll-mt-20 items-center gap-10 md:grid-cols-2 md:gap-16">
      <Reveal className={cn('min-w-0', flip && 'md:order-2')}>
        <div className={cn('relative mx-auto h-[26rem] max-w-md overflow-hidden rounded-[2rem] bg-gradient-to-b pt-12 sm:h-[30rem]', gradient)}>
          <div aria-hidden className="absolute inset-0 bg-[repeating-linear-gradient(135deg,rgb(255_255_255/0.12)_0_1px,transparent_1px_16px)]" />
          <div className="relative">{device}</div>
          <div aria-hidden className="absolute inset-x-0 bottom-0 h-28 bg-gradient-to-t from-white to-transparent" />
        </div>
      </Reveal>
      <Reveal delay={120} className={cn('min-w-0 max-w-md', flip && 'md:order-1 md:justify-self-end')}>
        <Tag>{tag}</Tag>
        <h2 id={`${id}-title`} className="mt-4 text-3xl leading-tight font-medium sm:text-4xl">{title}</h2>
        <p className="mt-4 text-[15px] leading-relaxed text-neutral-600">{text}</p>
        <Link to={cta.to} className="group mt-6 inline-flex items-center gap-1 text-sm font-semibold text-primary">
          {cta.label}
          <ChevronRight className="size-4 transition-transform group-hover:translate-x-1" aria-hidden />
        </Link>
      </Reveal>
    </section>
  )
}

/* ---------- bento ---------- */

function Bento() {
  return (
    <section aria-labelledby="bento-title" className="pb-24 sm:pb-32">
      <div className="mx-auto max-w-6xl px-4 sm:px-6">
        <Reveal className="text-center">
          <Eyebrow>Built in</Eyebrow>
          <h2 id="bento-title" className="mt-3 text-3xl font-medium sm:text-5xl">Everything a safe fix needs</h2>
        </Reveal>
        <div className="mt-12 grid gap-4 md:grid-cols-5">
          <BentoPanel className="md:col-span-2" caption="Nothing changes until the customer says yes.">
            <SceneCard className="w-full max-w-[15rem] space-y-2 text-xs">
              <p className="flex items-center gap-1.5 font-semibold"><ShieldCheck className="size-3.5 text-primary" /> Confirm an action</p>
              <p className="font-medium">Restore a data pack</p>
              <p className="text-neutral-500">Valid until 14:32 · 4:51 left</p>
              <div className="flex gap-1.5 pt-1">
                <span className="rounded-md bg-primary px-2 py-1 font-medium text-primary-foreground">Yes, go ahead</span>
                <span className="rounded-md border px-2 py-1 font-medium">No</span>
              </div>
            </SceneCard>
          </BentoPanel>
          <BentoPanel className="md:col-span-3" caption="Every action is tracked until it has a clear result.">
            <div className="grid w-full max-w-md gap-2.5 sm:grid-cols-2">
              {[
                ['Stopping subscription renewal', 'Succeeded', 'bg-success/12 text-success'],
                ['Reversing a charge', 'Pending', 'bg-info/10 text-info'],
                ['Restoring a data pack', 'Succeeded', 'bg-success/12 text-success'],
                ['Opening a review ticket', 'Review required', 'bg-warning/25 text-[color-mix(in_oklch,var(--warning),black_45%)]'],
              ].map(([label, status, tone], i) => (
                <SceneCard key={label} className={cn('flex items-center gap-2 p-3 text-xs', i > 1 && 'hidden sm:flex')}>
                  <Activity className="size-4 shrink-0 text-neutral-400" />
                  <span className="flex-1 font-medium">{label}</span>
                  <span className={cn('shrink-0 rounded-full px-1.5 text-[10px] font-semibold', tone)}>{status}</span>
                </SceneCard>
              ))}
            </div>
          </BentoPanel>
          <BentoPanel className="md:col-span-3" caption="Customers switch language at any time, mid-conversation.">
            <div className="flex w-full max-w-md flex-col items-center gap-3">
              <span className="flex rounded-full bg-white p-1 text-xs font-semibold shadow-sm">
                <span className="rounded-full px-3 py-1 text-neutral-500">EN</span>
                <span className="rounded-full bg-primary px-3 py-1 text-primary-foreground">සි</span>
                <span className="rounded-full px-3 py-1 text-neutral-500">த</span>
              </span>
              <SceneCard className="text-xs leading-relaxed">ආයුබෝවන්! ඔබගේ පෙරගෙවුම් අංකයේ ශේෂය, දත්ත, සම්බන්ධතාව සහ අගය එකතු කළ සේවා පිළිබඳ ගැටලු මට සොයා බැලිය හැක. මොකක්ද වෙලා තියෙන්නේ?</SceneCard>
            </div>
          </BentoPanel>
          <BentoPanel className="md:col-span-2" caption="Answers point to the records behind them.">
            <SceneCard className="w-full max-w-[15rem] space-y-2 text-xs">
              <p className="flex items-center gap-1.5 font-semibold"><ReceiptText className="size-3.5 text-primary" /> Sources</p>
              {['Charge history · 3 entries', 'Active packs · 5 GB data', 'Service terms · VAS'].map((s) => (
                <p key={s} className="rounded-md bg-neutral-50 px-2 py-1.5 text-neutral-600">{s}</p>
              ))}
            </SceneCard>
          </BentoPanel>
        </div>
      </div>
    </section>
  )
}

function BentoPanel({ className, caption, children }: { className?: string; caption: string; children: React.ReactNode }) {
  return (
    <Reveal className={cn('min-w-0', className)}>
      <figure className={cn('flex h-full flex-col items-center gap-6 rounded-3xl p-6 pb-7 text-center sm:p-8', PANEL)}>
        <div aria-hidden className="flex min-h-40 w-full flex-1 items-center justify-center">{children}</div>
        <figcaption className="max-w-xs text-sm text-neutral-600">{caption}</figcaption>
      </figure>
    </Reveal>
  )
}

/* ---------- dashboard: dark block with a computer bleeding off the bottom ---------- */

function DashboardBlock() {
  return (
    <section id="dashboard" aria-labelledby="dashboard-title" className="scroll-mt-20 px-4 pb-24 sm:px-6 sm:pb-32">
      <Reveal>
        <div className={cn('relative mx-auto max-w-6xl overflow-hidden rounded-[2rem] px-4 pt-14 text-center text-white sm:px-10 sm:pt-20', INK)}>
          <div
            aria-hidden
            className="absolute inset-0 bg-[repeating-conic-gradient(from_0deg_at_50%_105%,rgb(255_255_255/0.07)_0deg_0.4deg,transparent_0.4deg_5deg)] [mask-image:radial-gradient(ellipse_at_50%_100%,black_20%,transparent_70%)]"
          />
          <div aria-hidden className="absolute bottom-0 left-1/2 h-64 w-[40rem] -translate-x-1/2 rounded-full bg-primary/25 blur-[100px]" />
          <div className="relative">
            <Eyebrow className="text-white/50">Agent dashboard</Eyebrow>
            <h2 id="dashboard-title" className="mx-auto mt-3 max-w-2xl text-3xl leading-tight font-medium sm:text-5xl">
              Every hand-over arrives with the full story
            </h2>
            <p className="mx-auto mt-4 max-w-xl text-[15px] leading-relaxed text-white/60">
              Agents see the cases that need a person first, with the reason, the transcript, the evidence and every action Resolve took. Their outcome and note go back to the ticket.
            </p>
            <PillLink to="/agent" tone="brand" className="mt-8">
              Open the dashboard <ArrowRight className="size-4" aria-hidden />
            </PillLink>
            <ul className="mt-6 flex flex-wrap justify-center gap-2 text-xs text-white/70">
              {['Attention-first queue', 'Transcript and evidence', 'Review notes synced to tickets', 'j / k to move'].map((t) => (
                <li key={t} className="rounded-full border border-white/15 px-3 py-1">{t}</li>
              ))}
            </ul>
          </div>
          <div className="relative mx-auto mt-12 -mb-[12%] max-w-4xl sm:mt-16">
            <DesktopFrame label="Preview of the agent dashboard on a computer">
              <DashboardPreview />
            </DesktopFrame>
          </div>
        </div>
      </Reveal>
    </section>
  )
}

/* ---------- FAQ ---------- */

const FAQS = [
  {
    q: 'Is any of this real?',
    a: 'No. Every line, balance, charge and action is simulated for the demo. Nothing touches a real account, and the app says so on every screen.',
  },
  {
    q: 'Do I need an account to try it?',
    a: 'No. The chat starts as a guest for general questions. To look at a line or start a voice call, sign in with one of the demo lines when the chat asks.',
  },
  {
    q: 'Which languages does it speak?',
    a: 'English, Sinhala and Tamil. Switch with the EN | සි | த toggle at any time; the conversation carries on in the new language.',
  },
  {
    q: 'What happens when Resolve cannot fix something?',
    a: 'It offers a human review. If the customer agrees, the case goes to the agent dashboard with the transcript, the evidence and a review ticket.',
  },
  {
    q: 'How does a voice call relate to the chat?',
    a: 'A call continues the same conversation and case. Offers are read aloud, and the customer can answer by voice or with a tap.',
  },
]

function Faq() {
  const [open, setOpen] = useState(0)
  return (
    <section id="faq" aria-labelledby="faq-title" className="scroll-mt-20 pb-24 sm:pb-32">
      <div className="mx-auto grid max-w-6xl gap-10 px-4 sm:px-6 lg:grid-cols-[2fr_3fr] lg:gap-16">
        <Reveal>
          <Eyebrow>Frequent questions</Eyebrow>
          <h2 id="faq-title" className="mt-3 text-4xl leading-[1.1] font-medium sm:text-5xl">
            Got questions?
            <br />
            We’ve got answers.
          </h2>
          <p className="mt-4 max-w-sm text-[15px] text-neutral-600">Everything you need to know before you try the chat, the call or the dashboard.</p>
        </Reveal>
        <Reveal delay={120} className="flex flex-col gap-2.5">
          {FAQS.map((f, i) => {
            const on = open === i
            return (
              <div key={f.q} className={cn('overflow-hidden rounded-2xl transition-colors duration-300', on ? 'bg-[#111114] text-white' : PANEL)}>
                <h3>
                  <button
                    id={`faq-q-${i}`}
                    aria-expanded={on}
                    aria-controls={`faq-a-${i}`}
                    onClick={() => setOpen(on ? -1 : i)}
                    className="flex w-full items-center justify-between gap-4 px-5 py-4 text-left text-sm font-medium focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none"
                  >
                    {f.q}
                    <span className={cn('grid size-7 shrink-0 place-items-center rounded-full transition-all duration-300', on ? 'rotate-180 bg-white text-[#111114]' : 'bg-[#111114] text-white')}>
                      <ChevronDown className="size-4" aria-hidden />
                    </span>
                  </button>
                </h3>
                <div
                  id={`faq-a-${i}`}
                  role="region"
                  aria-labelledby={`faq-q-${i}`}
                  className={cn('grid transition-[grid-template-rows] duration-400 ease-out', on ? 'grid-rows-[1fr]' : 'grid-rows-[0fr]')}
                >
                  <div className="overflow-hidden">
                    <p className="px-5 pb-5 text-sm leading-relaxed text-white/70">{f.a}</p>
                  </div>
                </div>
              </div>
            )
          })}
        </Reveal>
      </div>
    </section>
  )
}

/* ---------- call to action ---------- */

function CallToAction() {
  return (
    <section aria-labelledby="cta-title" className="px-4 pb-20 sm:px-6">
      <Reveal>
        <div className="relative mx-auto max-w-6xl overflow-hidden rounded-[2rem] bg-primary px-6 py-16 text-center text-white sm:py-24">
          <div aria-hidden className="absolute inset-0 bg-[repeating-linear-gradient(135deg,rgb(255_255_255/0.07)_0_2px,transparent_2px_16px)]" />
          <TiltCard className="-left-6 top-14 -rotate-[14deg]">
            <p className="text-xs text-neutral-500">Daily charge</p>
            <p className="mt-1 flex items-center gap-1.5 font-semibold"><CircleCheck className="size-4 text-success" /> Stopped</p>
          </TiltCard>
          <TiltCard className="-right-6 bottom-12 rotate-[12deg]">
            <p className="text-xs text-neutral-500">Voice</p>
            <p className="mt-1 flex items-center gap-1.5 font-semibold"><Mic className="size-4 text-primary" /> Listening…</p>
          </TiltCard>
          <div className="relative">
            <Eyebrow className="text-white/70">Try it now</Eyebrow>
            <h2 id="cta-title" className="mx-auto mt-3 max-w-2xl text-4xl leading-[1.08] font-medium sm:text-6xl">
              Type it. Say it. Get it resolved.
            </h2>
            <p className="mx-auto mt-4 max-w-md text-[15px] text-white/85">Start as a guest in the chat, or call Resolve with a demo line.</p>
            <div className="mt-8 flex flex-wrap justify-center gap-3">
              <PillLink to="/chat" tone="white">
                <MessageSquareText className="size-4" aria-hidden /> Start a chat
              </PillLink>
              <PillLink to="/chat?call=1" tone="white">
                <Mic className="size-4" aria-hidden /> Call Resolve
              </PillLink>
            </div>
          </div>
        </div>
      </Reveal>
    </section>
  )
}

function TiltCard({ className, children }: { className?: string; children: React.ReactNode }) {
  return (
    <div aria-hidden className={cn('absolute hidden w-44 rounded-2xl bg-white p-4 text-left text-sm text-[#111114] shadow-xl md:block', className)}>
      {children}
    </div>
  )
}

/* ---------- footer ---------- */

const FOOTER = [
  {
    title: 'Systems',
    links: [
      { to: '/chat', label: 'Customer chat' },
      { to: '/chat?call=1', label: 'Voice call' },
      { to: '/agent', label: 'Agent dashboard' },
    ],
  },
  {
    title: 'Explore',
    links: [
      { href: '#engine', label: 'How it works' },
      { href: '#dashboard', label: 'For agents' },
      { href: '#faq', label: 'FAQ' },
    ],
  },
  {
    title: 'Languages',
    links: [{ label: 'English' }, { label: 'සිංහල' }, { label: 'தமிழ்' }],
  },
] as const

function SiteFooter() {
  return (
    <footer className="border-t border-black/5">
      <div className="mx-auto grid max-w-6xl gap-10 px-4 py-14 sm:px-6 md:grid-cols-[1.4fr_2fr]">
        <div>
          <Wordmark />
          <p className="mt-4 max-w-xs text-sm text-neutral-500">Prepaid support for chat, voice and agents, powered by one Resolve engine.</p>
        </div>
        <div className="grid grid-cols-2 gap-8 sm:grid-cols-3">
          {FOOTER.map((col) => (
            <div key={col.title}>
              <p className="text-sm font-semibold">{col.title}</p>
              <ul className="mt-4 space-y-2.5 text-sm text-neutral-500">
                {col.links.map((l) => (
                  <li key={l.label}>
                    {'to' in l ? (
                      <Link to={l.to} className="transition-colors hover:text-primary">{l.label}</Link>
                    ) : 'href' in l ? (
                      <a href={l.href} className="transition-colors hover:text-primary">{l.label}</a>
                    ) : (
                      l.label
                    )}
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      </div>
      <div className="border-t border-black/5">
        <div className="mx-auto flex max-w-6xl flex-col gap-2 px-4 py-6 text-xs text-neutral-500 sm:flex-row sm:items-center sm:justify-between sm:px-6">
          <span>HUTCH Resolve · demo build</span>
          <span>Every line, balance and action here is simulated.</span>
        </div>
      </div>
    </footer>
  )
}
