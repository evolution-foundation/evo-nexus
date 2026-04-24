import { useTranslation } from 'react-i18next'
import { useState } from 'react'
import { Package, AlertTriangle, CheckCircle, Loader2, XCircle } from 'lucide-react'

export interface Plugin {
  slug: string
  name: string
  version: string
  tier: string
  status: 'active' | 'disabled' | 'broken' | 'installing' | 'uninstalling'
  enabled: number
  source_url: string
  installed_at: string
  manifest_json: string
  install_sha256: string
  last_error?: string
  // Wave 1.1: per-capability disable state (JSON string from DB)
  capabilities_disabled?: string
  // Wave 2.0: plugin icon URL served by /plugins/<slug>/ui/<path>
  icon_url?: string | null
}

interface Props {
  plugin: Plugin
  onClick: () => void
  onToggle: (slug: string, enabled: boolean) => void
}

function StatusIcon({ status }: { status: Plugin['status'] }) {
  if (status === 'active') return <CheckCircle size={14} className="text-[#00FFA7]" />
  if (status === 'broken') return <XCircle size={14} className="text-red-400" />
  if (status === 'installing' || status === 'uninstalling') return <Loader2 size={14} className="text-yellow-400 animate-spin" />
  return <AlertTriangle size={14} className="text-[#667085]" />
}

function statusLabel(status: Plugin['status']): string {
  const map: Record<Plugin['status'], string> = {
    active: 'Active',
    disabled: 'Disabled',
    broken: 'Broken',
    installing: 'Installing',
    uninstalling: 'Uninstalling',
  }
  return map[status] ?? status
}

export default function PluginCard({ plugin, onClick, onToggle }: Props) {
  const { t } = useTranslation()
  const isEnabled = plugin.enabled === 1
  const busy = plugin.status === 'installing' || plugin.status === 'uninstalling'
  // Wave 2.0: track icon load error per card (useState prevents reset on re-render)
  const [iconError, setIconError] = useState(false)

  let manifest: { description?: string; capabilities?: string[] } = {}
  try {
    manifest = JSON.parse(plugin.manifest_json ?? '{}')
  } catch {
    // ignore malformed manifest
  }

  const showIcon = !iconError && !!plugin.icon_url

  return (
    <div
      className="group relative bg-[#161b22] border border-[#21262d] rounded-2xl p-5 cursor-pointer transition-all duration-300 hover:border-[#00FFA7]/40 hover:shadow-[0_0_24px_rgba(0,255,167,0.06)]"
      onClick={onClick}
    >
      <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-[#00FFA7]/20 to-transparent rounded-t-2xl" />

      <div className="flex items-start justify-between mb-3">
        <div className="flex items-center gap-3">
          <div className="flex items-center justify-center w-10 h-10 rounded-xl bg-[#00FFA7]/8 border border-[#00FFA7]/15 shrink-0">
            {showIcon ? (
              <img
                src={plugin.icon_url!}
                alt={plugin.name}
                className="w-[18px] h-[18px] object-contain"
                onError={() => setIconError(true)}
              />
            ) : (
              <Package size={18} className="text-[#00FFA7]" />
            )}
          </div>
          <div className="min-w-0">
            <p className="text-sm font-semibold text-[#e6edf3] truncate">{plugin.name}</p>
            <p className="text-xs text-[#667085]">v{plugin.version}</p>
          </div>
        </div>

        {/* Toggle */}
        <button
          onClick={(e) => {
            e.stopPropagation()
            if (!busy) onToggle(plugin.slug, !isEnabled)
          }}
          disabled={busy}
          className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors shrink-0 ${
            busy ? 'opacity-40 cursor-not-allowed' : 'cursor-pointer'
          } ${isEnabled ? 'bg-[#00FFA7]' : 'bg-[#344054]'}`}
          title={isEnabled ? t('common.disable') : t('common.enable')}
        >
          <span
            className={`inline-block h-3.5 w-3.5 rounded-full bg-white shadow-sm transform transition-transform ${
              isEnabled ? 'translate-x-4' : 'translate-x-0.5'
            }`}
          />
        </button>
      </div>

      {manifest.description && (
        <p className="text-xs text-[#667085] mb-3 line-clamp-2">{manifest.description}</p>
      )}

      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1.5">
          <StatusIcon status={plugin.status} />
          <span className={`text-xs font-medium ${
            plugin.status === 'active' ? 'text-[#00FFA7]' :
            plugin.status === 'broken' ? 'text-red-400' :
            'text-[#667085]'
          }`}>
            {statusLabel(plugin.status)}
          </span>
        </div>
        {manifest.capabilities && manifest.capabilities.length > 0 && (
          <span className="text-[10px] text-[#667085] bg-[#21262d] px-2 py-0.5 rounded-full">
            {manifest.capabilities.length} cap{manifest.capabilities.length !== 1 ? 's' : ''}
          </span>
        )}
      </div>

      {plugin.status === 'broken' && plugin.last_error && (
        <p className="mt-2 text-[11px] text-red-400/80 truncate" title={plugin.last_error}>
          {plugin.last_error}
        </p>
      )}
    </div>
  )
}
