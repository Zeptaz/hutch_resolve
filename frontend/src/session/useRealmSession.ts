import { useCallback, useEffect, useState } from 'react'
import { onUnauthorized, setCsrfToken, type Realm } from '@/api/client'
import { isApiError } from '@/api/errors'

type BaseSession = { id: string; expires_at: string; csrf_token: string }

export type SessionStatus = 'restoring' | 'signed-out' | 'active' | 'expired' | 'error'

export type RealmSession<S extends BaseSession> = {
  status: SessionStatus
  session: S | null
  error: unknown
  /** Run a sign-in style call (login, guest session) and adopt its session. */
  establish: (fn: () => Promise<S>) => Promise<void>
  logout: () => Promise<void>
  /** Retry restoring after a network/server error. */
  retry: () => void
}

/**
 * Session lifecycle for one realm (customer or agent): restore on load, track expiry,
 * keep the CSRF token in memory only, and flip to "expired" on any 401.
 */
export function useRealmSession<S extends BaseSession>(
  realm: Realm,
  restore: () => Promise<S>,
  logoutCall: () => Promise<void>,
): RealmSession<S> {
  const [status, setStatus] = useState<SessionStatus>('restoring')
  const [session, setSession] = useState<S | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [attempt, setAttempt] = useState(0)

  const adopt = useCallback(
    (s: S | null, next: SessionStatus) => {
      setCsrfToken(realm, s?.csrf_token ?? null)
      setSession(s)
      setStatus(next)
    },
    [realm],
  )

  // Restore an existing cookie session on load (and on retry). `restore` must be a stable function.
  useEffect(() => {
    let cancelled = false
    restore()
      .then((s) => !cancelled && adopt(s, 'active'))
      .catch((e) => {
        if (cancelled) return
        if (isApiError(e) && e.status === 401) {
          adopt(null, e.code === 'SESSION_EXPIRED' ? 'expired' : 'signed-out')
        } else {
          setError(e)
          setStatus('error')
        }
      })
    return () => {
      cancelled = true
    }
  }, [restore, adopt, attempt])

  // Any 401 elsewhere in this realm means the session is gone.
  useEffect(() => onUnauthorized(realm, () => adopt(null, 'expired')), [realm, adopt])

  // Expire locally at expires_at so the UI doesn't wait for the next failing request.
  useEffect(() => {
    if (status !== 'active' || !session) return
    const ms = Math.max(0, Date.parse(session.expires_at) - Date.now())
    const t = window.setTimeout(() => adopt(null, 'expired'), Math.min(ms, 2 ** 31 - 1))
    return () => window.clearTimeout(t)
  }, [status, session, adopt])

  const establish = useCallback(
    async (fn: () => Promise<S>) => {
      setError(null)
      try {
        adopt(await fn(), 'active')
      } catch (e) {
        setError(e)
        throw e
      }
    },
    [adopt],
  )

  const logout = useCallback(async () => {
    try {
      await logoutCall()
      adopt(null, 'signed-out')
      setError(null)
    } catch (e) {
      // A failed DELETE does not prove the cookie was revoked. Keep the local
      // credentials intact and expose a recoverable connection error instead
      // of pretending the user is signed out.
      setError(e)
      setStatus('error')
    }
  }, [logoutCall, adopt])

  const retry = useCallback(() => {
    setStatus('restoring')
    setAttempt((n) => n + 1)
  }, [])

  return { status, session, error, establish, logout, retry }
}
