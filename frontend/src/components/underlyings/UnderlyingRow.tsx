import { Link } from "react-router-dom"
import { Trash2 } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Switch } from "@/components/ui/switch"
import { TableCell, TableRow } from "@/components/ui/table"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { useDeleteUnderlying, useUnderlyingStatus, useUpdateUnderlying } from "@/hooks/queries"
import { formatTimestamp, integer, NO_VALUE, percent, relativeMinutes } from "@/lib/format"
import type { Underlying } from "@/types"

/**
 * Collection health per underlying. The status call is per-row on purpose: it
 * is a cheap count query, and rolling it into the list endpoint would mean
 * touching the backend for a display concern.
 */
export function UnderlyingRow({ underlying }: { underlying: Underlying }) {
  const status = useUnderlyingStatus(underlying.symbol)
  const update = useUpdateUnderlying()
  const remove = useDeleteUnderlying()

  const stale =
    underlying.is_active &&
    status.data?.market_open === true &&
    (status.data.minutes_since_poll === null || status.data.minutes_since_poll > 15)

  return (
    <TableRow>
      <TableCell>
        <Link to={`/chains/${underlying.symbol}`} className="font-medium hover:underline">
          {underlying.symbol}
        </Link>
      </TableCell>
      <TableCell className="max-w-0 truncate text-muted-foreground">{underlying.name}</TableCell>
      <TableCell className="text-right tabular-nums">
        {status.data ? integer(status.data.live_contracts) : NO_VALUE}
      </TableCell>
      <TableCell className="text-right tabular-nums text-muted-foreground">
        {status.data ? integer(status.data.total_contracts) : NO_VALUE}
      </TableCell>
      <TableCell className="text-right tabular-nums">{percent(underlying.dividend_yield, 2)}</TableCell>
      <TableCell>
        <Tooltip>
          <TooltipTrigger asChild>
            <span className="text-sm text-muted-foreground">
              {relativeMinutes(status.data?.minutes_since_poll ?? null)}
            </span>
          </TooltipTrigger>
          <TooltipContent>Last poll {formatTimestamp(underlying.last_polled_at)}</TooltipContent>
        </Tooltip>
      </TableCell>
      <TableCell>
        {stale ? (
          <Badge variant="destructive">Stale</Badge>
        ) : underlying.is_active ? (
          <Badge variant="outline">Collecting</Badge>
        ) : (
          <Badge variant="secondary">Paused</Badge>
        )}
      </TableCell>
      <TableCell>
        <div className="flex items-center justify-end gap-1">
          <Tooltip>
            {/*
              The trigger wraps the switch rather than becoming it: `asChild`
              writes its own data-state onto the child, which is the very
              attribute the switch styles its checked background from.
            */}
            <TooltipTrigger asChild>
              <span className="inline-flex">
                <Switch
                  checked={underlying.is_active}
                  aria-label={`${underlying.is_active ? "Pause" : "Resume"} collection for ${underlying.symbol}`}
                  onCheckedChange={(checked) =>
                    update.mutate({ symbol: underlying.symbol, is_active: checked })
                  }
                />
              </span>
            </TooltipTrigger>
            <TooltipContent>Pausing stops polling and keeps every quote already stored.</TooltipContent>
          </Tooltip>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                aria-label={`Delete ${underlying.symbol}`}
                onClick={() => {
                  if (
                    window.confirm(
                      `Delete ${underlying.symbol}, its contracts and every quote collected for it? This cannot be undone — pause it instead to keep the history.`,
                    )
                  ) {
                    remove.mutate(underlying.symbol)
                  }
                }}
              >
                <Trash2 />
              </Button>
            </TooltipTrigger>
            <TooltipContent>Delete the underlying and all of its history.</TooltipContent>
          </Tooltip>
        </div>
      </TableCell>
    </TableRow>
  )
}
