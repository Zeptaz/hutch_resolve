import { API_MODE } from '@/api/client'
import type { VoiceSessionGrant } from './contracts'

export const VOICE_PROTOCOL = 'zeptaz-hutch-v2'

/** The part of WebSocket the call uses, so the mock can stand in for it. */
export type VoiceSocket = {
  binaryType: BinaryType
  readonly readyState: number
  onopen: (() => void) | null
  onmessage: ((e: { data: string | ArrayBuffer }) => void) | null
  onclose: ((e: { code: number; reason: string }) => void) | null
  onerror: (() => void) | null
  send: (data: string | ArrayBuffer) => void
  close: (code?: number, reason?: string) => void
}

/**
 * Connect straight to Voice with the single-use grant from Resolve. The grant travels as a WebSocket
 * subprotocol, never in the URL, and Voice answers with only `zeptaz-hutch-v2`.
 */
export async function openVoiceSocket(grant: VoiceSessionGrant): Promise<VoiceSocket> {
  if (API_MODE === 'mock') return (await import('./mockSocket')).openMockSocket(grant.websocket_url)
  const ws = new WebSocket(grant.websocket_url, [VOICE_PROTOCOL, `hutch-grant.${grant.browser_grant}`])
  ws.binaryType = 'arraybuffer'
  return ws as unknown as VoiceSocket
}
