import { AlertTriangle, Inbox, Loader2 } from "lucide-react"

import { ApiError } from "@/lib/api"

export function LoadingState({ label = "Loading" }: { label?: string }) {
  return (
    <div className="flex items-center justify-center gap-2 py-12 text-sm text-muted-foreground">
      <Loader2 className="size-4 animate-spin" />
      {label}
    </div>
  )
}

export function EmptyState({ title, description }: { title: string; description?: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 py-12 text-center">
      <Inbox className="size-6 text-muted-foreground" />
      <p className="text-sm font-medium">{title}</p>
      {description ? <p className="max-w-md text-sm text-muted-foreground">{description}</p> : null}
    </div>
  )
}

/**
 * A 404 from a chain endpoint is the normal "nothing collected in this slot
 * yet" case, not a failure — say so rather than showing a red error.
 */
export function ErrorState({ error, context }: { error: unknown; context?: string }) {
  const status = error instanceof ApiError ? error.status : null
  const message = error instanceof Error ? error.message : String(error)

  // `context` is the caller's wording for "there is nothing here", so it only
  // fits a 404. A gateway error wearing it would tell the reader nothing was
  // collected when the truth is that the backend could not be reached.
  if (status === 404) {
    return <EmptyState title={context ?? "Nothing here yet"} description={message} />
  }
  return (
    <div className="flex flex-col items-center justify-center gap-2 py-12 text-center">
      <AlertTriangle className="size-6 text-destructive" />
      <p className="text-sm font-medium">Request failed</p>
      <p className="max-w-md text-sm text-muted-foreground">{message}</p>
    </div>
  )
}
