import { useState, type ReactNode } from "react"
import { BarChart3, TableIcon } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"

export interface TableColumn<Row> {
  key: string
  header: string
  /** Numeric columns are right-aligned and wear tabular figures. */
  numeric?: boolean
  render: (row: Row) => ReactNode
}

interface ChartCardProps<Row> {
  title: string
  description?: string
  legend?: ReactNode
  /** Chart-specific controls, rendered beside the table toggle. */
  actions?: ReactNode
  children: ReactNode
  /** The table-view twin. Every chart has one; it is how a colour-only read is avoided. */
  tableColumns: TableColumn<Row>[]
  tableRows: Row[]
  tableRowKey: (row: Row) => string
}

/**
 * A chart plus its WCAG-clean equivalent behind a toggle. The toggle is not a
 * nicety: three light-mode series hues sit below 3:1 against the surface, and
 * the table view is the documented relief for that.
 */
export function ChartCard<Row>({
  title,
  description,
  legend,
  actions,
  children,
  tableColumns,
  tableRows,
  tableRowKey,
}: ChartCardProps<Row>) {
  const [asTable, setAsTable] = useState(false)

  return (
    <Card className="gap-4">
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        {description ? <CardDescription>{description}</CardDescription> : null}
        <CardAction className="flex items-center gap-3">
          {actions}
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setAsTable((current) => !current)}
            aria-pressed={asTable}
          >
            {asTable ? <BarChart3 /> : <TableIcon />}
            {asTable ? "Chart" : "Table"}
          </Button>
        </CardAction>
      </CardHeader>
      <CardContent>
        {asTable ? (
          <div className="max-h-80 overflow-auto rounded-md border">
            <Table>
              <TableHeader className="sticky top-0 bg-card">
                <TableRow>
                  {tableColumns.map((column) => (
                    <TableHead key={column.key} className={column.numeric ? "text-right" : undefined}>
                      {column.header}
                    </TableHead>
                  ))}
                </TableRow>
              </TableHeader>
              <TableBody>
                {tableRows.map((row) => (
                  <TableRow key={tableRowKey(row)}>
                    {tableColumns.map((column) => (
                      <TableCell
                        key={column.key}
                        className={column.numeric ? "text-right tabular-nums" : undefined}
                      >
                        {column.render(row)}
                      </TableCell>
                    ))}
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        ) : (
          <>
            {legend ? <div className="mb-3">{legend}</div> : null}
            {children}
          </>
        )}
      </CardContent>
    </Card>
  )
}

/** Identity as a swatch beside text ink — never by colouring the text itself. */
export function Legend({ items }: { items: { label: string; color: string }[] }) {
  return (
    <ul className="flex flex-wrap items-center gap-4">
      {items.map((item) => (
        <li key={item.label} className="flex items-center gap-2 text-xs text-muted-foreground">
          <span
            aria-hidden
            className="h-0.5 w-4 rounded-full"
            style={{ backgroundColor: item.color }}
          />
          {item.label}
        </li>
      ))}
    </ul>
  )
}
