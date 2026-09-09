import { icons, type LucideIcon } from "lucide-react"

/**
 * Resolve a group's stored icon name to a Lucide component.
 *
 * Names are stored kebab-case (`bar-chart-3`), which is how Lucide's own site
 * names them, so what you copy from there is what you paste in. Anything
 * unrecognised falls back to a folder rather than rendering nothing, because a
 * missing icon in a nav list collapses the row's alignment.
 */
export function resolveIcon(name: string | null | undefined): LucideIcon {
  if (!name) return icons.Folder as LucideIcon
  const pascal = name.replace(/(^|-)([a-z0-9])/g, (_match, _sep, char: string) => char.toUpperCase())
  return (icons[pascal as keyof typeof icons] as LucideIcon) ?? (icons.Folder as LucideIcon)
}
