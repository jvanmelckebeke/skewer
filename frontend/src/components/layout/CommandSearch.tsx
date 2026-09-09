import { useEffect, useState } from "react"
import { useNavigate } from "react-router-dom"
import { Search } from "lucide-react"

import { GroupIcon } from "@/components/common/GroupIcon"
import { Button } from "@/components/ui/button"
import {
  Command,
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command"
import { useGroups, useUnderlyings } from "@/hooks/queries"

/**
 * Jump to a symbol or a group without going via the overview.
 *
 * Searches what is already tracked, not the whole market. Adding a new ticker
 * is a different act with different consequences (it starts collecting), and
 * it has its own dialog.
 */
export function CommandSearch() {
  const [open, setOpen] = useState(false)
  const navigate = useNavigate()
  const underlyings = useUnderlyings()
  const groups = useGroups()

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "k" && (event.metaKey || event.ctrlKey)) {
        event.preventDefault()
        setOpen((value) => !value)
      }
    }
    document.addEventListener("keydown", onKeyDown)
    return () => document.removeEventListener("keydown", onKeyDown)
  }, [])

  function go(to: string) {
    setOpen(false)
    navigate(to)
  }

  return (
    <>
      <Button
        variant="outline"
        className="text-muted-foreground w-full max-w-sm justify-start font-normal"
        onClick={() => setOpen(true)}
      >
        <Search />
        Search symbols and groups
        <kbd className="bg-muted text-muted-foreground ml-auto rounded px-1.5 py-0.5 font-mono text-xs">
          ⌘K
        </kbd>
      </Button>

      <CommandDialog open={open} onOpenChange={setOpen}>
        {/*
          CommandDialog renders its children straight into the dialog content
          without a Command wrapper, so the cmdk store has to be established
          here or every input and item below it renders without context.
        */}
        <Command>
          <CommandInput placeholder="Symbol or group name" />
          <CommandList>
            <CommandEmpty>Nothing tracked by that name.</CommandEmpty>
            <CommandGroup heading="Underlyings">
              {(underlyings.data ?? []).map((underlying) => (
                <CommandItem
                  key={underlying.id}
                  // The name joins the search text so "nvidia" finds NVDA.
                  value={`${underlying.symbol} ${underlying.name}`}
                  onSelect={() => go(`/chains/${underlying.symbol}`)}
                >
                  <span className="font-medium">{underlying.symbol}</span>
                  <span className="text-muted-foreground truncate">{underlying.name}</span>
                </CommandItem>
              ))}
            </CommandGroup>
            <CommandGroup heading="Groups">
              {(groups.data ?? []).map((group) => (
                  <CommandItem
                    key={group.id}
                    value={`group ${group.name}`}
                    onSelect={() => go(`/groups/${group.id}`)}
                  >
                    <GroupIcon name={group.icon} />
                    {group.name}
                  </CommandItem>
              ))}
            </CommandGroup>
          </CommandList>
        </Command>
      </CommandDialog>
    </>
  )
}
