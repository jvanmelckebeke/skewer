import { useMemo } from "react"
import { Link } from "react-router-dom"

import { EmptyState, LoadingState } from "@/components/common/States"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Switch } from "@/components/ui/switch"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { useUnderlyings, useUnderlyingStatuses, useUpdateUnderlying } from "@/hooks/queries"
import { collectionState, compareByState, type CollectionState } from "@/lib/collection"
import { formatDate, formatTimestamp, integer, relativeMinutes } from "@/lib/format"

const STATE_BADGE: Record<CollectionState, { label: string; variant: "destructive" | "outline" | "secondary" }> = {
  stale: { label: "Stale", variant: "destructive" },
  collecting: { label: "Collecting", variant: "outline" },
  paused: { label: "Paused", variant: "secondary" },
}

/**
 * Per-underlying collection health: the "is this actually working?" table.
 *
 * It lives here rather than on the overview because none of it is about
 * options. Catalogued counts, slot timings and a pause switch are things you
 * look at when something is wrong, not things you read to decide a trade.
 */
export function CollectionHealth() {
  const underlyings = useUnderlyings()
  const update = useUpdateUnderlying()

  const symbols = useMemo(
    () => (underlyings.data ?? []).map((underlying) => underlying.symbol),
    [underlyings.data],
  )
  const statuses = useUnderlyingStatuses(symbols)

  const rows = useMemo(() => {
    const list = (underlyings.data ?? []).map((underlying) => {
      const status = statuses.bySymbol.get(underlying.symbol)
      return { underlying, status, state: collectionState(underlying.is_active, status) }
    })
    return list.sort(
      (a, b) =>
        compareByState(a.state, b.state) || a.underlying.symbol.localeCompare(b.underlying.symbol),
    )
  }, [underlyings.data, statuses.bySymbol])

  return (
    <Card className="gap-4">
      <CardHeader>
        <CardTitle>Collection health</CardTitle>
        <CardDescription>
          One row per tracked underlying, anything wrong first. Stale means the market is open and
          nothing has been polled for over 15 minutes, which is three missed base-cadence slots.
          Catalogued counts every contract ever seen, expired ones included, because the dimension
          table is the record of what exists rather than of what was sampled.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {underlyings.isPending ? (
          <LoadingState label="Loading underlyings" />
        ) : rows.length === 0 ? (
          <EmptyState
            title="Nothing is being collected"
            description="Track an underlying to give the collector something to poll."
          />
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-28">Symbol</TableHead>
                <TableHead className="w-24">Venue</TableHead>
                <TableHead className="w-28">State</TableHead>
                <TableHead className="w-28">Last poll</TableHead>
                <TableHead className="w-28">Last slot</TableHead>
                <TableHead className="w-28 text-right">Live</TableHead>
                <TableHead className="w-28 text-right">Catalogued</TableHead>
                <TableHead className="w-32">Last EOD</TableHead>
                <TableHead className="w-20 text-right">Collect</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map(({ underlying, status, state }) => {
                const badge = STATE_BADGE[state]
                return (
                  <TableRow key={underlying.id}>
                    <TableCell>
                      <Link to={`/chains/${underlying.symbol}`} className="font-medium hover:underline">
                        {underlying.symbol}
                      </Link>
                    </TableCell>
                    <TableCell className="text-sm text-muted-foreground uppercase">
                      {underlying.venue}
                    </TableCell>
                    <TableCell>
                      <Badge variant={badge.variant}>{badge.label}</Badge>
                    </TableCell>
                    <TableCell>
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <span className="text-sm text-muted-foreground">
                            {relativeMinutes(status?.minutes_since_poll ?? null)}
                          </span>
                        </TooltipTrigger>
                        <TooltipContent>{formatTimestamp(underlying.last_polled_at)}</TooltipContent>
                      </Tooltip>
                    </TableCell>
                    <TableCell className="text-sm text-muted-foreground">
                      {/*
                        The last poll and the last persisted slot are different
                        questions. A poll that persisted nothing is a working
                        collector; a slot far behind the poll is a cadence that
                        chose not to store, not a fault.
                      */}
                      {status?.last_slot ? formatTimestamp(status.last_slot) : "never"}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {integer(status?.live_contracts)}
                    </TableCell>
                    <TableCell className="text-right tabular-nums text-muted-foreground">
                      {integer(status?.total_contracts)}
                    </TableCell>
                    <TableCell className="text-sm text-muted-foreground">
                      {formatDate(underlying.last_eod_snapshot_on)}
                    </TableCell>
                    <TableCell>
                      <div className="flex justify-end">
                        <Tooltip>
                          {/*
                            The trigger wraps the switch rather than becoming
                            it: `asChild` writes its own data-state onto the
                            child, which is the attribute the switch styles its
                            checked background from.
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
                          <TooltipContent>
                            Pausing stops polling and keeps every quote already stored.
                          </TooltipContent>
                        </Tooltip>
                      </div>
                    </TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  )
}
