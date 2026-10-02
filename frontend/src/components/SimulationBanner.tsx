import { FlaskConical, Wrench } from 'lucide-react'
import { API_MODE, type Realm } from '@/api/client'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'

/** Always-visible notice: every account and result here is synthetic (contract `simulation: true`). */
export function SimulationBanner({ realm }: { realm: Realm }) {
  return (
    <div className="flex items-center justify-center gap-2 bg-secondary px-4 py-1.5 text-xs text-secondary-foreground">
      <FlaskConical className="size-3.5 shrink-0" aria-hidden />
      <span>
        Simulation — synthetic accounts and records, not connected to real HUTCH systems.
        {API_MODE === 'mock' && <strong className="ml-1">Mock data from contract examples.</strong>}
      </span>
      {API_MODE === 'mock' && <MockControls realm={realm} />}
    </div>
  )
}

function MockControls({ realm }: { realm: Realm }) {
  const run = async (fn: (c: typeof import('@/api/mock').mockControls) => void) => {
    const { mockControls } = await import('@/api/mock')
    fn(mockControls)
    window.location.reload()
  }
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="xs" className="h-6">
          <Wrench aria-hidden /> Mock controls
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuLabel>Exercise UI states</DropdownMenuLabel>
        <DropdownMenuItem onSelect={() => run((c) => c.expireSession(realm))}>Expire this session</DropdownMenuItem>
        <DropdownMenuItem onSelect={() => run((c) => c.setOutage(!c.outage))}>Toggle service outage (503)</DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem variant="destructive" onSelect={() => run((c) => c.reset())}>
          Reset all mock data
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
