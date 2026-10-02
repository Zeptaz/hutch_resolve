import { ShieldCheck } from 'lucide-react'
import type { ProposalView } from '@/api/types'
import { humanize } from '@/lib/format'
import { CardFrame } from './ChatCards'

/** Read-only view of a proposed action. Interactive confirmation is added separately. */
export function ProposalSummary({ proposal }: { proposal: ProposalView }) {
  return (
    <CardFrame icon={<ShieldCheck />} title="Suggested action">
      <p className="font-medium">
        {humanize(proposal.action_type)}: {proposal.target_label}
      </p>
      <p className="mt-1 text-muted-foreground">{proposal.consequences}</p>
    </CardFrame>
  )
}
