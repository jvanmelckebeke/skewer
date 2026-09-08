import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts"

import { ChartCard, Legend, type TableColumn } from "@/components/charts/ChartCard"
import { TooltipShell } from "@/components/charts/ChartTooltip"
import { EmptyState } from "@/components/common/States"
import { formatClock, formatDateShort, formatTimestamp, integer, money, percent } from "@/lib/format"
import { AXIS, CHART_HEIGHT, DOT, GRID, LINE, VIZ } from "@/lib/viz"
import type { ContractSeriesPoint } from "@/types"

const COLUMNS: TableColumn<ContractSeriesPoint>[] = [
  { key: "ts", header: "Time", render: (row) => formatTimestamp(row.ts) },
  { key: "bid", header: "Bid", numeric: true, render: (row) => money(row.bid) },
  { key: "ask", header: "Ask", numeric: true, render: (row) => money(row.ask) },
  { key: "mark", header: "Mark", numeric: true, render: (row) => money(row.mark) },
  { key: "iv", header: "IV", numeric: true, render: (row) => percent(row.iv, 2) },
  { key: "spot", header: "Spot", numeric: true, render: (row) => money(row.spot) },
  { key: "volume", header: "Volume", numeric: true, render: (row) => integer(row.volume) },
]

/**
 * A window wider than a session spans several days, and a bare clock tick then
 * reads as going backwards at each midnight. Add the day when it does.
 */
function tickFormatter(points: ContractSeriesPoint[]) {
  const first = points[0]?.ts
  const last = points.at(-1)?.ts
  const multiDay = Boolean(first && last && first.slice(0, 10) !== last.slice(0, 10))
  return (iso: string) => (multiDay ? `${formatDateShort(iso.slice(0, 10))} ${formatClock(iso)}` : formatClock(iso))
}

/**
 * Price and volatility are different scales, so they get two charts rather than
 * two y-axes. A dual axis would let the alignment of the scales invent a
 * relationship the data does not contain.
 */
export function ContractHistoryChart({
  points,
  days,
}: {
  points: ContractSeriesPoint[]
  days: number
}) {
  const timeTick = tickFormatter(points)

  if (points.length === 0) {
    return (
      <ChartCard
        title="Quote history"
        description="Stored quotes for this contract."
        tableColumns={COLUMNS}
        tableRows={points}
        tableRowKey={(row) => row.ts}
      >
        <EmptyState
          title={`Nothing stored in the last ${days} days`}
          description="Widen the window, or check the collector — a contract in a slower cadence bucket is only sampled every 15 or 30 minutes, and once a day beyond a quarter out."
        />
      </ChartCard>
    )
  }

  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <ChartCard
        title="Bid, ask and mark"
        description="The stored two-sided quote. A gap is a slot the contract was not sampled in."
        legend={
          <Legend
            items={[
              { label: "Mark", color: VIZ.call },
              { label: "Bid", color: VIZ.put },
              { label: "Ask", color: VIZ.reference },
            ]}
          />
        }
        tableColumns={COLUMNS}
        tableRows={points}
        tableRowKey={(row) => row.ts}
      >
        <ResponsiveContainer width="100%" height={CHART_HEIGHT}>
          <LineChart data={points} margin={{ top: 8, right: 16, bottom: 4, left: 0 }}>
            <CartesianGrid {...GRID} />
            <XAxis dataKey="ts" {...AXIS} tickFormatter={timeTick} minTickGap={48} />
            <YAxis {...AXIS} width={56} tickFormatter={(value: number) => money(value)} domain={["auto", "auto"]} />
            <Tooltip
              cursor={{ stroke: VIZ.axis, strokeWidth: 1 }}
              content={({ active, payload }) => {
                if (!active || !payload?.length) return null
                const point = payload[0]?.payload as ContractSeriesPoint
                return (
                  <TooltipShell
                    title={formatTimestamp(point.ts)}
                    rows={[
                      { label: "Mark", value: money(point.mark), color: VIZ.call },
                      { label: "Bid", value: money(point.bid), color: VIZ.put },
                      { label: "Ask", value: money(point.ask), color: VIZ.reference },
                      { label: "Spot", value: money(point.spot) },
                    ]}
                  />
                )
              }}
            />
            <Line dataKey="mark" name="Mark" stroke={VIZ.call} dot={false} activeDot={{ ...DOT, stroke: "var(--color-card)" }} isAnimationActive={false} {...LINE} />
            <Line dataKey="bid" name="Bid" stroke={VIZ.put} dot={false} activeDot={{ ...DOT, stroke: "var(--color-card)" }} isAnimationActive={false} {...LINE} />
            <Line dataKey="ask" name="Ask" stroke={VIZ.reference} dot={false} activeDot={{ ...DOT, stroke: "var(--color-card)" }} isAnimationActive={false} {...LINE} />
          </LineChart>
        </ResponsiveContainer>
      </ChartCard>

      <ChartCard
        title="Implied volatility"
        description="Solved from the mark at capture time. Absent wherever the price carried no information about volatility."
        tableColumns={COLUMNS}
        tableRows={points}
        tableRowKey={(row) => row.ts}
      >
        <ResponsiveContainer width="100%" height={CHART_HEIGHT}>
          <LineChart data={points} margin={{ top: 8, right: 16, bottom: 4, left: 0 }}>
            <CartesianGrid {...GRID} />
            <XAxis dataKey="ts" {...AXIS} tickFormatter={timeTick} minTickGap={48} />
            <YAxis {...AXIS} width={56} tickFormatter={(value: number) => percent(value, 0)} domain={["auto", "auto"]} />
            <Tooltip
              cursor={{ stroke: VIZ.axis, strokeWidth: 1 }}
              content={({ active, payload }) => {
                if (!active || !payload?.length) return null
                const point = payload[0]?.payload as ContractSeriesPoint
                return (
                  <TooltipShell
                    title={formatTimestamp(point.ts)}
                    rows={[{ label: "IV", value: percent(point.iv, 2), color: VIZ.call }]}
                  />
                )
              }}
            />
            <Line dataKey="iv" name="IV" stroke={VIZ.call} dot={false} activeDot={{ ...DOT, stroke: "var(--color-card)" }} isAnimationActive={false} {...LINE} />
          </LineChart>
        </ResponsiveContainer>
      </ChartCard>
    </div>
  )
}
