import { cn } from "cn"

import { formatDateShort } from "@/lib/format"

interface ExpirationPickerProps {
  expirations: string[]
  value: string | null
  onChange: (expiration: string) => void
  /** Days to expiry per expiration, so the tenor is visible without arithmetic. */
  dteFor: (expiration: string) => number | null
}

/** One filter row above everything it scopes; every chart below re-renders against it. */
export function ExpirationPicker({ expirations, value, onChange, dteFor }: ExpirationPickerProps) {
  return (
    <div className="flex gap-2 overflow-x-auto pb-1" role="tablist" aria-label="Expiration">
      {expirations.map((expiration) => {
        const dte = dteFor(expiration)
        const active = expiration === value
        return (
          <button
            key={expiration}
            type="button"
            role="tab"
            aria-selected={active}
            onClick={() => onChange(expiration)}
            className={cn(
              "flex shrink-0 flex-col items-start rounded-md border px-3 py-1.5 text-left transition-colors",
              active ? "border-foreground/25 bg-muted" : "hover:bg-muted/50",
            )}
          >
            <span className="text-sm font-medium">{formatDateShort(expiration)}</span>
            <span className="text-xs text-muted-foreground tabular-nums">
              {dte === null ? "—" : `${dte}d`}
            </span>
          </button>
        )
      })}
    </div>
  )
}
