import type { ComplaintType } from '@/api/types'

/** Customer-facing names for complaint types. */
export const COMPLAINT_LABEL: Record<ComplaintType, string> = {
  BALANCE_RECHARGE: 'Balance or recharge',
  DATA_DEPLETION: 'Data ran out',
  CONNECTIVITY: 'No signal or connection',
  VAS_DISPUTE: 'Unknown subscription charge',
  PACKAGE_ACTIVATION: 'Package purchase',
}
