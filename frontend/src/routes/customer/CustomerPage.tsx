import { useEffect, useRef } from 'react'
import { customerApi } from '@/api/endpoints'
import { BrandMark } from '@/components/BrandMark'
import { SimulationBanner } from '@/components/SimulationBanner'
import { ErrorState, ExpiredState, LoadingState } from '@/components/states'
import { CustomerSessionProvider } from '@/session/providers'
import { useCustomerSession } from '@/session/context'
import { ChatShell } from './ChatShell'

export default function CustomerPage() {
  return (
    <CustomerSessionProvider>
      <div className="flex h-dvh flex-col">
        <SimulationBanner realm="customer" />
        <CustomerSessionGate />
      </div>
    </CustomerSessionProvider>
  )
}

/**
 * The chat has no sign-in page: a first visit starts a GUEST session (public FAQ scope).
 * Account-specific help needs a CUSTOMER session — how the chat upgrades is still a team decision.
 */
function CustomerSessionGate() {
  const { status, session, error, establish, retry } = useCustomerSession()
  const startedGuest = useRef(false)

  useEffect(() => {
    if (status === 'signed-out' && !startedGuest.current) {
      startedGuest.current = true
      establish(customerApi.createGuestSession).catch(() => {
        /* error is exposed via session.error */
      })
    }
  }, [status, establish])

  const startOver = () => {
    startedGuest.current = true
    establish(customerApi.createGuestSession).catch(() => {})
  }

  if (status === 'active' && session) return <ChatShell session={session} />

  return (
    <div className="flex flex-1 flex-col">
      <header className="border-b px-4 py-3">
        <BrandMark subtitle="Customer support" />
      </header>
      <main className="mx-auto w-full max-w-md flex-1 pt-10">
        {status === 'expired' ? (
          <ExpiredState
            message="For your security, chats end after 30 minutes of inactivity."
            actionLabel="Start a new chat"
            onAction={startOver}
          />
        ) : error ? (
          <ErrorState title="Could not start the chat" error={error} onRetry={status === 'error' ? retry : startOver} />
        ) : (
          <LoadingState label="Starting chat…" rows={2} />
        )}
      </main>
    </div>
  )
}
