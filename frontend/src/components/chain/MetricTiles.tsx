import { StatTile } from "@/components/common/StatTile"
import { Skeleton } from "@/components/ui/skeleton"
import { useMetricDescriptors } from "@/hooks/queries"
import { metricValue } from "@/lib/format"

/**
 * Driven by GET /api/chains/metrics, so a metric added with @chain_metric on
 * the backend appears here without a frontend change — the registry pattern
 * reaching the UI.
 */
export function MetricTiles({ metrics }: { metrics: Record<string, number | null> }) {
  const descriptors = useMetricDescriptors()

  if (descriptors.isPending) {
    return (
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4 xl:grid-cols-7">
        {Array.from({ length: 7 }, (_, index) => (
          <Skeleton key={index} className="h-[74px] rounded-lg" />
        ))}
      </div>
    )
  }

  const known = descriptors.data ?? []
  if (known.length === 0) return null

  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4 xl:grid-cols-7">
      {known.map((descriptor) => (
        <StatTile
          key={descriptor.id}
          label={descriptor.label}
          value={metricValue(metrics[descriptor.id] ?? null, descriptor.unit)}
          hint={descriptor.description}
        />
      ))}
    </div>
  )
}
