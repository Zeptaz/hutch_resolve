import { Bot } from 'lucide-react'
import { useI18n } from '@/i18n/context'
import { formatTime } from '@/lib/format'
import { cn } from '@/lib/utils'

/** A chat bubble: the customer's on the right in brand orange, Resolve's on the left in grey with the bot avatar. */
export function Bubble({
  speaker,
  time,
  animate,
  pending,
  children,
}: {
  speaker: 'USER' | 'ASSISTANT'
  time?: string
  /** Rise in from the speaker's corner (new messages only). */
  animate?: boolean
  /** The customer's message while it is still being sent. */
  pending?: boolean
  children: React.ReactNode
}) {
  const { t } = useI18n()
  const mine = speaker === 'USER'
  return (
    <div
      className={cn(
        'flex max-w-[85%] gap-2',
        mine ? 'ml-auto origin-bottom-right flex-row-reverse' : 'mr-auto origin-bottom-left',
        animate && 'animate-bubble-in',
      )}
    >
      {!mine && <BotAvatar />}
      <div className={cn('flex flex-col gap-1', mine && 'items-end')}>
        <span className="sr-only">{mine ? t('chat.youSaid') : t('chat.assistantSaid')}</span>
        <div
          className={cn(
            'rounded-2xl px-4 py-2.5 text-sm leading-relaxed whitespace-pre-wrap transition-opacity',
            mine ? 'rounded-br-md bg-primary text-primary-foreground' : 'rounded-bl-md bg-muted',
            pending && 'opacity-80',
          )}
        >
          {children}
        </div>
        {pending ? (
          <span className="text-[11px] text-muted-foreground">{t('chat.sending')}</span>
        ) : (
          time && <time className="text-[11px] text-muted-foreground">{formatTime(time)}</time>
        )}
      </div>
    </div>
  )
}

export function BotAvatar({ thinking }: { thinking?: boolean }) {
  return (
    <span
      aria-hidden
      className={cn(
        'mt-1 grid size-7 shrink-0 place-items-center rounded-full bg-accent text-accent-foreground',
        thinking && 'animate-thinking-ring',
      )}
    >
      <Bot className="size-4" />
    </span>
  )
}
