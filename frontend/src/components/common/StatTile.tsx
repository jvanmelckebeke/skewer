import { cn } from "cn"

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"

interface StatTileProps {
  label: string
  value: string
  hint?: string
  /** Secondary line under the value — a unit, a count, whatever qualifies it. */
  detail?: string
  className?: string
}

/**
 * Label, value, optional qualifier. Proportional figures on the value: at this
 * size tabular-nums makes short numbers look loose, and nothing aligns
 * vertically here.
 */
export function StatTile({ label, value, hint, detail, className }: StatTileProps) {
  const body = (
    <div className={cn("rounded-lg border bg-card px-4 py-3", className)}>
      <div className="text-xs font-medium text-muted-foreground">{label}</div>
      <div className="mt-1 text-2xl font-semibold leading-none">{value}</div>
      {detail ? <div className="mt-1.5 text-xs text-muted-foreground">{detail}</div> : null}
    </div>
  )

  if (!hint) return body
  return (
    <Tooltip>
      <TooltipTrigger asChild>{body}</TooltipTrigger>
      <TooltipContent className="max-w-xs text-pretty">{hint}</TooltipContent>
    </Tooltip>
  )
}
