import { useState, type FormEvent } from "react"
import { Link, useLocation } from "react-router-dom"
import { Plus, X } from "lucide-react"

import { GroupIcon } from "@/components/common/GroupIcon"
import { Input } from "@/components/ui/input"
import {
  SidebarGroup,
  SidebarGroupAction,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarMenu,
  SidebarMenuBadge,
  SidebarMenuButton,
  SidebarMenuItem,
  useSidebar,
} from "@/components/ui/sidebar"
import { Skeleton } from "@/components/ui/skeleton"
import { useCreateGroup, useGroups } from "@/hooks/queries"

/**
 * Groups as navigation rather than as a settings screen.
 *
 * Membership is the collection switch, so this list is also the answer to
 * "what is skewer actually polling". The badge is the member count, which
 * makes an empty group visible as the thing it is: nothing being collected.
 */
export function GroupsNav() {
  const groups = useGroups()
  const location = useLocation()
  const create = useCreateGroup()
  const { setOpen } = useSidebar()
  const [adding, setAdding] = useState(false)
  const [name, setName] = useState("")

  function submit(event: FormEvent) {
    event.preventDefault()
    const trimmed = name.trim()
    if (!trimmed) return
    create.mutate(
      { name: trimmed },
      {
        onSuccess: () => {
          setName("")
          setAdding(false)
        },
      },
    )
  }

  return (
    <SidebarGroup>
      <SidebarGroupLabel>Groups</SidebarGroupLabel>
      <SidebarGroupAction
        title={adding ? "Cancel" : "New group"}
        onClick={() => {
          // Naming a group is impossible in the icon-collapsed rail, so
          // opening the form opens the sidebar with it.
          setOpen(true)
          setAdding((open) => !open)
        }}
      >
        {adding ? <X /> : <Plus />}
      </SidebarGroupAction>

      <SidebarGroupContent>
        <SidebarMenu>
          {groups.isPending ? (
            <SidebarMenuItem>
              <Skeleton className="h-8 w-full" />
            </SidebarMenuItem>
          ) : (
            (groups.data ?? []).map((group) => {
              const to = `/groups/${group.id}`
              return (
                <SidebarMenuItem key={group.id}>
                  <SidebarMenuButton
                    asChild
                    isActive={location.pathname === to}
                    tooltip={group.name}
                  >
                    <Link to={to}>
                      <GroupIcon name={group.icon} />
                      <span>{group.name}</span>
                    </Link>
                  </SidebarMenuButton>
                  <SidebarMenuBadge>{group.underlyings.length}</SidebarMenuBadge>
                </SidebarMenuItem>
              )
            })
          )}

          {adding ? (
            <SidebarMenuItem>
              <form onSubmit={submit} className="px-1 py-1">
                <Input
                  autoFocus
                  value={name}
                  placeholder="Group name"
                  className="h-8"
                  onChange={(event) => setName(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Escape") setAdding(false)
                  }}
                />
              </form>
            </SidebarMenuItem>
          ) : null}
        </SidebarMenu>
      </SidebarGroupContent>
    </SidebarGroup>
  )
}
