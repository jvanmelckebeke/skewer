import { useMemo, useState } from "react"
import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceArea,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts"

import { ChartCard, Legend, type TableColumn } from "@/components/charts/ChartCard"
import { TooltipShell } from "@/components/charts/ChartTooltip"
import { EmptyState } from "@/components/common/States"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { formatDate, integer, money, percent } from "@/lib/format"
import { AXIS, CHART_HEIGHT, GRID, LINE, VIZ } from "@/lib/viz"
import type { SessionProfileResponse, SessionStrikePoint } from "@/types"

/** One row per strike: the median and the [p25, p75] band, per side. */
interface RibbonPoint {
  strike: number
  callBand: [number, number] | null
  callMedian: number | null
  putBand: [number, number] | null
  putMedian: number | null
  callPriced: number
  putPriced: number
}

/**
 * Keep each side only where it is out of the money.
 *
 * This is how a volatility smile is built, not a convenience: an in-the-money
 * option's price is nearly all intrinsic value, so the mid barely moves with
 * volatility and the implied number it yields is dominated by quote noise.
 * Measured on NVDA's 24 Aug expiry over the 21 Aug session, out-of-the-money
 * calls implied 24-43% and out-of-the-money puts 24-60%, a textbook put skew,
 * while the in-the-money halves of the same chain reached 274% and 120%. Those
 * are the solver being asked an unanswerable question, not volatility.
 *
 * Nothing is discarded: the table view lists every strike either way.
 */
function isOutOfTheMoney(point: SessionStrikePoint, spot: number): boolean {
  return point.right === "call" ? point.strike >= spot : point.strike <= spot
}

function toRibbon(points: SessionStrikePoint[]): RibbonPoint[] {
  const byStrike = new Map<number, RibbonPoint>()

  for (const point of points) {
    const row =
      byStrike.get(point.strike) ??
      ({
        strike: point.strike,
        callBand: null, callMedian: null, putBand: null, putMedian: null,
        callPriced: 0, putPriced: 0,
      } satisfies RibbonPoint)

    // A band needs both edges. A strike priced in only one slot all session has
    // a median but no spread, and drawing it as a zero-width band would claim a
    // certainty the single sample does not support.
    const band: [number, number] | null =
      point.iv_p25 !== null && point.iv_p75 !== null ? [point.iv_p25, point.iv_p75] : null

    if (point.right === "call") {
      row.callBand = band
      row.callMedian = point.iv_p50
      row.callPriced = point.priced
    } else {
      row.putBand = band
      row.putMedian = point.iv_p50
      row.putPriced = point.priced
    }
    byStrike.set(point.strike, row)
  }

  return [...byStrike.values()].sort((a, b) => a.strike - b.strike)
}

const COLUMNS: TableColumn<SessionStrikePoint>[] = [
  { key: "strike", header: "Strike", numeric: true, render: (r) => money(r.strike) },
  { key: "right", header: "Side", render: (r) => (r.right === "call" ? "Call" : "Put") },
  { key: "priced", header: "Priced / captured", numeric: true, render: (r) => `${r.priced} / ${r.samples}` },
  { key: "p25", header: "IV p25", numeric: true, render: (r) => percent(r.iv_p25, 2) },
  { key: "p50", header: "IV median", numeric: true, render: (r) => percent(r.iv_p50, 2) },
  { key: "p75", header: "IV p75", numeric: true, render: (r) => percent(r.iv_p75, 2) },
  {
    key: "iqr",
    header: "IQR",
    numeric: true,
    render: (r) =>
      r.iv_p25 === null || r.iv_p75 === null ? "–" : percent(r.iv_p75 - r.iv_p25, 2),
  },
  { key: "mark", header: "Median mark", numeric: true, render: (r) => money(r.mark_p50) },
  { key: "spread", header: "Median spread", numeric: true, render: (r) => percent(r.spread_p50, 2) },
  { key: "volume", header: "Volume", numeric: true, render: (r) => integer(r.volume) },
]

/**
 * The smile as a range rather than a photograph.
 *
 * The line is the session's median IV at each strike; the band is the
 * interquartile range, so its width says how much that strike was repriced
 * over the day. Calls and puts overlay deliberately: put-call parity says they
 * should agree at a shared strike, which makes the overlay a comparison rather
 * than a collision, and the places they separate are the informative ones.
 */
