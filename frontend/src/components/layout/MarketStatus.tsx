import { Badge } from "@/components/ui/badge"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { useCollectionStatus } from "@/hooks/queries"
import { formatMarketClock } from "@/lib/format"

/**
 * "Why do I have no data" is answered here before it is asked. Status wears an
 * icon-free but always-labelled badge — the words carry the state, not the dot.
 */
export function MarketStatus() {
  const { data } = useCollectionStatus()
  if (!data) return null

  const open = data.market_open
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Badge variant={open ? "default" : "outline"} className="gap-1.5">
          <span
            aria-hidden
            className="size-1.5 rounded-full"
            style={{ backgroundColor: open ? "#0ca30c" : "var(--color-muted-foreground)" }}
          />
          {open ? "Market open" : "Market closed"}
        </Badge>
      </TooltipTrigger>
      <TooltipContent className="max-w-xs">
        US session (XNYS). Market clock {formatMarketClock(data.now_market)}, polling every{" "}
        {data.base_poll_minutes} minutes while open. {data.active_underlyings.length} underlying
        {data.active_underlyings.length === 1 ? "" : "s"} in collection.
      </TooltipContent>
    </Tooltip>
  )
}
