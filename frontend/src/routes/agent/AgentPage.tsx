import { LogOut } from 'lucide-react'
import { Outlet } from 'react-router'
import { BrandMark } from '@/components/BrandMark'
import { SimulationBanner } from '@/components/SimulationBanner'
import { ErrorState, LoadingState } from '@/components/states'
import { Button } from '@/components/ui/button'
import { useAgentSession } from '@/session/context'
import { AgentSessionProvider } from '@/session/providers'
import { AgentLogin } from './AgentLogin'
import { CaseQueue } from './CaseQueue'

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

  if (status === 'restoring') return <LoadingState label="Checking your session…" />
  if (status === 'error') return <ErrorState title="Could not reach Resolve" error={error} onRetry={retry} />
  if (status !== 'active' || !session) return <AgentLogin expired={status === 'expired'} />

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <header className="flex items-center justify-between gap-3 border-b px-4 py-3">
        <BrandMark subtitle="Review dashboard" />
        <div className="flex items-center gap-3 text-sm">
          <span className="hidden text-muted-foreground sm:inline">
            Signed in as <span className="font-medium text-foreground">{session.principal_id}</span>
          </span>
          <Button variant="outline" size="sm" onClick={() => void logout()}>
            <LogOut aria-hidden /> Sign out
          </Button>
        </div>
      </header>
      <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
        <CaseQueue />
        <section aria-label="Case detail" className="relative min-h-0 flex-1 overflow-y-auto">
          <Outlet />
        </section>
      </div>
    </div>
  )
}
