import { makeTranslate, type Translate } from '@/i18n/context'
import type { MessageKey } from '@/i18n/messages'
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

const STATUS_KEY: Record<number, MessageKey> = {
  0: 'error.network',
  401: 'error.401',
  403: 'error.403',
  404: 'error.404',
  409: 'error.409',
  429: 'error.429',
  503: 'error.503',
}

/**
 * Short, user-facing explanation. Never exposes request internals beyond the reference ID.
 * Pass `t` for translated text; without it (agent dashboard) other errors show the server's message.
 */
export function describeError(e: unknown, t?: Translate): string {
  const tr = t ?? makeTranslate('en')
  if (!isApiError(e)) return tr('error.generic')
  const key = STATUS_KEY[e.status]
  if (key) return tr(key)
  return (!t && e.message) || tr('error.generic')
}
