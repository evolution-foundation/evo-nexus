import { useEffect, useState, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import {
  Settings as SettingsIcon, Save, X, Pencil, Check,
  Clock, Calendar, CalendarClock, RefreshCw,
  Bot, DollarSign, Brain, FolderKanban, Heart, GraduationCap, Target,
  Camera, Users, Compass, Cog, type LucideIcon,
} from 'lucide-react'
import { api } from '../lib/api'
import Markdown from '../components/Markdown'

// ── Toggle (copied from Providers.tsx) ─────────────────────────────────────
function Toggle({ on, onChange, disabled }: { on: boolean; onChange: (v: boolean) => void; disabled?: boolean }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      disabled={disabled}
      onClick={() => onChange(!on)}
      className={`relative inline-flex h-5 w-9 shrink-0 cursor-pointer rounded-full transition-colors duration-200 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#00FFA7] disabled:opacity-40 disabled:cursor-not-allowed ${
        on ? 'bg-[#00FFA7]' : 'bg-[#1e2a3a]'
      }`}
    >
      <span className={`pointer-events-none inline-block h-4 w-4 rounded-full bg-white shadow-sm transform transition-transform duration-200 translate-y-0.5 ${
        on ? 'translate-x-[18px]' : 'translate-x-[2px]'
      }`} />
    </button>
  )
}

// ── Agent meta (same as Routines.tsx) ──────────────────────────────────────
const AGENT_META: Record<string, { icon: LucideIcon; color: string; colorMuted: string; label: string }> = {
  atlas: { icon: FolderKanban, color: '#60A5FA', colorMuted: 'rgba(96,165,250,0.12)', label: 'Atlas' },
  clawdia: { icon: Brain, color: '#22D3EE', colorMuted: 'rgba(34,211,238,0.12)', label: 'Clawdia' },
  flux: { icon: DollarSign, color: '#34D399', colorMuted: 'rgba(52,211,153,0.12)', label: 'Flux' },
  kai: { icon: Heart, color: '#F472B6', colorMuted: 'rgba(244,114,182,0.12)', label: 'Kai' },
  mentor: { icon: GraduationCap, color: '#FBBF24', colorMuted: 'rgba(251,191,36,0.12)', label: 'Mentor' },
  nex: { icon: Target, color: '#FB923C', colorMuted: 'rgba(251,146,60,0.12)', label: 'Nex' },
  pixel: { icon: Camera, color: '#A78BFA', colorMuted: 'rgba(167,139,250,0.12)', label: 'Pixel' },
  pulse: { icon: Users, color: '#2DD4BF', colorMuted: 'rgba(45,212,191,0.12)', label: 'Pulse' },
  sage: { icon: Compass, color: '#818CF8', colorMuted: 'rgba(129,140,248,0.12)', label: 'Sage' },
}
const DEFAULT_AGENT_META = { icon: Bot, color: '#8b949e', colorMuted: 'rgba(139,148,158,0.12)', label: '' }
const SYSTEM_META = { icon: Cog, color: '#8b949e', colorMuted: 'rgba(139,148,158,0.12)', label: 'system' }

function getAgentMeta(agentName: string) {
  if (!agentName) return DEFAULT_AGENT_META
  if (agentName.toLowerCase() === 'system') return SYSTEM_META
  const key = Object.keys(AGENT_META).find((k) => agentName.toLowerCase().includes(k))
  return key ? AGENT_META[key] : DEFAULT_AGENT_META
}

// ── Input / label class strings (same as Providers.tsx) ────────────────────
const inp = 'w-full px-4 py-3 rounded-lg bg-[#0f1520] border border-[#1e2a3a] text-[#e2e8f0] placeholder-[#3d4f65] text-sm transition-colors duration-200 focus:outline-none focus:border-[#00FFA7]/60 focus:ring-1 focus:ring-[#00FFA7]/20'
const lbl = 'block text-[11px] font-semibold text-[#5a6b7f] mb-1.5 tracking-[0.08em] uppercase'

// ── Toast notification ──────────────────────────────────────────────────────
type ToastType = 'success' | 'error' | 'info'
interface Toast { id: number; type: ToastType; message: string }

function useToast() {
  const [toasts, setToasts] = useState<Toast[]>([])
  let counter = 0

  const show = useCallback((message: string, type: ToastType = 'success') => {
    const id = ++counter
    setToasts((prev) => [...prev, { id, type, message }])
    setTimeout(() => setToasts((prev) => prev.filter((t) => t.id !== id)), 3500)
  }, [])

  return { toasts, show }
}

