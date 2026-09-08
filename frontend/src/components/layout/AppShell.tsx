import { NavLink, Outlet } from "react-router-dom"
import { cn } from "cn"

import { ThemeToggle } from "@/components/layout/ThemeToggle"
import { MarketStatus } from "@/components/layout/MarketStatus"

const NAV = [
  { to: "/", label: "Overview", end: true },
  { to: "/groups", label: "Groups", end: false },
  { to: "/system", label: "Collector", end: false },
]

export function AppShell() {
  return (
    <div className="min-h-svh bg-background">
      <header className="sticky top-0 z-30 border-b bg-background/85 backdrop-blur">
        <div className="mx-auto flex h-14 max-w-[1600px] items-center gap-6 px-4 sm:px-6">
          <NavLink to="/" className="flex items-baseline gap-2">
            <span className="text-base font-semibold tracking-tight">skewer</span>
            <span className="hidden text-xs text-muted-foreground sm:inline">option surface</span>
          </NavLink>

          <nav className="flex items-center gap-1">
            {NAV.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                className={({ isActive }) =>
                  cn(
                    "rounded-md px-3 py-1.5 text-sm transition-colors",
                    isActive
                      ? "bg-muted font-medium text-foreground"
                      : "text-muted-foreground hover:text-foreground",
                  )
                }
              >
                {item.label}
              </NavLink>
            ))}
          </nav>

          <div className="ml-auto flex items-center gap-3">
            <MarketStatus />
            <ThemeToggle />
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-[1600px] px-4 py-6 sm:px-6">
        <Outlet />
      </main>
    </div>
  )
}
