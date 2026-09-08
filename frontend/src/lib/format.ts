/**
 * Formatters for a UI where null is a real, meaningful value. Every one of
 * these renders a missing number as an em-less placeholder rather than 0, so a
 * contract with no identifiable IV never looks like a contract at zero vol.
 */

export const NO_VALUE = "–"

function nullable(value: number | null | undefined): value is number {
  return value !== null && value !== undefined && Number.isFinite(value)
}

export function money(value: number | null | undefined, digits = 2): string {
  return nullable(value) ? value.toFixed(digits) : NO_VALUE
}

/** IV, delta and friends arrive as decimals; humans read vol in percent. */
export function percent(value: number | null | undefined, digits = 1): string {
  return nullable(value) ? `${(value * 100).toFixed(digits)}%` : NO_VALUE
}

export function decimal(value: number | null | undefined, digits = 3): string {
  return nullable(value) ? value.toFixed(digits) : NO_VALUE
}

export function integer(value: number | null | undefined): string {
  return nullable(value) ? value.toLocaleString("en-US", { maximumFractionDigits: 0 }) : NO_VALUE
}

/** 1,284 / 12.9K / 4.2M — for stat tiles, where width is scarce. */
export function compact(value: number | null | undefined, digits = 1): string {
  if (!nullable(value)) return NO_VALUE
  const abs = Math.abs(value)
  if (abs >= 1_000_000_000) return `${(value / 1_000_000_000).toFixed(digits)}B`
  if (abs >= 1_000_000) return `${(value / 1_000_000).toFixed(digits)}M`
  if (abs >= 10_000) return `${(value / 1_000).toFixed(digits)}K`
  return value.toLocaleString("en-US", { maximumFractionDigits: digits })
}

const DATE = new Intl.DateTimeFormat("en-GB", { day: "2-digit", month: "short", year: "numeric" })
const DATE_SHORT = new Intl.DateTimeFormat("en-GB", { day: "2-digit", month: "short" })
const TIME = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit" })

/** Backend dates are plain `YYYY-MM-DD`; parse as UTC so no timezone shifts the day. */
function fromIsoDate(iso: string): Date {
  return new Date(`${iso}T00:00:00Z`)
}

export function formatDate(iso: string | null | undefined): string {
  return iso ? DATE.format(fromIsoDate(iso)) : NO_VALUE
}

export function formatDateShort(iso: string | null | undefined): string {
  return iso ? DATE_SHORT.format(fromIsoDate(iso)) : NO_VALUE
}

export function formatTimestamp(iso: string | null | undefined): string {
  if (!iso) return NO_VALUE
  const at = new Date(iso)
  return `${DATE.format(at)} ${TIME.format(at)}`
}

export function formatClock(iso: string | null | undefined): string {
  return iso ? TIME.format(new Date(iso)) : NO_VALUE
}

/**
 * The exchange's own wall clock, not the viewer's. The backend already sends
 * the market time with its offset; rendering it in the browser's zone would
 * turn "the session opens at 09:30" into whatever that is locally.
 */
const MARKET = new Intl.DateTimeFormat("en-GB", {
  timeZone: "America/New_York",
  day: "2-digit",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
})

export function formatMarketClock(iso: string | null | undefined): string {
  return iso ? `${MARKET.format(new Date(iso))} ET` : NO_VALUE
}

export function relativeMinutes(minutes: number | null | undefined): string {
  if (!nullable(minutes)) return "never"
  if (minutes < 1) return "just now"
  if (minutes < 60) return `${Math.round(minutes)}m ago`
  const hours = minutes / 60
  if (hours < 24) return `${Math.round(hours)}h ago`
  return `${Math.round(hours / 24)}d ago`
}

/** Human-readable rendering for a metric, driven by its registry-declared unit. */
export function metricValue(value: number | null, unit: string): string {
  switch (unit) {
    case "vol":
      return percent(value, 2)
    case "ratio":
      return decimal(value, 2)
    case "currency":
      return money(value, 2)
    case "shares":
      return compact(value, 2)
    default:
      return decimal(value, 4)
  }
}

/**
 * `NVDA  250117C00140000` — the OCC symbol split into the four things it
 * encodes. Display only; the canonical parser lives in the backend.
 */
export function describeContract(symbol: string, expiration: string, strike: number, right: string): string {
  return `${symbol} ${formatDateShort(expiration)} ${strike} ${right === "call" ? "C" : "P"}`
}
