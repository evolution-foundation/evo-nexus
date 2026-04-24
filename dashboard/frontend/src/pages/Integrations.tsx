import { useEffect, useState, useCallback, useRef } from 'react'
import {
  Plus,
  Trash2,
  Plug,
  CheckCircle2,
  AlertCircle,
  Globe,
  MessageSquare,
  DollarSign,
  Video,
  Camera,
  Briefcase,
  Database,
  Settings,
  GitFork,
  Calendar,
  Mail,
  ListTodo,
  Zap,
  Hash,
  Send,
  Phone,
  Key,
  GitBranch,
  BookOpen,
  Image,
  Pencil,
  X,
  Loader2,
  Eye,
  EyeOff,
  Lock,
  Unlock,
  Puzzle,
  type LucideIcon,
} from 'lucide-react'
import { api } from '../lib/api'
import IntegrationDrawer from '../components/IntegrationDrawer'
import { getIntegrationMeta } from '../lib/integrationMeta'
import { useTranslation } from 'react-i18next'

interface EnvVarSpec {
  name: string
  description?: string
  required: boolean
  secret: boolean
  default?: string
}

interface HealthCheckSpec {
  type: 'http'
  url: string
  expect_status: number
  timeout_seconds: number
}

interface LastHealth {
  last_status: string | null
  last_checked_at: string | null
  last_error: string | null
}

interface Integration {
  name: string
  type: string
  status: 'ok' | 'error' | 'pending' | 'not_configured' | 'connected'
  kind: 'core' | 'custom' | 'plugin'
  // custom-only fields
  slug?: string
  description?: string
  envKeys?: string[]
  category?: string
  // plugin-only fields
  source_plugin?: string
  integration_slug?: string
  env_specs?: EnvVarSpec[]
  health_check?: HealthCheckSpec | null
  last_health?: LastHealth | null
  configured?: boolean
}

interface SocialAccount {
  index: number
  label: string
  status: string
  detail: string
  days_left: number | null
}

interface SocialPlatform {
  id: string
  name: string
  icon: string
  accounts: SocialAccount[]
  has_connected: boolean
}

interface DatabaseConnection {
  index: number
  label: string
  host: string | null
  port: number | null
  database: string | null
  ssl_mode?: string | null
  tls?: boolean
  allow_write: boolean
  query_timeout: number
  max_rows: number
}

interface DatabaseFlavor {
  slug: 'postgres' | 'mysql' | 'mongo' | 'redis' | string
  skill: string
  ok: boolean
  count: number
  connections: DatabaseConnection[]
  error?: string
}

type TabKey = 'integrations' | 'social' | 'databases'

// Category styling for integration types
const TYPE_META: Record<string, { icon: LucideIcon; color: string; colorMuted: string; glowColor: string }> = {
  'api': { icon: Globe, color: '#60A5FA', colorMuted: 'rgba(96,165,250,0.12)', glowColor: 'rgba(96,165,250,0.15)' },
  'mcp': { icon: Plug, color: '#A78BFA', colorMuted: 'rgba(167,139,250,0.12)', glowColor: 'rgba(167,139,250,0.15)' },
  'cli': { icon: Database, color: '#22D3EE', colorMuted: 'rgba(34,211,238,0.12)', glowColor: 'rgba(34,211,238,0.15)' },
  'erp': { icon: DollarSign, color: '#34D399', colorMuted: 'rgba(52,211,153,0.12)', glowColor: 'rgba(52,211,153,0.15)' },
  'bot': { icon: MessageSquare, color: '#FBBF24', colorMuted: 'rgba(251,191,36,0.12)', glowColor: 'rgba(251,191,36,0.15)' },
  'oauth': { icon: Globe, color: '#F472B6', colorMuted: 'rgba(244,114,182,0.12)', glowColor: 'rgba(244,114,182,0.15)' },
}

const DEFAULT_TYPE = { icon: Plug, color: '#8b949e', colorMuted: 'rgba(139,148,158,0.12)', glowColor: 'rgba(139,148,158,0.15)' }

// Per-integration icon + color (overrides TYPE_META when matched by name)
const INTEGRATION_ICONS: Record<string, { icon: LucideIcon; color: string; colorMuted: string }> = {
  'omie':           { icon: DollarSign,    color: '#34D399', colorMuted: 'rgba(52,211,153,0.12)' },
  'stripe':         { icon: DollarSign,    color: '#635BFF', colorMuted: 'rgba(99,91,255,0.12)' },
  'bling':          { icon: DollarSign,    color: '#3B82F6', colorMuted: 'rgba(59,130,246,0.12)' },
  'asaas':          { icon: Zap,           color: '#FBBF24', colorMuted: 'rgba(251,191,36,0.12)' },
  'todoist':        { icon: ListTodo,      color: '#E44332', colorMuted: 'rgba(228,67,50,0.12)' },
  'fathom':         { icon: Video,         color: '#7C3AED', colorMuted: 'rgba(124,58,237,0.12)' },
  'discord':        { icon: Hash,          color: '#5865F2', colorMuted: 'rgba(88,101,242,0.12)' },
  'telegram':       { icon: Send,          color: '#26A5E4', colorMuted: 'rgba(38,165,228,0.12)' },
  'whatsapp':       { icon: Phone,         color: '#25D366', colorMuted: 'rgba(37,211,102,0.12)' },
  'licensing':      { icon: Key,           color: '#00FFA7', colorMuted: 'rgba(0,255,167,0.12)' },
  'evolution api':  { icon: MessageSquare, color: '#00FFA7', colorMuted: 'rgba(0,255,167,0.12)' },
  'evolution go':   { icon: GitBranch,     color: '#00FFA7', colorMuted: 'rgba(0,255,167,0.12)' },
  'evo crm':        { icon: Database,      color: '#00FFA7', colorMuted: 'rgba(0,255,167,0.12)' },
  'ai image creator': { icon: Image,       color: '#F472B6', colorMuted: 'rgba(244,114,182,0.12)' },
  'github':         { icon: GitFork,       color: '#E6EDF3', colorMuted: 'rgba(230,237,243,0.12)' },
  'linear':         { icon: BookOpen,      color: '#5E6AD2', colorMuted: 'rgba(94,106,210,0.12)' },
  'google calendar': { icon: Calendar,     color: '#4285F4', colorMuted: 'rgba(66,133,244,0.12)' },
  'gmail':          { icon: Mail,          color: '#EA4335', colorMuted: 'rgba(234,67,53,0.12)' },
  'youtube':        { icon: Video,         color: '#FF0000', colorMuted: 'rgba(255,0,0,0.12)' },
  'instagram':      { icon: Camera,        color: '#E4405F', colorMuted: 'rgba(228,64,95,0.12)' },
  'linkedin':       { icon: Briefcase,     color: '#0A66C2', colorMuted: 'rgba(10,102,194,0.12)' },
  'notion':         { icon: BookOpen,      color: '#FFFFFF', colorMuted: 'rgba(255,255,255,0.08)' },
  'canva':          { icon: Globe,         color: '#00C4CC', colorMuted: 'rgba(0,196,204,0.12)' },
  'figma':          { icon: Globe,         color: '#A259FF', colorMuted: 'rgba(162,89,255,0.12)' },
}

function getIntegrationIcon(name: string) {
  const key = Object.keys(INTEGRATION_ICONS).find(k => name.toLowerCase().includes(k))
  return key ? INTEGRATION_ICONS[key] : null
}

function getTypeMeta(type: string) {
  if (!type) return DEFAULT_TYPE
  const key = Object.keys(TYPE_META).find((k) => type.toLowerCase().includes(k))
  return key ? TYPE_META[key] : DEFAULT_TYPE
}

// Social platform icon mapping
const PLATFORM_ICONS: Record<string, { icon: LucideIcon; color: string; colorMuted: string; glowColor: string }> = {
  'youtube': { icon: Video, color: '#EF4444', colorMuted: 'rgba(239,68,68,0.12)', glowColor: 'rgba(239,68,68,0.15)' },
  'instagram': { icon: Camera, color: '#E879F9', colorMuted: 'rgba(232,121,249,0.12)', glowColor: 'rgba(232,121,249,0.15)' },
  'linkedin': { icon: Briefcase, color: '#60A5FA', colorMuted: 'rgba(96,165,250,0.12)', glowColor: 'rgba(96,165,250,0.15)' },
}

const DEFAULT_PLATFORM = { icon: Globe, color: '#8b949e', colorMuted: 'rgba(139,148,158,0.12)', glowColor: 'rgba(139,148,158,0.15)' }

function getPlatformMeta(id: string) {
  const key = Object.keys(PLATFORM_ICONS).find((k) => id.toLowerCase().includes(k))
  return key ? PLATFORM_ICONS[key] : DEFAULT_PLATFORM
}

// Stat Card (matches Overview design)
function StatCard({ label, value, icon: Icon }: { label: string; value: string | number; icon: LucideIcon }) {
  return (
    <div className="group relative bg-[#161b22] border border-[#21262d] rounded-2xl p-5 transition-all duration-300 hover:border-[#00FFA7]/40 hover:shadow-[0_0_24px_rgba(0,255,167,0.06)]">
      <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-[#00FFA7]/20 to-transparent rounded-t-2xl" />
      <div className="flex items-start justify-between mb-3">
        <div className="flex items-center justify-center w-9 h-9 rounded-xl bg-[#00FFA7]/8 border border-[#00FFA7]/15">
          <Icon size={18} className="text-[#00FFA7]" />
        </div>
      </div>
      <p className="text-3xl font-bold text-[#e6edf3] tracking-tight">{value}</p>
      <p className="text-sm text-[#667085] mt-1">{label}</p>
    </div>
  )
}

function SkeletonCard() {
  return (
    <div className="rounded-xl border border-[#21262d] bg-[#161b22] p-5">
      <div className="flex items-start justify-between mb-3">
        <div className="h-10 w-10 rounded-lg bg-[#21262d] animate-pulse" />
        <div className="h-2 w-2 rounded-full bg-[#21262d] animate-pulse" />
      </div>
      <div className="h-4 w-32 rounded bg-[#21262d] animate-pulse mb-2" />
      <div className="h-3 w-20 rounded bg-[#21262d] animate-pulse" />
    </div>
  )
}

function SkeletonStat() {
  return <div className="skeleton h-24 rounded-2xl" />
}

// ─── Custom Integration Modal ─────────────────────────────────────────────────

const CATEGORY_OPTIONS = [
  { value: 'messaging', label: 'Messaging' },
  { value: 'payments', label: 'Payments' },
  { value: 'crm', label: 'CRM' },
  { value: 'social', label: 'Social' },
  { value: 'productivity', label: 'Productivity' },
  { value: 'other', label: 'Other' },
]

interface CustomIntegrationForm {
  displayName: string
  slug: string
  description: string
  category: string
  envKeys: { name: string; value: string }[]
}

const EMPTY_FORM: CustomIntegrationForm = {
  displayName: '',
  slug: '',
  description: '',
  category: 'other',
  envKeys: [],
}

function slugify(text: string): string {
  return text
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9 -]/g, '')
    .replace(/\s+/g, '-')
    .replace(/-+/g, '-')
    .replace(/^-|-$/g, '')
}

interface CustomModalProps {
  open: boolean
  initial?: CustomIntegrationForm & { slug: string }
  isEdit: boolean
  onClose: () => void
  onSaved: (envWritten?: boolean) => void
}

