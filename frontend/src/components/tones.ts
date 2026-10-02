import type { DeliveryState, EvidenceState, OperationStatus, ReviewStatus } from '@/api/types'

export type Tone = 'neutral' | 'info' | 'success' | 'warning' | 'danger'

// Each state family maps to tones explicitly so "pending" never looks like "success".

export const evidenceTone: Record<EvidenceState, Tone> = { SUFFICIENT: 'success', PARTIAL: 'warning', CONFLICTING: 'danger' }
export const reviewTone: Record<ReviewStatus, Tone> = { NEW: 'info', IN_REVIEW: 'warning', CLOSED: 'neutral' }
export const deliveryTone: Record<DeliveryState, Tone> = {
  PENDING: 'warning',
  DELIVERED: 'success',
  FAILED: 'danger',
  REVIEW_REQUIRED: 'danger',
}
export const operationTone: Record<OperationStatus, Tone> = {
  PENDING: 'warning',
  RUNNING: 'info',
  SUCCEEDED: 'success',
  FAILED: 'danger',
  UNKNOWN: 'warning',
  REVIEW_REQUIRED: 'danger',
}
