import type { Card, ConversationView } from '@/api/types'

/** The receipts (checks, findings, tickets, Trust Receipts...) one Resolve reply returned. */
export type ReceiptGroup = { messageId: string; caseId: string | null; time: string; cards: Card[] }

/** An offer the conversation showed earlier; it stays a record once it is no longer waiting for an answer. */
export type PastOffer = { messageId: string; time: string; card: Extract<Card, { type: 'confirmation' }> }

export type CallReceipts = {
  /** The current topic's receipts, shown under the call. */
  current: ReceiptGroup[]
  /** Everything else, oldest first. */
  history: ReceiptGroup[]
  /** Offers that are no longer open, oldest first. The open offer is shown on its own until it is answered. */
  pastOffers: PastOffer[]
}

const topicOf = (group: ReceiptGroup) => group.caseId ?? group.messageId

/**
 * Split the conversation's receipts into the current topic and history.
 *
 * Only replies that arrived during this call can be "current"; older chat receipts start in history.
 * A topic is a case (or, for answers outside a case such as a balance check, the one reply). It stays
 * current while later replies are about the same case or only close the call, and moves to history as
 * soon as a reply is about something else.
 */
export function splitCallReceipts(conversation: ConversationView | null, presentBeforeCall: ReadonlySet<string> | null,
                                  openProposalId: string | null): CallReceipts {
  const groups: ReceiptGroup[] = []
  const pastOffers: PastOffer[] = []
  let latest: ConversationView['messages'][number] | null = null
  for (const message of conversation?.messages ?? []) {
    if (message.speaker !== 'ASSISTANT' || !message.result) continue
    latest = message
    const cards: Card[] = []
    for (const card of message.result.cards) {
      if (card.type !== 'confirmation') cards.push(card)
      else if (card.data.id !== openProposalId) pastOffers.push({ messageId: message.id, time: message.created_at, card })
    }
    if (cards.length) groups.push({ messageId: message.id, caseId: message.result.case_id ?? null, time: message.created_at, cards })
  }

  const last = groups.at(-1)
  const isNew = (group: ReceiptGroup) => presentBeforeCall !== null && !presentBeforeCall.has(group.messageId)
  let topic = last && isNew(last) ? topicOf(last) : null
  if (topic !== null && latest && last && latest.id !== last.messageId) {
    const laterCase = latest.result?.case_id ?? null
    const closingOnly = latest.result?.pending_question?.code === 'CALL_ENDED'
    if (!closingOnly && (laterCase === null || laterCase !== last.caseId)) topic = null
  }
  const current = groups.filter((group) => isNew(group) && topicOf(group) === topic)
  return { current, history: groups.filter((group) => !current.includes(group)), pastOffers }
}
