import { API_MODE } from '@/api/client'
import type { VoiceSessionGrant } from './contracts'

/** Newest first. v4 adds `decision_recorded`, so Voice speaks the reply to an offer answered on screen. */
export const VOICE_PROTOCOLS = ['zeptaz-hutch-v4', 'zeptaz-hutch-v3'] as const
export const DECISION_SPEECH_PROTOCOL = 'zeptaz-hutch-v4'

/** The part of WebSocket the call uses, so the mock can stand in for it. */
export type VoiceSocket = {
  binaryType: BinaryType
  readonly readyState: number
  readonly protocol?: string
  onopen: (() => void) | null
  onmessage: ((e: { data: string | ArrayBuffer }) => void) | null
  onclose: ((e: { code: number; reason: string }) => void) | null
  onerror: (() => void) | null
  send: (data: string | ArrayBuffer) => void
  close: (code?: number, reason?: string) => void
}

/**
 * Connect straight to Voice with the single-use grant from Resolve. The grant travels as a WebSocket
 * subprotocol, never in the URL, and Voice answers with v4 (or v3 from an older Voice service).
 */
export async function openVoiceSocket(grant: VoiceSessionGrant): Promise<VoiceSocket> {
  if (API_MODE === 'mock') return (await import('./mockSocket')).openMockSocket(grant.websocket_url)
  const ws = new WebSocket(grant.websocket_url, [...VOICE_PROTOCOLS, `hutch-grant.${grant.browser_grant}`])
  ws.binaryType = 'arraybuffer'
  return ws as unknown as VoiceSocket
}
