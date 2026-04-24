import { useEffect, useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import {
  Package, Plus, Search, RefreshCw, Loader2, AlertTriangle,
  CheckCircle, Star,
} from 'lucide-react'
import { api } from '../lib/api'
import PluginCard, { type Plugin } from '../components/PluginCard'
import PluginInstallModal from '../components/PluginInstallModal'

// ------- Marketplace types -------

interface MarketplacePlugin {
  id: string
  name: string
  version: string
  description: string
  author: string
  tags: string[]
  verified: boolean
  tier: string
  repo_url: string
  homepage?: string
}

// ------- Marketplace tab -------

function MarketplaceTab() {
  const { t } = useTranslation()
  const [items, setItems] = useState<MarketplacePlugin[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [search, setSearch] = useState('')

  useEffect(() => {
    setLoading(true)
    ;(api.get('/plugins/marketplace') as Promise<MarketplacePlugin[]>)
      .then((data) => setItems(Array.isArray(data) ? data : []))
      .catch((e: unknown) => setError(e instanceof Error ? e.message : t('common.unexpectedError')))
      .finally(() => setLoading(false))
  }, [t])

  const filtered = items.filter((p) => {
    if (!search.trim()) return true
    const q = search.toLowerCase()
    return (
      p.name.toLowerCase().includes(q) ||
      p.description.toLowerCase().includes(q) ||
      p.tags.some((tag) => tag.toLowerCase().includes(q))
    )
  })

  if (loading) {
    return (
      <div className="flex items-center justify-center h-48">
        <Loader2 size={20} className="text-[#00FFA7] animate-spin" />
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex items-center justify-center h-48">
        <div className="text-center">
          <AlertTriangle size={20} className="text-red-400 mx-auto mb-2" />
          <p className="text-sm text-red-400">{error}</p>
        </div>
      </div>
    )
  }

  return (
    <div>
      {/* Search */}
      <div className="relative mb-6">
        <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-[#667085]" />
        <input
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder={t('plugins.searchMarketplace')}
          className="w-full bg-[#161b22] border border-[#21262d] rounded-xl pl-9 pr-4 py-2.5 text-sm text-[#e6edf3] placeholder-[#667085] focus:outline-none focus:border-[#00FFA7]/40 transition-colors"
        />
      </div>

      {filtered.length === 0 ? (
        <div className="text-center py-12">
          <Package size={32} className="text-[#344054] mx-auto mb-3" />
          <p className="text-[#667085] text-sm">{t('plugins.noMarketplaceResults')}</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {filtered.map((item) => (
            <div
              key={item.id}
              className="group bg-[#161b22] border border-[#21262d] rounded-2xl p-5 transition-all duration-300 hover:border-[#00FFA7]/30 hover:shadow-[0_0_20px_rgba(0,255,167,0.04)]"
            >
              <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-[#00FFA7]/15 to-transparent rounded-t-2xl" />

              <div className="flex items-start justify-between mb-3">
                <div className="flex items-center gap-2">
                  <div className="flex items-center justify-center w-9 h-9 rounded-xl bg-[#00FFA7]/8 border border-[#00FFA7]/15 shrink-0">
                    <Package size={16} className="text-[#00FFA7]" />
                  </div>
                  <div className="min-w-0">
                    <p className="text-sm font-semibold text-[#e6edf3] truncate">{item.name}</p>
                    <p className="text-xs text-[#667085]">v{item.version}</p>
                  </div>
                </div>
                {item.verified ? (
                  <span className="flex items-center gap-1 text-[10px] font-medium text-[#00FFA7] bg-[#00FFA7]/10 px-2 py-0.5 rounded-full border border-[#00FFA7]/20 shrink-0">
                    <CheckCircle size={10} />
                    {t('plugins.verified')}
                  </span>
                ) : (
                  <span className="text-[10px] font-medium text-[#667085] bg-[#21262d] px-2 py-0.5 rounded-full border border-[#344054] shrink-0">
                    {t('plugins.community')}
                  </span>
                )}
              </div>

              <p className="text-xs text-[#667085] mb-3 line-clamp-2">{item.description}</p>

              <div className="flex items-center justify-between">
                <div className="flex flex-wrap gap-1">
                  {item.tags.slice(0, 3).map((tag) => (
                    <span key={tag} className="text-[10px] text-[#667085] bg-[#0C111D] px-1.5 py-0.5 rounded">
                      {tag}
                    </span>
                  ))}
                </div>
                {item.repo_url && (
                  <a
                    href={item.repo_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    onClick={(e) => e.stopPropagation()}
                    className="text-[10px] text-[#667085] hover:text-[#00FFA7] transition-colors flex items-center gap-1"
                  >
                    <Star size={10} />
                    {t('plugins.viewRepo')}
                  </a>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ------- Main page -------

type Tab = 'installed' | 'marketplace'

export default function Plugins() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [tab, setTab] = useState<Tab>('installed')
  const [plugins, setPlugins] = useState<Plugin[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [showInstall, setShowInstall] = useState(false)
  const [search, setSearch] = useState('')

  const fetchPlugins = useCallback(() => {
    setLoading(true)
    ;(api.get('/plugins') as Promise<Plugin[]>)
      .then((data) => setPlugins(Array.isArray(data) ? data : []))
      .catch((e: unknown) => setError(e instanceof Error ? e.message : t('common.unexpectedError')))
      .finally(() => setLoading(false))
  }, [t])

  useEffect(() => {
    fetchPlugins()
  }, [fetchPlugins])

  async function handleToggle(slug: string, enabled: boolean) {
    try {
      await api.patch(`/plugins/${slug}`, { enabled })
      setPlugins((prev) =>
        prev.map((p) => p.slug === slug ? { ...p, enabled: enabled ? 1 : 0 } : p)
      )
    } catch {
      // silent
    }
  }

  const filtered = plugins.filter((p) => {
    if (!search.trim()) return true
    const q = search.toLowerCase()
    return p.name.toLowerCase().includes(q) || p.slug.toLowerCase().includes(q)
  })

  const tabs: { key: Tab; label: string }[] = [
    { key: 'installed', label: t('plugins.installed') },
    { key: 'marketplace', label: t('plugins.marketplace') },
  ]

  return (
    <div className="max-w-[1400px] mx-auto">
      {/* Header */}
      <div className="flex items-start justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-[#e6edf3] tracking-tight">{t('plugins.title')}</h1>
          <p className="text-[#667085] text-sm mt-1">{t('plugins.subtitle')}</p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={fetchPlugins}
            disabled={loading}
            className="p-2 rounded-lg text-[#667085] hover:text-[#D0D5DD] hover:bg-white/5 transition-colors"
            title={t('common.refresh')}
          >
            <RefreshCw size={16} className={loading ? 'animate-spin' : ''} />
          </button>
          <button
            onClick={() => setShowInstall(true)}
            className="flex items-center gap-2 px-4 py-2 text-sm font-medium bg-[#00FFA7] text-black rounded-lg hover:bg-[#00FFA7]/90 transition-colors"
          >
            <Plus size={16} />
            {t('plugins.install')}
          </button>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 mb-6 bg-[#161b22] border border-[#21262d] rounded-xl p-1 w-fit">
        {tabs.map(({ key, label }) => (
          <button
            key={key}
            onClick={() => setTab(key)}
            className={`px-4 py-1.5 text-sm font-medium rounded-lg transition-colors ${
              tab === key
                ? 'bg-[#00FFA7]/10 text-[#00FFA7] border border-[#00FFA7]/20'
                : 'text-[#667085] hover:text-[#D0D5DD]'
            }`}
          >
            {label}
            {key === 'installed' && plugins.length > 0 && (
              <span className="ml-1.5 text-[10px] bg-[#21262d] text-[#667085] px-1.5 py-0.5 rounded-full">
                {plugins.length}
              </span>
            )}
          </button>
        ))}
      </div>

      {/* Installed tab */}
      {tab === 'installed' && (
        <div>
          {/* Search */}
          <div className="relative mb-6">
            <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-[#667085]" />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder={t('plugins.searchInstalled')}
              className="w-full max-w-sm bg-[#161b22] border border-[#21262d] rounded-xl pl-9 pr-4 py-2.5 text-sm text-[#e6edf3] placeholder-[#667085] focus:outline-none focus:border-[#00FFA7]/40 transition-colors"
            />
          </div>

          {error && (
            <div className="flex items-center gap-2 text-sm text-red-400 bg-red-500/5 border border-red-500/20 rounded-xl p-4 mb-4">
              <AlertTriangle size={14} />
              {error}
            </div>
          )}

          {loading ? (
            <div className="flex items-center justify-center h-48">
              <Loader2 size={20} className="text-[#00FFA7] animate-spin" />
            </div>
          ) : filtered.length === 0 ? (
            <div className="text-center py-16">
              <div className="flex items-center justify-center w-16 h-16 rounded-2xl bg-[#161b22] border border-[#21262d] mx-auto mb-4">
                <Package size={28} className="text-[#344054]" />
              </div>
              <p className="text-[#667085] text-sm mb-4">
                {plugins.length === 0 ? t('plugins.noPlugins') : t('common.noResults')}
              </p>
              {plugins.length === 0 && (
                <button
                  onClick={() => setShowInstall(true)}
                  className="text-sm text-[#00FFA7] hover:text-[#00FFA7]/80 transition-colors"
                >
                  {t('plugins.installFirst')}
                </button>
              )}
            </div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
              {filtered.map((p) => (
                <PluginCard
                  key={p.slug}
                  plugin={p}
                  onClick={() => navigate(`/plugins/${p.slug}`)}
                  onToggle={handleToggle}
                />
              ))}
            </div>
          )}
        </div>
      )}

      {/* Marketplace tab */}
      {tab === 'marketplace' && <MarketplaceTab />}

      {/* Install modal */}
      {showInstall && (
        <PluginInstallModal
          onClose={() => setShowInstall(false)}
          onInstalled={fetchPlugins}
        />
      )}
    </div>
  )
}
