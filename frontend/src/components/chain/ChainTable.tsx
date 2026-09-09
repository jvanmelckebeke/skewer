import { useMemo } from "react"
import { Link } from "react-router-dom"
import { cn } from "cn"

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { decimal, integer, money, NO_VALUE, percent } from "@/lib/format"
import type { ContractQuote } from "@/types"

interface StrikeRow {
  strike: number
  call: ContractQuote | null
  put: ContractQuote | null
}

/** Calls left, puts right, strike down the middle — how a chain is read. */
function toRows(contracts: ContractQuote[]): StrikeRow[] {
  const byStrike = new Map<number, StrikeRow>()
  for (const contract of contracts) {
    const row = byStrike.get(contract.strike) ?? { strike: contract.strike, call: null, put: null }
    row[contract.right] = contract
    byStrike.set(contract.strike, row)
  }
  return [...byStrike.values()].sort((a, b) => a.strike - b.strike)
}

/**
 * Why a number is missing, in the reader's words. `iv_status` is the solver's
 * own reason code; showing it turns a blank cell into an explanation.
 */
const IV_STATUS_TEXT: Record<string, string> = {
  ok: "Solved from the mid of a two-sided quote.",
  no_quote: "No genuine two-sided quote to take a mid from — the provider returned a zero bid or ask, and a zero bid is not a price.",
  expired: "No time value left to imply a volatility from.",
  below_intr: "The quote violates the lower no-arbitrage bound; no volatility reproduces it.",
  above_bound: "The quote violates the upper no-arbitrage bound.",
  no_root: "The bracketed solve did not converge inside the searched range.",
  no_vega: "The price is insensitive to volatility at quote resolution, so no volatility is identifiable. A converged number here would be the bracket edge, not a measurement.",
}

function ivCell(contract: ContractQuote | null) {
  if (!contract) return NO_VALUE
  if (contract.iv !== null) return percent(contract.iv, 1)
  const reason = IV_STATUS_TEXT[contract.iv_status] ?? contract.iv_status
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="cursor-help text-muted-foreground underline decoration-dotted decoration-muted-foreground/60 underline-offset-4">
          {NO_VALUE}
        </span>
      </TooltipTrigger>
      <TooltipContent className="max-w-xs text-pretty">{reason}</TooltipContent>
    </Tooltip>
  )
}

function SideCells({
  contract,
  showGreeks,
  reverse,
}: {
  contract: ContractQuote | null
  showGreeks: boolean
  reverse: boolean
}) {
  const cells = [
    <TableCell key="oi" className="text-right tabular-nums text-muted-foreground">
      {integer(contract?.open_interest ?? null)}
    </TableCell>,
    <TableCell key="vol" className="text-right tabular-nums text-muted-foreground">
      {integer(contract?.volume ?? null)}
    </TableCell>,
    ...(showGreeks
      ? [
          <TableCell key="theta" className="text-right tabular-nums">
            {decimal(contract?.theta ?? null, 3)}
          </TableCell>,
          <TableCell key="vega" className="text-right tabular-nums">
            {decimal(contract?.vega ?? null, 3)}
          </TableCell>,
          <TableCell key="gamma" className="text-right tabular-nums">
            {decimal(contract?.gamma ?? null, 4)}
          </TableCell>,
        ]
      : []),
    <TableCell key="delta" className="text-right tabular-nums">
      {decimal(contract?.delta ?? null, 3)}
    </TableCell>,
    <TableCell key="iv" className="text-right tabular-nums">
      {ivCell(contract)}
    </TableCell>,
    <TableCell key="bid" className="text-right tabular-nums text-muted-foreground">
      {money(contract?.bid ?? null)}
    </TableCell>,
    <TableCell key="ask" className="text-right tabular-nums text-muted-foreground">
      {money(contract?.ask ?? null)}
    </TableCell>,
    <TableCell key="mark" className="text-right tabular-nums">
      {contract ? (
        <Link to={`/contracts/${contract.symbol}`} className="font-medium hover:underline">
          {money(contract.mark)}
        </Link>
      ) : (
        NO_VALUE
      )}
    </TableCell>,
  ]

  return <>{reverse ? [...cells].reverse() : cells}</>
}

const SIDE_HEADERS = ["OI", "Vol", "Delta", "IV", "Bid", "Ask", "Mark"]
const SIDE_HEADERS_GREEKS = ["OI", "Vol", "Theta", "Vega", "Gamma", "Delta", "IV", "Bid", "Ask", "Mark"]

export function ChainTable({
  contracts,
  spot,
  showGreeks,
}: {
  contracts: ContractQuote[]
  spot: number | null
  showGreeks: boolean
}) {
  const rows = useMemo(() => toRows(contracts), [contracts])
  const headers = showGreeks ? SIDE_HEADERS_GREEKS : SIDE_HEADERS

  /** The strike closest to spot — the row a reader looks for first. */
  const atmStrike = useMemo(() => {
    if (spot === null || rows.length === 0) return null
    return rows.reduce((best, row) =>
      Math.abs(row.strike - spot) < Math.abs(best.strike - spot) ? row : best,
    ).strike
  }, [rows, spot])

  return (
    <div className="overflow-x-auto rounded-lg border">
      <Table>
        <TableHeader>
          <TableRow className="hover:bg-transparent">
            <TableHead colSpan={headers.length} className="text-center text-xs uppercase tracking-wide">
              Calls
            </TableHead>
            <TableHead className="text-center text-xs uppercase tracking-wide">Strike</TableHead>
            <TableHead colSpan={headers.length} className="text-center text-xs uppercase tracking-wide">
              Puts
            </TableHead>
          </TableRow>
          <TableRow>
            {[...headers].reverse().map((header) => (
              <TableHead key={`call-${header}`} className="text-right">
                {header}
              </TableHead>
            ))}
            <TableHead className="text-center">—</TableHead>
            {headers.map((header) => (
              <TableHead key={`put-${header}`} className="text-right">
                {header}
              </TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row) => (
            <TableRow key={row.strike} className={cn(row.strike === atmStrike && "bg-muted/60")}>
              <SideCells contract={row.call} showGreeks={showGreeks} reverse />
              <TableCell className="text-center font-medium tabular-nums">{money(row.strike)}</TableCell>
              <SideCells contract={row.put} showGreeks={showGreeks} reverse={false} />
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}