export function SessionRibbonChart({ data }: { data: SessionProfileResponse }) {
  const [otmOnly, setOtmOnly] = useState(true)

  const spotMid =
    data.spot_low !== null && data.spot_high !== null ? (data.spot_low + data.spot_high) / 2 : null

  const visible = useMemo(() => {
    if (!otmOnly || spotMid === null) return data.points
    return data.points.filter((point) => isOutOfTheMoney(point, spotMid))
  }, [data.points, otmOnly, spotMid])

  const points = useMemo(() => toRibbon(visible), [visible])

  // Bound the axis to strikes that were actually priced. A listed-but-never-
  // quoted strike at 390 would otherwise stretch the axis across empty space.
  const drawn = points.filter((p) => p.callMedian !== null || p.putMedian !== null)
  const strikeDomain: [number, number] | undefined =
    drawn.length > 0 ? [drawn[0].strike, drawn[drawn.length - 1].strike] : undefined
  const rows = useMemo(
    () => [...data.points].sort((a, b) => a.strike - b.strike || a.right.localeCompare(b.right)),
    [data.points],
  )
  const withBand = points.filter((p) => p.callBand || p.putBand).length

  const body =
    data.slots < 2 ? (
      <EmptyState
        title={`Only ${data.slots} ${data.slots === 1 ? "slot" : "slots"} captured on this session`}
        description="A range needs more than one observation. Pick a session the collector ran through, or check the collector page if a full trading day is missing."
      />
    ) : withBand === 0 ? (
      <EmptyState
        title="Nothing to profile in this session"
        description="No strike in this expiration held an identifiable volatility across enough slots to describe a range."
      />
    ) : (
      <ResponsiveContainer width="100%" height={CHART_HEIGHT}>
        <ComposedChart data={points} margin={{ top: 24, right: 16, bottom: 4, left: 0 }}>
          <CartesianGrid {...GRID} />
          <XAxis
            dataKey="strike"
            type="number"
            domain={strikeDomain ?? ["dataMin", "dataMax"]}
            allowDataOverflow
            {...AXIS}
            tickFormatter={(value: number) => money(value, 0)}
          />
          <YAxis
            {...AXIS}
            width={52}
            tickFormatter={(value: number) => percent(value, 0)}
            domain={["auto", "auto"]}
          />

          {/* Spot is a range over a session, not a line. */}
          {data.spot_low !== null && data.spot_high !== null ? (
            <ReferenceArea
              x1={data.spot_low}
              x2={data.spot_high}
              fill={VIZ.reference}
              fillOpacity={0.12}
              stroke={VIZ.reference}
              strokeOpacity={0.5}
              label={{
                value: `spot ${money(data.spot_low)}–${money(data.spot_high)}`,
                position: "top",
                offset: 10,
                fill: VIZ.muted,
                fontSize: 11,
              }}
            />
          ) : null}

          <Tooltip
            cursor={{ stroke: VIZ.axis, strokeWidth: 1 }}
            content={({ active, payload, label }) => {
              if (!active || !payload?.length) return null
              const point = payload[0]?.payload as RibbonPoint
              const range = (band: [number, number] | null) =>
                band ? `${percent(band[0], 1)} – ${percent(band[1], 1)}` : "–"
              return (
                <TooltipShell
                  title={`Strike ${money(Number(label))}`}
                  rows={[
                    { label: "Call median", value: percent(point.callMedian, 2), color: VIZ.call },
                    { label: "Call IQR", value: range(point.callBand) },
                    { label: "Put median", value: percent(point.putMedian, 2), color: VIZ.put },
                    { label: "Put IQR", value: range(point.putBand) },
                  ]}
                  footer={`priced in ${point.callPriced} / ${point.putPriced} slots (call / put) of ${data.slots}`}
                />
              )
            }}
          />

          <Area
            dataKey="callBand"
            name="Call IQR"
            stroke="none"
            fill={VIZ.call}
            fillOpacity={0.22}
            isAnimationActive={false}
            connectNulls
          />
          <Area
            dataKey="putBand"
            name="Put IQR"
            stroke="none"
            fill={VIZ.put}
            fillOpacity={0.22}
            isAnimationActive={false}
            connectNulls
          />
          <Line
            dataKey="callMedian"
            name="Call median"
            stroke={VIZ.call}
            dot={false}
            isAnimationActive={false}
            connectNulls
            {...LINE}
          />
          <Line
            dataKey="putMedian"
            name="Put median"
            stroke={VIZ.put}
            dot={false}
            isAnimationActive={false}
            connectNulls
            {...LINE}
          />
        </ComposedChart>
      </ResponsiveContainer>
    )

  return (
    <ChartCard
      title="Session IV profile"
      description={
        `Median implied volatility per strike across ${data.slots} captured ` +
        `${data.slots === 1 ? "slot" : "slots"} on ${formatDate(data.session_date)}, with the ` +
        `interquartile range as a band. A wide band is a strike that was repriced all day. ` +
        (otmOnly
          ? "Showing out-of-the-money strikes only, where the price is all time value and the " +
            "implied number means something; switch it off for the in-the-money halves too."
          : "Showing every priced strike. In-the-money implied volatilities are dominated by " +
            "intrinsic value and are not reliable.")
      }
      actions={
        <div className="flex items-center gap-2">
          <Switch
            id="otm-only"
            checked={otmOnly}
            disabled={spotMid === null}
            onCheckedChange={setOtmOnly}
          />
          <Label htmlFor="otm-only" className="text-xs font-normal text-muted-foreground">
            Out of the money only
          </Label>
        </div>
      }
      legend={
        <Legend
          items={[
            { label: "Call median & IQR", color: VIZ.call },
            { label: "Put median & IQR", color: VIZ.put },
          ]}
        />
      }
      tableColumns={COLUMNS}
      tableRows={rows}
      tableRowKey={(row) => row.symbol}
    >
      {body}
    </ChartCard>
  )
}
