import type { ReactNode } from "react"

interface TooltipRow {
  label: string
  value: string
  color?: string
}

/**
 * Recharts hands its tooltip component an untyped payload; this is the shared
 * shell every chart renders into so the crosshair reads the same everywhere.
 */
export function TooltipShell({ title, rows, footer }: { title: string; rows: TooltipRow[]; footer?: ReactNode }) {
  return (
    <div className="rounded-md border bg-popover px-3 py-2 text-xs shadow-md">
      <div className="mb-1.5 font-medium text-popover-foreground">{title}</div>
      <ul className="space-y-1">
        {rows.map((row) => (
          <li key={row.label} className="flex items-center justify-between gap-4">
            <span className="flex items-center gap-1.5 text-muted-foreground">
              {row.color ? (
                <span
                  aria-hidden
                  className="size-2 rounded-full"
                  style={{ backgroundColor: row.color }}
                />
              ) : null}
              {row.label}
            </span>
            <span className="tabular-nums text-popover-foreground">{row.value}</span>
          </li>
        ))}
      </ul>
      {footer ? <div className="mt-1.5 text-muted-foreground">{footer}</div> : null}
    </div>
  )
}
