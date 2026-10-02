import { useState } from 'react'
import { ShieldCheck } from 'lucide-react'
import { API_MODE } from '@/api/client'
import { agentApi } from '@/api/endpoints'
import { describeError } from '@/api/errors'
import { BrandMark } from '@/components/BrandMark'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { useAgentSession } from '@/session/context'

export function AgentLogin({ expired }: { expired: boolean }) {
  const { establish } = useAgentSession()
  const [identity, setIdentity] = useState('')
  const [credential, setCredential] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<unknown>(null)

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setSubmitting(true)
    setError(null)
    try {
      await establish(() => agentApi.login({ demo_identity: identity.trim(), credential }))
    } catch (err) {
      setError(err)
      setCredential('')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="flex flex-1 flex-col overflow-y-auto">
      <header className="border-b px-4 py-3">
        <BrandMark subtitle="Review dashboard" />
      </header>
      <main className="flex flex-1 flex-col items-center px-4 pt-10 pb-10">
        <div className="flex max-w-md flex-col items-center gap-3 text-center">
          <h1 className="text-3xl font-bold tracking-tight text-balance">
            Cases that need a <span className="text-primary">person</span>
          </h1>
          <p className="text-sm leading-relaxed text-balance text-muted-foreground">
            Review evidence Resolve could not settle on its own, record what you found and keep the customer's ticket in step.
          </p>
        </div>
        <section className="mt-8 w-full max-w-sm rounded-3xl bg-muted/70 px-5 py-7 sm:px-7">
          <form onSubmit={submit} className="flex flex-col gap-4">
            <div className="flex items-center gap-2 text-sm font-semibold">
              <ShieldCheck aria-hidden className="size-4 text-muted-foreground" /> Agent sign in
            </div>
            {expired && (
              <p role="status" className="rounded-lg bg-warning px-3 py-2 text-sm text-warning-foreground">
                Your session ended. Sign in again to continue.
              </p>
            )}
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="identity">Demo identity</Label>
              <Input
                id="identity"
                autoComplete="username"
                required
                maxLength={128}
                value={identity}
                onChange={(e) => setIdentity(e.target.value)}
                className="bg-card"
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="credential">Credential</Label>
              <Input
                id="credential"
                type="password"
                autoComplete="current-password"
                required
                maxLength={256}
                value={credential}
                onChange={(e) => setCredential(e.target.value)}
                className="bg-card"
              />
            </div>
            {error != null && (
              <p role="alert" className="text-sm text-destructive">
                {describeError(error)}
              </p>
            )}
            <Button type="submit" size="lg" disabled={submitting || !identity.trim() || !credential}>
              {submitting ? 'Signing in…' : 'Sign in'}
            </Button>
            {API_MODE === 'mock' && <p className="text-xs text-muted-foreground">Mock mode: any identity and credential are accepted.</p>}
          </form>
        </section>
        <p className="mt-6 max-w-sm text-center text-xs text-muted-foreground">
          Agent access is limited to this simulation run. Notes you write are internal and never shown to customers.
        </p>
      </main>
    </div>
  )
}
