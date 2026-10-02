import { useCallback, useMemo, useState } from 'react'
import { LogOut } from 'lucide-react'
import { Outlet, useMatch } from 'react-router'
import { BrandMark } from '@/components/BrandMark'
import { SimulationBanner } from '@/components/SimulationBanner'
import { ErrorState, LoadingState } from '@/components/states'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { useAgentSession } from '@/session/context'
import { AgentSessionProvider } from '@/session/providers'
import { AgentLogin } from './AgentLogin'
import { CaseQueue } from './CaseQueue'
import { QueueSignal } from './queueSignal'

export default function AgentPage() {
  return (
    <AgentSessionProvider>
      <div className="flex h-dvh flex-col">
        <SimulationBanner realm="agent" />
        <AgentGate />
      </div>
    </AgentSessionProvider>
  )
}

/** Protected area: nothing below renders without an AGENT session. Authorization itself is server-side. */
function AgentGate() {
  const { status, session, error, retry, logout } = useAgentSession()
  const [queueTick, setQueueTick] = useState(0)
  const refreshQueue = useCallback(() => setQueueTick((n) => n + 1), [])
  const signal = useMemo(() => ({ tick: queueTick, refreshQueue }), [queueTick, refreshQueue])
  // On phones the queue and the case take turns; from lg up they sit side by side.
  const caseOpen = useMatch('/agent/cases/:caseId') != null

  if (status === 'restoring') return <LoadingState label="Checking your session…" />
  if (status === 'error') return <ErrorState title="Could not reach Resolve" error={error} onRetry={retry} />
  if (status !== 'active' || !session) return <AgentLogin expired={status === 'expired'} />

  return (
    <QueueSignal.Provider value={signal}>
      <div className="flex min-h-0 flex-1 flex-col">
        <header className="flex items-center justify-between gap-3 border-b px-4 py-3">
          <BrandMark subtitle="Review dashboard" />
          <div className="flex items-center gap-2">
            <span className="hidden rounded-full bg-muted px-3 py-1 text-xs text-muted-foreground sm:inline">
              Signed in as <span className="font-semibold text-foreground">{session.principal_id}</span>
            </span>
            <Button variant="ghost" size="sm" onClick={() => void logout()}>
              <LogOut aria-hidden /> <span className="hidden sm:inline">Sign out</span>
            </Button>
          </div>
        </header>
        <div className="flex min-h-0 flex-1">
          <div className={cn('min-h-0 w-full flex-col lg:flex lg:w-[25rem] lg:shrink-0 lg:border-r', caseOpen ? 'hidden' : 'flex')}>
            <CaseQueue />
          </div>
          <section aria-label="Case" className={cn('relative min-h-0 flex-1 overflow-y-auto', caseOpen ? 'block' : 'hidden lg:block')}>
            <Outlet />
          </section>
        </div>
      </div>
    </QueueSignal.Provider>
  )
}
