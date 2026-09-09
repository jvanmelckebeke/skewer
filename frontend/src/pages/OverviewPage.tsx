import { useMemo } from "react"

import { ErrorState, LoadingState } from "@/components/common/States"
import { AddUnderlyingDialog } from "@/components/underlyings/AddUnderlyingDialog"
import { SummaryTable } from "@/components/underlyings/SummaryTable"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { useChainSummary, useGroups } from "@/hooks/queries"
import type { ChainSummaryRow } from "@/types"

interface Section {
  key: string
  title: string
  description: string | null
  rows: ChainSummaryRow[]
}

export function OverviewPage() {
  const summary = useChainSummary()
  const groups = useGroups()

  const sections = useMemo<Section[]>(() => {
    const rows = summary.data ?? []
    const bySymbol = new Map(rows.map((row) => [row.symbol, row]))
    const grouped = groups.data ?? []

    if (grouped.length === 0) {
      return [{ key: "all", title: "Tracked", description: null, rows }]
    }

    const sections: Section[] = grouped.map((group) => ({
      key: `group-${group.id}`,
      title: group.name,
      description: group.description,
      rows: group.underlyings
        .map((underlying) => bySymbol.get(underlying.symbol))
        .filter((row): row is ChainSummaryRow => row !== undefined),
    }))

    // An underlying in no group is not polled, which is exactly why it has to
    // stay visible: it looks identical to a healthy one until you notice the
    // section it sits in.
    const claimed = new Set(grouped.flatMap((g) => g.underlyings.map((u) => u.symbol)))
    const orphans = rows.filter((row) => !claimed.has(row.symbol))
    if (orphans.length > 0) {
      sections.push({
        key: "ungrouped",
        title: "In no group",
        description: "Not polled. Membership is the collection switch, so these are frozen.",
        rows: orphans,
      })
    }
    return sections
  }, [summary.data, groups.data])

  const total = summary.data?.length ?? 0

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Overview</h1>
          <p className="text-sm text-muted-foreground">
            Where volatility sits on each surface. Right-click a row for its collection controls.
          </p>
        </div>
        <AddUnderlyingDialog />
      </div>

      {summary.isPending ? (
        <LoadingState label="Loading surfaces" />
      ) : summary.error ? (
        <ErrorState error={summary.error} context="Could not load the summary" />
      ) : total === 0 ? (
        <Card className="gap-0 py-0">
          <CardContent className="px-0">
            <SummaryTable
              rows={[]}
              emptyTitle="Nothing is being collected yet"
              emptyDescription="Track a ticker to start capturing its option chain on the collector's cadence."
            />
          </CardContent>
        </Card>
      ) : (
        sections.map((section) => (
          <Card key={section.key} className="gap-4">
            <CardHeader>
              <CardTitle>{section.title}</CardTitle>
              {section.description ? (
                <CardDescription>{section.description}</CardDescription>
              ) : null}
            </CardHeader>
            <CardContent className="px-0">
              <SummaryTable
                rows={section.rows}
                emptyTitle="Empty group"
                emptyDescription="Nothing in this group is being polled."
              />
            </CardContent>
          </Card>
        ))
      )}
    </div>
  )
}
