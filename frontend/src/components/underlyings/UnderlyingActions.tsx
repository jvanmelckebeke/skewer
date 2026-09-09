import { useNavigate } from "react-router-dom"
import { LineChart, Minus, Pause, Play, Trash2 } from "lucide-react"

import {
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuSeparator,
} from "@/components/ui/context-menu"
import { useDeleteUnderlying, useRemoveFromGroup, useUpdateUnderlying } from "@/hooks/queries"

/**
 * The destructive half of the overview, moved behind a right-click.
 *
 * A delete here takes the contracts and every quote with it, which is not
 * something that should sit one stray click away from a row you are reading.
 */
export function UnderlyingActions({
  symbol,
  isActive,
  // Set on a group page, where "take this out of the group" is a different
  // and much cheaper act than deleting the underlying.
  membership,
}: {
  symbol: string
  isActive: boolean
  membership?: { groupId: number; underlyingId: number; groupName: string }
}) {
  const navigate = useNavigate()
  const update = useUpdateUnderlying()
  const remove = useDeleteUnderlying()
  const removeFromGroup = useRemoveFromGroup()

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
      {membership ? (
        <ContextMenuItem
          onSelect={() =>
            removeFromGroup.mutate({
              groupId: membership.groupId,
              underlyingId: membership.underlyingId,
            })
          }
        >
          <Minus />
          Remove from {membership.groupName}
        </ContextMenuItem>
      ) : null}
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
