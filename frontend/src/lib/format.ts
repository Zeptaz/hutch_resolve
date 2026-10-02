// Display-only formatting. Never use these to derive authoritative amounts or outcomes.

const COLOMBO = 'Asia/Colombo'

const dateTime = new Intl.DateTimeFormat('en-LK', {
  timeZone: COLOMBO,
  dateStyle: 'medium',
  timeStyle: 'short',
})

const time = new Intl.DateTimeFormat('en-LK', { timeZone: COLOMBO, timeStyle: 'short' })

/** RFC3339 UTC → local Sri Lanka time, as the contract requires for display. */
export function formatDateTime(iso: string) {
  return dateTime.format(new Date(iso))
}

export function formatTime(iso: string) {
  return time.format(new Date(iso))
}

/** Signed integer minor LKR units (100 = LKR 1.00) → "LKR 1,000.00". */
export function formatLkr(minor: number) {
  const sign = minor < 0 ? '−' : ''
  const abs = Math.abs(minor)
  return `${sign}LKR ${(abs / 100).toLocaleString('en-LK', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

/** Integer bytes → decimal GB, per the contract. */
export function formatGb(bytes: number) {
  return `${(bytes / 1e9).toLocaleString('en-LK', { maximumFractionDigits: 2 })} GB`
}

/** "BALANCE_RECHARGE" → "Balance recharge" for enum values without a dedicated label. */
export function humanize(value: string) {
  const s = value.replace(/_/g, ' ').toLowerCase()
  return s.charAt(0).toUpperCase() + s.slice(1)
}

export function shortId(id: string) {
  return id.slice(-8)
}
