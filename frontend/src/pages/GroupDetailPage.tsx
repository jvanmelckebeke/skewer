import { useMemo } from "react"
import { Navigate, useParams } from "react-router-dom"

import { GroupIcon } from "@/components/common/GroupIcon"
import { ErrorState, LoadingState } from "@/components/common/States"
import { SummaryTable } from "@/components/underlyings/SummaryTable"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent } from "@/components/ui/card"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { useAddToGroup, useChainSummary, useGroups, useUnderlyings } from "@/hooks/queries"

export function GroupDetailPage() {
  const { id } = useParams<{ id: string }>()
  const groupId = Number(id)
  const groups = useGroups()
  const underlyings = useUnderlyings()
  const summary = useChainSummary(groupId)
  const add = useAddToGroup()

  const group = (groups.data ?? []).find((candidate) => candidate.id === groupId)

  const membership = useMemo(
    () =>
      group
        ? {
            groupId: group.id,
            groupName: group.name,
            idBySymbol: new Map(group.underlyings.map((u) => [u.symbol, u.id])),
          }
        : undefined,
    [group],
  )

  const candidates = useMemo(() => {
    if (!group) return []
    const members = new Set(group.underlyings.map((u) => u.id))
    return (underlyings.data ?? []).filter((underlying) => !members.has(underlying.id))
  }, [group, underlyings.data])

  if (!Number.isFinite(groupId)) return <Navigate to="/" replace />
  if (groups.isPending) return <LoadingState label="Loading group" />
  if (groups.error) return <ErrorState error={groups.error} context="Could not load groups" />
  if (!group) return <Navigate to="/" replace />

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="flex items-center gap-2 text-xl font-semibold tracking-tight">
            <GroupIcon name={group.icon} className="size-5" />
            {group.name}
            {group.is_default ? <Badge variant="secondary">Default</Badge> : null}
          </h1>
          <p className="text-sm text-muted-foreground">
            {group.description ?? "Everything in this group is polled on the collector's cadence."}
          </p>
        </div>

        {candidates.length > 0 ? (
          <Select
            value=""
            onValueChange={(value) =>
              add.mutate({ groupId: group.id, underlyingIds: [Number(value)] })
            }
          >
            <SelectTrigger className="w-64">
              <SelectValue placeholder="Add an underlying" />
            </SelectTrigger>
            <SelectContent>
              {candidates.map((underlying) => (
                <SelectItem key={underlying.id} value={String(underlying.id)}>
                  {underlying.symbol} — {underlying.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        ) : null}
      </div>

      <Card className="gap-0 py-0">
        <CardContent className="px-0">
          {summary.isPending ? (
            <LoadingState label="Loading surfaces" />
          ) : summary.error ? (
            <ErrorState error={summary.error} context="Could not load the summary" />
          ) : (
            <SummaryTable
              rows={summary.data}
              membership={membership}
              emptyTitle="Empty group"
              emptyDescription="Nothing in this group is polled. Add an underlying to start collecting it."
            />
          )}
        </CardContent>
      </Card>
    </div>
  )
}