function CustomModal({ open, initial, isEdit, onClose, onSaved }: CustomModalProps) {
  const [form, setForm] = useState<CustomIntegrationForm>(EMPTY_FORM)
  const [slugManual, setSlugManual] = useState(false)
  const [errors, setErrors] = useState<Partial<Record<keyof CustomIntegrationForm, string>>>({})
  const [saving, setSaving] = useState(false)
  const [visibleRows, setVisibleRows] = useState<Set<number>>(new Set())
  const overlayRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (open) {
      // Pre-fill name only; value intentionally blank (security)
      const baseForm = initial
        ? {
            ...initial,
            envKeys: (initial.envKeys as unknown as (string | { name: string; value: string })[]).map(k =>
              typeof k === 'string' ? { name: k, value: '' } : k
            ),
          }
        : EMPTY_FORM
      setForm(baseForm)
      setSlugManual(isEdit)
      setErrors({})
      setVisibleRows(new Set())
      setSaving(false)
    }
  }, [open, initial, isEdit])

  useEffect(() => {
    if (!open) return
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [open, onClose])

  const setField = <K extends keyof CustomIntegrationForm>(key: K, value: CustomIntegrationForm[K]) => {
    setForm(prev => {
      const next = { ...prev, [key]: value }
      if (key === 'displayName' && !slugManual) {
        next.slug = slugify(value as string)
      }
      return next
    })
    setErrors(prev => ({ ...prev, [key]: undefined }))
  }

  const validate = (): boolean => {
    const errs: Partial<Record<keyof CustomIntegrationForm, string>> = {}
    if (!form.displayName.trim()) errs.displayName = 'Required'
    if (!form.slug.trim()) {
      errs.slug = 'Required'
    } else if (!/^[a-z0-9][a-z0-9-]*[a-z0-9]$/.test(form.slug)) {
      errs.slug = 'Lowercase letters, digits and hyphens only'
    }
    setErrors(errs)
    return Object.keys(errs).length === 0
  }

  const handleSave = async () => {
    if (!validate()) return
    setSaving(true)
    try {
      const envKeyNames = form.envKeys.map(r => r.name).filter(n => n.trim())
      const envValues: Record<string, string> = {}
      for (const row of form.envKeys) {
        if (row.name.trim() && row.value.trim()) {
          envValues[row.name.trim()] = row.value.trim()
        }
      }
      const hasEnvValues = Object.keys(envValues).length > 0

      if (isEdit && initial?.slug) {
        await api.patch(`/integrations/custom/${initial.slug}`, {
          displayName: form.displayName,
          description: form.description,
          category: form.category,
          envKeys: envKeyNames,
          ...(hasEnvValues ? { envValues } : {}),
        })
      } else {
        await api.post('/integrations/custom', {
          slug: form.slug,
          displayName: form.displayName,
          description: form.description,
          category: form.category,
          envKeys: envKeyNames,
          ...(hasEnvValues ? { envValues } : {}),
        })
      }
      onSaved(hasEnvValues)
      onClose()
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'Error saving'
      setErrors({ displayName: msg })
    } finally {
      setSaving(false)
    }
  }

  const addEnvRow = () => {
    setField('envKeys', [...form.envKeys, { name: '', value: '' }])
  }

  const removeEnvRow = (idx: number) => {
    setField('envKeys', form.envKeys.filter((_, i) => i !== idx))
    setVisibleRows(prev => {
      const next = new Set(prev)
      next.delete(idx)
      return next
    })
  }

  const updateEnvRow = (idx: number, field: 'name' | 'value', val: string) => {
    const next = form.envKeys.map((r, i) =>
      i === idx ? { ...r, [field]: field === 'name' ? val.toUpperCase() : val } : r
    )
    setField('envKeys', next)
  }

  const toggleRowVisibility = (idx: number) => {
    setVisibleRows(prev => {
      const next = new Set(prev)
      if (next.has(idx)) next.delete(idx)
      else next.add(idx)
      return next
    })
  }

  if (!open) return null

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      {/* Backdrop */}
      <div
        ref={overlayRef}
        className="absolute inset-0 bg-black/60 backdrop-blur-[2px]"
        onClick={onClose}
      />
      {/* Panel */}
      <div className="relative w-full max-w-lg bg-[#0C111D] border border-[#21262d] rounded-2xl shadow-2xl flex flex-col max-h-[90vh]">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-[#21262d]">
          <h2 className="text-base font-semibold text-[#e6edf3]">
            {isEdit ? 'Edit Custom Integration' : 'New Custom Integration'}
          </h2>
          <button
            type="button"
            onClick={onClose}
            className="p-1.5 rounded-lg text-[#667085] hover:text-[#e6edf3] hover:bg-[#21262d] transition-colors"
          >
            <X size={16} />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-5 py-5 space-y-4">
          {/* Display Name */}
          <div>
            <label className="block text-xs font-medium text-[#8b949e] mb-1">
              Display Name <span className="text-red-400">*</span>
            </label>
            <input
              type="text"
              value={form.displayName}
              onChange={e => setField('displayName', e.target.value)}
              placeholder="My Custom API"
              className="w-full rounded-lg border border-[#21262d] bg-[#161b22] px-3 py-2 text-sm text-[#e6edf3] placeholder-[#3F3F46] focus:outline-none focus:border-[#00FFA7]/50 transition-colors"
            />
            {errors.displayName && <p className="text-xs text-red-400 mt-1">{errors.displayName}</p>}
          </div>

          {/* Slug */}
          <div>
            <label className="block text-xs font-medium text-[#8b949e] mb-1">
              Slug <span className="text-red-400">*</span>
            </label>
            <div className="flex items-center rounded-lg border border-[#21262d] bg-[#161b22] focus-within:border-[#00FFA7]/50 transition-colors">
              <span className="pl-3 text-xs text-[#3F3F46] shrink-0">custom-int-</span>
              <input
                type="text"
                value={form.slug}
                onChange={e => {
                  setSlugManual(true)
                  setField('slug', e.target.value)
                }}
                disabled={isEdit}
                placeholder="my-api"
                className="flex-1 bg-transparent px-1 py-2 text-sm text-[#e6edf3] placeholder-[#3F3F46] focus:outline-none disabled:opacity-50"
              />
            </div>
            {errors.slug && <p className="text-xs text-red-400 mt-1">{errors.slug}</p>}
          </div>

          {/* Description */}
          <div>
            <label className="block text-xs font-medium text-[#8b949e] mb-1">Description</label>
            <textarea
              value={form.description}
              onChange={e => setField('description', e.target.value)}
              rows={2}
              placeholder="What this integration does..."
              className="w-full rounded-lg border border-[#21262d] bg-[#161b22] px-3 py-2 text-sm text-[#e6edf3] placeholder-[#3F3F46] focus:outline-none focus:border-[#00FFA7]/50 transition-colors resize-none"
            />
          </div>

          {/* Category */}
          <div>
            <label className="block text-xs font-medium text-[#8b949e] mb-1">Category</label>
            <select
              value={form.category}
              onChange={e => setField('category', e.target.value)}
              className="w-full rounded-lg border border-[#21262d] bg-[#161b22] px-3 py-2 text-sm text-[#e6edf3] focus:outline-none focus:border-[#00FFA7]/50 transition-colors"
            >
              {CATEGORY_OPTIONS.map(o => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
          </div>

          {/* Env Keys */}
          <div>
            <label className="block text-xs font-medium text-[#8b949e] mb-1">Env Keys</label>
            <div className="space-y-1.5 mb-2">
              {form.envKeys.map((row, idx) => (
                <div key={idx} className="flex items-center gap-1.5">
                  {/* Name input */}
                  <input
                    type="text"
                    value={row.name}
                    onChange={e => updateEnvRow(idx, 'name', e.target.value)}
                    placeholder="MY_API_KEY"
                    className="w-44 shrink-0 rounded-lg border border-[#21262d] bg-[#161b22] px-3 py-1.5 text-xs text-[#00FFA7] placeholder-[#3F3F46] focus:outline-none focus:border-[#00FFA7]/50 transition-colors font-mono"
                  />
                  {/* Value input */}
                  <div className="relative flex-1">
                    <input
                      type={visibleRows.has(idx) ? 'text' : 'password'}
                      value={row.value}
                      onChange={e => updateEnvRow(idx, 'value', e.target.value)}
                      placeholder={isEdit ? 'leave empty to keep current' : 'secret value (optional)'}
                      className="w-full rounded-lg border border-[#21262d] bg-[#161b22] px-3 py-1.5 pr-8 text-xs text-[#e6edf3] placeholder-[#3F3F46] focus:outline-none focus:border-[#00FFA7]/50 transition-colors"
                    />
                    {row.value.length > 0 && (
                      <button
                        type="button"
                        onClick={() => toggleRowVisibility(idx)}
                        className="absolute right-2 top-1/2 -translate-y-1/2 text-[#667085] hover:text-[#e6edf3] transition-colors"
                        tabIndex={-1}
                      >
                        {visibleRows.has(idx) ? <EyeOff size={12} /> : <Eye size={12} />}
                      </button>
                    )}
                  </div>
                  {/* Remove button */}
                  <button
                    type="button"
                    onClick={() => removeEnvRow(idx)}
                    className="p-1 rounded text-[#667085] hover:text-red-400 transition-colors shrink-0"
                  >
                    <X size={12} />
                  </button>
                </div>
              ))}
            </div>
            <button
              type="button"
              onClick={addEnvRow}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-dashed border-[#21262d] text-xs text-[#667085] hover:text-[#e6edf3] hover:border-[#344054] transition-colors"
            >
              <Plus size={12} />
              Add env key
            </button>
          </div>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-3 px-5 py-4 border-t border-[#21262d]">
          <button
            type="button"
            onClick={onClose}
            className="px-4 py-2 rounded-lg text-sm text-[#667085] hover:text-[#e6edf3] hover:bg-[#21262d] transition-colors"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={handleSave}
            disabled={saving}
            className="flex items-center gap-1.5 px-4 py-2 rounded-lg bg-[#00FFA7] text-[#0C111D] text-sm font-semibold hover:bg-[#00e699] transition-colors disabled:opacity-60 disabled:cursor-not-allowed"
          >
            {saving && <Loader2 size={14} className="animate-spin" />}
            {isEdit ? 'Save Changes' : 'Create'}
          </button>
        </div>
      </div>
    </div>
  )
}

// ─── Integration Card ─────────────────────────────────────────────────────────

// ─── Plugin Integration Form ─────────────────────────────────────────────────
// Schema-driven form rendered inside the Configure modal (Wave 2.2r).
// Each EnvVarSpec becomes one field; secret=true uses password input with toggle.

function PluginEnvField({
  spec,
  value,
  onChange,
}: {
  spec: EnvVarSpec
  value: string
  onChange: (v: string) => void
}) {
  const [show, setShow] = useState(false)
  return (
    <div>
      <label className="flex items-center gap-1 text-xs font-medium text-[#e6edf3] mb-1">
        {spec.name}
        {spec.required && <span className="text-red-400">*</span>}
        {spec.secret && <Lock size={10} className="text-[#667085]" />}
      </label>
      {spec.description && (
        <p className="text-[10px] text-[#667085] mb-1">{spec.description}</p>
      )}
      <div className="relative">
        <input
          type={spec.secret && !show ? 'password' : 'text'}
          placeholder={spec.default || (spec.required ? 'Required' : 'Optional')}
          value={value}
          onChange={e => onChange(e.target.value)}
          className="w-full bg-[#161b22] border border-[#21262d] rounded-lg px-3 py-2 text-sm text-[#e6edf3] placeholder-[#3F3F46] focus:outline-none focus:border-[#00FFA7]/50 pr-9"
          autoComplete="off"
        />
        {spec.secret && (
          <button
            type="button"
            onClick={() => setShow(!show)}
            className="absolute right-2.5 top-1/2 -translate-y-1/2 text-[#667085] hover:text-[#e6edf3] transition-colors"
            tabIndex={-1}
          >
            {show ? <EyeOff size={14} /> : <Eye size={14} />}
          </button>
        )}
      </div>
    </div>
  )
}

function PluginIntegrationForm({
  specs,
  values,
  onChange,
}: {
  specs: EnvVarSpec[]
  values: Record<string, string>
  onChange: (v: Record<string, string>) => void
}) {
  return (
    <div className="space-y-4 mb-5">
      {specs.map(spec => (
        <PluginEnvField
          key={spec.name}
          spec={spec}
          value={values[spec.name] || ''}
          onChange={v => onChange({ ...values, [spec.name]: v })}
        />
      ))}
    </div>
  )
}

interface IntegrationCardProps {
  int: Integration
  onSelect: (int: Integration) => void
  onEdit?: (int: Integration) => void
  onDelete?: (int: Integration) => void
}

function IntegrationCard({ int, onSelect, onEdit, onDelete }: IntegrationCardProps) {
  const typeMeta = getTypeMeta(int.type)
  const intIcon = getIntegrationIcon(int.name)
  const Icon = intIcon?.icon ?? typeMeta.icon
  const iconColor = intIcon?.color ?? typeMeta.color
  const iconBg = intIcon?.colorMuted ?? typeMeta.colorMuted
  const isConnected = int.status === 'ok'
  const intMeta = int.kind === 'core' ? getIntegrationMeta(int.name) : null
  const isOAuth = intMeta?.oauthFlow === true
  const isConfigurable = !isOAuth && (
    (intMeta?.fields && intMeta.fields.length > 0) ||
    (int.kind === 'custom' && (int.envKeys?.length ?? 0) > 0)
  )
  const isCustom = int.kind === 'custom'
  const isClickable = !!intMeta || isConfigurable

  return (
    <div
      onClick={() => { if (isClickable) onSelect(int) }}
      role={isClickable ? 'button' : undefined}
      tabIndex={isClickable ? 0 : undefined}
      onKeyDown={(e) => {
        if (isClickable && (e.key === 'Enter' || e.key === ' ')) {
          e.preventDefault()
          onSelect(int)
        }
      }}
      aria-label={isClickable ? `Configurar ${int.name}` : undefined}
      className={[
        'group relative rounded-xl border border-[#21262d] bg-[#161b22] p-5 transition-all duration-300 hover:border-transparent',
        isClickable ? 'cursor-pointer' : '',
      ].join(' ')}
    >
      {/* Hover glow */}
      <div
        className="pointer-events-none absolute inset-0 rounded-xl opacity-0 transition-opacity duration-300 group-hover:opacity-100"
        style={{
          boxShadow: isConnected
            ? `inset 0 0 0 1px rgba(0,255,167,0.27), 0 0 20px rgba(0,255,167,0.10)`
            : `inset 0 0 0 1px ${typeMeta.color}44, 0 0 20px ${typeMeta.glowColor}`,
          borderRadius: 'inherit',
        }}
      />

      {/* Top row: icon + status dot + custom actions */}
      <div className="relative flex items-start justify-between mb-3">
        <div
          className="flex h-10 w-10 items-center justify-center rounded-lg transition-transform duration-300 group-hover:scale-110"
          style={{ backgroundColor: iconBg }}
        >
          <Icon size={20} style={{ color: iconColor }} />
        </div>
        <div className="flex items-center gap-1.5">
          {isCustom && (
            <>
              <button
                type="button"
                onClick={(e) => { e.stopPropagation(); onEdit?.(int) }}
                className="p-1 rounded text-[#667085] hover:text-[#00FFA7] transition-colors opacity-0 group-hover:opacity-100"
                title="Edit"
              >
                <Pencil size={13} />
              </button>
              <button
                type="button"
                onClick={(e) => { e.stopPropagation(); onDelete?.(int) }}
                className="p-1 rounded text-[#667085] hover:text-red-400 transition-colors opacity-0 group-hover:opacity-100"
                title="Delete"
              >
                <Trash2 size={13} />
              </button>
            </>
          )}
          <span
            className="inline-block h-2.5 w-2.5 rounded-full mt-1"
            style={{
              backgroundColor: isConnected ? '#00FFA7' : '#3F3F46',
              boxShadow: isConnected ? '0 0 8px rgba(0,255,167,0.5)' : 'none',
            }}
          />
        </div>
      </div>

      {/* Name + custom badge */}
      <div className="relative flex items-center gap-2 mb-2">
        <h3 className="text-[15px] font-semibold text-[#e6edf3] transition-colors duration-200 group-hover:text-white">
          {int.name}
        </h3>
        {isCustom && (
          <span className="text-[9px] font-semibold uppercase tracking-wider px-1.5 py-0.5 rounded-full bg-[#00FFA7]/10 text-[#00FFA7] border border-[#00FFA7]/20">
            Custom
          </span>
        )}
      </div>

      {/* Description for custom integrations */}
      {isCustom && int.description && (
        <p className="relative text-xs text-[#667085] mb-2 line-clamp-2">{int.description}</p>
      )}

      {/* Bottom badges + configure affordance */}
      <div className="relative flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span
            className="text-[10px] font-medium uppercase tracking-wider px-2 py-0.5 rounded-full border"
            style={{
              backgroundColor: typeMeta.colorMuted,
              color: typeMeta.color,
              borderColor: `${typeMeta.color}33`,
            }}
          >
            {int.type}
          </span>
          <span className={`text-[10px] font-medium px-2 py-0.5 rounded-full border ${
            isConnected
              ? 'bg-[#00FFA7]/10 text-[#00FFA7] border-[#00FFA7]/25'
              : 'bg-[#FBBF24]/10 text-[#FBBF24] border-[#FBBF24]/25'
          }`}>
            {isConnected ? 'Connected' : 'Not configured'}
          </span>
        </div>

        {/* Hover affordance */}
        {isOAuth ? (
          <span className="flex items-center gap-1 text-[11px] text-[#667085] group-hover:text-[#00FFA7] opacity-0 group-hover:opacity-100 transition-all duration-200">
            Conectar
          </span>
        ) : isConfigurable ? (
          <span className="flex items-center gap-1 text-[11px] text-[#667085] group-hover:text-[#00FFA7] opacity-0 group-hover:opacity-100 transition-all duration-200">
            <Settings size={11} />
            Configurar
          </span>
        ) : null}
      </div>
    </div>
  )
}

// ─── Main page ────────────────────────────────────────────────────────────────

export default function Integrations() {
  const { t } = useTranslation()
  const [activeTab, setActiveTab] = useState<TabKey>('integrations')
  const [integrations, setIntegrations] = useState<Integration[]>([])
  const [platforms, setPlatforms] = useState<SocialPlatform[]>([])
  const [dbFlavors, setDbFlavors] = useState<DatabaseFlavor[]>([])
  const [loading, setLoading] = useState(true)
  const [envValues, setEnvValues] = useState<Record<string, string>>({})
  const [selectedIntegration, setSelectedIntegration] = useState<Integration | null>(null)

  // custom modal state
  const [modalOpen, setModalOpen] = useState(false)
  const [modalIsEdit, setModalIsEdit] = useState(false)
  const [modalInitial, setModalInitial] = useState<(CustomIntegrationForm & { slug: string }) | undefined>(undefined)

  // delete confirm
  const [deleteTarget, setDeleteTarget] = useState<Integration | null>(null)
  const [deleting, setDeleting] = useState(false)

  // env written toast
  const [envToast, setEnvToast] = useState(false)

  const loadData = useCallback(() => {
    Promise.all([
      api.get('/integrations').catch(() => ({ integrations: [] })),
      api.get('/social-accounts').catch(() => ({ platforms: [] })),
      api.get('/config/env').catch(() => ({ entries: [] })),
      api.get('/integrations/databases').catch(() => ({ flavors: [], total: 0 })),
    ]).then(([intData, socialData, envData, dbData]) => {
      const ints = (intData?.integrations || []).map((i: any) => ({
        name: i.name || '',
        type: i.type || i.category || '',
        status: i.kind === 'plugin'
          ? (i.status as Integration['status'])
          : ((i.status === 'ok' || i.configured) ? 'ok' as const : 'pending' as const),
        kind: (i.kind || 'core') as Integration['kind'],
        slug: i.slug,
        description: i.description,
        envKeys: i.envKeys,
        category: i.category,
        // plugin-only
        source_plugin: i.source_plugin,
        integration_slug: i.integration_slug,
        env_specs: i.env_specs,
        health_check: i.health_check,
        last_health: i.last_health,
        configured: i.configured,
      }))
      setIntegrations(ints)
      setPlatforms(socialData?.platforms || [])
      setDbFlavors((dbData?.flavors as DatabaseFlavor[]) || [])

      const envMap: Record<string, string> = {}
      for (const entry of (envData?.entries ?? [])) {
        if (entry.type === 'var' && entry.key) {
          envMap[entry.key] = entry.value ?? ''
        }
      }
      setEnvValues(envMap)
    }).finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    loadData()
  }, [loadData])

  const handleDisconnect = async (platformId: string, index: number) => {
    try {
      const data = await api.delete(`/social-accounts/${platformId}/${index}`)
      setPlatforms(data?.platforms || [])
    } catch (e) {
      console.error(e)
    }
  }

  const openCreateModal = () => {
    setModalInitial(undefined)
    setModalIsEdit(false)
    setModalOpen(true)
  }

  const openEditModal = (int: Integration) => {
    const rawKeys: string[] = int.envKeys || []
    setModalInitial({
      slug: int.slug || '',
      displayName: int.name,
      description: int.description || '',
      category: int.category || 'other',
      envKeys: rawKeys.map(k => ({ name: k, value: '' })),
    })
    setModalIsEdit(true)
    setModalOpen(true)
  }

  const handleDeleteConfirm = async () => {
    if (!deleteTarget?.slug) return
    setDeleting(true)
    try {
      await api.delete(`/integrations/custom/${deleteTarget.slug}`)
      setDeleteTarget(null)
      loadData()
    } catch (e) {
      console.error(e)
    } finally {
      setDeleting(false)
    }
  }

  const coreIntegrations = integrations.filter(i => i.kind === 'core')
  const customIntegrations = integrations.filter(i => i.kind === 'custom')
  const pluginIntegrations = integrations.filter(i => i.kind === 'plugin')

  // Plugin integration configure modal state
  const [pluginIntegModalOpen, setPluginIntegModalOpen] = useState(false)
  const [pluginIntegTarget, setPluginIntegTarget] = useState<Integration | null>(null)
  const [pluginIntegValues, setPluginIntegValues] = useState<Record<string, string>>({})
  const [pluginIntegSaving, setPluginIntegSaving] = useState(false)
  const [pluginIntegError, setPluginIntegError] = useState<string | null>(null)
  const [pluginTestResult, setPluginTestResult] = useState<{ ok: boolean | null; status_code: number | null; duration_ms: number; error: string | null } | null>(null)
  const [pluginTesting, setPluginTesting] = useState(false)

  const openPluginIntegModal = (int: Integration) => {
    setPluginIntegTarget(int)
    setPluginIntegValues({})
    setPluginIntegError(null)
    setPluginTestResult(null)
    setPluginIntegModalOpen(true)
  }

  const handlePluginIntegSave = async () => {
    if (!pluginIntegTarget?.slug) return
    setPluginIntegSaving(true)
    setPluginIntegError(null)
    try {
      await api.post(`/integrations/plugin/${pluginIntegTarget.slug}`, { env_vars: pluginIntegValues })
      setPluginIntegModalOpen(false)
      loadData()
    } catch (e: any) {
      setPluginIntegError(e?.message || 'Save failed')
    } finally {
      setPluginIntegSaving(false)
    }
  }

  const handlePluginIntegTest = async () => {
    if (!pluginIntegTarget?.slug) return
    setPluginTesting(true)
    setPluginTestResult(null)
    try {
      const res = await api.post(`/integrations/plugin/${pluginIntegTarget.slug}/test`, {})
      setPluginTestResult(res)
    } catch {
      setPluginTestResult({ ok: false, status_code: null, duration_ms: 0, error: 'Request failed' })
    } finally {
      setPluginTesting(false)
    }
  }

  // Brain repo status (best-effort — may fail if feature not deployed)
  const [brainRepoStatus, setBrainRepoStatus] = useState<{
    connected: boolean
    repo_url: string | null
    last_sync: string | null
    pending_count: number
  } | null>(null)

  useEffect(() => {
    api.get('/brain-repo/status')
      .then((d: any) => setBrainRepoStatus(d))
      .catch(() => {/* feature may not be deployed yet */})
  }, [])
  const connectedCount = integrations.filter((i) => i.status === 'ok').length
  const totalSocialAccounts = platforms.reduce((sum, p) => sum + p.accounts.length, 0)
  const connectedPlatformsCount = platforms.filter(p => p.has_connected).length
  const totalDbConnections = dbFlavors.reduce((sum, f) => sum + f.count, 0)
  const sqlDbCount = dbFlavors.filter(f => f.slug === 'postgres' || f.slug === 'mysql').reduce((sum, f) => sum + f.count, 0)
  const nosqlDbCount = dbFlavors.filter(f => f.slug === 'mongo' || f.slug === 'redis').reduce((sum, f) => sum + f.count, 0)


  return (
    <div className="max-w-[1400px] mx-auto">
      <IntegrationDrawer
        integration={selectedIntegration ? {
          ...selectedIntegration,
          status: (selectedIntegration.status === 'connected' || selectedIntegration.status === 'ok')
            ? 'ok'
            : selectedIntegration.status === 'error'
            ? 'error'
            : 'pending',
          kind: selectedIntegration.kind === 'plugin' ? 'custom' : selectedIntegration.kind,
        } : null}
        envValues={envValues}
        onClose={() => setSelectedIntegration(null)}
        onSaved={() => {
          setSelectedIntegration(null)
          loadData()
        }}
      />

      <CustomModal
        open={modalOpen}
        initial={modalInitial}
        isEdit={modalIsEdit}
        onClose={() => setModalOpen(false)}
        onSaved={(envWritten) => {
          loadData()
          if (envWritten) {
            setEnvToast(true)
            setTimeout(() => setEnvToast(false), 6000)
          }
        }}
      />

      {/* Env written toast */}
      {envToast && (
        <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-50 flex items-center gap-3 px-5 py-3 rounded-xl bg-[#161b22] border border-[#00FFA7]/30 shadow-2xl text-sm text-[#e6edf3]">
          <CheckCircle2 size={16} className="text-[#00FFA7] shrink-0" />
          <span>Saved — env values written to <code className="text-[#00FFA7] font-mono text-xs">.env</code>. Restart services to pick up the new values.</span>
          <button type="button" onClick={() => setEnvToast(false)} className="ml-2 text-[#667085] hover:text-[#e6edf3]">
            <X size={14} />
          </button>
        </div>
      )}


      {/* Delete confirm dialog */}
      {deleteTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
          <div className="absolute inset-0 bg-black/60 backdrop-blur-[2px]" onClick={() => setDeleteTarget(null)} />
          <div className="relative w-full max-w-sm bg-[#0C111D] border border-[#21262d] rounded-2xl shadow-2xl p-6">
            <h3 className="text-base font-semibold text-[#e6edf3] mb-2">Delete Custom Integration</h3>
            <p className="text-sm text-[#667085] mb-5">
              Delete <span className="text-[#e6edf3] font-medium">{deleteTarget.name}</span>? This removes the SKILL.md file permanently.
            </p>
            <div className="flex items-center justify-end gap-3">
              <button
                type="button"
                onClick={() => setDeleteTarget(null)}
                className="px-4 py-2 rounded-lg text-sm text-[#667085] hover:text-[#e6edf3] hover:bg-[#21262d] transition-colors"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleDeleteConfirm}
                disabled={deleting}
                className="flex items-center gap-1.5 px-4 py-2 rounded-lg bg-red-500/80 text-white text-sm font-semibold hover:bg-red-500 transition-colors disabled:opacity-60 disabled:cursor-not-allowed"
              >
                {deleting && <Loader2 size={14} className="animate-spin" />}
                Delete
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Plugin Integration Configure Modal — Wave 2.2r */}
      {pluginIntegModalOpen && pluginIntegTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
          <div className="absolute inset-0 bg-black/60 backdrop-blur-[2px]" onClick={() => setPluginIntegModalOpen(false)} />
          <div className="relative w-full max-w-md bg-[#0C111D] border border-[#21262d] rounded-2xl shadow-2xl p-6">
            <div className="flex items-center gap-3 mb-4">
              <div className="flex items-center justify-center w-8 h-8 rounded-lg bg-purple-500/10 border border-purple-500/20">
                <Puzzle size={16} className="text-purple-400" />
              </div>
              <div>
                <h3 className="text-base font-semibold text-[#e6edf3]">Configure {pluginIntegTarget.name}</h3>
                <p className="text-[11px] text-[#667085]">via plugin: {pluginIntegTarget.source_plugin}</p>
              </div>
              <button
                type="button"
                onClick={() => setPluginIntegModalOpen(false)}
                className="ml-auto text-[#667085] hover:text-[#e6edf3] transition-colors"
              >
                <X size={16} />
              </button>
            </div>

            {/* Dynamic form from env_specs */}
            <PluginIntegrationForm
              specs={pluginIntegTarget.env_specs || []}
              values={pluginIntegValues}
              onChange={setPluginIntegValues}
            />

            {pluginIntegError && (
              <p className="text-xs text-red-400 mb-3">{pluginIntegError}</p>
            )}

            {/* Test result */}
            {pluginTestResult && (
              <div className={`text-xs rounded-lg px-3 py-2 mb-3 ${
                pluginTestResult.ok ? 'bg-[#00FFA7]/10 text-[#00FFA7] border border-[#00FFA7]/20'
                  : 'bg-red-500/10 text-red-400 border border-red-500/20'
              }`}>
                {pluginTestResult.ok
                  ? `Connected — ${pluginTestResult.duration_ms}ms`
                  : `Error: ${pluginTestResult.error || `HTTP ${pluginTestResult.status_code}`}`
                }
              </div>
            )}

            <div className="flex items-center gap-2">
              {pluginIntegTarget.health_check && (
                <button
                  type="button"
                  onClick={handlePluginIntegTest}
                  disabled={pluginTesting}
                  className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm text-[#667085] hover:text-[#e6edf3] hover:bg-[#21262d] transition-colors disabled:opacity-60"
                >
                  {pluginTesting ? <Loader2 size={14} className="animate-spin" /> : <Zap size={14} />}
                  Test
                </button>
              )}
              <div className="ml-auto flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => setPluginIntegModalOpen(false)}
                  className="px-4 py-2 rounded-lg text-sm text-[#667085] hover:text-[#e6edf3] hover:bg-[#21262d] transition-colors"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  onClick={handlePluginIntegSave}
                  disabled={pluginIntegSaving}
                  className="flex items-center gap-1.5 px-4 py-2 rounded-lg bg-[#00FFA7]/80 text-black text-sm font-semibold hover:bg-[#00FFA7] transition-colors disabled:opacity-60 disabled:cursor-not-allowed"
                >
                  {pluginIntegSaving && <Loader2 size={14} className="animate-spin" />}
                  Save
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Header */}
      <div className="mb-8">
        <h1 className="text-2xl font-bold text-[#e6edf3] tracking-tight">{t('integrations.title')}</h1>
        <p className="text-[#667085] text-sm mt-1">Connected services, APIs, social accounts & databases</p>
      </div>

      {/* Tab bar */}
      <div className="mb-6 flex items-center gap-1 p-1 rounded-xl bg-[#0C111D] border border-[#21262d] w-fit">
        {([
          { key: 'integrations' as TabKey, label: 'Integrations', icon: Plug },
          { key: 'social' as TabKey,       label: 'Social',       icon: Globe },
          { key: 'databases' as TabKey,    label: 'Databases',    icon: Database },
        ]).map(({ key, label, icon: TabIcon }) => {
          const active = activeTab === key
          return (
            <button
              key={key}
              type="button"
              onClick={() => setActiveTab(key)}
              className={`flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-all ${
                active
                  ? 'bg-[#00FFA7]/10 text-[#00FFA7] border border-[#00FFA7]/25 shadow-[0_0_12px_rgba(0,255,167,0.08)]'
                  : 'text-[#667085] hover:text-[#e6edf3] border border-transparent'
              }`}
            >
              <TabIcon size={14} />
              {label}
            </button>
          )
        })}
      </div>

      {/* Stats — contextual per tab */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-6">
        {loading ? (
          <>
            <SkeletonStat />
            <SkeletonStat />
            <SkeletonStat />
          </>
        ) : activeTab === 'integrations' ? (
          <>
            <StatCard label="Connected" value={connectedCount} icon={CheckCircle2} />
            <StatCard label="Core Integrations" value={coreIntegrations.length} icon={Plug} />
            <StatCard label="Custom Integrations" value={customIntegrations.length} icon={Settings} />
          </>
        ) : activeTab === 'social' ? (
          <>
            <StatCard label="Connected Platforms" value={connectedPlatformsCount} icon={CheckCircle2} />
            <StatCard label="Social Accounts" value={totalSocialAccounts} icon={Globe} />
            <StatCard label="Platforms Available" value={platforms.length} icon={Plug} />
          </>
        ) : (
          <>
            <StatCard label="Total Databases" value={totalDbConnections} icon={Database} />
            <StatCard label="SQL (Postgres, MySQL)" value={sqlDbCount} icon={Plug} />
            <StatCard label="NoSQL (Mongo, Redis)" value={nosqlDbCount} icon={Plug} />
          </>
        )}
      </div>

      {loading ? (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {[...Array(6)].map((_, i) => <SkeletonCard key={i} />)}
        </div>
      ) : (
        <>
          {activeTab === 'integrations' && (<>
          {/* GitHub — Brain Repo */}
          <div className="mb-6">
            <div className="flex items-center gap-2.5 mb-3">
              <div className="flex items-center justify-center w-7 h-7 rounded-lg bg-[#E6EDF3]/8 border border-[#E6EDF3]/15">
                <GitBranch size={14} className="text-[#E6EDF3]" />
              </div>
              <h2 className="text-base font-semibold text-[#e6edf3]">GitHub (Brain Repo)</h2>
            </div>
            <div
              className="group relative rounded-xl border bg-[#161b22] p-5 transition-all duration-300"
              style={{
                borderColor: brainRepoStatus?.connected ? 'rgba(0,255,167,0.25)' : '#21262d',
              }}
            >
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <div className="flex items-center justify-center w-10 h-10 rounded-xl border"
                    style={{
                      backgroundColor: brainRepoStatus?.connected ? 'rgba(0,255,167,0.08)' : 'rgba(230,237,243,0.04)',
                      borderColor: brainRepoStatus?.connected ? 'rgba(0,255,167,0.2)' : '#21262d',
                    }}>
                    <GitBranch size={18} style={{ color: brainRepoStatus?.connected ? '#00FFA7' : '#8b949e' }} />
                  </div>
                  <div>
                    <p className="text-sm font-semibold text-[#e6edf3]">Brain Repo</p>
                    {brainRepoStatus?.connected && brainRepoStatus.repo_url ? (
                      <a
                        href={brainRepoStatus.repo_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-[11px] text-[#00FFA7]/70 hover:text-[#00FFA7] transition-colors truncate max-w-xs block"
                      >
                        {brainRepoStatus.repo_url}
                      </a>
                    ) : (
                      <p className="text-[11px] text-[#667085]">Version control for workspace configuration</p>
                    )}
                  </div>
                </div>
                <div className="flex items-center gap-3">
                  {brainRepoStatus?.connected ? (
                    <>
                      {(brainRepoStatus.pending_count ?? 0) > 0 && (
                        <span className="text-[10px] px-2 py-0.5 rounded-full bg-[#F59E0B]/10 text-[#F59E0B] border border-[#F59E0B]/20">
                          {brainRepoStatus.pending_count} pending
                        </span>
                      )}
                      {brainRepoStatus.last_sync && (
                        <span className="text-[10px] text-[#667085]">
                          Synced {new Date(brainRepoStatus.last_sync).toLocaleDateString()}
                        </span>
                      )}
                      <span className="flex items-center gap-1.5 px-2 py-1 rounded-full bg-[#00FFA7]/10 border border-[#00FFA7]/20 text-[10px] font-semibold uppercase tracking-wider text-[#00FFA7]">
                        <span className="h-1.5 w-1.5 rounded-full bg-[#00FFA7]" />
                        Connected
                      </span>
                      <a
                        href="/settings/brain-repo"
                        className="text-xs px-3 py-1.5 rounded-lg border border-[#21262d] text-[#8b949e] hover:text-[#e6edf3] hover:border-[#30363d] transition-colors"
                      >
                        Manage
                      </a>
                    </>
                  ) : (
                    <a
                      href="/settings/brain-repo"
                      className="text-xs px-3 py-1.5 rounded-lg bg-[#00FFA7]/10 text-[#00FFA7] border border-[#00FFA7]/20 hover:bg-[#00FFA7]/20 transition-all"
                    >
                      Connect
                    </a>
                  )}
                </div>
              </div>
            </div>
          </div>

          {/* Core Integrations */}
          <div className="mb-10">
            <div className="flex items-center gap-2.5 mb-4">
              <div className="flex items-center justify-center w-7 h-7 rounded-lg bg-[#00FFA7]/8 border border-[#00FFA7]/15">
                <Plug size={14} className="text-[#00FFA7]" />
              </div>
              <h2 className="text-base font-semibold text-[#e6edf3]">Core Integrations</h2>
              <span className="text-xs px-2 py-0.5 rounded-full bg-[#00FFA7]/10 text-[#00FFA7] border border-[#00FFA7]/20">
                {coreIntegrations.length}
              </span>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {coreIntegrations.map((int, i) => (
                <IntegrationCard
                  key={i}
                  int={int}
                  onSelect={setSelectedIntegration}
                />
              ))}
            </div>
          </div>

          {/* Plugin Integrations — Wave 2.2r */}
          {pluginIntegrations.length > 0 && (
          <div className="mb-10">
            <div className="flex items-center justify-between mb-4">
              <div className="flex items-center gap-2.5">
                <div className="flex items-center justify-center w-7 h-7 rounded-lg bg-purple-500/10 border border-purple-500/20">
                  <Puzzle size={14} className="text-purple-400" />
                </div>
                <h2 className="text-base font-semibold text-[#e6edf3]">Plugin Integrations</h2>
                <span className="text-xs px-2 py-0.5 rounded-full bg-purple-500/10 text-purple-400 border border-purple-500/20">
                  {pluginIntegrations.length}
                </span>
              </div>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {pluginIntegrations.map((int, i) => {
                const isConnected = int.status === 'connected' || int.status === 'ok'
                const isError = int.status === 'error'
                const hasHealthCheck = !!int.health_check
                return (
                  <div
                    key={i}
                    className="group relative rounded-xl border border-[#21262d] bg-[#161b22] p-5 transition-all duration-300 hover:border-purple-500/30"
                  >
                    {/* Top row */}
                    <div className="flex items-start justify-between mb-3">
                      <div className="flex items-center justify-center h-10 w-10 rounded-lg bg-purple-500/10 transition-transform duration-300 group-hover:scale-110">
                        <Puzzle size={20} className="text-purple-400" />
                      </div>
                      <div className="flex items-center gap-1.5">
                        <span
                          className="inline-block h-2.5 w-2.5 rounded-full mt-1"
                          style={{
                            backgroundColor: isConnected ? '#00FFA7' : isError ? '#EF4444' : '#3F3F46',
                            boxShadow: isConnected ? '0 0 8px rgba(0,255,167,0.5)' : 'none',
                          }}
                        />
                      </div>
                    </div>

                    {/* Name + badges */}
                    <div className="flex items-center gap-2 mb-1 flex-wrap">
                      <h3 className="text-[15px] font-semibold text-[#e6edf3]">{int.name}</h3>
                      <span className="text-[9px] font-semibold uppercase tracking-wider px-1.5 py-0.5 rounded-full bg-purple-500/10 text-purple-400 border border-purple-500/20">
                        via plugin
                      </span>
                    </div>
                    <p className="text-[11px] text-[#667085] mb-3">
                      {int.source_plugin}
                    </p>

                    {/* Status + category badges */}
                    <div className="flex items-center gap-2 mb-3 flex-wrap">
                      <span className="text-[10px] font-medium uppercase tracking-wider px-2 py-0.5 rounded-full border border-purple-500/20 text-purple-400 bg-purple-500/8">
                        {int.category}
                      </span>
                      <span className={`text-[10px] font-medium px-2 py-0.5 rounded-full border ${
                        isConnected
                          ? 'bg-[#00FFA7]/10 text-[#00FFA7] border-[#00FFA7]/25'
                          : isError
                          ? 'bg-red-500/10 text-red-400 border-red-500/25'
                          : 'bg-[#FBBF24]/10 text-[#FBBF24] border-[#FBBF24]/25'
                      }`}>
                        {isConnected ? 'Connected' : isError ? 'Error' : 'Not configured'}
                      </span>
                    </div>
                    {int.last_health?.last_error && isError && (
                      <p className="text-[10px] text-red-400 mb-2 truncate" title={int.last_health.last_error}>
                        {int.last_health.last_error}
                      </p>
                    )}

                    {/* Action buttons */}
                    <div className="flex items-center gap-2">
                      <button
                        type="button"
                        onClick={() => openPluginIntegModal(int)}
                        className="flex items-center gap-1 text-[11px] px-2.5 py-1 rounded-lg bg-[#00FFA7]/10 text-[#00FFA7] border border-[#00FFA7]/20 hover:bg-[#00FFA7]/20 transition-all"
                      >
                        <Settings size={11} /> Configure
                      </button>
                      {hasHealthCheck && (
                        <span className="text-[10px] text-[#667085] italic">health check available</span>
                      )}
                    </div>
                  </div>
                )
              })}
            </div>
          </div>
          )}

          {/* Custom Integrations */}
          <div className="mb-10">
            <div className="flex items-center justify-between mb-4">
              <div className="flex items-center gap-2.5">
                <div className="flex items-center justify-center w-7 h-7 rounded-lg bg-[#00FFA7]/8 border border-[#00FFA7]/15">
                  <Settings size={14} className="text-[#00FFA7]" />
                </div>
                <h2 className="text-base font-semibold text-[#e6edf3]">Custom Integrations</h2>
                {customIntegrations.length > 0 && (
                  <span className="text-xs px-2 py-0.5 rounded-full bg-[#00FFA7]/10 text-[#00FFA7] border border-[#00FFA7]/20">
                    {customIntegrations.length}
                  </span>
                )}
              </div>
              <button
                type="button"
                onClick={openCreateModal}
                className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-full bg-[#00FFA7]/10 text-[#00FFA7] border border-[#00FFA7]/20 hover:bg-[#00FFA7]/20 transition-all"
              >
                <Plus size={13} /> Add Custom
              </button>
            </div>

            {customIntegrations.length === 0 ? (
              <div
                onClick={openCreateModal}
                role="button"
                tabIndex={0}
                onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openCreateModal() } }}
                className="cursor-pointer rounded-xl border border-dashed border-[#21262d] hover:border-[#00FFA7]/30 bg-[#161b22]/50 p-8 flex flex-col items-center justify-center gap-2 transition-colors group"
              >
                <div className="flex items-center justify-center w-10 h-10 rounded-xl bg-[#00FFA7]/8 border border-[#00FFA7]/15 group-hover:bg-[#00FFA7]/15 transition-colors">
                  <Plus size={20} className="text-[#00FFA7]" />
                </div>
                <p className="text-sm font-medium text-[#667085] group-hover:text-[#e6edf3] transition-colors">Add custom integration</p>
                <p className="text-xs text-[#3F3F46]">Creates a SKILL.md template in .claude/skills/</p>
              </div>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                {customIntegrations.map((int, i) => (
                  <IntegrationCard
                    key={i}
                    int={int}
                    onSelect={() => {}}
                    onEdit={openEditModal}
                    onDelete={setDeleteTarget}
                  />
                ))}
                {/* Add more card */}
                <div
                  onClick={openCreateModal}
                  role="button"
                  tabIndex={0}
                  onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openCreateModal() } }}
                  className="cursor-pointer rounded-xl border border-dashed border-[#21262d] hover:border-[#00FFA7]/30 bg-[#161b22]/50 p-5 flex flex-col items-center justify-center gap-2 transition-colors group min-h-[120px]"
                >
                  <Plus size={18} className="text-[#3F3F46] group-hover:text-[#00FFA7] transition-colors" />
                  <p className="text-xs text-[#3F3F46] group-hover:text-[#667085] transition-colors">Add custom integration</p>
                </div>
              </div>
            )}
          </div>

          </>)}

          {activeTab === 'social' && (
          /* Social Accounts */
          <div>
            <div className="flex items-center gap-2.5 mb-6">
              <div className="flex items-center justify-center w-7 h-7 rounded-lg bg-[#00FFA7]/8 border border-[#00FFA7]/15">
                <Globe size={14} className="text-[#00FFA7]" />
              </div>
              <h2 className="text-base font-semibold text-[#e6edf3]">Social Accounts</h2>
            </div>

            <div className="space-y-6">
              {platforms.map((platform) => {
                const platMeta = getPlatformMeta(platform.id)
                const PlatIcon = platMeta.icon

                return (
                  <div key={platform.id}>
                    {/* Platform header */}
                    <div className="flex items-center justify-between mb-3">
                      <div className="flex items-center gap-3">
                        <div
                          className="flex h-8 w-8 items-center justify-center rounded-lg"
                          style={{ backgroundColor: platMeta.colorMuted }}
                        >
                          <PlatIcon size={16} style={{ color: platMeta.color }} />
                        </div>
                        <span className="font-semibold text-[#e6edf3] text-sm">{platform.name}</span>
                        <span className="text-[11px] px-2 py-0.5 rounded-full bg-white/[0.04] text-[#667085] border border-[#21262d]">
                          {platform.accounts.length} account{platform.accounts.length !== 1 ? 's' : ''}
                        </span>
                      </div>
                      <a
                        href={`/connect/${platform.id}`}
                        className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-full bg-[#00FFA7]/10 text-[#00FFA7] border border-[#00FFA7]/20 hover:bg-[#00FFA7]/20 hover:shadow-[0_0_12px_rgba(0,255,167,0.10)] transition-all"
                      >
                        <Plus size={13} /> Add account
                      </a>
                    </div>

                    {/* Account cards */}
                    {platform.accounts.length > 0 ? (
                      <div className="space-y-2">
                        {platform.accounts.map((acc) => {
                          const isOk = acc.status === 'connected'
                          const isExpiring = acc.status === 'expiring'
                          const isExpired = acc.status === 'expired'

                          return (
                            <div
                              key={acc.index}
                              className="group relative rounded-xl border border-[#21262d] bg-[#161b22] p-4 flex items-center justify-between transition-all duration-300 hover:border-transparent"
                            >
                              {/* Hover glow */}
                              <div
                                className="pointer-events-none absolute inset-0 rounded-xl opacity-0 transition-opacity duration-300 group-hover:opacity-100"
                                style={{
                                  boxShadow: `inset 0 0 0 1px ${platMeta.color}44, 0 0 16px ${platMeta.glowColor}`,
                                  borderRadius: 'inherit',
                                }}
                              />

                              <div className="relative flex items-center gap-3">
                                {/* Status dot */}
                                <span
                                  className="inline-block w-2.5 h-2.5 rounded-full shrink-0"
                                  style={{
                                    backgroundColor: isOk ? '#00FFA7' : isExpired ? '#EF4444' : isExpiring ? '#FBBF24' : '#3F3F46',
                                    boxShadow: isOk ? '0 0 6px rgba(0,255,167,0.5)' : isExpired ? '0 0 6px rgba(239,68,68,0.5)' : 'none',
                                  }}
                                />
                                <div>
                                  <p className="text-sm font-medium text-[#e6edf3]">{acc.label}</p>
                                  <p className="text-xs text-[#667085] mt-0.5">{acc.detail}</p>
                                </div>
                              </div>

                              <div className="relative flex items-center gap-2">
                                <span className={`inline-flex items-center gap-1 text-[10px] font-medium px-2.5 py-1 rounded-full border ${
                                  isOk ? 'bg-[#00FFA7]/10 text-[#00FFA7] border-[#00FFA7]/25' :
                                  isExpiring ? 'bg-[#FBBF24]/10 text-[#FBBF24] border-[#FBBF24]/25' :
                                  isExpired ? 'bg-red-500/10 text-red-400 border-red-500/25' :
                                  'bg-white/[0.04] text-[#667085] border-[#21262d]'
                                }`}>
                                  {isOk && <CheckCircle2 size={10} />}
                                  {(isExpiring || isExpired) && <AlertCircle size={10} />}
                                  {isOk ? 'Connected' :
                                   isExpiring ? `Expires in ${acc.days_left}d` :
                                   isExpired ? 'Expired' : 'Incomplete'}
                                </span>
                                <button
                                  onClick={() => handleDisconnect(platform.id, acc.index)}
                                  className="p-1.5 rounded-lg hover:bg-red-500/10 text-[#667085] hover:text-red-400 transition-colors"
                                  title="Remove"
                                >
                                  <Trash2 size={14} />
                                </button>
                              </div>
                            </div>
                          )
                        })}
                      </div>
                    ) : (
                      <div className="rounded-xl border border-dashed border-[#21262d] bg-[#161b22]/50 p-6 text-center">
                        <p className="text-sm text-[#667085]">No accounts connected</p>
                        <p className="text-xs text-[#3F3F46] mt-1">Click "Add account" to get started</p>
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          </div>
          )}

          {activeTab === 'databases' && (
            <DatabasesTab
              flavors={dbFlavors}
              onReload={loadData}
            />
          )}
        </>
      )}
    </div>
  )
}


// ─── Databases Tab (CRUD via .env — pattern matches social accounts) ────────

interface DatabasesTabProps {
  flavors: DatabaseFlavor[]
  onReload: () => void
}

interface FlavorMeta {
  color: string
  colorMuted: string
  label: string
  defaultPort: number
}

const DB_FLAVOR_META: Record<string, FlavorMeta> = {
  postgres: { color: '#00AEEF', colorMuted: 'rgba(0,174,239,0.10)', label: 'Postgres', defaultPort: 5432 },
  mysql:    { color: '#F29111', colorMuted: 'rgba(242,145,17,0.10)', label: 'MySQL',    defaultPort: 3306 },
  mongo:    { color: '#4DB33D', colorMuted: 'rgba(77,179,61,0.10)',  label: 'MongoDB',  defaultPort: 27017 },
  redis:    { color: '#DC382D', colorMuted: 'rgba(220,56,45,0.10)',  label: 'Redis',    defaultPort: 6379 },
}

type DbFormState = {
  label: string
  host: string
  port: string
  database: string
  user: string
  password: string
  ssl_mode: string
  ssl_ca_path: string
  dsn: string
  // Mongo
  uri: string
  auth_source: string
  tls: boolean
  // Redis
  url: string
  db: string
  username: string
  //
  allow_write: boolean
  query_timeout: string
  max_rows: string
}

const EMPTY_DB_FORM: DbFormState = {
  label: '',
  host: '',
  port: '',
  database: '',
  user: '',
  password: '',
  ssl_mode: '',
  ssl_ca_path: '',
  dsn: '',
  uri: '',
  auth_source: '',
  tls: false,
  url: '',
  db: '',
  username: '',
  allow_write: false,
  query_timeout: '',
  max_rows: '',
}

function DatabasesTab({ flavors, onReload }: DatabasesTabProps) {
  const postgres = flavors.find(f => f.slug === 'postgres') ?? { slug: 'postgres', skill: 'db-postgres', ok: true, count: 0, connections: [] }
  const mysql    = flavors.find(f => f.slug === 'mysql')    ?? { slug: 'mysql',    skill: 'db-mysql',    ok: true, count: 0, connections: [] }
  const mongo    = flavors.find(f => f.slug === 'mongo')    ?? { slug: 'mongo',    skill: 'db-mongo',    ok: true, count: 0, connections: [] }
  const redis    = flavors.find(f => f.slug === 'redis')    ?? { slug: 'redis',    skill: 'db-redis',    ok: true, count: 0, connections: [] }

  const [modalFlavor, setModalFlavor] = useState<'postgres' | 'mysql' | 'mongo' | 'redis' | null>(null)
  const [editingIndex, setEditingIndex] = useState<number | null>(null)
  const [formInitial, setFormInitial] = useState<Partial<DbFormState>>({})

  const [deleteTarget, setDeleteTarget] = useState<{ flavor: 'postgres' | 'mysql' | 'mongo' | 'redis'; index: number; label: string } | null>(null)
  const [deleting, setDeleting] = useState(false)

  const openCreate = (flavor: 'postgres' | 'mysql' | 'mongo' | 'redis') => {
    setEditingIndex(null)
    setFormInitial({})
    setModalFlavor(flavor)
  }

  const openEdit = (flavor: 'postgres' | 'mysql' | 'mongo' | 'redis', conn: DatabaseConnection) => {
    setEditingIndex(conn.index)
    const usesConnString = conn.host === '<dsn>' || conn.host === '<uri>' || conn.host === '<url>'
    setFormInitial({
      label: conn.label,
      host: usesConnString ? '' : (conn.host ?? ''),
      port: conn.port ? String(conn.port) : '',
      database: flavor === 'redis' ? '' : (conn.database ?? ''),
      db: flavor === 'redis' ? (conn.database ?? '') : '',
      ssl_mode: conn.ssl_mode ?? '',
      allow_write: conn.allow_write,
      query_timeout: String(conn.query_timeout),
      max_rows: String(conn.max_rows),
    })
    setModalFlavor(flavor)
  }

  const handleDelete = async () => {
    if (!deleteTarget) return
    setDeleting(true)
    try {
      await api.delete(`/integrations/databases/${deleteTarget.flavor}/${deleteTarget.index}`)
      setDeleteTarget(null)
      onReload()
    } catch (e) {
      console.error(e)
    } finally {
      setDeleting(false)
    }
  }

  return (
    <div className="space-y-8">
      {/* Delete confirm */}
      {deleteTarget && (
        <div className="fixed inset-0 z-[70] flex items-center justify-center p-4">
          <div className="absolute inset-0 bg-black/60 backdrop-blur-[2px]" onClick={() => setDeleteTarget(null)} />
          <div className="relative w-full max-w-sm bg-[#0C111D] border border-[#21262d] rounded-2xl shadow-2xl p-6">
            <h3 className="text-base font-semibold text-[#e6edf3] mb-2">Remove database connection</h3>
            <p className="text-sm text-[#667085] mb-5">
              Remove <span className="text-[#e6edf3] font-medium">{deleteTarget.label}</span>? The env variables will be deleted from{' '}
              <code className="text-[#00FFA7] font-mono text-xs">.env</code>.
            </p>
            <div className="flex items-center justify-end gap-3">
              <button onClick={() => setDeleteTarget(null)} className="px-4 py-2 rounded-lg text-sm text-[#667085] hover:text-[#e6edf3] hover:bg-[#21262d] transition-colors">Cancel</button>
              <button onClick={handleDelete} disabled={deleting} className="flex items-center gap-1.5 px-4 py-2 rounded-lg bg-red-500/80 text-white text-sm font-semibold hover:bg-red-500 transition-colors disabled:opacity-60">
                {deleting && <Loader2 size={14} className="animate-spin" />} Remove
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Modal */}
      {modalFlavor && (
        <DatabaseFormModal
          flavor={modalFlavor}
          editingIndex={editingIndex}
          initial={formInitial}
          onClose={() => setModalFlavor(null)}
          onSaved={() => { setModalFlavor(null); onReload() }}
        />
      )}

      <FlavorSection flavor={postgres} onAdd={() => openCreate('postgres')} onEdit={(c) => openEdit('postgres', c)} onDelete={(c) => setDeleteTarget({ flavor: 'postgres', index: c.index, label: c.label })} />
      <FlavorSection flavor={mysql}    onAdd={() => openCreate('mysql')}    onEdit={(c) => openEdit('mysql', c)}    onDelete={(c) => setDeleteTarget({ flavor: 'mysql', index: c.index, label: c.label })} />
      <FlavorSection flavor={mongo}    onAdd={() => openCreate('mongo')}    onEdit={(c) => openEdit('mongo', c)}    onDelete={(c) => setDeleteTarget({ flavor: 'mongo', index: c.index, label: c.label })} />
      <FlavorSection flavor={redis}    onAdd={() => openCreate('redis')}    onEdit={(c) => openEdit('redis', c)}    onDelete={(c) => setDeleteTarget({ flavor: 'redis', index: c.index, label: c.label })} />
    </div>
  )
}

// ─── Flavor section ──────────────────────────────────────────────────────────

function FlavorSection({ flavor, onAdd, onEdit, onDelete }: {
  flavor: DatabaseFlavor
  onAdd: () => void
  onEdit: (c: DatabaseConnection) => void
  onDelete: (c: DatabaseConnection) => void
}) {
  const meta = DB_FLAVOR_META[flavor.slug] ?? { color: '#667085', colorMuted: 'rgba(102,112,133,0.10)', label: flavor.slug, defaultPort: 0 }

  return (
    <section>
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2.5">
          <div className="flex items-center justify-center w-7 h-7 rounded-lg" style={{ backgroundColor: meta.colorMuted, border: `1px solid ${meta.color}33` }}>
            <Database size={14} style={{ color: meta.color }} />
          </div>
          <h2 className="text-base font-semibold text-[#e6edf3]">{meta.label}</h2>
          {flavor.count > 0 && (
            <span className="text-xs px-2 py-0.5 rounded-full" style={{ backgroundColor: meta.colorMuted, color: meta.color, border: `1px solid ${meta.color}40` }}>
              {flavor.count}
            </span>
          )}
          {!flavor.ok && (
            <span className="text-[10px] px-2 py-0.5 rounded-full bg-red-500/10 text-red-400 border border-red-500/25 flex items-center gap-1">
              <AlertCircle size={10} /> {flavor.error || 'parser error'}
            </span>
          )}
        </div>
        <button onClick={onAdd} className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-full bg-[#00FFA7]/10 text-[#00FFA7] border border-[#00FFA7]/20 hover:bg-[#00FFA7]/20 transition-all">
          <Plus size={13} /> Add {meta.label}
        </button>
      </div>

      {flavor.connections.length === 0 ? (
        <div onClick={onAdd} role="button" tabIndex={0} onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onAdd() } }}
          className="cursor-pointer rounded-xl border border-dashed border-[#21262d] hover:border-[#00FFA7]/30 bg-[#161b22]/50 p-8 flex flex-col items-center justify-center gap-2 transition-colors group">
          <div className="flex items-center justify-center w-10 h-10 rounded-xl bg-[#00FFA7]/8 border border-[#00FFA7]/15 group-hover:bg-[#00FFA7]/15 transition-colors">
            <Plus size={20} className="text-[#00FFA7]" />
          </div>
          <p className="text-sm font-medium text-[#667085] group-hover:text-[#e6edf3] transition-colors">Add {meta.label} connection</p>
          <p className="text-xs text-[#3F3F46]">Host, port, user, password — stored in .env automatically</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {flavor.connections.map(conn => (
            <ConnectionCard key={conn.index} conn={conn} flavor={flavor.slug} meta={meta} onEdit={() => onEdit(conn)} onDelete={() => onDelete(conn)} />
          ))}
        </div>
      )}
    </section>
  )
}

// ─── Connection card ─────────────────────────────────────────────────────────

function ConnectionCard({ conn, flavor, meta, onEdit, onDelete }: {
  conn: DatabaseConnection
  flavor: string
  meta: FlavorMeta
  onEdit: () => void
  onDelete: () => void
}) {
  const envPrefix = `DB_${flavor.toUpperCase()}_${conn.index}`
  const hostDisplay =
    conn.host === '<dsn>' ? 'via DSN' :
    conn.host === '<uri>' ? 'via URI' :
    conn.host === '<url>' ? 'via URL' :
    (conn.host ?? '—')
  const dbLabel = flavor === 'redis' ? 'DB index' : 'Database'
  const sslLabel = flavor === 'redis' || flavor === 'mongo' ? 'TLS' : 'SSL'
  const sslValue =
    flavor === 'redis' || flavor === 'mongo'
      ? (conn as DatabaseConnection & { tls?: boolean }).tls ? 'on' : 'off'
      : (conn.ssl_mode ?? '—')

  return (
    <div className="group relative rounded-xl border border-[#21262d] bg-[#161b22] p-4 transition-all hover:border-transparent">
      <div className="pointer-events-none absolute inset-0 rounded-xl opacity-0 transition-opacity duration-300 group-hover:opacity-100"
        style={{ boxShadow: `inset 0 0 0 1px ${meta.color}44, 0 0 16px ${meta.color}22`, borderRadius: 'inherit' }} />

      <div className="relative flex items-start justify-between mb-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 mb-0.5">
            <span className="text-sm font-semibold text-[#e6edf3] truncate">{conn.label}</span>
            <span className="text-[10px] px-1.5 py-0.5 rounded-full font-mono text-[#667085] bg-white/[0.04] border border-[#21262d] shrink-0">#{conn.index}</span>
          </div>
          <p className="text-[11px] text-[#667085] font-mono truncate">{envPrefix}_*</p>
        </div>
        <div className="flex items-center gap-1.5 shrink-0">
          {conn.allow_write ? (
            <span className="inline-flex items-center gap-1 text-[10px] font-medium px-2 py-0.5 rounded-full bg-amber-500/10 text-amber-400 border border-amber-500/25" title="ALLOW_WRITE=true">
              <Unlock size={10} /> read/write
            </span>
          ) : (
            <span className="inline-flex items-center gap-1 text-[10px] font-medium px-2 py-0.5 rounded-full bg-[#00FFA7]/10 text-[#00FFA7] border border-[#00FFA7]/25">
              <Lock size={10} /> read-only
            </span>
          )}
        </div>
      </div>

      <div className="relative grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs mb-3">
        <div><p className="text-[10px] uppercase tracking-wider text-[#3F3F46] mb-0.5">Host</p><p className="text-[#e6edf3] font-mono truncate" title={hostDisplay}>{hostDisplay}</p></div>
        <div><p className="text-[10px] uppercase tracking-wider text-[#3F3F46] mb-0.5">Port</p><p className="text-[#e6edf3] font-mono">{conn.port ?? '—'}</p></div>
        <div><p className="text-[10px] uppercase tracking-wider text-[#3F3F46] mb-0.5">{dbLabel}</p><p className="text-[#e6edf3] font-mono truncate" title={conn.database ?? '—'}>{conn.database ?? '—'}</p></div>
        <div><p className="text-[10px] uppercase tracking-wider text-[#3F3F46] mb-0.5">{sslLabel}</p><p className="text-[#e6edf3] font-mono">{sslValue}</p></div>
      </div>

      <div className="relative flex items-center justify-between gap-2 pt-3 border-t border-[#21262d]">
        <div className="flex items-center gap-3 text-[10px] text-[#667085]">
          <span>timeout <span className="text-[#e6edf3] font-mono">{conn.query_timeout}s</span></span>
          <span>max rows <span className="text-[#e6edf3] font-mono">{conn.max_rows}</span></span>
        </div>
        <div className="flex items-center gap-1">
          <button onClick={onEdit} className="flex items-center gap-1 px-2 py-1 rounded-lg text-[10px] text-[#667085] hover:text-[#e6edf3] hover:bg-[#21262d] transition-colors" title="Edit">
            <Pencil size={11} /> Edit
          </button>
          <button onClick={onDelete} className="p-1.5 rounded-lg hover:bg-red-500/10 text-[#667085] hover:text-red-400 transition-colors" title="Remove">
            <Trash2 size={12} />
          </button>
        </div>
      </div>
    </div>
  )
}

// ─── Add/Edit modal ──────────────────────────────────────────────────────────

const SSL_MODES_POSTGRES = ['', 'disable', 'require', 'verify-ca', 'verify-full']

function DatabaseFormModal({ flavor, editingIndex, initial, onClose, onSaved }: {
  flavor: 'postgres' | 'mysql' | 'mongo' | 'redis'
  editingIndex: number | null
  initial: Partial<DbFormState>
  onClose: () => void
  onSaved: () => void
}) {
  const meta = DB_FLAVOR_META[flavor]
  const isEdit = editingIndex !== null

  const [form, setForm] = useState<DbFormState>({
    ...EMPTY_DB_FORM,
    port: String(meta.defaultPort),
    query_timeout: '30',
    max_rows: '1000',
    ssl_mode: flavor === 'postgres' ? 'require' : '',
    ...initial,
  } as DbFormState)
  const [showPassword, setShowPassword] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const update = <K extends keyof DbFormState>(key: K, value: DbFormState[K]) => {
    setForm(prev => ({ ...prev, [key]: value }))
  }

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)

    if (!form.label.trim()) { setError('Label is required'); return }

    // Host required unless a connection string is provided
    const connectionStringField =
      flavor === 'mongo' ? form.uri.trim() :
      flavor === 'redis' ? form.url.trim() :
      form.dsn.trim()
    if (!connectionStringField && !form.host.trim()) {
      setError(flavor === 'mongo' ? 'Host or URI is required' :
               flavor === 'redis' ? 'Host or URL is required' :
               'Host is required (or use a full DSN)')
      return
    }

    const body: Record<string, unknown> = {
      label: form.label.trim(),
      host: form.host.trim() || undefined,
      port: form.port.trim() || undefined,
      allow_write: form.allow_write ? 'true' : undefined,
      query_timeout: form.query_timeout.trim() || undefined,
      max_rows: form.max_rows.trim() || undefined,
    }

    if (flavor === 'postgres' || flavor === 'mysql') {
      body.database = form.database.trim() || undefined
      body.user = form.user.trim() || undefined
      body.ssl_ca_path = form.ssl_ca_path.trim() || undefined
      body.dsn = form.dsn.trim() || undefined
      if (flavor === 'postgres') body.ssl_mode = form.ssl_mode || undefined
    } else if (flavor === 'mongo') {
      body.database = form.database.trim() || undefined
      body.user = form.user.trim() || undefined
      body.auth_source = form.auth_source.trim() || undefined
      body.tls = form.tls ? 'true' : undefined
      body.uri = form.uri.trim() || undefined
    } else if (flavor === 'redis') {
      body.db = form.db.trim() || undefined
      body.username = form.username.trim() || undefined
      body.tls = form.tls ? 'true' : undefined
      body.url = form.url.trim() || undefined
    }

    if (isEdit) {
      // Keep current password if untouched
      body.password = form.password === '' ? '__KEEP__' : form.password
    } else {
      body.password = form.password
    }

    setSaving(true)
    try {
      if (isEdit) {
        await api.put(`/integrations/databases/${flavor}/${editingIndex}`, body)
      } else {
        await api.post(`/integrations/databases/${flavor}`, body)
      }
      onSaved()
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'Failed to save'
      setError(msg)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/60 backdrop-blur-[2px]" onClick={onClose} />
      <div className="relative w-full max-w-lg max-h-[90vh] overflow-y-auto bg-[#0C111D] border border-[#21262d] rounded-2xl shadow-2xl">
        <form onSubmit={submit}>
          {/* Header */}
          <div className="flex items-center justify-between p-5 border-b border-[#21262d]">
            <div className="flex items-center gap-3">
              <div className="flex items-center justify-center w-9 h-9 rounded-lg" style={{ backgroundColor: meta.colorMuted, border: `1px solid ${meta.color}33` }}>
                <Database size={16} style={{ color: meta.color }} />
              </div>
              <div>
                <h3 className="text-base font-semibold text-[#e6edf3]">{isEdit ? 'Edit' : 'Add'} {meta.label} connection</h3>
                <p className="text-xs text-[#667085] mt-0.5">Saved to <code className="text-[#00FFA7] font-mono">.env</code> as <code className="text-[#00FFA7] font-mono">DB_{flavor.toUpperCase()}_N_*</code></p>
              </div>
            </div>
            <button type="button" onClick={onClose} className="p-1.5 rounded-lg text-[#667085] hover:text-[#e6edf3] hover:bg-[#21262d]"><X size={16} /></button>
          </div>

          {/* Fields */}
          <div className="p-5 space-y-4">
            {error && (
              <div className="flex items-start gap-2 rounded-lg bg-red-500/10 border border-red-500/25 px-3 py-2 text-xs text-red-300">
                <AlertCircle size={14} className="shrink-0 mt-0.5" /><span>{error}</span>
              </div>
            )}

            <Field label="Label" required hint="How agents refer to this DB (e.g. msgops-dev)">
              <input type="text" value={form.label} onChange={e => update('label', e.target.value)} placeholder="msgops-dev" autoFocus className={inputClass} />
            </Field>

            <div className="grid grid-cols-[1fr,90px] gap-3">
              <Field label="Host">
                <input type="text" value={form.host} onChange={e => update('host', e.target.value)} placeholder="db.example.com" className={inputClass} />
              </Field>
              <Field label="Port">
                <input type="number" value={form.port} onChange={e => update('port', e.target.value)} placeholder={String(meta.defaultPort)} className={inputClass} />
              </Field>
            </div>

            {/* "Database" / "DB" — numeric for redis, name for the others */}
            {flavor === 'redis' ? (
              <Field label="DB index" hint="Redis numeric database (default 0)">
                <input type="number" min={0} value={form.db} onChange={e => update('db', e.target.value)} placeholder="0" className={inputClass} />
              </Field>
            ) : (
              <Field label="Database" hint={flavor === 'mongo' ? 'Optional if included in URI' : undefined}>
                <input type="text" value={form.database} onChange={e => update('database', e.target.value)} placeholder="my_database" className={inputClass} />
              </Field>
            )}

            <div className="grid grid-cols-2 gap-3">
              <Field label={flavor === 'redis' ? 'Username (ACL)' : 'User'} hint={flavor === 'redis' ? 'Optional (Redis 6+ ACL)' : undefined}>
                <input
                  type="text"
                  value={flavor === 'redis' ? form.username : form.user}
                  onChange={e => update(flavor === 'redis' ? 'username' : 'user', e.target.value)}
                  placeholder={flavor === 'redis' ? 'default' : 'agent_readonly'}
                  className={inputClass}
                  autoComplete="off"
                />
              </Field>
              <Field label="Password" hint={isEdit ? 'Leave blank to keep current' : undefined}>
                <div className="relative">
                  <input type={showPassword ? 'text' : 'password'} value={form.password} onChange={e => update('password', e.target.value)} placeholder={isEdit ? '••••••• (unchanged)' : 'password'} className={inputClass + ' pr-9'} autoComplete="new-password" />
                  <button type="button" onClick={() => setShowPassword(v => !v)} className="absolute right-2 top-1/2 -translate-y-1/2 p-1 text-[#667085] hover:text-[#e6edf3]">
                    {showPassword ? <EyeOff size={14} /> : <Eye size={14} />}
                  </button>
                </div>
              </Field>
            </div>

            {flavor === 'postgres' && (
              <Field label="SSL mode" hint="Postgres connection encryption">
                <select value={form.ssl_mode} onChange={e => update('ssl_mode', e.target.value)} className={inputClass}>
                  {SSL_MODES_POSTGRES.map(m => <option key={m} value={m}>{m || '— none —'}</option>)}
                </select>
              </Field>
            )}

            {(flavor === 'postgres' || flavor === 'mysql') && (
              <Field label="SSL CA path" hint={flavor === 'postgres' ? 'Optional, for verify-ca / verify-full' : 'Optional, enables TLS verification'}>
                <input type="text" value={form.ssl_ca_path} onChange={e => update('ssl_ca_path', e.target.value)} placeholder="/path/to/ca.pem" className={inputClass} />
              </Field>
            )}

            {flavor === 'mongo' && (
              <div className="grid grid-cols-[1fr,auto] gap-3 items-end">
                <Field label="Auth source" hint="Optional (e.g. admin)">
                  <input type="text" value={form.auth_source} onChange={e => update('auth_source', e.target.value)} placeholder="admin" className={inputClass} />
                </Field>
                <label className="flex items-center gap-2 cursor-pointer select-none pb-2">
                  <input type="checkbox" checked={form.tls} onChange={e => update('tls', e.target.checked)} className="peer sr-only" />
                  <span className={`w-9 h-5 rounded-full border transition-all flex items-center ${form.tls ? 'bg-[#00FFA7]/30 border-[#00FFA7]/50' : 'bg-[#161b22] border-[#21262d]'}`}>
                    <span className={`block w-4 h-4 rounded-full transition-all ${form.tls ? 'ml-4 bg-[#00FFA7]' : 'ml-0.5 bg-[#667085]'}`} />
                  </span>
                  <span className="text-xs text-[#e6edf3] font-medium">TLS</span>
                </label>
              </div>
            )}

            {flavor === 'redis' && (
              <label className="flex items-center gap-2.5 cursor-pointer select-none">
                <input type="checkbox" checked={form.tls} onChange={e => update('tls', e.target.checked)} className="peer sr-only" />
                <span className={`w-9 h-5 rounded-full border transition-all flex items-center ${form.tls ? 'bg-[#00FFA7]/30 border-[#00FFA7]/50' : 'bg-[#161b22] border-[#21262d]'}`}>
                  <span className={`block w-4 h-4 rounded-full transition-all ${form.tls ? 'ml-4 bg-[#00FFA7]' : 'ml-0.5 bg-[#667085]'}`} />
                </span>
                <div>
                  <span className="text-xs text-[#e6edf3] font-medium">TLS (rediss://)</span>
                  <p className="text-[10px] text-[#667085] mt-0.5">Enable for managed Redis (Upstash, AWS, etc).</p>
                </div>
              </label>
            )}

            {/* Advanced */}
            <details className="group rounded-lg border border-[#21262d] bg-[#161b22]/50">
              <summary className="cursor-pointer px-3 py-2 text-xs text-[#667085] hover:text-[#e6edf3] flex items-center gap-1.5 select-none">
                <span className="group-open:rotate-90 transition-transform">▸</span> Advanced
              </summary>
              <div className="px-3 pb-3 pt-1 space-y-3">
                {(flavor === 'postgres' || flavor === 'mysql') && (
                  <Field label="Full DSN (overrides host/port/user/password)" hint="Use only if the components above don't cover your setup">
                    <input type="text" value={form.dsn} onChange={e => update('dsn', e.target.value)} placeholder={flavor === 'postgres' ? 'postgresql://user:pw@host:5432/db' : 'mysql://user:pw@host:3306/db'} className={inputClass + ' font-mono text-xs'} />
                  </Field>
                )}
                {flavor === 'mongo' && (
                  <Field label="Full URI (overrides host/port/user/password)" hint="Use for Atlas or complex connection strings">
                    <input type="text" value={form.uri} onChange={e => update('uri', e.target.value)} placeholder="mongodb+srv://user:pw@cluster.abc.mongodb.net/db" className={inputClass + ' font-mono text-xs'} />
                  </Field>
                )}
                {flavor === 'redis' && (
                  <Field label="Full URL (overrides host/port/username/password)" hint="Use for managed Redis (Upstash, AWS, etc)">
                    <input type="text" value={form.url} onChange={e => update('url', e.target.value)} placeholder="rediss://user:pw@host:6380/0" className={inputClass + ' font-mono text-xs'} />
                  </Field>
                )}

                <div className="grid grid-cols-2 gap-3">
                  <Field label="Query timeout (s)">
                    <input type="number" min={1} value={form.query_timeout} onChange={e => update('query_timeout', e.target.value)} className={inputClass} />
                  </Field>
                  <Field label="Max rows returned">
                    <input type="number" min={1} value={form.max_rows} onChange={e => update('max_rows', e.target.value)} className={inputClass} />
                  </Field>
                </div>

                <label className="flex items-center gap-2.5 cursor-pointer select-none">
                  <input type="checkbox" checked={form.allow_write} onChange={e => update('allow_write', e.target.checked)} className="peer sr-only" />
                  <span className={`w-9 h-5 rounded-full border transition-all flex items-center ${form.allow_write ? 'bg-amber-500/30 border-amber-500/50' : 'bg-[#161b22] border-[#21262d]'}`}>
                    <span className={`block w-4 h-4 rounded-full transition-all ${form.allow_write ? 'ml-4 bg-amber-400' : 'ml-0.5 bg-[#667085]'}`} />
                  </span>
                  <div>
                    <span className="text-xs text-[#e6edf3] font-medium">Allow write queries</span>
                    <p className="text-[10px] text-[#667085] mt-0.5">Default off. When on, DELETE/UPDATE/INSERT are permitted on this DB.</p>
                  </div>
                </label>
              </div>
            </details>
          </div>

          {/* Footer */}
          <div className="flex items-center justify-end gap-3 px-5 py-4 border-t border-[#21262d] bg-[#0a0f18]">
            <button type="button" onClick={onClose} className="px-4 py-2 rounded-lg text-sm text-[#667085] hover:text-[#e6edf3] hover:bg-[#21262d] transition-colors">Cancel</button>
            <button type="submit" disabled={saving} className="flex items-center gap-1.5 px-4 py-2 rounded-lg bg-[#00FFA7] text-[#0C111D] text-sm font-semibold hover:bg-[#00FFA7]/90 transition-colors disabled:opacity-60">
              {saving && <Loader2 size={14} className="animate-spin" />} {isEdit ? 'Save changes' : 'Add connection'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

// ─── Small form building blocks ──────────────────────────────────────────────

const inputClass = "w-full bg-[#161b22] border border-[#21262d] rounded-lg px-3 py-2 text-sm text-[#e6edf3] placeholder:text-[#3F3F46] focus:outline-none focus:border-[#00FFA7]/40 focus:ring-1 focus:ring-[#00FFA7]/20 transition-colors"

function Field({ label, hint, required, children }: { label: string; hint?: string; required?: boolean; children: React.ReactNode }) {
  return (
    <div>
      <label className="flex items-center gap-1.5 text-xs font-medium text-[#e6edf3] mb-1.5">
        {label}{required && <span className="text-[#00FFA7]">*</span>}
      </label>
      {children}
      {hint && <p className="text-[10px] text-[#667085] mt-1">{hint}</p>}
    </div>
  )
}
