import { useMemo } from "react"
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts"

import { ChartCard, Legend, type TableColumn } from "@/components/charts/ChartCard"
import { TooltipShell } from "@/components/charts/ChartTooltip"
import { EmptyState } from "@/components/common/States"
import { formatDate, money, percent } from "@/lib/format"
import { AXIS, CHART_HEIGHT, DOT, GRID, LINE, VIZ } from "@/lib/viz"
import type { ContractQuote } from "@/types"

interface SmilePoint {
  strike: number
  call: number | null
  put: number | null
}

/**
 * One row per strike with the call and put IV side by side. The backend's
 * /smile endpoint sorts calls and puts into one series, which puts two y values
 * on one x; splitting by right here keeps each line single-valued.
 */
function toSmile(contracts: ContractQuote[]): SmilePoint[] {
  const byStrike = new Map<number, SmilePoint>()
  for (const contract of contracts) {
    if (contract.iv === null) continue
    const point = byStrike.get(contract.strike) ?? { strike: contract.strike, call: null, put: null }
    point[contract.right] = contract.iv
    byStrike.set(contract.strike, point)
  }
  return [...byStrike.values()].sort((a, b) => a.strike - b.strike)
}

const COLUMNS: TableColumn<SmilePoint>[] = [
  { key: "strike", header: "Strike", numeric: true, render: (row) => money(row.strike) },
  { key: "call", header: "Call IV", numeric: true, render: (row) => percent(row.call, 2) },
  { key: "put", header: "Put IV", numeric: true, render: (row) => percent(row.put, 2) },
]

export function SmileChart({
  contracts,
  expiration,
  spot,
}: {
  contracts: ContractQuote[]
  expiration: string
  spot: number | null
}) {
  const data = useMemo(() => toSmile(contracts), [contracts])

  const body =
    data.length === 0 ? (
      <EmptyState
        title="No identifiable volatility in this expiration"
        description="Every contract here quoted one-sided or at a price the solver could not imply a volatility from."
      />
    ) : (
      <ResponsiveContainer width="100%" height={CHART_HEIGHT}>
        <LineChart data={data} margin={{ top: 24, right: 16, bottom: 4, left: 0 }}>
          <CartesianGrid {...GRID} />
          <XAxis
            dataKey="strike"
            type="number"
            domain={["dataMin", "dataMax"]}
            {...AXIS}
            tickFormatter={(value: number) => money(value, 0)}
          />
          <YAxis
            {...AXIS}
            width={52}
            tickFormatter={(value: number) => percent(value, 0)}
            domain={["auto", "auto"]}
          />
          {spot !== null ? (
            <ReferenceLine
              x={spot}
              stroke={VIZ.reference}
              strokeWidth={2}
              label={{ value: `spot ${money(spot)}`, position: "top", offset: 10, fill: VIZ.muted, fontSize: 11 }}
            />
          ) : null}
          <Tooltip
            cursor={{ stroke: VIZ.axis, strokeWidth: 1 }}
            content={({ active, payload, label }) => {
              if (!active || !payload?.length) return null
              const point = payload[0]?.payload as SmilePoint
              return (
                <TooltipShell
                  title={`Strike ${money(Number(label))}`}
                  rows={[
                    { label: "Call IV", value: percent(point.call, 2), color: VIZ.call },
                    { label: "Put IV", value: percent(point.put, 2), color: VIZ.put },
                  ]}
                />
              )
            }}
          />
          <Line
            dataKey="call"
            name="Call"
            stroke={VIZ.call}
            dot={false}
            activeDot={{ ...DOT, stroke: "var(--color-card)" }}
            connectNulls
            isAnimationActive={false}
            {...LINE}
          />
          <Line
            dataKey="put"
            name="Put"
            stroke={VIZ.put}
            dot={false}
            activeDot={{ ...DOT, stroke: "var(--color-card)" }}
            connectNulls
            isAnimationActive={false}
            {...LINE}
          />
        </LineChart>
      </ResponsiveContainer>
    )

  return (
    <ChartCard
      title="Volatility smile"
      description={`Implied volatility by strike, ${formatDate(expiration)}. Contracts with no identifiable IV are absent, not zero.`}
      legend={<Legend items={[{ label: "Call", color: VIZ.call }, { label: "Put", color: VIZ.put }]} />}
      tableColumns={COLUMNS}
      tableRows={data}
      tableRowKey={(row) => String(row.strike)}
    >
      {body}
    </ChartCard>
  )
}
