import type { Card } from '@/api/types'
import type { Translate } from '@/i18n/context'

/** The record's name in the side list, from its card type (titles match the cards themselves). */
export function recordTitle(t: Translate, card: Card): string {
  switch (card.type) {
    case 'account':
      return t('card.yourLine')
    case 'timeline':
      return t('card.whatHappened')
    case 'calculation':
      return card.data.unit === 'BYTES' ? t('card.dataCheck') : t('card.balanceCheck')
    case 'finding':
      return t('card.found')
    case 'confirmation':
      return t('confirm.decision')
    case 'ticket':
      return t('card.reviewRequest')
    case 'receipt':
      return t('card.receipt')
    case 'package_catalogue':
      return t('package.catalogue')
    default:
      return t('card.receipt')
  }
}
