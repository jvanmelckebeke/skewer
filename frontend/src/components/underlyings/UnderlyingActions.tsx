import { useNavigate } from "react-router-dom"
import { LineChart, Pause, Play, Trash2 } from "lucide-react"

import {
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuSeparator,
} from "@/components/ui/context-menu"
import { useDeleteUnderlying, useUpdateUnderlying } from "@/hooks/queries"

/**
 * The destructive half of the overview, moved behind a right-click.
 *
 * A delete here takes the contracts and every quote with it, which is not
 * something that should sit one stray click away from a row you are reading.
 */
export function UnderlyingActions({ symbol, isActive }: { symbol: string; isActive: boolean }) {
  const navigate = useNavigate()
  const update = useUpdateUnderlying()
  const remove = useDeleteUnderlying()

  return (
    <ContextMenuContent className="w-52">
      <ContextMenuItem onSelect={() => navigate(`/chains/${symbol}`)}>
        <LineChart />
        Open chain
      </ContextMenuItem>
      <ContextMenuItem
        onSelect={() => update.mutate({ symbol, is_active: !isActive })}
      >
        {isActive ? <Pause /> : <Play />}
        {isActive ? "Pause collection" : "Resume collection"}
      </ContextMenuItem>
      <ContextMenuSeparator />
      <ContextMenuItem
        variant="destructive"
        onSelect={() => {
          if (
            window.confirm(
              `Delete ${symbol}, its contracts and every quote collected for it? This cannot be undone — pause it instead to keep the history.`,
            )
          ) {
            remove.mutate(symbol)
          }
        }}
      >
        <Trash2 />
        Delete and drop history
      </ContextMenuItem>
    </ContextMenuContent>
  )
}
