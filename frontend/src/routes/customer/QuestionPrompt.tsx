import { useEffect, useState } from 'react'
import { Repeat, Signal, Smartphone, Wallet } from 'lucide-react'
import { customerApi } from '@/api/endpoints'
import type { CaseView, ComplaintType, PendingQuestion, ReportedFacts, TurnInput } from '@/api/types'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { COMPLAINT_LABEL } from '@/lib/labels'
import { CardFrame } from './cards/ChatCards'

const COMPLAINT_ICON: Record<ComplaintType, React.ReactNode> = {
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
  activeCaseId,
}: {
  question: PendingQuestion
  disabled: boolean
  onAnswer: (input: TurnInput, label: string) => void
  /** Category picked just before, used as the details form's starting value. */
  defaultComplaint?: ComplaintType | null
  activeCaseId?: string | null
}) {
  const allowed = question.allowed_input_types
  if (question.code === 'CHOOSE_ACTION' && activeCaseId) {
    return <ActionChoice caseId={activeCaseId} disabled={disabled} onAnswer={onAnswer} />
  }
  if (allowed.includes('complaint_details')) {
    const initial = defaultComplaint ?? 'BALANCE_RECHARGE'
    return <DetailsForm key={initial} initialType={initial} disabled={disabled} onAnswer={onAnswer} />
  }
  if (allowed.includes('category_selection')) {
    return (
      <div role="group" aria-label="Choose a category" className="flex flex-wrap gap-2">
        {(Object.keys(COMPLAINT_LABEL) as ComplaintType[]).map((t) => (
          <Button
            key={t}
            variant="outline"
            disabled={disabled}
            onClick={() => onAnswer({ type: 'category_selection', complaint_type: t }, COMPLAINT_LABEL[t])}
            className="rounded-full"
          >
            <span aria-hidden className="[&_svg]:size-4">
              {COMPLAINT_ICON[t]}
            </span>
            {COMPLAINT_LABEL[t]}
          </Button>
        ))}
      </div>
    )
  }
  return null
}

const ACTION_CHOICE_LABEL: Record<string, string> = {
  DEACTIVATE_VAS: 'Stop future renewals',
  SEND_SETTINGS_INSTRUCTIONS: 'Send settings instructions',
  CREATE_REVIEW_TICKET: 'Send to the review team',
}

/**
 * Options when Resolve made more than one action eligible. The contract has no structured
 * "choose action" input yet, so each option sends its wording as text; choosing only requests
 * an offer — the customer still confirms it on the confirmation card.
 */
function ActionChoice({
  caseId,
  disabled,
  onAnswer,
}: {
  caseId: string
  disabled: boolean
  onAnswer: (input: TurnInput, label: string) => void
}) {
  const [caseView, setCaseView] = useState<CaseView | null>(null)
  useEffect(() => {
    let cancelled = false
    customerApi
      .getCase(caseId)
      .then((c) => !cancelled && setCaseView(c))
      .catch(() => {
        /* fall back to typing an answer */
      })
    return () => {
      cancelled = true
    }
  }, [caseId])

  const actions = caseView?.investigation?.eligible_actions ?? []
  if (actions.length === 0) return null
  return (
    <div role="group" aria-label="Choose what to do" className="flex flex-wrap gap-2">
      {actions.map((a) => {
        const text = `${ACTION_CHOICE_LABEL[a.action_type] ?? a.action_type} (${a.target_label})`
        return (
          <Button
            key={`${a.action_type}-${a.target_id}`}
            variant="outline"
            disabled={disabled}
            onClick={() => onAnswer({ type: 'text', text }, text)}
            className="h-auto rounded-full py-1.5 whitespace-normal"
          >
            {text}
          </Button>
        )
      })}
    </div>
  )
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
  initialType: ComplaintType
  disabled: boolean
  onAnswer: (input: TurnInput, label: string) => void
}) {
  const [type, setType] = useState<ComplaintType>(initialType)
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
    if (!(start < end)) return setError('The start time must be before the end time.')
    if (end.getTime() - start.getTime() > MAX_WINDOW_DAYS * 86400 * 1000) return setError(`Please choose a period of ${MAX_WINDOW_DAYS} days or less.`)
    const facts: ReportedFacts = {}
    if (amount.trim()) {
      const lkr = Number(amount)
      if (!Number.isFinite(lkr) || lkr <= 0) return setError('Enter the amount in rupees, e.g. 1000.')
      facts.amount_minor = Math.round(lkr * 100) // what the customer reports, not evidence
    }
    if (reference.trim()) facts.recharge_reference = reference.trim()
    if (description.trim()) facts.description = description.trim()
    setError(null)
    onAnswer(
      { type: 'complaint_details', complaint_type: type, window_start: start.toISOString(), window_end: end.toISOString(), reported_facts: facts },
      `${COMPLAINT_LABEL[type]}: ${from.replace('T', ' ')} → ${to.replace('T', ' ')}${amount ? `, LKR ${amount}` : ''}`,
    )
  }

  return (
    <CardFrame icon={<Wallet />} title="Tell us a bit more">
      <form onSubmit={submit} className="flex flex-col gap-3">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="q-type">What is it about?</Label>
          <Select value={type} onValueChange={(v) => setType(v as ComplaintType)}>
            <SelectTrigger id="q-type" className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {(Object.keys(COMPLAINT_LABEL) as ComplaintType[]).map((t) => (
                <SelectItem key={t} value={t}>
                  {COMPLAINT_LABEL[t]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <fieldset className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <legend className="mb-1.5 text-sm font-medium">When did it happen? (your local time)</legend>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="q-from" className="text-xs text-muted-foreground">
              From
            </Label>
            <Input id="q-from" type="datetime-local" required value={from} onChange={(e) => setFrom(e.target.value)} />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="q-to" className="text-xs text-muted-foreground">
              To
            </Label>
            <Input id="q-to" type="datetime-local" required value={to} onChange={(e) => setTo(e.target.value)} />
          </div>
        </fieldset>
        {type === 'BALANCE_RECHARGE' && (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="q-amount">Recharge amount (LKR, optional)</Label>
              <Input id="q-amount" inputMode="decimal" placeholder="e.g. 1000" value={amount} onChange={(e) => setAmount(e.target.value)} />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="q-ref">Recharge reference (optional)</Label>
              <Input id="q-ref" maxLength={128} value={reference} onChange={(e) => setReference(e.target.value)} />
            </div>
          </div>
        )}
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="q-desc">Anything else? (optional)</Label>
          <Textarea id="q-desc" rows={2} maxLength={2000} value={description} onChange={(e) => setDescription(e.target.value)} />
        </div>
        {error && (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        )}
        <Button type="submit" disabled={disabled} className="w-fit">
          Check my account
        </Button>
      </form>
    </CardFrame>
  )
}
