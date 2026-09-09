import { CollectionHealth } from "@/components/collector/CollectionHealth"
import { ErrorState, LoadingState } from "@/components/common/States"
import { StatTile } from "@/components/common/StatTile"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { useCollectionStatus } from "@/hooks/queries"
import { formatMarketClock, formatTimestamp } from "@/lib/format"

export function SystemPage() {
  const status = useCollectionStatus()

  if (status.isPending) return <LoadingState label="Loading collector status" />
  if (status.error) return <ErrorState error={status.error} context="Could not reach the collector" />

  const data = status.data

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Collector</h1>
        <p className="max-w-2xl text-sm text-muted-foreground">
          What the collector is configured to do and whether it should be running right now.
        </p>
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatTile
          label="Session"
          value={data.market_open ? "Open" : "Closed"}
          detail={`Market clock ${formatMarketClock(data.now_market)}`}
        />
        <StatTile label="Base cadence" value={`${data.base_poll_minutes} min`} detail="Fastest bucket" />
        <StatTile
          label="In collection"
          value={String(data.active_underlyings.length)}
          detail={data.active_underlyings.join(", ") || "Nothing is being polled"}
        />
        <StatTile
          label="Jobs enabled"
          value={`${data.jobs.filter((job) => job.enabled).length} / ${data.jobs.length}`}
          detail="A disabled job logs why at startup"
        />
      </div>

      <CollectionHealth />

      <div className="grid gap-4 xl:grid-cols-2">
        <Card className="gap-4">
          <CardHeader>
            <CardTitle>Cadence buckets</CardTitle>
            <CardDescription>
              Every poll fetches the whole chain; what varies is what gets persisted. Contracts are
              bucketed by days to expiry and sampled at a rate matched to how fast they re-price.
              Every strike is covered at every sampled instant, so a smile is never truncated.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Bucket</TableHead>
                  <TableHead className="text-right">Max DTE</TableHead>
                  <TableHead className="text-right">Interval</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.cadence_buckets.map((bucket) => (
                  <TableRow key={bucket.name}>
                    <TableCell className="font-medium">{bucket.name}</TableCell>
                    <TableCell className="text-right tabular-nums">
                      {bucket.max_dte === null ? (
                        <span className="text-muted-foreground">no limit</span>
                      ) : (
                        bucket.max_dte
                      )}
                    </TableCell>
                    <TableCell className="text-right tabular-nums text-muted-foreground">
                      {bucket.interval_minutes ? `${bucket.interval_minutes} min` : (bucket.note ?? "—")}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>

        <Card className="gap-4">
          <CardHeader>
            <CardTitle>Background jobs</CardTitle>
            <CardDescription>
              Jobs register themselves at import; a trigger factory returning nothing disables just
              that one without taking the others down.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Job</TableHead>
                  <TableHead>Trigger</TableHead>
                  <TableHead className="text-right">State</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.jobs.map((job) => (
                  <TableRow key={job.id}>
                    <TableCell className="font-medium">{job.id}</TableCell>
                    <TableCell className="font-mono text-xs text-muted-foreground">{job.trigger}</TableCell>
                    <TableCell className="text-right">
                      <Badge variant={job.enabled ? "outline" : "secondary"}>
                        {job.enabled ? "Enabled" : "Disabled"}
                      </Badge>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      </div>

      <p className="text-xs text-muted-foreground">
        Server time {formatTimestamp(data.now_utc)} UTC.
      </p>
    </div>
  )
}
