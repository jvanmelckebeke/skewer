import { useEffect, useMemo, useState } from "react"
import { Link, useParams } from "react-router-dom"
import { ArrowLeft } from "lucide-react"

import { ChainTable } from "@/components/chain/ChainTable"
import { ExpirationPicker } from "@/components/chain/ExpirationPicker"
import { MetricTiles } from "@/components/chain/MetricTiles"
import { SessionRibbonChart } from "@/components/charts/SessionRibbonChart"
import { SmileChart } from "@/components/charts/SmileChart"
import { TermChart } from "@/components/charts/TermChart"
import { ErrorState, LoadingState } from "@/components/common/States"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import { useChain, useSessionProfile, useTermStructure } from "@/hooks/queries"
import { formatDate, formatTimestamp, integer, money } from "@/lib/format"
import type { ContractQuote } from "@/types"

export function ChainPage() {
  const { symbol = "" } = useParams()
  const chain = useChain(symbol)
  const term = useTermStructure(symbol)

  const [expiration, setExpiration] = useState<string | null>(null)
  const [showGreeks, setShowGreeks] = useState(false)
  // Both null means "the backend's most recent session, and its nearest
  // expiry". The response carries both lists back, so the pickers populate
  // from one round trip and never need the live chain's expirations — a past
  // session holds expiries that have since expired off it.
  const [session, setSession] = useState<string | null>(null)
  const [sessionExpiry, setSessionExpiry] = useState<string | null>(null)

  const profile = useSessionProfile(symbol, sessionExpiry, session)

  const expirations = chain.data?.expirations ?? []

  // Default to the front expiration, and re-anchor when the previously selected
  // one rolls off the chain.
  useEffect(() => {
    if (expirations.length === 0) return
    if (expiration === null || !expirations.includes(expiration)) {
      setExpiration(expirations[0])
    }
  }, [expirations, expiration])

  const dteByExpiration = useMemo(() => {
    const map = new Map<string, number>()
    for (const contract of chain.data?.contracts ?? []) {
      if (!map.has(contract.expiration)) map.set(contract.expiration, contract.dte)
    }
    return map
  }, [chain.data])

  const selected: ContractQuote[] = useMemo(() => {
    if (!chain.data || expiration === null) return []
    return chain.data.contracts.filter((contract) => contract.expiration === expiration)
  }, [chain.data, expiration])

  if (chain.isPending) return <LoadingState label={`Loading ${symbol} chain`} />
  if (chain.error) {
    return (
      <div className="space-y-4">
        <BackLink />
        <ErrorState error={chain.error} context={`No stored chain for ${symbol} yet`} />
      </div>
    )
  }

  const { spot, ts, contracts, metrics } = chain.data
  const priced = contracts.filter((contract) => contract.iv !== null).length

  return (
    <div className={chain.isFetching ? "space-y-6 opacity-70 transition-opacity" : "space-y-6"}>
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="space-y-1">
          <BackLink />
          <div className="flex items-baseline gap-3">
            <h1 className="text-2xl font-semibold tracking-tight">{symbol}</h1>
            <span className="text-2xl font-semibold tabular-nums text-muted-foreground">
              {money(spot)}
            </span>
          </div>
          <p className="text-sm text-muted-foreground">
            Snapshot {formatTimestamp(ts)} · {integer(contracts.length)} contracts,{" "}
            {integer(priced)} with an identifiable volatility
          </p>
        </div>
        <Badge variant="outline">{expirations.length} expirations</Badge>
      </div>

      <MetricTiles metrics={metrics} />

      <TermChart points={term.data?.points ?? []} />

      <div className="space-y-4">
        <ExpirationPicker
          expirations={expirations}
          value={expiration}
          onChange={setExpiration}
          dteFor={(value) => dteByExpiration.get(value) ?? null}
        />

        {expiration ? (
          <>
            <SmileChart contracts={selected} expiration={expiration} spot={spot} />

            <div className="flex flex-wrap items-center justify-between gap-3 pt-2">
              <div>
                <h2 className="text-sm font-medium">Session profile</h2>
                <p className="text-sm text-muted-foreground">
                  How this expiration behaved across a whole session, rather than at one instant.
                </p>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <Select
                  value={session ?? profile.data?.session_date ?? ""}
                  onValueChange={(value) => {
                    // A different session holds a different set of expiries, so
                    // the old selection cannot be carried across.
                    setSession(value)
                    setSessionExpiry(null)
                  }}
                  disabled={!profile.data}
                >
                  <SelectTrigger className="w-52">
                    <SelectValue placeholder="Session" />
                  </SelectTrigger>
                  <SelectContent>
                    {(profile.data?.available_sessions ?? []).map((option) => (
                      <SelectItem key={option.session_date} value={option.session_date}>
                        {formatDate(option.session_date)} · {option.slots}{" "}
                        {option.slots === 1 ? "slot" : "slots"}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>

                <Select
                  value={sessionExpiry ?? profile.data?.expiration ?? ""}
                  onValueChange={setSessionExpiry}
                  disabled={!profile.data}
                >
                  <SelectTrigger className="w-44">
                    <SelectValue placeholder="Expiration" />
                  </SelectTrigger>
                  <SelectContent>
                    {(profile.data?.available_expirations ?? []).map((day) => (
                      <SelectItem key={day} value={day}>
                        {formatDate(day)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>

            {profile.isPending ? (
              <LoadingState label="Loading session profile" />
            ) : profile.error ? (
              <ErrorState error={profile.error} context="No session profile for this expiration" />
            ) : (
              <SessionRibbonChart data={profile.data} />
            )}

            <div className="flex items-center justify-between gap-4">
              <h2 className="text-sm font-medium">
                Chain · {integer(selected.length)} contracts
              </h2>
              <div className="flex items-center gap-2">
                <Switch id="greeks" checked={showGreeks} onCheckedChange={setShowGreeks} />
                <Label htmlFor="greeks" className="text-sm font-normal text-muted-foreground">
                  Show gamma, vega, theta
                </Label>
              </div>
            </div>

            <ChainTable contracts={selected} spot={spot} showGreeks={showGreeks} />

            <p className="text-xs text-muted-foreground">
              Vega is quoted per volatility point and theta per calendar day; delta is per share,
              not per 100-share contract. A dash means the value is not identifiable — hover it for
              the reason. Contracts in slower cadence buckets are absent from slots they were not
              sampled in; nothing is interpolated in.
            </p>
          </>
        ) : null}
      </div>
    </div>
  )
}

function BackLink() {
  return (
    <Button variant="ghost" size="sm" asChild className="-ml-2 h-7 text-muted-foreground">
      <Link to="/">
        <ArrowLeft />
        Underlyings
      </Link>
    </Button>
  )
}
