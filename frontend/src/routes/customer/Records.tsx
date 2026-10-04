import { useEffect, useRef, useState, type ReactNode } from 'react'
import { ChevronRight, FileText } from 'lucide-react'
import { useI18n } from '@/i18n/context'
import { formatTime } from '@/lib/format'
import { cn } from '@/lib/utils'

/** One receipt-like item from the chat (a check, finding, decision, progress or receipt), kept beside it. */
export type ChatRecord = { key: string; time: string; title: string; node: ReactNode }

/** Which record the customer asked to see; `n` changes on every request so the same record can be shown again. */
export type RecordFocus = { key: string; n: number }

/**
 * The chat's records, newest first. The newest is open; older ones fold to their title and time.
 * Records are rendered from the conversation exactly as the chat received them; nothing is recomputed.
 */
export function RecordsSection({ records, focus }: { records: ChatRecord[]; focus: RecordFocus | null }) {
  const { t } = useI18n()
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})
  const [flash, setFlash] = useState<string | null>(null)
  const items = useRef(new Map<string, HTMLLIElement>())
  const newest = records[0]?.key

  useEffect(() => {
    if (!focus) return
    setExpanded((current) => ({ ...current, [focus.key]: true }))
    setFlash(focus.key)
    const frame = requestAnimationFrame(() => items.current.get(focus.key)?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }))
    const timer = window.setTimeout(() => setFlash(null), 1600)
    return () => {
      cancelAnimationFrame(frame)
      window.clearTimeout(timer)
    }
  }, [focus])

  return (
    <section aria-label={t('panel.records')} className="flex flex-col gap-2">
      <h2 className="text-sm font-semibold">{t('panel.records')}</h2>
      {records.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t('panel.recordsEmpty')}</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {records.map((record) => {
            const open = expanded[record.key] ?? record.key === newest
            return (
              <li
                key={record.key}
                ref={(el) => {
                  if (el) items.current.set(record.key, el)
                  else items.current.delete(record.key)
                }}
                className={cn('rounded-2xl transition-shadow', flash === record.key && 'ring-2 ring-primary/50')}
              >
                <button
                  type="button"
                  aria-expanded={open}
                  onClick={() => setExpanded((current) => ({ ...current, [record.key]: !open }))}
                  className="flex w-full items-center gap-2 rounded-xl px-1 py-1.5 text-left text-sm hover:bg-muted"
                >
                  <ChevronRight aria-hidden className={cn('size-4 shrink-0 text-muted-foreground transition-transform', open && 'rotate-90')} />
                  <FileText aria-hidden className="size-3.5 shrink-0 text-muted-foreground" />
                  <span className="min-w-0 flex-1 truncate font-medium">{record.title}</span>
                  <time dateTime={record.time} className="shrink-0 text-xs text-muted-foreground">{formatTime(record.time)}</time>
                </button>
                {open && <div className="mt-1 text-sm">{record.node}</div>}
              </li>
            )
          })}
        </ul>
      )}
    </section>
  )
}

/** Links in the thread to the records a reply saved, so the conversation still says where they went. */
export function SavedRecordLinks({ records, onOpen }: { records: ChatRecord[]; onOpen: (key: string) => void }) {
  const { t } = useI18n()
  if (records.length === 0) return null
  return (
    <p className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
      <span>{t('chat.savedToRecords')}</span>
      {records.map((record) => (
        <button
          key={record.key}
          type="button"
          onClick={() => onOpen(record.key)}
          className="inline-flex items-center gap-1 rounded-full border bg-background px-2 py-0.5 text-foreground hover:bg-muted"
        >
          <FileText aria-hidden className="size-3" /> {record.title}
        </button>
      ))}
    </p>
  )
}
