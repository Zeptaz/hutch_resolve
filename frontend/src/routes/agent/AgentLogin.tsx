import { useState } from 'react'
import { API_MODE } from '@/api/client'
import { agentApi } from '@/api/endpoints'
import { describeError } from '@/api/errors'
import { BrandMark } from '@/components/BrandMark'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
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
    <main className="grid flex-1 place-items-center bg-muted/40 p-4">
      <Card className="w-full max-w-sm">
        <CardHeader className="gap-4">
          <BrandMark subtitle="Review dashboard" />
          <div>
            <CardTitle>Agent sign in</CardTitle>
            <CardDescription>Use the demo agent identity configured for this environment.</CardDescription>
          </div>
        </CardHeader>
        <CardContent>
          <form onSubmit={submit} className="flex flex-col gap-4">
            {expired && (
              <p role="status" className="rounded-md bg-warning px-3 py-2 text-sm text-warning-foreground">
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
              />
            </div>
            {error != null && (
              <p role="alert" className="text-sm text-destructive">
                {describeError(error)}
              </p>
            )}
            <Button type="submit" size="lg" disabled={submitting}>
              {submitting ? 'Signing in…' : 'Sign in'}
            </Button>
            {API_MODE === 'mock' && (
              <p className="text-xs text-muted-foreground">Mock mode: any identity and credential are accepted.</p>
            )}
          </form>
        </CardContent>
      </Card>
    </main>
  )
}
