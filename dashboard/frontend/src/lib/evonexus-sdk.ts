/**
 * evonexus-sdk.ts — Wave 2.1
 *
 * Global SDK exposed on window.EvoNexus for use by plugin page bundles.
 * Plugin bundles run in the main window scope (same-scope, no iframe).
 * They call dashboard APIs via this object rather than crafting fetch() calls.
 *
 * Initialised once in App.tsx alongside hydratePluginUiRegistry().
 * NavigatorBridge (PluginPageHost.tsx) adds window.EvoNexus.navigate().
 *
 * Usage in plugin bundle:
 *   const { data } = await window.EvoNexus.readonlyData('pm-essentials', 'open_projects')
 *   await window.EvoNexus.writableData('pm-essentials', 'projects', 'POST', { name: 'X' })
 */

const API_BASE = import.meta.env.DEV ? 'http://localhost:8080' : ''

/** Credentials included for same-origin session cookie (no token needed). */
async function apiFetch(path: string, options?: RequestInit): Promise<Response> {
  return fetch(`${API_BASE}${path}`, {
    credentials: 'include',
    ...options,
  })
}

export interface ReadonlyDataResult {
  query: string
  count: number
  rows: Record<string, unknown>[]
}

export interface WritableDataResult {
  id?: number
  updated?: number
  deleted?: number
}

/** SDK object shape exposed on window.EvoNexus */
export interface EvoNexusSDK {
  /** Fetch ticket list, optionally filtered by source_plugin */
  getTickets(params?: {
    source_plugin?: string
    status?: string[]
    priority?: string[]
    limit?: number
    offset?: number
  }): Promise<{ tickets: Record<string, unknown>[]; total: number }>

  /** Execute a declared readonly query */
  readonlyData(slug: string, queryId: string, params?: Record<string, string>): Promise<ReadonlyDataResult>

  /** Execute a writable mutation (POST=insert, PUT=update, DELETE=delete) */
  writableData(
    slug: string,
    resourceId: string,
    method: 'POST' | 'PUT' | 'DELETE',
    body: Record<string, unknown>
  ): Promise<WritableDataResult>

  /** Navigate to a dashboard route (wired by NavigatorBridge) */
  navigate(to: string): void
}

/** Build the SDK object (navigate is a stub until NavigatorBridge overwrites it). */
function buildSdk(): EvoNexusSDK {
  return {
    async getTickets(params = {}) {
      const qs = new URLSearchParams()
      if (params.source_plugin) qs.set('source_plugin', params.source_plugin)
      if (params.limit != null) qs.set('limit', String(params.limit))
      if (params.offset != null) qs.set('offset', String(params.offset))
      ;(params.status ?? []).forEach((s) => qs.append('status', s))
      ;(params.priority ?? []).forEach((p) => qs.append('priority', p))
      const res = await apiFetch(`/api/tickets?${qs.toString()}`)
      if (!res.ok) throw new Error(`GET /api/tickets failed: ${res.status}`)
      return res.json()
    },

    async readonlyData(slug, queryId, params = {}) {
      const qs = new URLSearchParams(params)
      const res = await apiFetch(`/api/plugins/${slug}/readonly-data/${queryId}?${qs.toString()}`)
      if (!res.ok) throw new Error(`readonlyData '${queryId}' failed: ${res.status}`)
      return res.json()
    },

    async writableData(slug, resourceId, method, body) {
      const res = await apiFetch(`/api/plugins/${slug}/data/${resourceId}`, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}))
        throw new Error((detail as { error?: string }).error ?? `writableData failed: ${res.status}`)
      }
      return res.json()
    },

    navigate(to) {
      // Stub until NavigatorBridge wires the real React Router navigate().
      // Falls back to window.location for pre-hydration calls.
      window.location.href = to
    },
  }
}

/** Initialise window.EvoNexus if not already done. Safe to call multiple times. */
export function initEvoNexusSdk(): void {
  if (typeof window === 'undefined') return
  const w = window as Window & typeof globalThis & { EvoNexus?: Partial<EvoNexusSDK> }
  if (!w.EvoNexus) {
    w.EvoNexus = buildSdk()
  } else {
    // Merge so NavigatorBridge additions aren't overwritten if called again
    const sdk = buildSdk()
    w.EvoNexus = { ...sdk, ...w.EvoNexus }
  }
}
