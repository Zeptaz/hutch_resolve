import { Link } from 'react-router'
import {
  ArrowRight,
  BadgeCheck,
  Check,
  FlaskConical,
  Languages,
  LayoutDashboard,
  MessageCircle,
  Mic,
  SearchCheck,
  ShieldCheck,
  UserRoundCheck,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { ChatPreview, DashboardPreview, DesktopFrame, PhoneFrame, VoicePreview } from './devices'
import { Splash } from './Splash'

const SYSTEMS = [
  { id: 'chat', name: 'Chat', icon: MessageCircle },
  { id: 'voice', name: 'Voice', icon: Mic },
  { id: 'dashboard', name: 'Dashboard', icon: LayoutDashboard },
] as const

const STEPS = [
  { icon: SearchCheck, title: 'Understands the issue', text: 'Balance, data, connection or value-added service problems, in English, Sinhala or Tamil.' },
  { icon: BadgeCheck, title: 'Checks the line', text: 'Looks at the account evidence for the line before it answers, instead of guessing.' },
  { icon: ShieldCheck, title: 'Asks before acting', text: 'Every change is shown with its target and effect, and runs only after the customer says yes.' },
  { icon: UserRoundCheck, title: 'Hands over to a person', text: 'Anything it cannot settle safely goes to an agent with the full case and a review ticket.' },
] as const

export default function LandingPage() {
  return (
    <div className="min-h-dvh overflow-x-clip bg-background">
      <Splash />
      <SiteHeader />
      <main>
        <Hero />
        <HowItWorks />
        <SystemSection
          id="chat"
          eyebrow="Customer chat"
          icon={MessageCircle}
          title="Support that checks before it answers"
          text="Customers describe the problem in their own words. Resolve looks at their line, explains what it found and offers a fix they can accept or decline."
          points={[
            'Answers grounded in the line’s own records',
            'Confirmation cards with the exact action and its effect',
            'Live action status and a case panel for every issue',
            'English, Sinhala and Tamil',
          ]}
          cta={{ to: '/chat', label: 'Open the chat' }}
          device={<PhoneFrame label="Preview of the customer chat on a phone"><ChatPreview /></PhoneFrame>}
        />
        <SystemSection
          id="voice"
          eyebrow="Voice agent"
          icon={Mic}
          title="Say it out loud, keep the same case"
          text="A call picks up the conversation where the chat left it. Customers speak, hear the reply and answer offers by voice or with a tap."
          points={[
            'Live captions for both sides of the call',
            'Offers are read aloud before anything changes',
            'Mute, interrupt or end the call at any time',
            'The call and the chat share one case',
          ]}
          note="Needs a demo line: sign in from the chat when asked."
          cta={{ to: '/chat?call=1', label: 'Start a voice call' }}
          device={<PhoneFrame label="Preview of a voice call on a phone"><VoicePreview /></PhoneFrame>}
          flip
        />
        <SystemSection
          id="dashboard"
          eyebrow="Agent dashboard"
          icon={LayoutDashboard}
          title="Every hand-over, with the full story"
          text="Agents see the cases that need a person first, with the reason, the transcript, the evidence and every action Resolve took."
          points={[
            'A queue that puts cases needing attention first',
            'Transcript, evidence and actions in one view',
            'Review outcomes and notes synced to the ticket',
            'Keyboard shortcuts to move through the queue',
          ]}
          cta={{ to: '/agent', label: 'Open the dashboard' }}
          device={<DesktopFrame label="Preview of the agent dashboard on a computer"><DashboardPreview /></DesktopFrame>}
          wide
        />
      </main>
      <SiteFooter />
    </div>
  )
}

function SiteHeader() {
  return (
    <header className="sticky top-0 z-30 border-b border-border/60 bg-background/80 backdrop-blur-md">
      <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-4 py-3 sm:px-6">
        <a href="#top" className="text-base font-bold tracking-tight whitespace-nowrap">
          HUTCH <span className="text-primary">Resolve</span>
        </a>
        <nav aria-label="Systems" className="hidden items-center gap-1 sm:flex">
          {SYSTEMS.map((s) => (
            <a key={s.id} href={`#${s.id}`} className="rounded-lg px-3 py-1.5 text-sm font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground">
              {s.name}
            </a>
          ))}
        </nav>
        <Button asChild size="sm">
          <Link to="/chat">Try the chat</Link>
        </Button>
      </div>
    </header>
  )
}

function Hero() {
  return (
    <section id="top" className="relative isolate scroll-mt-20">
      <div aria-hidden className="absolute inset-x-0 -top-24 -z-10 h-[34rem] bg-[radial-gradient(46rem_26rem_at_50%_0%,color-mix(in_oklch,var(--primary)_16%,transparent),transparent)]" />
      <div aria-hidden className="absolute inset-0 -z-10 bg-[linear-gradient(to_right,var(--border)_1px,transparent_1px),linear-gradient(to_bottom,var(--border)_1px,transparent_1px)] [mask-image:radial-gradient(ellipse_at_top,black_20%,transparent_65%)] bg-[size:44px_44px] opacity-60" />
      <div className="mx-auto flex max-w-4xl flex-col items-center px-4 pt-16 pb-14 text-center sm:px-6 sm:pt-24 sm:pb-20">
        <span className="animate-rise-in inline-flex items-center gap-1.5 rounded-full border bg-card px-3 py-1 text-xs font-medium text-muted-foreground shadow-xs">
          <span className="size-1.5 rounded-full bg-primary" /> One engine for chat, voice and agents
        </span>
        <h1 className="animate-rise-in mt-6 text-4xl font-bold tracking-tight text-balance [--i:1] sm:text-6xl">
          Prepaid issues, <span className="text-primary">resolved</span> with care.
        </h1>
        <p className="animate-rise-in mt-5 max-w-2xl text-base text-pretty text-muted-foreground [--i:2] sm:text-lg">
          HUTCH Resolve listens to the customer, checks their line, and fixes what it safely can once the customer agrees.
          The rest goes to a person, with the whole case attached.
        </p>
        <div className="animate-rise-in mt-8 flex flex-wrap justify-center gap-3 [--i:3]">
          {SYSTEMS.map((s, i) => (
            <a
              key={s.id}
              href={`#${s.id}`}
              className={cn(
                'group inline-flex items-center gap-2 rounded-xl border px-4 py-2.5 text-sm font-semibold shadow-xs transition-all hover:-translate-y-0.5 hover:shadow-md',
                i === 0 ? 'border-primary bg-primary text-primary-foreground' : 'bg-card hover:border-primary/40',
              )}
            >
              <s.icon className="size-4" aria-hidden />
              {s.name}
              <ArrowRight className="size-4 transition-transform group-hover:translate-x-0.5" aria-hidden />
            </a>
          ))}
        </div>
      </div>
    </section>
  )
}

function HowItWorks() {
  return (
    <section aria-labelledby="how-title" className="border-y bg-muted/40">
      <div className="mx-auto max-w-6xl px-4 py-14 sm:px-6 sm:py-16">
        <p className="text-sm font-semibold text-primary">The Resolve engine</p>
        <h2 id="how-title" className="mt-1 max-w-xl text-2xl font-bold tracking-tight sm:text-3xl">
          The same careful steps, whichever way the customer reaches out.
        </h2>
        <ol className="mt-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {STEPS.map((step, i) => (
            <li key={step.title} className="relative rounded-2xl border bg-card p-5 shadow-xs">
              <span className="absolute top-5 right-5 font-mono text-xs text-muted-foreground">0{i + 1}</span>
              <span className="grid size-10 place-items-center rounded-xl bg-accent text-accent-foreground">
                <step.icon className="size-5" aria-hidden />
              </span>
              <h3 className="mt-4 font-semibold">{step.title}</h3>
              <p className="mt-1 text-sm text-muted-foreground">{step.text}</p>
            </li>
          ))}
        </ol>
      </div>
    </section>
  )
}

