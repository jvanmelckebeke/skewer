import { useState, type FormEvent } from "react"
import { Plus } from "lucide-react"

import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { useAddUnderlying, useGroups } from "@/hooks/queries"

const DEFAULT_GROUP = "__default__"

export function AddUnderlyingDialog() {
  const [open, setOpen] = useState(false)
  const [symbol, setSymbol] = useState("")
  const [groupId, setGroupId] = useState<string>(DEFAULT_GROUP)
  const groups = useGroups()
  const add = useAddUnderlying()

  function submit(event: FormEvent) {
    event.preventDefault()
    const ticker = symbol.trim().toUpperCase()
    if (!ticker) return
    add.mutate(
      { symbol: ticker, group_id: groupId === DEFAULT_GROUP ? null : Number(groupId) },
      {
        onSuccess: () => {
          setSymbol("")
          setGroupId(DEFAULT_GROUP)
          setOpen(false)
        },
      },
    )
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button size="sm">
          <Plus />
          Track underlying
        </Button>
      </DialogTrigger>
      <DialogContent>
        <form onSubmit={submit}>
          <DialogHeader>
            <DialogTitle>Track an underlying</DialogTitle>
            <DialogDescription>
              The ticker is checked against the provider before it is stored. US listings only —
              there is no option data for European tickers, so those are rejected here rather than
              collecting nothing.
            </DialogDescription>
          </DialogHeader>

          <div className="grid gap-4 py-4">
            <div className="grid gap-2">
              <Label htmlFor="symbol">Ticker</Label>
              <Input
                id="symbol"
                value={symbol}
                autoFocus
                placeholder="NVDA"
                autoComplete="off"
                spellCheck={false}
                onChange={(event) => setSymbol(event.target.value.toUpperCase())}
              />
            </div>

            <div className="grid gap-2">
              <Label htmlFor="group">Group</Label>
              <Select value={groupId} onValueChange={setGroupId}>
                <SelectTrigger id="group" className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={DEFAULT_GROUP}>Default group</SelectItem>
                  {(groups.data ?? []).map((group) => (
                    <SelectItem key={group.id} value={String(group.id)}>
                      {group.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <p className="text-xs text-muted-foreground">
                Group membership is the collection switch: an underlying in no group keeps its
                history but is not polled.
              </p>
            </div>

            {add.error ? (
              <p className="text-sm text-destructive">{(add.error as Error).message}</p>
            ) : null}
          </div>

          <DialogFooter>
            <Button type="submit" disabled={add.isPending || symbol.trim() === ""}>
              {add.isPending ? "Checking chain…" : "Track"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
