import type { Calculation } from '@/api/types'
import { Table, TableBody, TableCell, TableFooter, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { formatCalcValue, humanize } from '@/lib/format'
import { cn } from '@/lib/utils'

/**
 * Renders a server calculation verbatim. Expected, observed and difference come from Resolve;
 * this component never adds terms up or decides whether they match.
 */
export function CalculationTable({ calc, title = true }: { calc: Calculation; title?: boolean }) {
  const fmt = (n: number) => formatCalcValue(calc.unit, n)
  return (
    <div className="flex flex-col gap-2">
      {title && <h3 className="font-semibold">{humanize(calc.code)}</h3>}
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Item</TableHead>
            <TableHead className="text-right">Amount</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          <TableRow>
            <TableCell className="text-muted-foreground">Opening</TableCell>
            <TableCell className="text-right font-mono">{fmt(calc.opening)}</TableCell>
          </TableRow>
          {calc.terms.map((t) => (
            <TableRow key={t.evidence_id}>
              <TableCell>{t.label}</TableCell>
              <TableCell className="text-right font-mono">{fmt(t.value)}</TableCell>
            </TableRow>
          ))}
        </TableBody>
        <TableFooter>
          <TableRow>
            <TableCell>Expected</TableCell>
            <TableCell className="text-right font-mono">{fmt(calc.expected)}</TableCell>
          </TableRow>
          <TableRow>
            <TableCell>Recorded</TableCell>
            <TableCell className="text-right font-mono">{calc.observed == null ? 'Unavailable' : fmt(calc.observed)}</TableCell>
          </TableRow>
          <TableRow>
            <TableCell>Difference</TableCell>
            <TableCell className={cn('text-right font-mono', calc.delta ? 'text-destructive' : 'text-success')}>
              {calc.delta == null ? 'Unavailable' : fmt(calc.delta)}
            </TableCell>
          </TableRow>
        </TableFooter>
      </Table>
    </div>
  )
}
