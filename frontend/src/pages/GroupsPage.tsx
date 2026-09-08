import { useState, type FormEvent } from "react"
import { Link } from "react-router-dom"
import { Plus, Trash2, X } from "lucide-react"

import { ErrorState, LoadingState } from "@/components/common/States"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  useAddToGroup,
  useCreateGroup,
  useDeleteGroup,
  useGroups,
  useRemoveFromGroup,
  useUnderlyings,
} from "@/hooks/queries"
import type { Group } from "@/types"

export function GroupsPage() {
  const groups = useGroups()
  const [name, setName] = useState("")
  const create = useCreateGroup()

  function submit(event: FormEvent) {
    event.preventDefault()
    const trimmed = name.trim()
    if (!trimmed) return
    create.mutate({ name: trimmed }, { onSuccess: () => setName("") })
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Groups</h1>
          <p className="max-w-2xl text-sm text-muted-foreground">
            Group membership is the collection switch. An underlying that belongs to no group keeps
            everything already collected but stops being polled.
          </p>
        </div>
        <form onSubmit={submit} className="flex gap-2">
          <Input
            value={name}
            placeholder="New group"
            onChange={(event) => setName(event.target.value)}
            className="w-48"
          />
          <Button type="submit" disabled={create.isPending || name.trim() === ""}>
            <Plus />
            Create
          </Button>
        </form>
      </div>

      {create.error ? <p className="text-sm text-destructive">{(create.error as Error).message}</p> : null}

      {groups.isPending ? (
        <LoadingState label="Loading groups" />
      ) : groups.error ? (
        <ErrorState error={groups.error} context="Could not load groups" />
      ) : (
        <div className="grid gap-4 lg:grid-cols-2 xl:grid-cols-3">
          {groups.data.map((group) => (
            <GroupCard key={group.id} group={group} />
          ))}
        </div>
      )}
    </div>
  )
}

function GroupCard({ group }: { group: Group }) {
  const underlyings = useUnderlyings()
  const add = useAddToGroup()
  const remove = useRemoveFromGroup()
  const destroy = useDeleteGroup()

  const memberIds = new Set(group.underlyings.map((underlying) => underlying.id))
  const candidates = (underlyings.data ?? []).filter((underlying) => !memberIds.has(underlying.id))

  return (
    <Card className="gap-4">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          {group.name}
          {group.is_default ? <Badge variant="secondary">Default</Badge> : null}
        </CardTitle>
        <CardDescription>
          {group.description ?? `${group.underlyings.length} underlying${group.underlyings.length === 1 ? "" : "s"}`}
        </CardDescription>
        {group.is_default ? null : (
          <CardAction>
            <Button
              variant="ghost"
              size="icon"
              aria-label={`Delete group ${group.name}`}
              onClick={() => {
                if (window.confirm(`Delete the group "${group.name}"? The underlyings themselves are kept.`)) {
                  destroy.mutate(group.id)
                }
              }}
            >
              <Trash2 />
            </Button>
          </CardAction>
        )}
      </CardHeader>

      <CardContent className="space-y-3">
        {group.underlyings.length === 0 ? (
          <p className="text-sm text-muted-foreground">Empty — nothing in this group is polled.</p>
        ) : (
          <ul className="flex flex-wrap gap-2">
            {group.underlyings.map((underlying) => (
              <li key={underlying.id}>
                <span className="inline-flex items-center gap-1 rounded-md border py-0.5 pl-2 pr-0.5 text-sm">
                  <Link to={`/chains/${underlying.symbol}`} className="font-medium hover:underline">
                    {underlying.symbol}
                  </Link>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="size-6"
                    aria-label={`Remove ${underlying.symbol} from ${group.name}`}
                    onClick={() => remove.mutate({ groupId: group.id, underlyingId: underlying.id })}
                  >
                    <X />
                  </Button>
                </span>
              </li>
            ))}
          </ul>
        )}

        {candidates.length > 0 ? (
          <Select
            value=""
            onValueChange={(value) => add.mutate({ groupId: group.id, underlyingIds: [Number(value)] })}
          >
            <SelectTrigger className="w-full">
              <SelectValue placeholder="Add an underlying" />
            </SelectTrigger>
            <SelectContent>
              {candidates.map((underlying) => (
                <SelectItem key={underlying.id} value={String(underlying.id)}>
                  {underlying.symbol} — {underlying.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        ) : null}
      </CardContent>
    </Card>
  )
}