function SystemSection({
  id,
  eyebrow,
  icon: Icon,
  title,
  text,
  points,
  note,
  cta,
  device,
  flip,
  wide,
}: {
  id: string
  eyebrow: string
  icon: typeof MessageCircle
  title: string
  text: string
  points: string[]
  note?: string
  cta: { to: string; label: string }
  device: React.ReactNode
  /** Device on the left on large screens. */
  flip?: boolean
  /** A computer preview: give the device more room than the text. */
  wide?: boolean
}) {
  return (
    <section id={id} aria-labelledby={`${id}-title`} className="scroll-mt-16">
      <div
        className={cn(
          'mx-auto grid max-w-6xl items-center gap-12 px-4 py-16 sm:px-6 sm:py-24 lg:gap-16',
          wide ? 'lg:grid-cols-[2fr_3fr]' : 'lg:grid-cols-2',
        )}
      >
        <div className={cn('max-w-xl min-w-0', flip && 'lg:order-2')}>
          <span className="inline-flex items-center gap-2 rounded-full bg-accent px-3 py-1 text-xs font-semibold text-accent-foreground">
            <Icon className="size-3.5" aria-hidden /> {eyebrow}
          </span>
          <h2 id={`${id}-title`} className="mt-4 text-3xl font-bold tracking-tight text-balance sm:text-4xl">
            {title}
          </h2>
          <p className="mt-4 text-pretty text-muted-foreground sm:text-lg">{text}</p>
          <ul className="mt-6 space-y-2.5">
            {points.map((p) => (
              <li key={p} className="flex gap-2.5 text-sm">
                <span className="mt-0.5 grid size-5 shrink-0 place-items-center rounded-full bg-primary/12 text-primary">
                  <Check className="size-3" aria-hidden />
                </span>
                {p}
              </li>
            ))}
          </ul>
          <div className="mt-8 flex flex-wrap items-center gap-x-4 gap-y-2">
            <Button asChild className="h-11 rounded-xl px-5 text-base shadow-sm">
              <Link to={cta.to}>
                {cta.label} <ArrowRight aria-hidden />
              </Link>
            </Button>
            {note && <p className="text-xs text-muted-foreground">{note}</p>}
          </div>
        </div>
        <div className={cn('relative min-w-0', flip && 'lg:order-1')}>
          <div aria-hidden className="absolute inset-[8%] -z-10 rounded-full bg-primary/15 blur-3xl" />
          <div className={cn(!wide && 'animate-float')}>{device}</div>
        </div>
      </div>
    </section>
  )
}

function SiteFooter() {
  return (
    <footer className="border-t bg-muted/40">
      <div className="mx-auto flex max-w-6xl flex-col gap-4 px-4 py-8 text-sm text-muted-foreground sm:flex-row sm:items-center sm:justify-between sm:px-6">
        <span className="font-bold tracking-tight text-foreground">
          HUTCH <span className="text-primary">Resolve</span>
        </span>
        <span className="flex items-center gap-1.5">
          <FlaskConical className="size-4 shrink-0" aria-hidden />
          A demo: every line, balance and action here is simulated.
        </span>
        <span className="flex items-center gap-1.5">
          <Languages className="size-4 shrink-0" aria-hidden /> English · සිංහල · தமிழ்
        </span>
      </div>
    </footer>
  )
}
