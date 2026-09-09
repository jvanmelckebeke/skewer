export type CollectionState = "stale" | "collecting" | "paused"

/**
 * What a state decision needs, and no more. Both the status endpoint and the
 * chain summary row carry this field, so either can answer the question and
 * the overview does not have to make a request per row to ask it.
 */
export interface CollectionSignals {
  is_stale: boolean
}

/**
 * How an underlying's collection is doing.
 *
 * The staleness rule itself lives in the backend, which is the only layer that
 * knows when the session opened. Deciding it here would mean re-deriving that
 * at every call site, and getting it wrong the same way three times: measured
 * from the last poll alone, every underlying looks stalled for the first
 * quarter hour of every session.
 */
export function collectionState(
  isActive: boolean,
  signals: CollectionSignals | undefined,
): CollectionState {
  if (!isActive) return "paused"
  return signals?.is_stale ? "stale" : "collecting"
}

const ORDER: Record<CollectionState, number> = { stale: 0, collecting: 1, paused: 2 }

/** Anything wrong sorts to the top, because that is what the page is for. */
export function compareByState(a: CollectionState, b: CollectionState): number {
  return ORDER[a] - ORDER[b]
}
