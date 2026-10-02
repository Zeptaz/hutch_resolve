import { ApiError } from './errors'

export const API_BASE = '/api/v1'
export const API_MODE: 'mock' | 'live' = import.meta.env.VITE_API_MODE === 'live' ? 'live' : 'mock'

/** Customer and agent sessions use separate cookies and CSRF tokens (docs/contracts.md). */
export type Realm = 'customer' | 'agent'

type Method = 'GET' | 'POST' | 'PATCH' | 'DELETE'

export type RequestOptions = {
  body?: unknown
  query?: Record<string, string | number | undefined>
  /** Required by the contract for most mutations; reuse the same key when retrying the same request. */
  idempotencyKey?: string
  signal?: AbortSignal
}

export type RawResponse = { status: number; body: unknown }

type Transport = (realm: Realm, method: Method, path: string, opts: RequestOptions) => Promise<RawResponse>

const liveTransport: Transport = async (_realm, method, path, opts) => {
  const headers: Record<string, string> = { Accept: 'application/json' }
  if (opts.body !== undefined) headers['Content-Type'] = 'application/json'
  if (opts.idempotencyKey) headers['Idempotency-Key'] = opts.idempotencyKey
  const csrf = method === 'GET' ? null : csrfTokens[_realm]
  if (csrf) headers['X-CSRF-Token'] = csrf

  let res: Response
  try {
    res = await fetch(API_BASE + path + toQuery(opts.query), {
      method,
      headers,
      body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
      credentials: 'same-origin',
      signal: opts.signal,
    })
  } catch (e) {
    if ((e as Error).name === 'AbortError') throw e
    return { status: 0, body: null }
  }
  const text = await res.text()
  let body: unknown = null
  if (text) {
    try {
      body = JSON.parse(text)
    } catch {
      body = null
    }
  }
  return { status: res.status, body }
}

let mockTransport: Transport | null = null
async function getTransport(): Promise<Transport> {
  if (API_MODE === 'live') return liveTransport
  // Loaded lazily so contract fixtures never ship in a live build's main bundle.
  mockTransport ??= (await import('./mock')).mockTransport
  return mockTransport
}

const csrfTokens: Record<Realm, string | null> = { customer: null, agent: null }
const unauthorizedListeners: Record<Realm, Set<() => void>> = { customer: new Set(), agent: new Set() }

export function setCsrfToken(realm: Realm, token: string | null) {
  csrfTokens[realm] = token
}

/** Notified whenever a request in this realm returns 401, so the session UI can show "expired". */
export function onUnauthorized(realm: Realm, fn: () => void) {
  unauthorizedListeners[realm].add(fn)
  return () => {
    unauthorizedListeners[realm].delete(fn)
  }
}

function toQuery(query: RequestOptions['query']) {
  if (!query) return ''
  const params = new URLSearchParams()
  for (const [k, v] of Object.entries(query)) if (v !== undefined && v !== '') params.set(k, String(v))
  const s = params.toString()
  return s ? `?${s}` : ''
}

export async function request<T>(realm: Realm, method: Method, path: string, opts: RequestOptions = {}): Promise<T> {
  const transport = await getTransport()
  const { status, body } = await transport(realm, method, path, opts)

  if (status >= 200 && status < 300) return body as T

  if (status === 0) throw new ApiError(0, { code: 'NETWORK_ERROR', message: 'Network error', retryable: true })

  const envelope = (body as { error?: ConstructorParameters<typeof ApiError>[1] } | null)?.error
  const err = new ApiError(status, envelope ?? { code: 'UNEXPECTED_RESPONSE', message: `HTTP ${status}` })
  // A failed restore (GET /session) is expected on first visit, and a refused sign-in (POST …/sessions)
  // is a wrong credential, not an expiry; neither is broadcast as "session ended".
  if (status === 401 && !path.endsWith('/session') && !path.endsWith('/sessions')) unauthorizedListeners[realm].forEach((fn) => fn())
  throw err
}

export function newId(): string {
  return crypto.randomUUID()
}
