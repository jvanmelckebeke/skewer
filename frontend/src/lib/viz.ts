/**
 * Chart tokens and shared Recharts geometry.
 *
 * The hexes themselves live in index.css so light and dark swap in one place;
 * everything here refers to them by role. Slot order is fixed: calls are always
 * slot 1 and puts always slot 2, whichever of them the current filter left on
 * screen, so a reader who learned the pairing keeps it.
 */

export const VIZ = {
  call: "var(--viz-1)",
  put: "var(--viz-2)",
  reference: "var(--viz-3)",
  grid: "var(--viz-grid)",
  axis: "var(--viz-axis)",
  muted: "var(--viz-muted)",
} as const

export const seriesColor = (right: "call" | "put") => (right === "call" ? VIZ.call : VIZ.put)

/** 2px round-capped lines, >=8px markers, hairline solid grid — the mark specs. */
export const LINE = { strokeWidth: 2, strokeLinecap: "round", strokeLinejoin: "round" } as const
export const DOT = { r: 4, strokeWidth: 2 } as const
export const GRID = { stroke: VIZ.grid, strokeWidth: 1, vertical: false } as const

export const AXIS = {
  stroke: VIZ.axis,
  tickLine: false,
  tick: { fill: VIZ.muted, fontSize: 11 },
} as const

/** Height that includes the x-axis band, so the label row is never clipped. */
export const CHART_HEIGHT = 280
