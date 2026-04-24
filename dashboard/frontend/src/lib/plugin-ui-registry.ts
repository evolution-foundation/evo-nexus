/**
 * plugin-ui-registry.ts — Wave 2.1
 *
 * Client-side registry for plugin UI declarations (pages + sidebar_groups).
 * Mirrors the pattern of agent-meta.ts:
 *   - module-level singleton, idempotent hydration
 *   - synchronous getters always work (return empty on pre-hydration)
 *   - silently keeps prior state on fetch error
 */

// ---------------------------------------------------------------------------
// Types (mirror server-side PluginPage / PluginSidebarGroup Pydantic models)
// ---------------------------------------------------------------------------

export interface PluginPage {
  id: string
  label: string
  /** Sub-path under /plugins-ui/<slug>/ */
  path: string
  /** Relative bundle path, e.g. "ui/pages/projects.js" */
  bundle: string
  custom_element_name: string
  sidebar_group?: string | null
  icon?: string | null
  order?: number
}

export interface PluginSidebarGroup {
  id: string
  label: string
  order?: number
  collapsible?: boolean
}

export interface PluginUiEntry {
  slug: string
  version: string
  pages: PluginPage[]
  sidebar_groups: PluginSidebarGroup[]
}

// ---------------------------------------------------------------------------
// Module-level registry
// ---------------------------------------------------------------------------

let _registry: PluginUiEntry[] = []
let _hydrated = false

// ---------------------------------------------------------------------------
// hydratePluginUiRegistry — called once post-login in App.tsx
// ---------------------------------------------------------------------------

/**
 * Fetch /api/plugin-ui-registry and populate the local registry.
 *
 * - Idempotent: second call is a no-op unless `force` is true.
 * - Silently keeps prior state on network / non-2xx error.
 */
export async function hydratePluginUiRegistry(force = false): Promise<void> {
  if (_hydrated && !force) return
  try {
    const API = import.meta.env.DEV ? 'http://localhost:8080' : ''
    const res = await fetch(`${API}/api/plugin-ui-registry`, { credentials: 'include' })
    if (!res.ok) return
    const data: { plugins: PluginUiEntry[] } = await res.json()
    _registry = data.plugins ?? []
    _hydrated = true
  } catch {
    // Network error — keep prior state, don't mark hydrated so next call retries
  }
}

// ---------------------------------------------------------------------------
// Synchronous getters
// ---------------------------------------------------------------------------

/** All registered plugin UI entries. */
export function getPluginUiRegistry(): PluginUiEntry[] {
  return _registry
}

/** True once hydratePluginUiRegistry() has completed at least once successfully. */
export function isPluginUiRegistryHydrated(): boolean {
  return _hydrated
}

/**
 * All pages across all active plugins, enriched with the plugin slug.
 * Includes a `bundle_url` with `?v=<version>` cache-buster.
 */
export function getAllPluginPages(): (PluginPage & { slug: string; bundle_url: string })[] {
  const pages: (PluginPage & { slug: string; bundle_url: string })[] = []
  for (const entry of _registry) {
    for (const page of entry.pages) {
      pages.push({
        ...page,
        slug: entry.slug,
        bundle_url: `/plugins/${entry.slug}/${page.bundle}?v=${entry.version}`,
      })
    }
  }
  return pages
}

/** All sidebar groups across all active plugins. */
export function getAllPluginSidebarGroups(): (PluginSidebarGroup & { slug: string })[] {
  const groups: (PluginSidebarGroup & { slug: string })[] = []
  for (const entry of _registry) {
    for (const group of entry.sidebar_groups) {
      groups.push({ ...group, slug: entry.slug })
    }
  }
  return groups
}

/**
 * Find a specific page by slug + page id.
 * Returns undefined if not found (pre-hydration or disabled).
 */
export function getPluginPage(
  slug: string,
  pageId: string
): (PluginPage & { slug: string; bundle_url: string }) | undefined {
  return getAllPluginPages().find((p) => p.slug === slug && p.id === pageId)
}
