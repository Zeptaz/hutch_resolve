import type { ApiErrorBody } from './types'

export type ErrorCode = ApiErrorBody['error']['code'] | 'NETWORK_ERROR' | 'UNEXPECTED_RESPONSE'

/** Error raised for any non-2xx response, using the contract's error envelope. */
export class ApiError extends Error {
  readonly status: number
  readonly code: ErrorCode
  readonly retryable: boolean
  readonly requestId: string | null
  readonly details: Record<string, unknown>

  constructor(status: number, body: Partial<ApiErrorBody['error']> & { code: ErrorCode }) {
    super(body.message ?? body.code)
    this.name = 'ApiError'
    this.status = status
    this.code = body.code
    this.retryable = body.retryable ?? false
    this.requestId = body.request_id ?? null
    this.details = (body.details as Record<string, unknown> | undefined) ?? {}
  }

  /** 401: the session is missing or expired; the user must sign in / start again. */
  get needsReauth() {
    return this.status === 401
  }
}

export function isApiError(e: unknown): e is ApiError {
  return e instanceof ApiError
}

/** Short, user-facing explanation. Never exposes request internals beyond the reference ID. */
export function describeError(e: unknown): string {
  if (!isApiError(e)) return 'Something went wrong. Please try again.'
  switch (e.status) {
    case 401:
      return 'Your session has ended. Please start again.'
    case 403:
      return 'You do not have access to this.'
    case 404:
      return 'We could not find that item.'
    case 409:
      return 'This was changed elsewhere. Refresh to see the latest version.'
    case 429:
      return 'Too many requests. Please wait a moment and try again.'
    case 503:
      return 'A service is temporarily unavailable. Please try again shortly.'
    case 0:
      return 'Could not reach the server. Check your connection and try again.'
    default:
      return e.message || 'Something went wrong. Please try again.'
  }
}
