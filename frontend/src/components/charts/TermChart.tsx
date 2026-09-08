import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts"

import { ChartCard, type TableColumn } from "@/components/charts/ChartCard"
import { TooltipShell } from "@/components/charts/ChartTooltip"
import { EmptyState } from "@/components/common/States"
import { formatDate, formatDateShort, integer, percent } from "@/lib/format"
import { AXIS, CHART_HEIGHT, DOT, GRID, LINE, VIZ } from "@/lib/viz"
import type { TermPoint } from "@/types"

/** Recharts hands the label renderer loose coordinates; narrow them at the edge. */
interface LabelProps {
  index?: number
  x?: number | string
  y?: number | string
}

const COLUMNS: TableColumn<TermPoint>[] = [
  { key: "expiration", header: "Expiration", render: (row) => formatDate(row.expiration) },
  { key: "atm_iv", header: "ATM IV", numeric: true, render: (row) => percent(row.atm_iv, 2) },
  { key: "contracts", header: "Priced contracts", numeric: true, render: (row) => integer(row.contracts) },
]

/**
 * ATM IV per expiration. One series, so no legend box — the title names it —
 * and the last point carries a direct label instead.
 */
export function TermChart({ points }: { points: TermPoint[] }) {
  const last = points.at(-1)

  const body =
    points.length === 0 ? (
      <EmptyState
        title="No term structure"
        description="An expiration only contributes a point when its strikes bracket the money, so ATM volatility can be interpolated rather than extrapolated."
      />
    ) : (
      <ResponsiveContainer width="100%" height={CHART_HEIGHT}>
        <LineChart data={points} margin={{ top: 8, right: 56, bottom: 4, left: 0 }}>
          <CartesianGrid {...GRID} />
          <XAxis dataKey="expiration" {...AXIS} tickFormatter={formatDateShort} minTickGap={24} />
          <YAxis {...AXIS} width={52} tickFormatter={(value: number) => percent(value, 0)} domain={["auto", "auto"]} />
          <Tooltip
            cursor={{ stroke: VIZ.axis, strokeWidth: 1 }}
            content={({ active, payload }) => {
              if (!active || !payload?.length) return null
              const point = payload[0]?.payload as TermPoint
              return (
                <TooltipShell
                  title={formatDate(point.expiration)}
                  rows={[
                    { label: "ATM IV", value: percent(point.atm_iv, 2), color: VIZ.call },
                    { label: "Priced contracts", value: integer(point.contracts) },
                  ]}
                />
              )
            }}
          />
          <Line
            dataKey="atm_iv"
            name="ATM IV"
            stroke={VIZ.call}
            dot={{ ...DOT, fill: VIZ.call, stroke: "var(--color-card)" }}
            activeDot={{ ...DOT, r: 5, stroke: "var(--color-card)" }}
            isAnimationActive={false}
            label={(props: LabelProps) =>
              props.index === points.length - 1 && last ? (
                <text
                  x={Number(props.x ?? 0) + 8}
                  y={Number(props.y ?? 0) + 4}
                  fill="var(--color-muted-foreground)"
                  fontSize={11}
                >
                  {percent(last.atm_iv, 1)}
                </text>
              ) : (
                <g />
              )
            }
            {...LINE}
          />
        </LineChart>
      </ResponsiveContainer>
    )

  return (
    <ChartCard
      title="ATM IV term structure"
      description="At-the-money implied volatility per expiration, interpolated at log-moneyness zero."
      tableColumns={COLUMNS}
      tableRows={points}
      tableRowKey={(row) => row.expiration}
    >
      {body}
    </ChartCard>
  )
}
