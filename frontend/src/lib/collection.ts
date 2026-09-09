import type { Underlying, UnderlyingStatus } from "@/types"

export type CollectionState = "stale" | "collecting" | "paused"

/**
 * Whether an underlying looks like it has stopped being collected.
 *
 * The window is 15 minutes against a 5-minute base cadence, so it takes three
 * missed polls to raise it. A shorter window would flag every underlying whose
 * contracts all sit in a slower tenor bucket, which is not a fault.
 *
 * Only meaningful while the market is open. Outside the session nothing is
 * polled, so "no poll for 14 hours" is the collector working correctly.
 */
export function collectionState(
  underlying: Pick<Underlying, "is_active">,
  status: UnderlyingStatus | undefined,
): CollectionState {
  if (!underlying.is_active) return "paused"
  if (!status?.market_open) return "collecting"
  const minutes = status.minutes_since_poll
  return minutes === null || minutes > 15 ? "stale" : "collecting"
}

const ORDER: Record<CollectionState, number> = { stale: 0, collecting: 1, paused: 2 }

/** Anything wrong sorts to the top, because that is what the page is for. */
export function compareByState(a: CollectionState, b: CollectionState): number {
  return ORDER[a] - ORDER[b]
}
