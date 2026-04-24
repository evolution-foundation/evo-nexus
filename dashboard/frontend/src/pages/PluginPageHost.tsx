/**
 * PluginPageHost — Wave 2.1
 *
 * Full-screen host for plugin pages served at /plugins-ui/:slug/*
 *
 * Renders a vanilla Web Component bundle in Shadow DOM same-scope mode
 * (matching the existing widget pattern). The bundle is loaded via dynamic
 * import once; subsequent navigations just remount the custom element.
 *
 * The component also sets up window.EvoNexus.navigate() via NavigatorBridge
 * so plugin code can trigger React Router navigation.
 */

import { useEffect, useRef, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import {
  getAllPluginPages,
  hydratePluginUiRegistry,
  isPluginUiRegistryHydrated,
} from '../lib/plugin-ui-registry'

// ---------------------------------------------------------------------------
// Module-level set to prevent double-loading the same bundle
// ---------------------------------------------------------------------------
const loadedBundles = new Set<string>()

async function loadPageBundle(bundleUrl: string): Promise<void> {
  if (loadedBundles.has(bundleUrl)) return
  await import(/* @vite-ignore */ bundleUrl)
  loadedBundles.add(bundleUrl)
}

// ---------------------------------------------------------------------------
// NavigatorBridge — wires window.EvoNexus.navigate() to useNavigate()
// Must render inside <Routes> (react-router Router context).
// ---------------------------------------------------------------------------
function NavigatorBridge() {
  const navigate = useNavigate()

  useEffect(() => {
    if (typeof window === 'undefined') return
    ;(window as Window & typeof globalThis & { EvoNexus?: { navigate: (to: string) => void } }).EvoNexus = {
      ...(window as Window & typeof globalThis & { EvoNexus?: Record<string, unknown> }).EvoNexus,
      navigate: (to: string) => navigate(to),
    }
    return () => {
      // Leave the bridge in place on unmount so other plugin pages can still navigate
    }
  }, [navigate])

  return null
}

// ---------------------------------------------------------------------------
// PageSlot — mounts the custom element once the bundle is loaded
// ---------------------------------------------------------------------------
interface PageSlotProps {
  customElementName: string
  bundleUrl: string
  slug: string
}

function PageSlot({ customElementName, bundleUrl, slug }: PageSlotProps) {
  const ref = useRef<HTMLDivElement>(null)
  const [error, setError] = useState<string | null>(null)
  const [ready, setReady] = useState(false)

  useEffect(() => {
    let cancelled = false
    setReady(false)
    setError(null)

    loadPageBundle(bundleUrl)
      .then(() => {
        if (!cancelled) setReady(true)
      })
      .catch((e: unknown) => {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : 'Failed to load plugin page bundle')
        }
      })

    return () => { cancelled = true }
  }, [bundleUrl])

  useEffect(() => {
    if (!ready || !ref.current) return
    // Clear previous content then mount the web component
    ref.current.innerHTML = ''
    const el = document.createElement(customElementName)
    // Pass slug as attribute so the component can call /api/plugins/<slug>/...
    el.setAttribute('slug', slug)
    ref.current.appendChild(el)
  }, [ready, customElementName, slug])

  if (error) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="bg-[#161b22] border border-red-500/30 rounded-2xl p-6 max-w-lg text-center">
          <p className="text-red-400 text-sm font-medium mb-2">Plugin page failed to load</p>
          <p className="text-[#5a6b7f] text-xs font-mono">{error}</p>
        </div>
      </div>
    )
  }

  if (!ready) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-[#5a6b7f] text-sm">Loading plugin…</div>
      </div>
    )
  }

  return <div ref={ref} className="w-full h-full" />
}

// ---------------------------------------------------------------------------
// PluginPageHost — main export
// ---------------------------------------------------------------------------
export default function PluginPageHost() {
  const { slug, '*': splat } = useParams<{ slug: string; '*': string }>()
  const navigate = useNavigate()

  const pageSubPath = splat || ''

  // Wait for the registry to hydrate before declaring "not found". On hard refresh
  // the App-level hydrate effect may not have completed yet; trigger it here too
  // so that deep-linking to a plugin page works.
  const [registryReady, setRegistryReady] = useState(isPluginUiRegistryHydrated())
  useEffect(() => {
    if (registryReady) return
    let cancelled = false
    hydratePluginUiRegistry().then(() => {
      if (!cancelled) setRegistryReady(isPluginUiRegistryHydrated())
    })
    return () => {
      cancelled = true
    }
  }, [registryReady])

  // Find the matching page declaration from the registry
  const allPages = getAllPluginPages()
  const page = allPages.find(
    (p) => p.slug === slug && (p.path === pageSubPath || p.path === pageSubPath.replace(/\/$/, ''))
  )

  // Also check for index (empty path)
  const resolvedPage = page ?? allPages.find((p) => p.slug === slug && (p.path === '' || p.path === 'index'))

  if (!slug) {
    return (
      <div className="flex items-center justify-center h-full">
        <p className="text-[#5a6b7f] text-sm">Invalid plugin URL.</p>
      </div>
    )
  }

  if (!resolvedPage) {
    if (!registryReady) {
      return (
        <div className="flex items-center justify-center h-full">
          <div className="text-[#5a6b7f] text-sm">Loading plugin…</div>
        </div>
      )
    }
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-center">
          <p className="text-[#5a6b7f] text-sm mb-2">
            Plugin page <code className="text-xs bg-[#21262d] px-1 rounded">{slug}/{pageSubPath || '(index)'}</code> not found.
          </p>
          <button
            onClick={() => navigate('/plugins')}
            className="text-xs text-[#00FFA7] hover:underline"
          >
            Go to Plugins
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="w-full h-full flex flex-col">
      <NavigatorBridge />
      <PageSlot
        customElementName={resolvedPage.custom_element_name}
        bundleUrl={resolvedPage.bundle_url}
        slug={slug}
      />
    </div>
  )
}
