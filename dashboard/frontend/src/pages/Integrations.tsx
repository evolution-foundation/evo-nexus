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
  type LucideIcon,
} from 'lucide-react'
import { api } from '../lib/api'
import IntegrationDrawer from '../components/IntegrationDrawer'
import { getIntegrationMeta } from '../lib/integrationMeta'
import { useTranslation } from 'react-i18next'

interface Integration {
  name: string
  type: string
  status: 'ok' | 'error' | 'pending'
  kind: 'core' | 'custom'
  // custom-only fields
  slug?: string
  description?: string
  envKeys?: string[]
  category?: string
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
  const [integrations, setIntegrations] = useState<Integration[]>([])
  const [platforms, setPlatforms] = useState<SocialPlatform[]>([])
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
    ]).then(([intData, socialData, envData]) => {
      const ints = (intData?.integrations || []).map((i: any) => ({
        name: i.name || '',
        type: i.type || i.category || '',
        status: (i.status === 'ok' || i.configured) ? 'ok' as const : 'pending' as const,
        kind: i.kind || 'core',
        slug: i.slug,
        description: i.description,
        envKeys: i.envKeys,
        category: i.category,
      }))
      setIntegrations(ints)
      setPlatforms(socialData?.platforms || [])

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
  const connectedCount = integrations.filter((i) => i.status === 'ok').length
  const totalSocialAccounts = platforms.reduce((sum, p) => sum + p.accounts.length, 0)

  return (
    <div className="max-w-[1400px] mx-auto">
      <IntegrationDrawer
        integration={selectedIntegration}
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

      {/* Header */}
      <div className="mb-8">
        <h1 className="text-2xl font-bold text-[#e6edf3] tracking-tight">{t('integrations.title')}</h1>
        <p className="text-[#667085] text-sm mt-1">Connected services, APIs & social accounts</p>
      </div>

      {/* Stats Bar */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-6">
        {loading ? (
          <>
            <SkeletonStat />
            <SkeletonStat />
            <SkeletonStat />
          </>
        ) : (
          <>
            <StatCard label="Connected" value={connectedCount} icon={CheckCircle2} />
            <StatCard label="Total Integrations" value={integrations.length} icon={Plug} />
            <StatCard label="Social Accounts" value={totalSocialAccounts} icon={Globe} />
          </>
        )}
      </div>

      {loading ? (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {[...Array(6)].map((_, i) => <SkeletonCard key={i} />)}
        </div>
      ) : (
        <>
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

          {/* Social Accounts */}
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
        </>
      )}
    </div>
  )
}
