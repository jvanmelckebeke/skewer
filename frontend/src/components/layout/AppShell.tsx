import { Outlet } from "react-router-dom"

import { AppSidebar } from "@/components/layout/AppSidebar"
import { CommandSearch } from "@/components/layout/CommandSearch"
import { MarketStatus } from "@/components/layout/MarketStatus"
import { ThemeToggle } from "@/components/layout/ThemeToggle"
import { SidebarInset, SidebarProvider, SidebarTrigger } from "@/components/ui/sidebar"

export function AppShell() {
  return (
    <SidebarProvider>
      <AppSidebar />
      <SidebarInset>
        <header className="sticky top-0 z-30 flex h-14 items-center gap-3 border-b bg-background/85 px-4 backdrop-blur">
          <SidebarTrigger className="-ml-1" />
          <div className="flex-1">
            <CommandSearch />
          </div>
          <MarketStatus />
          <ThemeToggle />
        </header>

        <main className="mx-auto w-full max-w-[1600px] px-4 py-6 sm:px-6">
          <Outlet />
        </main>
      </SidebarInset>
    </SidebarProvider>
  )
}
