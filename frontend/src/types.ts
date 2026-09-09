/**
 * Mirrors backend/app/schemas/*.py. Nullable fields are nullable here for the
 * same reason they are there: skewer returns null where a number cannot be
 * identified, and a 0 would plot and lie.
 */

export type Right = "call" | "put"

export type Venue = "occ" | "eurex" | "euronext"

export interface Underlying {
  id: number
  symbol: string
  name: string
  currency: string
  venue: Venue
  venue_product: string | null
  dividend_yield: number
  dividend_yield_is_manual: boolean
  is_active: boolean
  last_polled_at: string | null
  last_eod_snapshot_on: string | null
  created_at: string
}

export interface UnderlyingStatus {
  symbol: string
  is_active: boolean
  live_contracts: number
  total_contracts: number
  last_polled_at: string | null
  last_slot: string | null
  last_eod_snapshot_on: string | null
  minutes_since_poll: number | null
  market_open: boolean
  is_stale: boolean
}

/**
 * One tracked underlying reduced to the numbers a list can show, from
 * `GET /api/chains/summary`.
 *
 * `metrics` is keyed by metric id and always carries every registered id, so a
 * fixed set of columns can be rendered without checking which keys came back.
 * A null value means the chain could not answer, never that the answer is zero.
 */
export interface ChainSummaryRow {
  symbol: string
  name: string
  venue: Venue
  currency: string
  is_active: boolean
  market_open: boolean
  minutes_since_poll: number | null
  is_stale: boolean
  ts: string | null
  spot: number | null
  metrics: Record<string, number | null>
}

export interface Group {
  id: number
  name: string
  description: string | null
  icon: string | null
  is_default: boolean
  position: number
  created_at: string
  underlyings: Underlying[]
}

export interface ContractQuote {
  /** The contract's key: an OCC symbol, or venue-prefixed like `EUX:<ISIN>`. */
  symbol: string
  expiration: string
  strike: number
  right: Right
  dte: number
  bid: number | null
  ask: number | null
  mark: number | null
  last: number | null
  last_trade_at: string | null
  volume: number
  open_interest: number | null
  iv: number | null
  iv_provider: number | null
  iv_status: string
  delta: number | null
  gamma: number | null
  vega: number | null
  theta: number | null
  rho: number | null
  model_price: number | null
  moneyness: number | null
}

export interface ChainResponse {
  symbol: string
  ts: string
  spot: number | null
  expirations: string[]
  contracts: ContractQuote[]
  metrics: Record<string, number | null>
  /** The q every greek on this chain was solved against. */
  dividend_yield: number | null
  /** True when a human set it; false when it was read off put-call parity. */
  dividend_yield_is_manual: boolean
}

export interface TermPoint {
  expiration: string
  atm_iv: number
  contracts: number
}

export interface TermStructureResponse {
  symbol: string
  ts: string
  spot: number | null
  points: TermPoint[]
}

export type MetricUnit = "vol" | "ratio" | "currency" | "shares"

export interface MetricDescriptor {
  id: string
  label: string
  unit: MetricUnit
  description: string
}

export interface ContractSeriesPoint {
  ts: string
  mark: number | null
  bid: number | null
  ask: number | null
  iv: number | null
  spot: number | null
  volume: number
}

export interface ContractSeriesResponse {
  /** The contract's key: an OCC symbol, or venue-prefixed like `EUX:<ISIN>`. */
  symbol: string
  expiration: string
  strike: number
  right: Right
  points: ContractSeriesPoint[]
}

export interface CadenceBucket {
  name: string
  /** Null on the open-ended tail bucket, which has no upper tenor. */
  max_dte: number | null
  interval_minutes: number | null
  note: string | null
}

export interface CollectionJob {
  id: string
  trigger: string
  enabled: boolean
}

export interface CollectionStatus {
  now_utc: string
  now_market: string
  market_open: boolean
  base_poll_minutes: number
  active_underlyings: string[]
  cadence_buckets: CadenceBucket[]
  jobs: CollectionJob[]
}

export interface SessionStrikePoint {
  /** The contract's key: an OCC symbol, or venue-prefixed like `EUX:<ISIN>`. */
  symbol: string
  strike: number
  right: Right
  /** Slots this contract was captured in, and how many of those yielded an IV. */
  samples: number
  priced: number
  iv_p25: number | null
  iv_p50: number | null
  iv_p75: number | null
  iv_min: number | null
  iv_max: number | null
  mark_p50: number | null
  /** Median spread as a fraction of mark, so it compares across strikes. */
  spread_p50: number | null
  volume: number
}

export interface SessionSummary {
  session_date: string
  slots: number
}

export interface SessionProfileResponse {
  symbol: string
  session_date: string
  expiration: string
  slots: number
  spot_low: number | null
  spot_high: number | null
  available_sessions: SessionSummary[]
  /** Expirations held by *this session*, not the live chain. */
  available_expirations: string[]
  points: SessionStrikePoint[]
}
