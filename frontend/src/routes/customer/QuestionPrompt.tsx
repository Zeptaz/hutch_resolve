import { useState } from 'react'
import { Repeat, Signal, Smartphone, Wallet } from 'lucide-react'
import type { ComplaintType, PendingQuestion, ReportedFacts, TurnInput } from '@/api/types'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { useI18n } from '@/i18n/context'
import { CardFrame } from '@/components/CardFrame'

type CustomerComplaint = Exclude<ComplaintType, 'PACKAGE_ACTIVATION'>
const COMPLAINTS: CustomerComplaint[] = ['BALANCE_RECHARGE', 'DATA_DEPLETION', 'CONNECTIVITY', 'VAS_DISPUTE']

const COMPLAINT_ICON: Record<CustomerComplaint, React.ReactNode> = {
  BALANCE_RECHARGE: <Wallet />,
  DATA_DEPLETION: <Smartphone />,
  CONNECTIVITY: <Signal />,
  VAS_DISPUTE: <Repeat />,
}

const MAX_WINDOW_DAYS = 30

/** Structured answers for the server's pending question (the fallback when free text isn't enough). */
export function QuestionPrompt({
  question,
  disabled,
  onAnswer,
  defaultComplaint,
}: {
  question: PendingQuestion
  disabled: boolean
  onAnswer: (input: TurnInput, label: string) => void
  /** Category picked just before, used as the details form's starting value. */
  defaultComplaint?: ComplaintType | null
}) {
  const { t } = useI18n()
  const allowed = question.allowed_input_types
  if (allowed.includes('complaint_details')) {
    const initial = defaultComplaint && defaultComplaint !== 'PACKAGE_ACTIVATION' ? defaultComplaint : 'BALANCE_RECHARGE'
    return <DetailsForm key={initial} initialType={initial} disabled={disabled} onAnswer={onAnswer} />
  }
  if (allowed.includes('category_selection')) {
    return (
      <div role="group" aria-label={t('q.chooseCategory')} className="flex flex-wrap gap-2">
        {COMPLAINTS.map((c) => (
          <Button
            key={c}
            variant="outline"
            disabled={disabled}
            onClick={() => onAnswer({ type: 'category_selection', complaint_type: c }, t(`complaint.${c}`))}
            className="h-auto rounded-full border-transparent bg-muted/70 py-1.5 whitespace-normal hover:bg-muted"
          >
            <span aria-hidden className="[&_svg]:size-4">
              {COMPLAINT_ICON[c]}
            </span>
            {t(`complaint.${c}`)}
          </Button>
        ))}
      </div>
    )
  }
  return null
}

function toLocalInput(d: Date) {
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function DetailsForm({
  initialType,
  disabled,
  onAnswer,
}: {
  initialType: CustomerComplaint
  disabled: boolean
  onAnswer: (input: TurnInput, label: string) => void
}) {
  const { t } = useI18n()
  const [type, setType] = useState<CustomerComplaint>(initialType)
  const [from, setFrom] = useState(() => toLocalInput(new Date(Date.now() - 6 * 3600 * 1000)))
  const [to, setTo] = useState(() => toLocalInput(new Date()))
  const [amount, setAmount] = useState('')
  const [reference, setReference] = useState('')
  const [description, setDescription] = useState('')
  const [error, setError] = useState<string | null>(null)

  const submit = (e: React.FormEvent) => {
    e.preventDefault()
    const start = new Date(from)
    const end = new Date(to)
    if (!(start < end)) return setError(t('q.err.order'))
    if (end.getTime() - start.getTime() > MAX_WINDOW_DAYS * 86400 * 1000) return setError(t('q.err.window', { days: MAX_WINDOW_DAYS }))
    const facts: ReportedFacts = {
      amount_minor: null, recharge_reference: null, subscription_id: null, description: null,
      claimed_loss_minor: null, reported_balance_minor: null,
    }
    if (amount.trim()) {
      const lkr = Number(amount)
      if (!Number.isFinite(lkr) || lkr <= 0) return setError(t('q.err.amount'))
      facts.amount_minor = Math.round(lkr * 100) // what the customer reports, not evidence
    }
    if (reference.trim()) facts.recharge_reference = reference.trim()
    if (description.trim()) facts.description = description.trim()
    setError(null)
    onAnswer(
      { type: 'complaint_details', complaint_type: type, window_start: start.toISOString(), window_end: end.toISOString(), reported_facts: facts },
      `${t(`complaint.${type}`)}: ${from.replace('T', ' ')} → ${to.replace('T', ' ')}${amount ? `, LKR ${amount}` : ''}`,
    )
  }

  return (
    <CardFrame icon={<Wallet />} title={t('q.tellMore')}>
      <form onSubmit={submit} className="flex flex-col gap-3 [&_input]:bg-card [&_textarea]:bg-card [&_[data-slot=select-trigger]]:bg-card">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="q-type">{t('q.about')}</Label>
          <Select value={type} onValueChange={(v) => setType(v as CustomerComplaint)}>
            <SelectTrigger id="q-type" className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {COMPLAINTS.map((c) => (
                <SelectItem key={c} value={c}>
                  {t(`complaint.${c}`)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <fieldset className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <legend className="mb-1.5 text-sm font-medium">{t('q.when')}</legend>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="q-from" className="text-xs text-muted-foreground">
              {t('q.from')}
            </Label>
            <Input id="q-from" type="datetime-local" required value={from} onChange={(e) => setFrom(e.target.value)} />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="q-to" className="text-xs text-muted-foreground">
              {t('q.to')}
            </Label>
            <Input id="q-to" type="datetime-local" required value={to} onChange={(e) => setTo(e.target.value)} />
          </div>
        </fieldset>
        {type === 'BALANCE_RECHARGE' && (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="q-amount">{t('q.amount')}</Label>
              <Input
                id="q-amount"
                inputMode="decimal"
                placeholder={t('q.amountPlaceholder')}
                value={amount}
                onChange={(e) => setAmount(e.target.value)}
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="q-ref">{t('q.reference')}</Label>
              <Input id="q-ref" maxLength={128} value={reference} onChange={(e) => setReference(e.target.value)} />
            </div>
          </div>
        )}
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="q-desc">{t('q.anythingElse')}</Label>
          <Textarea id="q-desc" rows={2} maxLength={2000} value={description} onChange={(e) => setDescription(e.target.value)} />
        </div>
        {error && (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        )}
        <Button type="submit" disabled={disabled} className="h-auto w-fit py-2 whitespace-normal">
          {t('q.submit')}
        </Button>
      </form>
    </CardFrame>
  )
}
