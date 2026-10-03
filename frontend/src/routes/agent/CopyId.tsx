import { useState } from 'react'
import { Copy } from 'lucide-react'
import { cn } from '@/lib/utils'

/** Short ID with a copy button; the full value is in the title and the clipboard. */
export function CopyId({ label, value }: { label: string; value: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <button
      type="button"
      onClick={() => {
        void navigator.clipboard?.writeText(value).then(() => {
          setCopied(true)
          window.setTimeout(() => setCopied(false), 1500)
        })
      }}
      className="inline-flex items-center gap-1 rounded font-mono hover:text-foreground"
      title={`Copy ${label.toLowerCase()} ${value}`}
      aria-label={`Copy ${label.toLowerCase()}`}
    >
      {value.slice(0, 8)}…
      <Copy className="size-3" aria-hidden />
      <span aria-live="polite" className={cn('font-sans', !copied && 'sr-only')}>
        {copied ? 'Copied' : ''}
      </span>
    </button>
  )
}
