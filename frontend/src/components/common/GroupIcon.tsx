import { createElement } from "react"

import { resolveIcon } from "@/lib/icons"

/**
 * A group's icon, by stored name.
 *
 * Built with `createElement` rather than by assigning the resolved icon to a
 * capitalised local and rendering it as JSX. Both produce the same element,
 * but the latter reads to the linter as a component defined during render,
 * which it is not: `resolveIcon` hands back stable references out of lucide's
 * icon map, so a given name always resolves to the same component.
 */
export function GroupIcon({ name, className }: { name: string | null; className?: string }) {
  return createElement(resolveIcon(name), { className })
}
