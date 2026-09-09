import { Link, useLocation } from "react-router-dom"
import { FolderCog, LayoutDashboard, Settings2 } from "lucide-react"

import { GroupsNav } from "@/components/layout/GroupsNav"
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarRail,
} from "@/components/ui/sidebar"

export function AppSidebar() {
  const location = useLocation()

  return (
    <Sidebar collapsible="icon">
      <SidebarHeader>
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton size="lg" asChild tooltip="skewer">
              <Link to="/">
                <div className="bg-primary text-primary-foreground flex aspect-square size-8 items-center justify-center rounded-lg text-sm font-bold">
                  s
                </div>
                <div className="grid leading-tight">
                  <span className="font-semibold">skewer</span>
                  <span className="text-muted-foreground text-xs">option surface</span>
                </div>
              </Link>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarHeader>

      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupContent>
            <SidebarMenu>
              <SidebarMenuItem>
                <SidebarMenuButton
                  asChild
                  isActive={location.pathname === "/"}
                  tooltip="Overview"
                >
                  <Link to="/">
                    <LayoutDashboard />
                    <span>Overview</span>
                  </Link>
                </SidebarMenuButton>
              </SidebarMenuItem>
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>

        <GroupsNav />
      </SidebarContent>

      <SidebarFooter>
        <SidebarMenu>
          <SidebarMenuItem>
            {/*
              The groups page is now for editing a group rather than for
              finding one: renaming, icons, descriptions, bulk membership.
              Browsing happens in the nav above.
            */}
            <SidebarMenuButton
              asChild
              isActive={location.pathname === "/groups"}
              tooltip="Manage groups"
            >
              <Link to="/groups">
                <FolderCog />
                <span>Manage groups</span>
              </Link>
            </SidebarMenuButton>
          </SidebarMenuItem>
          <SidebarMenuItem>
            {/*
              The collector sits in the footer because it is where you go when
              something looks wrong, not somewhere you pass through.
            */}
            <SidebarMenuButton
              asChild
              isActive={location.pathname === "/system"}
              tooltip="Collector"
            >
              <Link to="/system">
                <Settings2 />
                <span>Collector</span>
              </Link>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarFooter>

      <SidebarRail />
    </Sidebar>
  )
}
