import { Link } from "react-router-dom"

import { EmptyState } from "@/components/common/States"
import { Badge } from "@/components/ui/badge"
import { ContextMenu, ContextMenuTrigger } from "@/components/ui/context-menu"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { UnderlyingActions } from "@/components/underlyings/UnderlyingActions"
import { useMetricDescriptors } from "@/hooks/queries"
import { collectionState } from "@/lib/collection"
import { metricValue, money } from "@/lib/format"
import type { ChainSummaryRow } from "@/types"

/**
 * Which registered metrics earn a column here.
 *
 * Not the whole registry: max pain and net gamma exposure are numbers you go
 * looking for on one chain, not ones you scan a list with. These three are the
 * shape of the surface — where vol sits, how it tilts, and which way it slopes
 * out in time.
 */
const COLUMNS = ["atm_iv", "skew_25d", "iv_term_slope"] as const

export function SummaryTable({
  rows,
  emptyTitle = "Nothing here yet",
  emptyDescription,
  membership,
}: {
  rows: ChainSummaryRow[]
  emptyTitle?: string
  emptyDescription?: string
  /** Set on a group page so the row menu can offer to remove membership. */
  membership?: { groupId: number; groupName: string; idBySymbol: Map<string, number> }
}) {
  const descriptors = useMetricDescriptors()
  const byId = new Map((descriptors.data ?? []).map((metric) => [metric.id, metric]))

  if (rows.length === 0) {
    return <EmptyState title={emptyTitle} description={emptyDescription} />
  }

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead className="w-32">Symbol</TableHead>
          <TableHead>Name</TableHead>
          <TableHead className="w-32 text-right">Spot</TableHead>
          {COLUMNS.map((id) => {
            const metric = byId.get(id)
            return (
              <TableHead key={id} className="w-32 text-right">
                {metric ? (
                  <Tooltip>
                    <TooltipTrigger className="cursor-help">{metric.label}</TooltipTrigger>
                    <TooltipContent className="max-w-xs text-pretty">
                      {metric.description}
                    </TooltipContent>
                  </Tooltip>
                ) : (
                  id
                )}
              </TableHead>
            )
          })}
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((row) => {
          const state = collectionState(row.is_active, row)
          return (
            <ContextMenu key={row.symbol}>
              <ContextMenuTrigger asChild>
                <TableRow>
                  <TableCell>
                    <div className="flex items-center gap-2">
                      <Link
                        to={`/chains/${row.symbol}`}
                        className="font-medium hover:underline"
                      >
                        {row.symbol}
                      </Link>
                      {state === "paused" ? (
                        <Badge variant="secondary">Paused</Badge>
                      ) : state === "stale" ? (
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <Badge variant="destructive">Stale</Badge>
                          </TooltipTrigger>
                          <TooltipContent>
                            The market is open and nothing has been polled for over 15 minutes.
                            The Collector page has the detail.
                          </TooltipContent>
                        </Tooltip>
                      ) : null}
                    </div>
                  </TableCell>
                  <TableCell className="max-w-0 truncate text-muted-foreground">
                    {row.name}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {money(row.spot)}
                    <span className="ml-1 text-xs text-muted-foreground">{row.currency}</span>
                  </TableCell>
                  {COLUMNS.map((id) => (
                    <TableCell key={id} className="text-right tabular-nums">
                      {/*
                        A null metric renders as the placeholder, never as 0.
                        An ATM IV of zero would plot and read like a real
                        number for a chain that simply could not answer.
                      */}
                      {metricValue(row.metrics[id] ?? null, byId.get(id)?.unit ?? "")}
                    </TableCell>
                  ))}
                </TableRow>
              </ContextMenuTrigger>
              <UnderlyingActions
                symbol={row.symbol}
                isActive={row.is_active}
                membership={
                  membership && membership.idBySymbol.has(row.symbol)
                    ? {
                        groupId: membership.groupId,
                        groupName: membership.groupName,
                        underlyingId: membership.idBySymbol.get(row.symbol)!,
                      }
                    : undefined
                }
              />
            </ContextMenu>
          )
        })}
      </TableBody>
    </Table>
  )
}
