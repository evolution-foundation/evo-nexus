import { useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import {
  ArrowLeft, Package, CheckCircle, XCircle, AlertTriangle,
  Loader2, RefreshCw, Trash2, ShieldCheck, Download, Layers,
  ToggleRight, ToggleLeft, Terminal, X,
} from 'lucide-react'
import { api } from '../lib/api'
import type { Plugin } from '../components/PluginCard'
import UpdatePreviewModal from '../components/UpdatePreviewModal'

interface HealthResult {
  slug: string
  status: 'active' | 'broken' | 'not_installed'
  tampered_files?: string[]
  reason?: string
}

interface AuditEntry {
  id: number
  action: string
  success: number
  created_at: string
  payload?: string
}

interface Heartbeat {
  id: string
  agent: string
  enabled: boolean
  source_plugin?: string
  interval_seconds: number
}

interface Trigger {
  id: number
  name: string
  enabled: boolean
  source_plugin?: string
  type: string
}

interface CapabilityItem {
  id: string
  label: string
  type: string
  enabled: boolean
}

// ---------------------------------------------------------------------------
// CapabilitySwitch — single row with label and toggle
// ---------------------------------------------------------------------------
function CapabilitySwitch({
  item,
  onToggle,
  loading,
}: {
  item: CapabilityItem
  onToggle: (type: string, id: string, enabled: boolean) => void
  loading: boolean
}) {
  return (
    <div className="flex items-center justify-between py-2 px-3 rounded-lg hover:bg-[#21262d]/50 transition-colors">
      <div className="flex-1 min-w-0 mr-3">
        <p className="text-sm text-[#D0D5DD] truncate">{item.label}</p>
        <p className="text-xs text-[#667085]">{item.type}</p>
      </div>
      <button
        onClick={() => onToggle(item.type, item.id, !item.enabled)}
        disabled={loading}
        className={`flex items-center gap-1.5 px-2 py-1 rounded-md text-xs font-medium transition-colors border ${
          item.enabled
            ? 'bg-[#00FFA7]/10 text-[#00FFA7] border-[#00FFA7]/20 hover:bg-[#00FFA7]/20'
            : 'bg-[#21262d] text-[#667085] border-[#344054] hover:text-[#D0D5DD]'
        } disabled:opacity-50`}
        title={item.enabled ? 'Disable' : 'Enable'}
      >
        {loading ? (
          <Loader2 size={11} className="animate-spin" />
        ) : item.enabled ? (
          <ToggleRight size={11} />
        ) : (
          <ToggleLeft size={11} />
        )}
        {item.enabled ? 'On' : 'Off'}
      </button>
    </div>
  )
}

// ---------------------------------------------------------------------------
// CapabilityGroup — collapsible section per capability type
// ---------------------------------------------------------------------------
function CapabilityGroup({
  title,
  items,
  onToggle,
  loadingId,
}: {
  title: string
  items: CapabilityItem[]
  onToggle: (type: string, id: string, enabled: boolean) => void
  loadingId: string | null
}) {
  if (items.length === 0) return null
  return (
    <div className="mt-3 first:mt-0">
      <p className="text-xs text-[#667085] font-medium uppercase tracking-wide mb-1 px-1">{title}</p>
      <div className="space-y-0.5">
        {items.map((item) => (
          <CapabilitySwitch
            key={`${item.type}:${item.id}`}
            item={item}
            onToggle={onToggle}
            loading={loadingId === `${item.type}:${item.id}`}
          />
        ))}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------
export default function PluginDetail() {
  const { slug } = useParams<{ slug: string }>()
  const navigate = useNavigate()
  const { t } = useTranslation()

  const [plugin, setPlugin] = useState<Plugin | null>(null)
  const [health, setHealth] = useState<HealthResult | null>(null)
  const [audit, setAudit] = useState<AuditEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [healthLoading, setHealthLoading] = useState(false)
  const [removing, setRemoving] = useState(false)
  const [updateMsg, setUpdateMsg] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [previewOpen, setPreviewOpen] = useState(false)

  // Wave 1.1 — Capabilities section state
  const [heartbeats, setHeartbeats] = useState<Heartbeat[]>([])
  const [triggers, setTriggers] = useState<Trigger[]>([])
  const [capLoadingId, setCapLoadingId] = useState<string | null>(null)

  // Wave 2.0 — Icon fallback state
  const [iconError, setIconError] = useState(false)

  // Wave 2.3 — MCP restart banner dismiss (persisted via localStorage)
  const mcpBannerKey = `mcp-restart-dismissed-${slug}`
  const [mcpBannerDismissed, setMcpBannerDismissed] = useState<boolean>(
    () => localStorage.getItem(mcpBannerKey) === '1'
  )
  function dismissMcpBanner() {
    localStorage.setItem(mcpBannerKey, '1')
    setMcpBannerDismissed(true)
  }

  useEffect(() => {
    if (!slug) return
    setLoading(true)
    Promise.all([
      api.get('/plugins') as Promise<Plugin[]>,
      api.get(`/plugins/${slug}/audit`) as Promise<AuditEntry[]>,
      api.get(`/heartbeats?source_plugin=${encodeURIComponent(slug)}`) as Promise<{ heartbeats: Heartbeat[] }>,
      api.get(`/triggers?source_plugin=${encodeURIComponent(slug)}`) as Promise<{ triggers: Trigger[] }>,
    ])
      .then(([plugins, auditLog, hbData, trigData]) => {
        const found = plugins.find((p) => p.slug === slug)
        if (!found) { setError('Plugin not found'); return }
        setPlugin(found)
        setAudit(Array.isArray(auditLog) ? auditLog : [])
        setHeartbeats(Array.isArray(hbData.heartbeats) ? hbData.heartbeats : [])
        setTriggers(Array.isArray(trigData.triggers) ? trigData.triggers : [])
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : t('common.unexpectedError')))
      .finally(() => setLoading(false))
  }, [slug, t])

  async function checkHealth() {
    if (!slug) return
    setHealthLoading(true)
    try {
      const result = await api.get(`/plugins/${slug}/health`) as HealthResult
      setHealth(result)
    } catch {
      setHealth(null)
    } finally {
      setHealthLoading(false)
    }
  }

  async function handleUninstall() {
    if (!slug || !window.confirm(t('plugins.confirmUninstall'))) return
    setRemoving(true)
    try {
      await api.delete(`/plugins/${slug}`)
      navigate('/plugins')
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t('common.unexpectedError'))
      setRemoving(false)
    }
  }

  async function handleToggle() {
    if (!plugin) return
    const next = plugin.enabled !== 1
    try {
      await api.patch(`/plugins/${plugin.slug}`, { enabled: next })
      setPlugin({ ...plugin, enabled: next ? 1 : 0 })
    } catch {
      // silent — refetch if needed
    }
  }

  // ---------------------------------------------------------------------------
  // Wave 1.1 — per-capability toggle handler
  // ---------------------------------------------------------------------------
  async function handleCapabilityToggle(type: string, id: string, enabled: boolean) {
    if (!slug) return
    const loadingKey = `${type}:${id}`
    setCapLoadingId(loadingKey)
    try {
      // Heartbeats and triggers reuse their own PATCH endpoints
      if (type === 'heartbeats') {
        await api.patch(`/heartbeats/${encodeURIComponent(id)}`, { enabled })
        setHeartbeats((prev) => prev.map((hb) => hb.id === id ? { ...hb, enabled } : hb))
      } else if (type === 'triggers') {
        await api.patch(`/triggers/${id}`, { enabled })
        setTriggers((prev) => prev.map((tr) => tr.id === Number(id) ? { ...tr, enabled } : tr))
      } else {
        // All other types via new PATCH capabilities endpoint
        const result = await api.patch(`/plugins/${slug}/capabilities`, { type, id, enabled }) as {
          slug: string
          capabilities_disabled: Record<string, string[]>
        }
        // Update plugin state with new capabilities_disabled
        if (plugin) {
          setPlugin({ ...plugin, capabilities_disabled: JSON.stringify(result.capabilities_disabled) })
        }
      }
    } catch (e: unknown) {
      console.error('capability toggle failed:', e)
    } finally {
      setCapLoadingId(null)
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 size={24} className="text-[#00FFA7] animate-spin" />
      </div>
    )
  }

  if (error || !plugin) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="text-center">
          <p className="text-red-400 mb-2">{error ?? 'Plugin not found'}</p>
          <button onClick={() => navigate('/plugins')} className="text-sm text-[#667085] hover:text-[#D0D5DD]">
            {t('common.back')}
          </button>
        </div>
      </div>
    )
  }

  let manifest: Record<string, unknown> = {}
  try {
    manifest = JSON.parse(plugin.manifest_json ?? '{}')
  } catch {
    // ignore
  }

  const capabilities = Array.isArray(manifest['capabilities']) ? manifest['capabilities'] as string[] : []

  // Wave 2.3 — MCP servers installed (stored in manifest_json["mcp_servers_installed"])
  const mcpServersInstalled = Array.isArray(manifest['mcp_servers_installed'])
    ? manifest['mcp_servers_installed'] as Array<{ effective_name: string }>
    : []

  // ---------------------------------------------------------------------------
  // Build capability items from manifest + capabilities_disabled
  // ---------------------------------------------------------------------------
  let capsDisabled: Record<string, string[]> = {}
  try {
    capsDisabled = JSON.parse(plugin.capabilities_disabled ?? '{}')
  } catch {
    // ignore
  }

  function isCapDisabled(type: string, id: string): boolean {
    return (capsDisabled[type] ?? []).includes(id)
  }

  // Widgets from manifest
  const manifestObj = manifest as Record<string, unknown>
  const uiEntryPoints = (manifestObj['ui_entry_points'] as Record<string, unknown> | undefined) ?? {}
  const widgetSpecs = Array.isArray(uiEntryPoints['widgets']) ? uiEntryPoints['widgets'] as Array<Record<string, string>> : []
  const widgetItems: CapabilityItem[] = widgetSpecs.map((w) => ({
    id: w['id'] ?? '',
    label: w['label'] ?? w['id'] ?? '',
    type: 'widgets',
    enabled: !isCapDisabled('widgets', w['id'] ?? ''),
  })).filter((w) => w.id)

  // Readonly data from manifest
  const rdSpecs = Array.isArray(manifestObj['readonly_data']) ? manifestObj['readonly_data'] as Array<Record<string, string>> : []
  const rdItems: CapabilityItem[] = rdSpecs.map((q) => ({
    id: q['id'] ?? '',
    label: q['id'] ?? '',
    type: 'readonly_data',
    enabled: !isCapDisabled('readonly_data', q['id'] ?? ''),
  })).filter((q) => q.id)

  // Claude hooks from manifest
  const hookSpecs = Array.isArray(manifestObj['claude_hooks']) ? manifestObj['claude_hooks'] as Array<Record<string, string>> : []
  const hookItems: CapabilityItem[] = hookSpecs.map((h) => ({
    id: h['handler_path'] ?? '',
    label: `${h['event'] ?? ''}: ${h['handler_path'] ?? ''}`,
    type: 'claude_hooks',
    enabled: !isCapDisabled('claude_hooks', h['handler_path'] ?? ''),
  })).filter((h) => h.id)

  // Skills from manifest (installed manifest lists them)
  // Note: manifest_json contains the plugin.yaml manifest, not .install-manifest.json
  // So we derive skill/agent/command names from the capabilities array and plugin prefix
  const pluginPrefix = `plugin-${slug}-`
  const skillNames = capabilities.includes('skills')
    ? (Array.isArray(manifestObj['skills']) ? manifestObj['skills'] as Array<Record<string, string>> : [])
        .map((s) => ({ name: s['name'] ?? s['src']?.split('/').pop()?.replace('.md', '') ?? '', src: s['src'] ?? '' }))
        .filter((s) => s.name)
    : []
  const skillItems: CapabilityItem[] = skillNames.map((s) => {
    const id = `${pluginPrefix}${s.name}`
    return { id, label: s.name, type: 'skills', enabled: !isCapDisabled('skills', id) }
  })

  // Agents from manifest
  const agentNames = Array.isArray(manifestObj['agents'])
    ? (manifestObj['agents'] as Array<Record<string, string>>)
        .map((a) => a['name'] ?? a['src']?.split('/').pop()?.replace('.md', '') ?? '')
        .filter(Boolean)
    : []
  const agentItems: CapabilityItem[] = agentNames.map((name) => {
    const id = `${pluginPrefix}${name}`
    return { id, label: name, type: 'agents', enabled: !isCapDisabled('agents', id) }
  })

  // Commands from manifest
  const commandNames = Array.isArray(manifestObj['commands'])
    ? (manifestObj['commands'] as Array<Record<string, string>>)
        .map((c) => c['name'] ?? c['src']?.split('/').pop()?.replace('.md', '') ?? '')
        .filter(Boolean)
    : []
  const commandItems: CapabilityItem[] = commandNames.map((name) => {
    const id = `${pluginPrefix}${name}`
    return { id, label: name, type: 'commands', enabled: !isCapDisabled('commands', id) }
  })

  // Rules from manifest
  const ruleNames = Array.isArray(manifestObj['rules'])
    ? (manifestObj['rules'] as Array<Record<string, string>>)
        .map((r) => r['name'] ?? r['src']?.split('/').pop() ?? '')
        .filter(Boolean)
    : []
  const ruleItems: CapabilityItem[] = ruleNames.map((name) => {
    const id = `${pluginPrefix}${name}`
    return { id, label: name, type: 'rules', enabled: !isCapDisabled('rules', id) }
  })

  // Heartbeat items (fetched from API)
  const heartbeatItems: CapabilityItem[] = heartbeats.map((hb) => ({
    id: hb.id,
    label: `${hb.id} (${hb.agent})`,
    type: 'heartbeats',
    enabled: hb.enabled,
  }))

  // Trigger items (fetched from API)
  const triggerItems: CapabilityItem[] = triggers.map((tr) => ({
    id: String(tr.id),
    label: tr.name,
    type: 'triggers',
    enabled: tr.enabled,
  }))

  // Wave 2.3 — MCP servers declared in the manifest (display-only; plugin owns
  // them, user toggle happens via plugin-level enable/disable for now).
  const mcpServersDeclared = Array.isArray(manifest['mcp_servers'])
    ? manifest['mcp_servers'] as Array<{ name: string; command?: string }>
    : []
  const mcpItems: CapabilityItem[] = mcpServersDeclared.map((mcp) => ({
    id: `plugin-${slug}-${mcp.name}`,
    label: `${mcp.name} (${mcp.command ?? '—'})`,
    type: 'mcp_servers',
    enabled: plugin.enabled === 1,
  }))

  // Wave 2.2r — Integrations declared in the manifest (display-only; the
  // Custom tab at /integrations is the real configuration surface).
  const integrationsDeclared = Array.isArray(manifest['integrations'])
    ? manifest['integrations'] as Array<{ slug: string; label: string; category?: string }>
    : []
  const integrationItems: CapabilityItem[] = integrationsDeclared.map((it) => ({
    id: `${slug}-${it.slug}`,
    label: `${it.label}${it.category ? ` · ${it.category}` : ''}`,
    type: 'integrations',
    enabled: plugin.enabled === 1,
  }))

  const hasAnyCapabilities =
    heartbeatItems.length > 0 ||
    triggerItems.length > 0 ||
    widgetItems.length > 0 ||
    rdItems.length > 0 ||
    hookItems.length > 0 ||
    skillItems.length > 0 ||
    agentItems.length > 0 ||
    commandItems.length > 0 ||
    ruleItems.length > 0 ||
    mcpItems.length > 0 ||
    integrationItems.length > 0

  return (
    <div className="max-w-3xl mx-auto">
      {/* Back */}
      <button
        onClick={() => navigate('/plugins')}
        className="flex items-center gap-1.5 text-sm text-[#667085] hover:text-[#D0D5DD] mb-6 transition-colors"
      >
        <ArrowLeft size={14} />
        {t('plugins.title')}
      </button>

      {/* Header */}
      <div className="flex items-start justify-between mb-6">
        <div className="flex items-center gap-4">
          <div className="flex items-center justify-center w-14 h-14 rounded-2xl bg-[#00FFA7]/8 border border-[#00FFA7]/15">
            {!iconError && plugin.icon_url ? (
              <img
                src={plugin.icon_url}
                alt={plugin.name}
                className="w-6 h-6 object-contain"
                onError={() => setIconError(true)}
              />
            ) : (
              <Package size={24} className="text-[#00FFA7]" />
            )}
          </div>
          <div>
            <h1 className="text-xl font-bold text-[#e6edf3]">{plugin.name}</h1>
            <p className="text-sm text-[#667085]">
              {plugin.slug} &middot; v{plugin.version}
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={handleToggle}
            className={`px-3 py-1.5 text-xs font-medium rounded-lg transition-colors border ${
              plugin.enabled === 1
                ? 'bg-[#00FFA7]/10 text-[#00FFA7] border-[#00FFA7]/20 hover:bg-[#00FFA7]/20'
                : 'bg-[#21262d] text-[#667085] border-[#344054] hover:text-[#D0D5DD]'
            }`}
          >
            {plugin.enabled === 1 ? t('common.enabled') : t('common.disabled')}
          </button>
          <button
            onClick={() => setPreviewOpen(true)}
            disabled={removing}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-[#00FFA7] border border-[#00FFA7]/20 rounded-lg hover:bg-[#00FFA7]/10 disabled:opacity-50 transition-colors"
          >
            <Download size={12} />
            Atualizar
          </button>
          <button
            onClick={handleUninstall}
            disabled={removing}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-red-400 border border-red-500/20 rounded-lg hover:bg-red-500/10 disabled:opacity-50 transition-colors"
          >
            {removing ? <Loader2 size={12} className="animate-spin" /> : <Trash2 size={12} />}
            {t('common.uninstall')}
          </button>
        </div>
      </div>

      {updateMsg && (
        <div className="mb-4 text-xs text-[#D0D5DD] bg-[#161b22] border border-[#21262d] rounded-lg px-3 py-2">
          {updateMsg}
        </div>
      )}

      {/* Wave 2.3 — MCP restart banner (persistent until dismissed) */}
      {mcpServersInstalled.length > 0 && !mcpBannerDismissed && (
        <div className="mb-4 bg-blue-500/5 border border-blue-500/20 rounded-xl px-4 py-3 flex items-start gap-3">
          <Terminal size={15} className="text-blue-400 mt-0.5 shrink-0" />
          <div className="flex-1 min-w-0">
            <p className="text-sm font-medium text-blue-300 mb-0.5">
              Restart Claude Code CLI para ativar os MCP servers
            </p>
            <p className="text-xs text-blue-400/70">
              {mcpServersInstalled.length} MCP server{mcpServersInstalled.length > 1 ? 's' : ''} instalado{mcpServersInstalled.length > 1 ? 's' : ''}.
              Cmd+Q no Claude Code e reabra o aplicativo.
            </p>
          </div>
          <button
            onClick={dismissMcpBanner}
            className="text-blue-500/40 hover:text-blue-400 transition-colors shrink-0"
            title="Dispensar"
          >
            <X size={14} />
          </button>
        </div>
      )}

      <div className="space-y-4">
        {/* Manifest details */}
        <section className="bg-[#161b22] border border-[#21262d] rounded-2xl p-5">
          <h2 className="text-sm font-semibold text-[#e6edf3] mb-4">{t('plugins.manifestDetails')}</h2>
          <dl className="grid grid-cols-2 gap-x-8 gap-y-3 text-sm">
            {[
              { label: t('common.version'), value: plugin.version },
              { label: t('plugins.author'), value: manifest['author'] as string },
              { label: t('plugins.license'), value: manifest['license'] as string },
              { label: t('plugins.tier'), value: plugin.tier },
              { label: t('common.status'), value: plugin.status },
              { label: t('common.createdAt'), value: new Date(plugin.installed_at).toLocaleString() },
            ].map(({ label, value }) =>
              value ? (
                <div key={label}>
                  <dt className="text-xs text-[#667085] mb-0.5">{label}</dt>
                  <dd className="text-[#e6edf3]">{value}</dd>
                </div>
              ) : null
            )}
          </dl>
          {manifest['description'] && (
            <div className="mt-4 pt-4 border-t border-[#21262d]">
              <dt className="text-xs text-[#667085] mb-1">{t('common.description')}</dt>
              <dd className="text-sm text-[#D0D5DD]">{manifest['description'] as string}</dd>
            </div>
          )}
          {capabilities.length > 0 && (
            <div className="mt-4 pt-4 border-t border-[#21262d]">
              <p className="text-xs text-[#667085] mb-2">{t('plugins.capabilities')}</p>
              <div className="flex flex-wrap gap-1.5">
                {capabilities.map((cap) => (
                  <span key={cap} className="text-xs bg-[#00FFA7]/10 text-[#00FFA7] border border-[#00FFA7]/20 px-2 py-0.5 rounded-full">
                    {cap}
                  </span>
                ))}
              </div>
            </div>
          )}
        </section>

        {/* Capabilities — Wave 1.1 */}
        {hasAnyCapabilities && (
          <section className="bg-[#161b22] border border-[#21262d] rounded-2xl p-5">
            <h2 className="text-sm font-semibold text-[#e6edf3] mb-1 flex items-center gap-2">
              <Layers size={14} className="text-[#00FFA7]" />
              Capabilities
            </h2>
            <p className="text-xs text-[#667085] mb-4">
              Toggle individual capabilities. Plugin-level on/off overrides all.
            </p>
            <CapabilityGroup title="Heartbeats" items={heartbeatItems} onToggle={handleCapabilityToggle} loadingId={capLoadingId} />
            <CapabilityGroup title="Triggers" items={triggerItems} onToggle={handleCapabilityToggle} loadingId={capLoadingId} />
            <CapabilityGroup title="MCP Servers" items={mcpItems} onToggle={handleCapabilityToggle} loadingId={capLoadingId} />
            <CapabilityGroup title="Integrations" items={integrationItems} onToggle={handleCapabilityToggle} loadingId={capLoadingId} />
            <CapabilityGroup title="Widgets" items={widgetItems} onToggle={handleCapabilityToggle} loadingId={capLoadingId} />
            <CapabilityGroup title="Read-only Queries" items={rdItems} onToggle={handleCapabilityToggle} loadingId={capLoadingId} />
            <CapabilityGroup title="Claude Hooks" items={hookItems} onToggle={handleCapabilityToggle} loadingId={capLoadingId} />
            <CapabilityGroup title="Skills" items={skillItems} onToggle={handleCapabilityToggle} loadingId={capLoadingId} />
            <CapabilityGroup title="Agents" items={agentItems} onToggle={handleCapabilityToggle} loadingId={capLoadingId} />
            <CapabilityGroup title="Commands" items={commandItems} onToggle={handleCapabilityToggle} loadingId={capLoadingId} />
            <CapabilityGroup title="Rules" items={ruleItems} onToggle={handleCapabilityToggle} loadingId={capLoadingId} />
          </section>
        )}

        {/* Health */}
        <section className="bg-[#161b22] border border-[#21262d] rounded-2xl p-5">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-sm font-semibold text-[#e6edf3] flex items-center gap-2">
              <ShieldCheck size={14} className="text-[#00FFA7]" />
              {t('plugins.health')}
            </h2>
            <button
              onClick={checkHealth}
              disabled={healthLoading}
              className="flex items-center gap-1.5 text-xs text-[#667085] hover:text-[#D0D5DD] transition-colors"
            >
              {healthLoading ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
              {t('common.refresh')}
            </button>
          </div>
          {health ? (
            <div>
              <div className="flex items-center gap-2 mb-2">
                {health.status === 'active' ? (
                  <CheckCircle size={14} className="text-[#00FFA7]" />
                ) : (
                  <XCircle size={14} className="text-red-400" />
                )}
                <span className={`text-sm font-medium ${health.status === 'active' ? 'text-[#00FFA7]' : 'text-red-400'}`}>
                  {health.status}
                </span>
              </div>
              {health.reason && (
                <p className="text-xs text-[#667085]">{health.reason}</p>
              )}
              {health.tampered_files && health.tampered_files.length > 0 && (
                <div className="mt-2 bg-red-500/5 border border-red-500/20 rounded-lg p-3">
                  <p className="text-xs text-red-400 font-medium mb-1 flex items-center gap-1.5">
                    <AlertTriangle size={12} /> {t('plugins.tamperedFiles')}
                  </p>
                  <ul className="space-y-0.5">
                    {health.tampered_files.map((f) => (
                      <li key={f} className="text-xs text-red-300/80 font-mono">{f}</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          ) : (
            <p className="text-sm text-[#667085]">{t('plugins.healthNotChecked')}</p>
          )}
        </section>

        {/* Audit log */}
        {audit.length > 0 && (
          <section className="bg-[#161b22] border border-[#21262d] rounded-2xl p-5">
            <h2 className="text-sm font-semibold text-[#e6edf3] mb-4">{t('plugins.auditLog')}</h2>
            <div className="space-y-1.5">
              {audit.slice(0, 20).map((entry) => (
                <div key={entry.id} className="flex items-center gap-3 text-xs py-1">
                  <span className="text-[#667085] w-32 shrink-0">
                    {new Date(entry.created_at).toLocaleString()}
                  </span>
                  <span className={`font-medium ${entry.success ? 'text-[#00FFA7]' : 'text-red-400'}`}>
                    {entry.action}
                  </span>
                  {!entry.success && (
                    <span className="text-red-400/60">failed</span>
                  )}
                </div>
              ))}
            </div>
          </section>
        )}
      </div>

      {/* Update preview modal — AC1.2.1, AC1.2.2 */}
      {previewOpen && plugin && (
        <UpdatePreviewModal
          slug={plugin.slug}
          sourceUrl={plugin.source_url ?? ''}
          onClose={() => setPreviewOpen(false)}
          onApplied={async () => {
            setUpdateMsg(t('plugins.updatePreviewApplied', {
              from: plugin.version,
              to: '…',
            }))
            const plugins = await api.get('/plugins') as Plugin[]
            const found = plugins.find((p) => p.slug === slug)
            if (found) {
              setPlugin(found)
              setUpdateMsg(t('plugins.updatePreviewApplied', {
                from: plugin.version,
                to: found.version,
              }))
            }
          }}
        />
      )}
    </div>
  )
}
