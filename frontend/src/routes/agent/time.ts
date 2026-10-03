import { useEffect, useState } from 'react'

const rtf = new Intl.RelativeTimeFormat('en', { numeric: 'auto' })

/** "just now", "4 min ago", "3 hr ago", then a date. Display only. */
export function relativeTime(iso: string, now = Date.now()) {
  const s = Math.round((Date.parse(iso) - now) / 1000)
  const a = Math.abs(s)
  if (a < 45) return 'just now'
  if (a < 3600) return rtf.format(Math.round(s / 60), 'minute').replace('minutes', 'min').replace('minute', 'min')
  if (a < 86400) return rtf.format(Math.round(s / 3600), 'hour').replace('hours', 'hr').replace('hour', 'hr')
  return rtf.format(Math.round(s / 86400), 'day')
}

/** Current time, refreshed every `ms`, for expiry checks that must not read the clock during render. */
export function useNow(ms: number) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), ms)
    return () => window.clearInterval(id)
  }, [ms])
  return now
}