function ToastStack({ toasts }: { toasts: Toast[] }) {
  if (!toasts.length) return null
  return (
    <div className="fixed bottom-6 right-6 z-50 flex flex-col gap-2">
      {toasts.map((t) => (
        <div
          key={t.id}
          className={`flex items-center gap-2 px-4 py-2.5 rounded-lg text-sm font-medium shadow-lg border transition-all ${
            t.type === 'success'
              ? 'bg-[#0b1018] border-[#00FFA7]/30 text-[#00FFA7]'
              : t.type === 'error'
              ? 'bg-[#0b1018] border-red-500/30 text-red-400'
              : 'bg-[#0b1018] border-[#21262d] text-[#667085]'
          }`}
        >
          <span>{t.message}</span>
        </div>
      ))}
    </div>
  )
}

// ── Frequency badge ─────────────────────────────────────────────────────────
function FreqBadge({ freq }: { freq: string }) {
  const styles: Record<string, string> = {
    daily: 'bg-[#00FFA7]/10 text-[#00FFA7] border-[#00FFA7]/20',
    weekly: 'bg-[#818CF8]/10 text-[#818CF8] border-[#818CF8]/20',
    monthly: 'bg-[#FBBF24]/10 text-[#FBBF24] border-[#FBBF24]/20',
  }
  const icons: Record<string, LucideIcon> = { daily: Clock, weekly: Calendar, monthly: CalendarClock }
  const FreqIcon = icons[freq] || Clock
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium border ${styles[freq] || 'bg-[#1e2a3a] text-[#667085] border-[#1e2a3a]'}`}>
      <FreqIcon size={9} />
      {freq}
    </span>
  )
}

// ── Types ───────────────────────────────────────────────────────────────────
interface WorkspaceConfig {
  name: string
  owner: string
  company: string
  language: string
  timezone: string
  port: number
}

interface RoutineEntry {
  name: string
  slug: string
  frequency: string
  schedule: string
  agent: string
  script: string
  enabled: boolean
  custom?: boolean
  args?: string
}

// ── Tab: Workspace ──────────────────────────────────────────────────────────
const TIMEZONES = [
  'America/Sao_Paulo', 'America/New_York', 'America/Chicago', 'America/Denver',
  'America/Los_Angeles', 'America/Toronto', 'America/Bogota', 'America/Lima',
  'America/Buenos_Aires', 'America/Santiago', 'America/Caracas', 'America/Manaus',
  'Europe/London', 'Europe/Paris', 'Europe/Berlin', 'Europe/Madrid', 'Europe/Rome',
  'Europe/Lisbon', 'Europe/Amsterdam', 'Europe/Stockholm', 'Europe/Warsaw',
  'Asia/Tokyo', 'Asia/Shanghai', 'Asia/Kolkata', 'Asia/Dubai', 'Asia/Singapore',
  'Asia/Seoul', 'Asia/Jakarta', 'Australia/Sydney', 'Pacific/Auckland',
  'UTC',
]

function WorkspaceTab({ showToast }: { showToast: (msg: string, type?: ToastType) => void }) {
  const { t } = useTranslation()
  const [config, setConfig] = useState<WorkspaceConfig>({
    name: '', owner: '', company: '', language: 'pt-BR', timezone: 'America/Sao_Paulo', port: 8080,
  })
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    api.get('/settings/workspace')
      .then((data: any) => {
        const ws = data?.workspace || {}
        const db = data?.dashboard || {}
        setConfig({
          name: ws.name || '',
          owner: ws.owner || '',
          company: ws.company || '',
          language: ws.language || 'pt-BR',
          timezone: ws.timezone || 'America/Sao_Paulo',
          port: db.port || ws.port || 8080,
        })
      })
      .catch(() => showToast('Failed to load workspace config', 'error'))
      .finally(() => setLoading(false))
  }, [])

  const handleSave = async () => {
    setSaving(true)
    try {
      await api.put('/settings/workspace', {
        workspace: {
          name: config.name,
          owner: config.owner,
          company: config.company,
          language: config.language,
          timezone: config.timezone,
        },
        dashboard: { port: config.port },
      })
      showToast('Workspace saved successfully')
    } catch {
      showToast('Failed to save workspace config', 'error')
    } finally {
      setSaving(false)
    }
  }

  if (loading) return (
    <div className="space-y-4">
      {[...Array(6)].map((_, i) => <div key={i} className="skeleton h-14 rounded-lg" />)}
    </div>
  )

  return (
    <div className="max-w-xl space-y-5">
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div>
          <label className={lbl}>{t('settings.name')}</label>
          <input
            type="text"
            className={inp}
            value={config.name}
            onChange={(e) => setConfig((p) => ({ ...p, name: e.target.value }))}
            placeholder={t('settings.workspacePlaceholder')}
          />
        </div>
        <div>
          <label className={lbl}>{t('settings.owner')}</label>
          <input
            type="text"
            className={inp}
            value={config.owner}
            onChange={(e) => setConfig((p) => ({ ...p, owner: e.target.value }))}
            placeholder={t('settings.ownerPlaceholder')}
          />
        </div>
        <div>
          <label className={lbl}>{t('settings.company')}</label>
          <input
            type="text"
            className={inp}
            value={config.company}
            onChange={(e) => setConfig((p) => ({ ...p, company: e.target.value }))}
            placeholder={t('settings.companyPlaceholder')}
          />
        </div>
        <div>
          <label className={lbl}>{t('settings.language')}</label>
          <select
            className={inp}
            value={config.language}
            onChange={(e) => setConfig((p) => ({ ...p, language: e.target.value }))}
          >
            <option value="pt-BR">pt-BR — Português (Brasil)</option>
            <option value="pt-PT">pt-PT — Português (Portugal)</option>
            <option value="en-US">en-US — English (US)</option>
            <option value="en-GB">en-GB — English (UK)</option>
            <option value="es">es — Español</option>
            <option value="es-MX">es-MX — Español (México)</option>
            <option value="fr">fr — Français</option>
            <option value="de">de — Deutsch</option>
            <option value="it">it — Italiano</option>
            <option value="ja">ja — 日本語</option>
            <option value="ko">ko — 한국어</option>
            <option value="zh-CN">zh-CN — 中文 (简体)</option>
            <option value="zh-TW">zh-TW — 中文 (繁體)</option>
            <option value="ru">ru — Русский</option>
            <option value="ar">ar — العربية</option>
            <option value="hi">hi — हिन्दी</option>
            <option value="nl">nl — Nederlands</option>
            <option value="pl">pl — Polski</option>
            <option value="tr">tr — Türkçe</option>
            <option value="uk">uk — Українська</option>
          </select>
        </div>
        <div>
          <label className={lbl}>{t('settings.timezone')}</label>
          <select
            className={inp}
            value={config.timezone}
            onChange={(e) => setConfig((p) => ({ ...p, timezone: e.target.value }))}
          >
            {TIMEZONES.map((tz) => (
              <option key={tz} value={tz}>{tz}</option>
            ))}
          </select>
        </div>
        <div>
          <label className={lbl}>{t('settings.port')}</label>
          <input
            type="number"
            className={inp}
            value={config.port}
            onChange={(e) => setConfig((p) => ({ ...p, port: parseInt(e.target.value) || 8080 }))}
            min={1024}
            max={65535}
          />
        </div>
      </div>

      <div className="pt-2">
        <button
          onClick={handleSave}
          disabled={saving}
          className="flex items-center gap-2 px-5 py-2.5 rounded-lg bg-[#00FFA7] text-[#080c14] font-semibold text-sm hover:bg-[#00e69a] transition-colors disabled:opacity-40"
        >
          <Save size={14} />
          {saving ? t('common.saving') : t('settings.saveWorkspace')}
        </button>
      </div>
    </div>
  )
}

// ── Tab: Routines ───────────────────────────────────────────────────────────
function RoutinesTab({ showToast }: { showToast: (msg: string, type?: ToastType) => void }) {
  const [routines, setRoutines] = useState<{ daily: RoutineEntry[]; weekly: RoutineEntry[]; monthly: RoutineEntry[] }>({
    daily: [], weekly: [], monthly: [],
  })
  const [loading, setLoading] = useState(true)
  const [toggling, setToggling] = useState<string | null>(null)
  const [editingSlug, setEditingSlug] = useState<string | null>(null)
  const [editValue, setEditValue] = useState('')
  const [reloading, setReloading] = useState(false)

  const load = () => {
    setLoading(true)
    api.get('/settings/routines')
      .then((data: any) => {
        setRoutines({
          daily: data?.daily || [],
          weekly: data?.weekly || [],
          monthly: data?.monthly || [],
        })
      })
      .catch(() => showToast('Failed to load routines', 'error'))
      .finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [])

  const reloadScheduler = async () => {
    setReloading(true)
    try {
      await api.post('/settings/scheduler/reload')
      showToast('Scheduler reloaded', 'success')
    } catch {
      showToast('Scheduler not running or reload failed', 'error')
    } finally {
      setReloading(false)
    }
  }

  const handleToggle = async (freq: string, slug: string, current: boolean) => {
    const key = `${freq}/${slug}`
    setToggling(key)
    try {
      await api.patch(`/settings/routines/${freq}/${slug}/toggle`, { enabled: !current })
      load()
      await reloadScheduler()
    } catch {
      showToast('Failed to toggle routine', 'error')
    } finally {
      setToggling(null)
    }
  }

  const startEdit = (slug: string, currentSchedule: string) => {
    setEditingSlug(slug)
    setEditValue(currentSchedule)
  }

  const commitEdit = async (freq: string, slug: string) => {
    try {
      await api.patch(`/settings/routines/${freq}/${slug}/toggle`, { schedule: editValue })
      setEditingSlug(null)
      load()
      await reloadScheduler()
    } catch {
      showToast('Failed to update schedule', 'error')
    }
  }

  const FREQS: Array<{ key: 'daily' | 'weekly' | 'monthly'; label: string }> = [
    { key: 'daily', label: 'Daily' },
    { key: 'weekly', label: 'Weekly' },
    { key: 'monthly', label: 'Monthly' },
  ]

  if (loading) return (
    <div className="space-y-3">
      {[...Array(8)].map((_, i) => <div key={i} className="skeleton h-12 rounded-lg" />)}
    </div>
  )

  const totalCount = routines.daily.length + routines.weekly.length + routines.monthly.length

  return (
    <div>
      {/* Top bar */}
      <div className="flex items-center justify-between mb-5">
        <div className="flex items-center gap-3">
          <span className="text-[#667085] text-sm">{totalCount} routines configured</span>
          <button
            onClick={reloadScheduler}
            disabled={reloading}
            title="Reload scheduler"
            className="flex items-center gap-1.5 text-[11px] px-3 py-1.5 rounded-md text-[#5a6b7f] border border-[#1e2a3a] hover:text-[#8a9aae] hover:border-[#2e3a4a] transition-colors disabled:opacity-40"
          >
            <RefreshCw size={11} className={reloading ? 'animate-spin' : ''} />
            Reload scheduler
          </button>
        </div>
      </div>

      {/* Sections */}
      <div className="space-y-6">
        {FREQS.map(({ key }) => {
          const list = routines[key]
          if (!list.length) return null
          return (
            <div key={key}>
              <div className="flex items-center gap-2 mb-3">
                <FreqBadge freq={key} />
                <span className="text-[#667085] text-[11px]">{list.length} routines</span>
              </div>
              <div className="bg-[#161b22] border border-[#21262d] rounded-2xl overflow-hidden">
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="text-[#667085] text-[11px] uppercase tracking-wider font-medium border-b border-[#21262d]">
                        <th className="px-4 py-3 text-left w-12">Enabled</th>
                        <th className="px-4 py-3 text-left">Name</th>
                        <th className="px-4 py-3 text-left">Agent</th>
                        <th className="px-4 py-3 text-left">Schedule</th>
                        <th className="px-4 py-3 text-left">Script</th>
                        <th className="px-4 py-3 text-right w-12">Args</th>
                      </tr>
                    </thead>
                    <tbody>
                      {list.map((r) => {
                        const agentMeta = getAgentMeta(r.agent)
                        const rowKey = `${key}/${r.slug}`
                        const isEditing = editingSlug === r.slug
                        return (
                          <tr key={r.slug} className="border-t border-[#21262d]/60 hover:bg-[#0d1117]/40 transition-colors">
                            <td className="px-4 py-3">
                              <Toggle
                                on={r.enabled}
                                disabled={toggling === rowKey}
                                onChange={() => handleToggle(key, r.slug, r.enabled)}
                              />
                            </td>
                            <td className="px-4 py-3 text-[#e6edf3] text-[13px] font-medium whitespace-nowrap">{r.name}</td>
                            <td className="px-4 py-3">
                              {r.agent ? (
                                <span
                                  className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[11px] font-medium border"
                                  style={{
                                    backgroundColor: agentMeta.colorMuted,
                                    color: agentMeta.color,
                                    borderColor: `${agentMeta.color}33`,
                                  }}
                                >
                                  {(() => { const Icon = agentMeta.icon; return <Icon size={11} /> })()}
                                  {r.agent}
                                </span>
                              ) : (
                                <span className="text-[#3d4f65] text-[12px]">—</span>
                              )}
                            </td>
                            <td className="px-4 py-3 text-[#667085] text-[13px] whitespace-nowrap">
                              {isEditing ? (
                                <span className="flex items-center gap-1">
                                  <input
                                    type="text"
                                    autoFocus
                                    value={editValue}
                                    onChange={(e) => setEditValue(e.target.value)}
                                    onKeyDown={(e) => {
                                      if (e.key === 'Enter') commitEdit(key, r.slug)
                                      if (e.key === 'Escape') setEditingSlug(null)
                                    }}
                                    className="w-28 px-2 py-1 rounded bg-[#0f1520] border border-[#00FFA7]/40 text-[#e6edf3] text-[12px] focus:outline-none font-mono"
                                  />
                                  <button onClick={() => commitEdit(key, r.slug)} className="text-[#00FFA7] hover:text-[#00e69a] p-0.5 transition-colors">
                                    <Check size={13} />
                                  </button>
                                  <button onClick={() => setEditingSlug(null)} className="text-[#667085] hover:text-red-400 p-0.5 transition-colors">
                                    <X size={13} />
                                  </button>
                                </span>
                              ) : (
                                <button
                                  onClick={() => startEdit(r.slug, r.schedule)}
                                  className="flex items-center gap-1.5 group hover:text-[#e6edf3] transition-colors"
                                  title="Click to edit schedule"
                                >
                                  <code className="font-mono text-[12px]">{r.schedule || '—'}</code>
                                  <Pencil size={10} className="opacity-0 group-hover:opacity-60 transition-opacity" />
                                </button>
                              )}
                            </td>
                            <td className="px-4 py-3">
                              <code className="text-[11px] text-[#5a6b7f] font-mono bg-[#0b1018] px-2 py-0.5 rounded border border-[#152030]">
                                {r.script}
                              </code>
                            </td>
                            <td className="px-4 py-3 text-right">
                              {r.args ? (
                                <code className="text-[10px] text-[#5a6b7f] font-mono bg-[#0b1018] px-1.5 py-0.5 rounded border border-[#152030]">{r.args}</code>
                              ) : (
                                <span className="text-[#3d4f65] text-[12px]">—</span>
                              )}
                            </td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          )
        })}
      </div>

    </div>
  )
}

// ── Tab: Reference ──────────────────────────────────────────────────────────
const REF_TABS = [
  { key: 'claude-md', label: 'CLAUDE.md', format: 'md' },
  { key: 'makefile', label: 'Makefile targets', format: 'json' },
  { key: 'commands', label: 'Commands', format: 'json' },
] as const

function ReferenceTab() {
  const [activeRef, setActiveRef] = useState<string>('claude-md')
  const [content, setContent] = useState<Record<string, string>>({})
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (content[activeRef]) return
    setLoading(true)
    const tab = REF_TABS.find((t) => t.key === activeRef)
    const fetcher = tab?.format === 'json' ? api.get(`/config/${activeRef}`) : api.getRaw(`/config/${activeRef}`)
    fetcher
      .then((data: any) => {
        if (tab?.format === 'json') {
          if (activeRef === 'makefile' && Array.isArray(data)) {
            const header = '| Command | Description |\n|---------|-------------|\n'
            const rows = data.map((item: any) => `| \`make ${item.name || ''}\` | ${item.description || ''} |`).join('\n')
            setContent((prev) => ({ ...prev, [activeRef]: header + rows }))
          } else if (activeRef === 'commands' && Array.isArray(data)) {
            const header = '| Command | Content |\n|---------|--------|\n'
            const rows = data.map((item: any) => `| \`${item.name || ''}\` | ${(item.content || '').substring(0, 100).replace(/\n/g, ' ')} |`).join('\n')
            setContent((prev) => ({ ...prev, [activeRef]: header + rows }))
          } else {
            setContent((prev) => ({ ...prev, [activeRef]: JSON.stringify(data, null, 2) }))
          }
        } else {
          setContent((prev) => ({ ...prev, [activeRef]: data }))
        }
      })
      .catch(() => setContent((prev) => ({ ...prev, [activeRef]: 'Failed to load content' })))
      .finally(() => setLoading(false))
  }, [activeRef, content])

  return (
    <div>
      {/* Sub-tabs */}
      <div className="flex gap-1 mb-5 border-b border-[#21262d]">
        {REF_TABS.map((tab) => (
          <button
            key={tab.key}
            onClick={() => setActiveRef(tab.key)}
            className={`px-4 py-2.5 text-sm font-medium whitespace-nowrap transition-colors border-b-2 -mb-px ${
              activeRef === tab.key
                ? 'text-[#00FFA7] border-[#00FFA7]'
                : 'text-[#667085] border-transparent hover:text-[#e6edf3] hover:border-[#21262d]'
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-6">
        {loading ? (
          <div className="space-y-3">
            {[...Array(10)].map((_, i) => (
              <div key={i} className="skeleton h-5 rounded" style={{ width: `${60 + (i * 7) % 40}%` }} />
            ))}
          </div>
        ) : (
          <div className="markdown-content">
            <Markdown>{content[activeRef] || ''}</Markdown>
          </div>
        )}
      </div>
    </div>
  )
}

// ── Tab: Notifications ──────────────────────────────────────────────────────
function NotificationsTab() {
  const STORAGE_KEY = 'evonexus.notifications.enabled'
  const [enabled, setEnabled] = useState(() => {
    try { return localStorage.getItem(STORAGE_KEY) !== 'false' } catch { return true }
  })
  const [permission, setPermission] = useState<NotificationPermission | 'unsupported'>(() => {
    if (typeof Notification === 'undefined') return 'unsupported'
    return Notification.permission
  })

  const handleToggle = async (value: boolean) => {
    setEnabled(value)
    try { localStorage.setItem(STORAGE_KEY, value ? 'true' : 'false') } catch {}

    if (value && typeof Notification !== 'undefined' && Notification.permission === 'default') {
      const result = await Notification.requestPermission()
      setPermission(result)
    }
  }

  return (
    <div className="max-w-xl space-y-6">
      <div className="bg-[#161b22] border border-[#21262d] rounded-2xl overflow-hidden">
        <div className="px-5 py-4 border-b border-[#21262d]">
          <h3 className="text-[13px] font-semibold text-[#e6edf3]">Browser Notifications</h3>
          <p className="text-[11px] text-[#667085] mt-0.5">
            Show an OS notification when an agent is waiting for your approval while you're in another tab.
          </p>
        </div>
        <div className="px-5 py-4 flex items-center justify-between gap-4">
          <div className="flex-1 min-w-0">
            <p className="text-[13px] text-[#e6edf3] font-medium">Enable notifications</p>
            <p className="text-[11px] text-[#667085] mt-0.5">
              {permission === 'unsupported'
                ? 'Browser notifications are not supported in this environment.'
                : permission === 'denied'
                ? 'Permission denied — allow notifications in your browser settings first.'
                : permission === 'granted'
                ? 'Permission granted.'
                : 'Permission will be requested when first needed.'}
            </p>
          </div>
          <Toggle
            on={enabled}
            onChange={handleToggle}
            disabled={permission === 'unsupported' || permission === 'denied'}
          />
        </div>
      </div>

      <div className="text-[11px] text-[#3d4f65] space-y-1">
        <p>What triggers a notification:</p>
        <ul className="list-disc list-inside space-y-0.5 text-[#4a5568]">
          <li>Agent requests a tool permission (Write, Bash, etc.) while you are in another tab</li>
        </ul>
        <p className="mt-2">Tab title and favicon badge are always active regardless of this setting.</p>
      </div>
    </div>
  )
}

// ── Tab: Trust ──────────────────────────────────────────────────────────────
function TrustTab({ showToast }: { showToast: (msg: string, type?: ToastType) => void }) {
  const [enabled, setEnabled] = useState<boolean | null>(null)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    api.get('/settings/chat')
      .then((d: { trustMode: boolean }) => setEnabled(d.trustMode))
      .catch(() => {
        setEnabled(false)
        showToast('Failed to load trust mode setting', 'error')
      })
  }, [])

  const handleToggle = async (value: boolean) => {
    setSaving(true)
    const prev = enabled
    setEnabled(value)
    try {
      await api.patch('/settings/chat', { trustMode: value })
    } catch {
      setEnabled(prev)
      showToast('Failed to save. Try again.', 'error')
    } finally {
      setSaving(false)
    }
  }

  const isLoading = enabled === null

  return (
    <div className="max-w-xl space-y-6">
      <div className="bg-[#161b22] border border-[#21262d] rounded-2xl overflow-hidden">
        <div className="px-5 py-4 border-b border-[#21262d]">
          <h3 className="text-[13px] font-semibold text-[#e6edf3]">Trust mode</h3>
          <p className="text-[11px] text-[#667085] mt-0.5">
            When ON, agents run Write / Edit / Bash / NotebookEdit / Agent without asking for approval. OFF keeps the per-tool Allow/Deny prompt.
          </p>
        </div>
        <div className="px-5 py-4 flex items-center justify-between gap-4">
          <div className="flex-1 min-w-0">
            <p className="text-[13px] text-[#e6edf3] font-medium">Auto-approve all tools in chat</p>
            <p className="text-[11px] text-[#667085] mt-0.5">
              {isLoading
                ? 'Loading…'
                : enabled
                  ? 'ON — agents execute mutating tools without asking.'
                  : 'OFF — every mutating tool call requires your explicit approval.'}
            </p>
          </div>
          <Toggle on={!!enabled} onChange={handleToggle} disabled={isLoading || saving} />
        </div>
      </div>

      <div className="text-[11px] text-[#3d4f65] space-y-1">
        <p className="text-amber-400/80">⚠ Trust mode disables the main safety guardrail. Only enable if you know what the agents you use will do.</p>
        <p className="mt-2">Applies to new chat turns — ongoing turns finish under the policy they started with.</p>
      </div>
    </div>
  )
}

// ── Main Settings page ──────────────────────────────────────────────────────
const TABS = [
  { key: 'workspace', labelKey: 'settings.tabs.workspace' },
  { key: 'routines', labelKey: 'settings.tabs.routines' },
  { key: 'notifications', labelKey: 'settings.tabs.notifications' },
  { key: 'trust', labelKey: 'settings.tabs.trust' },
  { key: 'reference', labelKey: 'settings.tabs.reference' },
] as const

type TabKey = 'workspace' | 'routines' | 'notifications' | 'trust' | 'reference'

export default function Settings() {
  const { t } = useTranslation()
  const [activeTab, setActiveTab] = useState<TabKey>('workspace')
  const { toasts, show: showToast } = useToast()

  return (
    <div className="max-w-[1200px] mx-auto font-[Inter,-apple-system,sans-serif]">
      {/* Header */}
      <div className="flex items-center gap-3 mb-8">
        <div className="flex items-center justify-center w-10 h-10 rounded-xl bg-[#161b22] border border-[#21262d]">
          <SettingsIcon size={20} className="text-[#00FFA7]" />
        </div>
        <div>
          <h1 className="text-xl font-bold text-white tracking-tight">{t('settings.title')}</h1>
          <p className="text-[#5a6b7f] text-sm mt-0.5">{t('settings.headerSubtitle')}</p>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 mb-7 border-b border-[#21262d]">
        {TABS.map((tab) => (
          <button
            key={tab.key}
            onClick={() => setActiveTab(tab.key)}
            className={`px-5 py-2.5 text-sm font-medium whitespace-nowrap transition-colors border-b-2 -mb-px ${
              activeTab === tab.key
                ? 'text-[#00FFA7] border-[#00FFA7]'
                : 'text-[#667085] border-transparent hover:text-[#e6edf3] hover:border-[#21262d]'
            }`}
          >
            {t(tab.labelKey)}
          </button>
        ))}
      </div>

      {/* Tab content */}
      {activeTab === 'workspace' && <WorkspaceTab showToast={showToast} />}
      {activeTab === 'routines' && <RoutinesTab showToast={showToast} />}
      {activeTab === 'notifications' && <NotificationsTab />}
      {activeTab === 'trust' && <TrustTab showToast={showToast} />}
      {activeTab === 'reference' && <ReferenceTab />}

      <ToastStack toasts={toasts} />
    </div>
  )
}
