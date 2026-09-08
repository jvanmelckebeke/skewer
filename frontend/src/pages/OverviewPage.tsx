import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Table,
  TableBody,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { EmptyState, ErrorState, LoadingState } from "@/components/common/States"
import { AddUnderlyingDialog } from "@/components/underlyings/AddUnderlyingDialog"
import { UnderlyingRow } from "@/components/underlyings/UnderlyingRow"
import { useUnderlyings } from "@/hooks/queries"

export function OverviewPage() {
  const underlyings = useUnderlyings()

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Tracked underlyings</h1>
          <p className="text-sm text-muted-foreground">
            Pick one to open its option surface.
          </p>
        </div>
        <AddUnderlyingDialog />
      </div>

      <Card className="gap-0 py-0">
        <CardHeader className="sr-only">
          <CardTitle>Underlyings</CardTitle>
          <CardDescription>Collection health per tracked ticker.</CardDescription>
        </CardHeader>
        <CardContent className="px-0">
          {underlyings.isPending ? (
            <LoadingState label="Loading underlyings" />
          ) : underlyings.error ? (
            <ErrorState error={underlyings.error} context="Could not load underlyings" />
          ) : underlyings.data.length === 0 ? (
            <EmptyState
              title="Nothing is being collected yet"
              description="Track a US-listed ticker to start capturing its option chain on the collector's cadence."
            />
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-24">Symbol</TableHead>
                  {/* Name absorbs the slack so the numeric columns stay together. */}
                  <TableHead>Name</TableHead>
                  <TableHead className="w-32 text-right">Live contracts</TableHead>
                  <TableHead className="w-28 text-right">Catalogued</TableHead>
                  <TableHead className="w-28 text-right">Div. yield</TableHead>
                  <TableHead className="w-28">Last poll</TableHead>
                  <TableHead className="w-28">State</TableHead>
                  <TableHead className="w-28 text-right">Collect</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {underlyings.data.map((underlying) => (
                  <UnderlyingRow key={underlying.id} underlying={underlying} />
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <p className="text-xs text-muted-foreground">
        Catalogued counts every contract skewer has ever seen for the ticker, expired ones included;
        live counts those that have not expired. The dimension table is the record of what exists,
        independent of what was sampled.
      </p>
    </div>
  )
}
