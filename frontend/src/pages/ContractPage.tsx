import { Link, useParams, useSearchParams } from "react-router-dom"
import { ArrowLeft } from "lucide-react"

import { ContractHistoryChart } from "@/components/charts/ContractHistoryChart"
import { StatTile } from "@/components/common/StatTile"
import { ErrorState, LoadingState } from "@/components/common/States"
import { Button } from "@/components/ui/button"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { useContractSeries } from "@/hooks/queries"
import { formatDate, formatTimestamp, integer, money, percent } from "@/lib/format"

const WINDOWS = [1, 5, 15, 30, 90]

export function ContractPage() {
  const { occSymbol = "" } = useParams()

  // The window lives in the URL so a link to a contract carries the span it was
  // read over.
  const [params, setParams] = useSearchParams()
  const requested = Number(params.get("days"))
  const days = WINDOWS.includes(requested) ? requested : 5
  const setDays = (value: string) => setParams({ days: value }, { replace: true })
  const series = useContractSeries(occSymbol, days)

  // The OCC root is the leading alphabetic run — enough to link back to the chain.
  const root = occSymbol.replace(/[0-9].*$/, "")

  const latest = series.data?.points.at(-1)

  return (
    <div className="space-y-6">
      <Button variant="ghost" size="sm" asChild className="-ml-2 h-7 text-muted-foreground">
        <Link to={root ? `/chains/${root}` : "/"}>
          <ArrowLeft />
          {root || "Underlyings"}
        </Link>
      </Button>

      {series.isPending ? (
        <LoadingState label="Loading quote history" />
      ) : series.error ? (
        <ErrorState error={series.error} context={`No stored history for ${occSymbol}`} />
      ) : (
        <>
          <div className="flex flex-wrap items-end justify-between gap-4">
            <div>
              <h1 className="text-2xl font-semibold tracking-tight">
                {money(series.data.strike)} {series.data.right === "call" ? "Call" : "Put"} ·{" "}
                {formatDate(series.data.expiration)}
              </h1>
              <p className="font-mono text-sm text-muted-foreground">{series.data.symbol}</p>
            </div>

            <Select value={String(days)} onValueChange={setDays}>
              <SelectTrigger className="w-40">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {WINDOWS.map((window) => (
                  <SelectItem key={window} value={String(window)}>
                    Last {window} {window === 1 ? "day" : "days"}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
            <StatTile label="Mark" value={money(latest?.mark ?? null)} detail="Latest stored quote" />
            <StatTile label="Implied volatility" value={percent(latest?.iv ?? null, 2)} />
            <StatTile label="Spot" value={money(latest?.spot ?? null)} />
            <StatTile label="Volume" value={integer(latest?.volume ?? null)} />
            <StatTile
              label="Stored points"
              value={integer(series.data.points.length)}
              detail={latest ? `Through ${formatTimestamp(latest.ts)}` : undefined}
            />
          </div>

          <ContractHistoryChart points={series.data.points} days={days} />
        </>
      )}
    </div>
  )
}
