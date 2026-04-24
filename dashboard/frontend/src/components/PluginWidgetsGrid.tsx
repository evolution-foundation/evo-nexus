import { useEffect, useState, useRef } from 'react'
import { api } from '../lib/api'
import { Puzzle } from 'lucide-react'

interface PluginWidget {
  slug: string
  widget_id: string
  custom_element_name: string
  bundle_url: string
  mount_point: string
}

const loadedBundles = new Set<string>()

async function loadWidget(widget: PluginWidget): Promise<void> {
  if (loadedBundles.has(widget.bundle_url)) return
  // Use dynamic import with vite-ignore to prevent static analysis errors on runtime URLs
  await import(/* @vite-ignore */ widget.bundle_url)
  loadedBundles.add(widget.bundle_url)
}

function WidgetSlot({ widget }: { widget: PluginWidget }) {
  const ref = useRef<HTMLDivElement>(null)
  const [error, setError] = useState<string | null>(null)
  const [ready, setReady] = useState(false)

  useEffect(() => {
    let cancelled = false
    loadWidget(widget)
      .then(() => {
        if (!cancelled) setReady(true)
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : 'Failed to load widget')
      })
    return () => { cancelled = true }
  }, [widget.bundle_url])

  useEffect(() => {
    if (!ready || !ref.current) return
    // Mount the web component if not already there
    const existing = ref.current.querySelector(widget.custom_element_name)
    if (!existing) {
      const el = document.createElement(widget.custom_element_name)
      ref.current.appendChild(el)
    }
  }, [ready, widget.custom_element_name])

  if (error) {
    return (
      <div className="bg-[#161b22] border border-red-500/20 rounded-2xl p-4 text-xs text-red-400">
        Widget <code>{widget.widget_id}</code>: {error}
      </div>
    )
  }

  return (
    <div className="bg-[#161b22] border border-[#21262d] rounded-2xl overflow-hidden">
      {!ready && (
        <div className="flex items-center justify-center h-24 text-xs text-[#667085]">
          Loading widget…
        </div>
      )}
      <div ref={ref} />
    </div>
  )
}

interface Props {
  mountPoint?: string
}

export default function PluginWidgetsGrid({ mountPoint = 'overview' }: Props) {
  const [widgets, setWidgets] = useState<PluginWidget[]>([])

  useEffect(() => {
    api.get(`/plugins/widgets?mount=${mountPoint}`)
      .then((data: unknown) => {
        setWidgets(Array.isArray(data) ? data as PluginWidget[] : [])
      })
      .catch(() => setWidgets([]))
  }, [mountPoint])

  if (widgets.length === 0) return null

  return (
    <div className="mb-8">
      <div className="flex items-center gap-2 mb-3">
        <Puzzle size={14} className="text-[#00FFA7]" />
        <h3 className="text-xs font-medium text-[#667085] uppercase tracking-wider">Plugin Widgets</h3>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
        {widgets.map((w) => (
          <WidgetSlot key={`${w.slug}-${w.widget_id}`} widget={w} />
        ))}
      </div>
    </div>
  )
}
