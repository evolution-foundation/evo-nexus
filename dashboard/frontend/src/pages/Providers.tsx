import { useEffect, useRef, useState } from 'react'
import { CheckCircle2, AlertCircle, RefreshCw, X } from 'lucide-react'
import { api } from '../lib/api'
import { useTranslation } from 'react-i18next'

interface ProviderEnvVars { [key: string]: string }

interface Provider {
  id: string; name: string; description: string; cli_command: string
  is_active: boolean; installed: boolean; version: string | null; path: string | null
  has_config: boolean; env_vars: ProviderEnvVars; requires_logout: boolean
  setup_hint: string | null; default_model: string | null
  default_base_url: string | null; default_region: string | null
}

interface ProvidersResponse {
  providers: Provider[]; active_provider: string
  claude_installed: boolean; openclaude_installed: boolean
}

const ENV_VAR_LABELS: Record<string, string> = {
  CLAUDE_CODE_USE_OPENAI: 'Use OpenAI (flag)', CLAUDE_CODE_USE_GEMINI: 'Use Gemini (flag)',
  CLAUDE_CODE_USE_BEDROCK: 'Use Bedrock (flag)', CLAUDE_CODE_USE_VERTEX: 'Use Vertex (flag)',
  OPENAI_BASE_URL: 'Base URL', OPENAI_API_KEY: 'API Key', OPENAI_MODEL: 'Model',
  GEMINI_API_KEY: 'API Key', GEMINI_MODEL: 'Model', AWS_REGION: 'AWS Region',
  AWS_BEARER_TOKEN_BEDROCK: 'Bearer Token', ANTHROPIC_VERTEX_PROJECT_ID: 'GCP Project ID',
  CLOUD_ML_REGION: 'Region',
}

const PROVIDER_COLORS: Record<string, string> = {
  anthropic: '#D4A574', openrouter: '#6366F1', openai: '#10A37F',
  gemini: '#4285F4', codex_auth: '#10A37F', bedrock: '#FF9900', vertex: '#4285F4',
}

function isFlag(key: string) { return key.startsWith('CLAUDE_CODE_USE_') }
function isSecret(key: string) { return key.includes('KEY') || key.includes('SECRET') || key.includes('TOKEN') }

/* ── Toggle switch ── */
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

// Custom combobox — styled dropdown that lets users pick a discovered model
// but also accept free-text input for edge cases (new models, custom routes).
// Replaces <datalist>, whose browser-native rendering was unreliable
// (appeared as an OS-level sidebar with "owned_by" noise, no consistent
// styling, and click-to-select that looked like plain text insertion
// rather than a dropdown).
interface ComboboxOption { id: string; description?: string; owned_by?: string }
interface ModelComboboxProps {
  value: string
  onChange: (v: string) => void
  options: ComboboxOption[]
  placeholder?: string
  inputClassName: string
}

function ModelCombobox({ value, onChange, options, placeholder, inputClassName }: ModelComboboxProps) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState(value)
  const wrapperRef = useRef<HTMLDivElement>(null)

  // Keep internal query in sync if parent resets the value externally
  useEffect(() => { setQuery(value) }, [value])

  // Close on outside click
  useEffect(() => {
    if (!open) return
    const handler = (e: MouseEvent) => {
      if (wrapperRef.current && !wrapperRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  const q = query.toLowerCase().trim()
  const filtered = q
    ? options.filter(o => o.id.toLowerCase().includes(q))
    : options

  const pick = (id: string) => {
    setQuery(id)
    onChange(id)
    setOpen(false)
  }

  return (
    <div ref={wrapperRef} className="relative">
      <div className="relative">
        <input
          type="text"
          value={query}
          onChange={(e) => {
            const v = e.target.value
            setQuery(v)
            onChange(v)
            if (!open) setOpen(true)
          }}
          onFocus={() => setOpen(true)}
          onKeyDown={(e) => {
            if (e.key === 'Escape') setOpen(false)
            if (e.key === 'ArrowDown' && !open) setOpen(true)
          }}
          placeholder={placeholder}
          className={`${inputClassName} pr-9`}
          autoComplete="off"
          role="combobox"
          aria-expanded={open}
        />
        <button
          type="button"
          onClick={() => setOpen(o => !o)}
          tabIndex={-1}
          className="absolute right-2 top-1/2 -translate-y-1/2 p-1 rounded text-[#5a6b7f] hover:text-[#e2e8f0] transition-colors"
          aria-label="Toggle model list"
        >
          <svg width="12" height="12" viewBox="0 0 12 12" fill="none" className={`transition-transform ${open ? 'rotate-180' : ''}`}>
            <path d="M2 4l4 4 4-4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </button>
      </div>
      {open && filtered.length > 0 && (
        <div className="absolute z-[60] top-full left-0 right-0 mt-1 max-h-64 overflow-y-auto rounded-lg bg-[#0b1018] border border-[#1e2a3a] shadow-[0_8px_24px_rgba(0,0,0,0.5)]">
          {filtered.map(m => {
            const isActive = m.id === value
            return (
              <button
                key={m.id}
                type="button"
                onClick={() => pick(m.id)}
                className={`w-full text-left px-3 py-2 transition-colors border-b border-[#152030] last:border-b-0 ${
                  isActive
                    ? 'bg-[#00FFA7]/10 text-[#00FFA7]'
                    : 'text-[#e2e8f0] hover:bg-[#152030]'
                }`}
              >
                <div className="font-mono text-xs">{m.id}</div>
                {m.description && (
                  <div className={`text-[10px] mt-0.5 ${isActive ? 'text-[#00FFA7]/70' : 'text-[#5a6b7f]'}`}>
                    {m.description}
                  </div>
                )}
              </button>
            )
          })}
        </div>
      )}
      {open && filtered.length === 0 && (
        <div className="absolute z-[60] top-full left-0 right-0 mt-1 rounded-lg bg-[#0b1018] border border-[#1e2a3a] shadow-[0_8px_24px_rgba(0,0,0,0.5)]">
          <div className="px-3 py-2.5 text-[11px] text-[#5a6b7f]">
            Nenhum modelo corresponde — {q ? 'ajuste a busca' : 'use o texto livre'}
          </div>
        </div>
      )}
    </div>
  )
}

export default function Providers() {
  const { t } = useTranslation()
  const [providers, setProviders] = useState<Provider[]>([])
  const [activeProvider, setActiveProvider] = useState('anthropic')
  const [loading, setLoading] = useState(true)
  const [configOpen, setConfigOpen] = useState<string | null>(null)
  const [editVars, setEditVars] = useState<ProviderEnvVars>({})
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState<string | null>(null)
  const [testResults, setTestResults] = useState<Record<string, { success: boolean; message: string }>>({})
  const [claudeInstalled, setClaudeInstalled] = useState(false)
  const [openclaudeInstalled, setOpenclaudeInstalled] = useState(false)
  const [codexAuth, setCodexAuth] = useState<{ authenticated: boolean; method?: string } | null>(null)
  const [authModal, setAuthModal] = useState(false)
  const [authMode, setAuthMode] = useState<'browser' | 'device'>('browser')
  const [authUrl, setAuthUrl] = useState('')
  const [callbackUrl, setCallbackUrl] = useState('')
  const [authLoading, setAuthLoading] = useState(false)
  const [authMessage, setAuthMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null)
  const [deviceCode, setDeviceCode] = useState<{ user_code: string; verification_url: string; interval: number; expires_in: number } | null>(null)
  const [devicePolling, setDevicePolling] = useState(false)
  const [toggling, setToggling] = useState<string | null>(null)

  // Dynamic model discovery — populated when Configure modal opens for
  // openai/codex_auth. Shape: { [providerId]: { loading, models[], error? } }
  type ModelList = {
    loading: boolean;
    models: Array<{ id: string; description?: string; owned_by?: string }>;
    error?: string;
    validatedKey?: string;  // remembers which API key we validated, to skip refetch
  }
  const [modelLists, setModelLists] = useState<Record<string, ModelList>>({})
  const apiKeyDebounceRef = useRef<number | null>(null)

  const load = () => {
    setLoading(true)
    api.get('/providers').then((data: ProvidersResponse) => {
      setProviders(data.providers || [])
      setActiveProvider(data.active_provider || 'none')
      setClaudeInstalled(data.claude_installed)
      setOpenclaudeInstalled(data.openclaude_installed)
    }).catch(() => setProviders([])).finally(() => setLoading(false))
  }

  useEffect(() => { load(); loadCodexAuth() }, [])
  useEffect(() => {
    if (!devicePolling) return
    const interval = (deviceCode?.interval || 5) * 1000
    const timer = setInterval(pollDeviceAuth, interval)
    return () => clearInterval(timer)
  }, [devicePolling])

  const handleToggle = async (id: string, turnOn: boolean) => {
    setToggling(id)
    try {
      await api.post('/providers/active', { provider_id: turnOn ? id : 'none' })
      load()
    } catch (e) { console.error(e) }
    finally { setToggling(null) }
  }

  const openConfig = (prov: Provider) => {
    setConfigOpen(prov.id)
    const vars: ProviderEnvVars = {}
    for (const [k, v] of Object.entries(prov.env_vars)) {
      if (isFlag(k)) continue
      vars[k] = v.includes('****') ? '' : v
    }
    setEditVars(vars)
    // Prefetch available models so the MODEL field renders as a real dropdown.
    // - codex_auth: static list from backend, always available.
    // - openai:     needs a valid API key first; we fetch when the user types.
    if (prov.id === 'codex_auth') {
      loadCodexModels()
    } else if (prov.id === 'openai' && vars['OPENAI_API_KEY']) {
      // If the user had a key saved (and it came through unmasked because
      // they cleared the field), try validating it right away.
      loadOpenAIModels(vars['OPENAI_API_KEY'])
    }
  }

  const loadCodexModels = async () => {
    setModelLists(prev => ({ ...prev, codex_auth: { loading: true, models: [] } }))
    try {
      const r: any = await api.get('/providers/codex_auth/models')
      setModelLists(prev => ({
        ...prev,
        codex_auth: {
          loading: false,
          models: r.models || [],
          error: r.auth_ok ? undefined : 'Faça login OAuth antes de usar — os aliases funcionam só com token Codex ativo.',
        },
      }))
    } catch {
      setModelLists(prev => ({
        ...prev,
        codex_auth: { loading: false, models: [], error: 'Falha ao carregar aliases Codex' },
      }))
    }
  }

  const loadOpenAIModels = async (apiKey: string) => {
    const trimmed = (apiKey || '').trim()
    if (!trimmed || trimmed.length < 20) {
      setModelLists(prev => ({
        ...prev,
        openai: { loading: false, models: [], error: 'API key muito curta' },
      }))
      return
    }
    // Skip refetch if we already validated this exact key
    const existing = modelLists.openai
    if (existing?.validatedKey === trimmed && !existing.error) return

    setModelLists(prev => ({ ...prev, openai: { loading: true, models: [] } }))
    try {
      const r: any = await api.post('/providers/openai/models', { api_key: trimmed })
      if (r.valid) {
        setModelLists(prev => ({
          ...prev,
          openai: { loading: false, models: r.models || [], validatedKey: trimmed },
        }))
      } else {
        setModelLists(prev => ({
          ...prev,
          openai: { loading: false, models: [], error: r.error || 'API key inválida', validatedKey: trimmed },
        }))
      }
    } catch {
      setModelLists(prev => ({
        ...prev,
        openai: { loading: false, models: [], error: 'Falha na requisição' },
      }))
    }
  }

  // Called by the API_KEY input onChange — debounces the model fetch so we
  // don't spam the OpenAI API on every keystroke.
  const scheduleOpenAIModelFetch = (apiKey: string) => {
    if (apiKeyDebounceRef.current) window.clearTimeout(apiKeyDebounceRef.current)
    apiKeyDebounceRef.current = window.setTimeout(() => loadOpenAIModels(apiKey), 600)
  }

  const handleSave = async () => {
    if (!configOpen) return
    setSaving(true)
    try {
      const prov = providers.find(p => p.id === configOpen)
      const finalVars = { ...editVars }
      if (prov?.default_base_url && !finalVars.OPENAI_BASE_URL) finalVars.OPENAI_BASE_URL = prov.default_base_url
      if (prov?.default_model) {
        const mk = Object.keys(finalVars).find(k => k.includes('MODEL'))
        if (mk && !finalVars[mk]) finalVars[mk] = prov.default_model
      }
      await api.post(`/providers/${configOpen}/config`, { env_vars: finalVars })
      await api.post('/providers/active', { provider_id: configOpen })
      setConfigOpen(null)
      load()
    } catch (e) { console.error(e) } finally { setSaving(false) }
  }

  const handleTest = async (id: string) => {
    setTesting(id)
    try {
      const result = await api.post(`/providers/${id}/test`) as any
      setTestResults(prev => ({ ...prev, [id]: { success: result.success, message: result.success ? `${result.cli} ${result.version}` : result.error || 'Test failed' } }))
    } catch { setTestResults(prev => ({ ...prev, [id]: { success: false, message: 'Request failed' } })) }
    finally { setTesting(null) }
  }

  const loadCodexAuth = () => { api.get('/providers/openai/status').then((d: any) => setCodexAuth(d)).catch(() => setCodexAuth(null)) }
  const startBrowserAuth = async () => { setAuthLoading(true); setAuthMessage(null); try { const d = await api.post('/providers/openai/auth-start') as any; setAuthUrl(d.authorize_url) } catch { setAuthMessage({ type: 'error', text: 'Failed to start auth' }) } finally { setAuthLoading(false) } }
  const completeBrowserAuth = async () => { if (!callbackUrl.includes('code=')) { setAuthMessage({ type: 'error', text: 'Invalid URL - must contain ?code=' }); return }; setAuthLoading(true); try { const r = await api.post('/providers/openai/auth-complete', { callback_url: callbackUrl }) as any; if (r.status === 'ok') { setAuthMessage({ type: 'success', text: r.message || 'Authenticated' }); setAuthModal(false); loadCodexAuth(); load() } else { setAuthMessage({ type: 'error', text: r.error || 'Auth failed' }) } } catch { setAuthMessage({ type: 'error', text: 'Auth error' }) } finally { setAuthLoading(false) } }
  const startDeviceAuth = async () => { setAuthLoading(true); setAuthMessage(null); try { const d = await api.post('/providers/openai/device-start') as any; if (d.error) setAuthMessage({ type: 'error', text: d.error }); else { setDeviceCode(d); setDevicePolling(true) } } catch { setAuthMessage({ type: 'error', text: 'Device auth not available' }) } finally { setAuthLoading(false) } }
  const pollDeviceAuth = async () => { try { const r = await api.post('/providers/openai/device-poll') as any; if (r.status === 'authorized') { setDevicePolling(false); setDeviceCode(null); setAuthModal(false); loadCodexAuth(); load() } } catch {} }
  const handleOpenAILogout = async () => { try { await api.post('/providers/openai/logout'); setCodexAuth({ authenticated: false }); load() } catch {} }

  const configuredCount = providers.filter(p => p.has_config && p.installed).length
  const hasActive = activeProvider !== 'none' && providers.some(p => p.id === activeProvider)

  const inp = "w-full px-4 py-3 rounded-lg bg-[#0f1520] border border-[#1e2a3a] text-[#e2e8f0] placeholder-[#3d4f65] text-sm transition-colors duration-200 focus:outline-none focus:border-[#00FFA7]/60 focus:ring-1 focus:ring-[#00FFA7]/20 font-mono"
  const lbl = "block text-[11px] font-semibold text-[#5a6b7f] mb-1.5 tracking-[0.08em] uppercase"

  return (
    <div className="max-w-[1200px] mx-auto font-[Inter,-apple-system,sans-serif]">
      {/* Header */}
      <div className="mb-6">
        <h1 className="text-xl font-bold text-white tracking-tight">{t('providers.title')}</h1>
        <p className="text-[#5a6b7f] text-sm mt-1">Configure and activate AI providers for your workspace</p>
      </div>

      {/* Status bar */}
      {!loading && (
        <div className="flex items-center gap-5 mb-6 px-4 py-3 rounded-lg border border-[#152030] bg-[#0b1018]">
          <div className="flex items-center gap-4 text-[11px] tracking-wide uppercase text-[#5a6b7f]">
            <span className="flex items-center gap-1.5">
              <span className={`w-1.5 h-1.5 rounded-full ${claudeInstalled ? 'bg-[#00FFA7]' : 'bg-[#ef4444]'}`} />
              claude {claudeInstalled ? '' : '(missing)'}
            </span>
            <span className="flex items-center gap-1.5">
              <span className={`w-1.5 h-1.5 rounded-full ${openclaudeInstalled ? 'bg-[#00FFA7]' : 'bg-[#5a6b7f]'}`} />
              openclaude {openclaudeInstalled ? '' : '(missing)'}
            </span>
          </div>
          <div className="ml-auto flex items-center gap-4 text-[11px] tracking-wide uppercase text-[#5a6b7f]">
            <span>{providers.length} available</span>
            <span>{configuredCount} configured</span>
            <span className={hasActive ? 'text-[#00FFA7]' : 'text-[#ef4444]'}>
              {hasActive ? '1 active' : 'none active'}
            </span>
          </div>
        </div>
      )}

      {/* Provider list */}
      {loading ? (
        <div className="space-y-3">
          {[...Array(3)].map((_, i) => <div key={i} className="h-20 rounded-lg bg-[#0b1018] border border-[#152030] animate-pulse" />)}
        </div>
      ) : (
        <div className="space-y-2">
          {providers.map((prov) => {
            const color = PROVIDER_COLORS[prov.id] || '#5a6b7f'
            const isInstalled = prov.cli_command === 'claude' ? claudeInstalled : openclaudeInstalled
            const isActive = prov.is_active && activeProvider === prov.id

            return (
              <div key={prov.id}
                className={`rounded-lg border bg-[#0b1018] transition-colors ${
                  isActive ? 'border-[#00FFA7]/30' : 'border-[#152030] hover:border-[#1e2a3a]'
                }`}
              >
                <div className="flex items-center gap-4 px-5 py-4">
                  {/* Color dot */}
                  <div className="w-2.5 h-2.5 rounded-full shrink-0" style={{ backgroundColor: color }} />

                  {/* Info */}
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <h3 className="text-sm font-semibold text-white truncate">{prov.name}</h3>
                      <code className="text-[9px] px-1.5 py-0.5 rounded bg-[#152030] text-[#5a6b7f] border border-[#1e2a3a] shrink-0">
                        {prov.cli_command}
                      </code>
                      {!isInstalled && (
                        <span className="text-[9px] px-1.5 py-0.5 rounded bg-[#1a0a0a] text-[#f87171] border border-[#3a1515] shrink-0">
                          not installed
                        </span>
                      )}
                    </div>
                    <p className="text-[11px] text-[#3d4f65] mt-0.5 truncate">{prov.description}</p>
                  </div>

                  {/* Codex OAuth badge — only on the dedicated codex_auth card */}
                  {prov.id === 'codex_auth' && codexAuth?.authenticated && (
                    <span className="text-[9px] px-2 py-0.5 rounded bg-[#10A37F]/10 text-[#10A37F] border border-[#10A37F]/20 shrink-0">
                      OAuth
                    </span>
                  )}

                  {/* Actions */}
                  <div className="flex items-center gap-2 shrink-0">
                    {/* Codex OAuth login / logout — only on codex_auth card */}
                    {prov.id === 'codex_auth' && isInstalled && !codexAuth?.authenticated && (
                      <button onClick={() => { setAuthModal(true); setAuthMode('browser'); setAuthUrl(''); setCallbackUrl(''); setAuthMessage(null); setDeviceCode(null); setDevicePolling(false); startBrowserAuth() }}
                        className="text-[11px] px-3 py-1.5 rounded-md bg-[#10A37F]/10 text-[#10A37F] border border-[#10A37F]/20 hover:bg-[#10A37F]/20 transition-colors font-medium">
                        Login
                      </button>
                    )}
                    {prov.id === 'codex_auth' && codexAuth?.authenticated && (
                      <button onClick={handleOpenAILogout}
                        className="text-[11px] px-2 py-1 rounded-md text-[#f87171] hover:bg-[#1a0a0a] transition-colors">
                        Logout
                      </button>
                    )}

                    {/* Configure */}
                    <button onClick={() => openConfig(prov)}
                      className="text-[11px] px-3 py-1.5 rounded-md text-[#5a6b7f] border border-[#1e2a3a] hover:text-[#8a9aae] hover:border-[#2e3a4a] transition-colors">
                      Configure
                    </button>

                    {/* Test */}
                    <button onClick={() => handleTest(prov.id)} disabled={testing === prov.id}
                      className="text-[11px] px-3 py-1.5 rounded-md text-[#5a6b7f] border border-[#1e2a3a] hover:text-[#8a9aae] hover:border-[#2e3a4a] transition-colors disabled:opacity-40">
                      {testing === prov.id ? <RefreshCw size={11} className="animate-spin" /> : 'Test'}
                    </button>

                    {/* Toggle switch */}
                    <Toggle
                      on={isActive}
                      disabled={!isInstalled || toggling === prov.id}
                      onChange={(on) => handleToggle(prov.id, on)}
                    />
                  </div>
                </div>

                {/* Test result */}
                {testResults[prov.id] && (
                  <div className={`mx-5 mb-3 px-3 py-1.5 rounded text-[10px] ${
                    testResults[prov.id].success ? 'bg-[#00FFA7]/5 text-[#00FFA7]' : 'bg-[#1a0a0a] text-[#f87171]'
                  }`}>
                    {testResults[prov.id].message}
                  </div>
                )}

                {/* Logout warning */}
                {prov.requires_logout && isActive && (
                  <div className="mx-5 mb-3 px-3 py-1.5 rounded bg-[#1a1500] text-[10px] text-[#FBBF24]">
                    Run /logout in Claude Code if you were previously logged into Anthropic
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}

      {/* Configuration Modal */}
      {configOpen && (() => {
        const prov = providers.find(p => p.id === configOpen)
        if (!prov) return null
        const editableVars = Object.entries(prov.env_vars).filter(([k]) => !isFlag(k))

        return (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
            <div className="w-full max-w-lg mx-4 rounded-xl border border-[#152030] bg-[#0b1018] shadow-[0_4px_40px_rgba(0,0,0,0.4)]">
              <div className="flex items-center justify-between px-6 py-4 border-b border-[#152030]">
                <div className="flex items-center gap-2">
                  <div className="w-2.5 h-2.5 rounded-full" style={{ backgroundColor: PROVIDER_COLORS[prov.id] || '#5a6b7f' }} />
                  <h2 className="text-sm font-semibold text-white">{prov.name}</h2>
                </div>
                <button onClick={() => setConfigOpen(null)} className="p-1 rounded text-[#5a6b7f] hover:text-white transition-colors">
                  <X size={16} />
                </button>
              </div>

              <div className="px-6 py-5 space-y-4">
                {editableVars.length === 0 ? (
                  <p className="text-sm text-[#5a6b7f]">No configuration needed. Uses native Claude Code authentication.</p>
                ) : (
                  editableVars.map(([key]) => {
                    const isModelField = key.includes('MODEL')
                    const isApiKeyField = key === 'OPENAI_API_KEY'
                    const currentList = modelLists[prov.id]
                    const hasDiscoveredModels = isModelField
                      && (prov.id === 'openai' || prov.id === 'codex_auth')
                      && currentList
                      && currentList.models.length > 0

                    return (
                      <div key={key}>
                        <label className={lbl}>
                          {ENV_VAR_LABELS[key] || key}
                          <span className="ml-1 text-[#3d4f65] font-normal normal-case tracking-normal">({key})</span>
                          {isModelField && currentList?.loading && (
                            <span className="ml-2 text-[#5a6b7f]"><RefreshCw size={10} className="inline animate-spin" /> carregando…</span>
                          )}
                        </label>

                        {hasDiscoveredModels ? (
                          <>
                            <ModelCombobox
                              value={editVars[key] || ''}
                              onChange={(v) => setEditVars(prev => ({ ...prev, [key]: v }))}
                              options={currentList!.models}
                              placeholder={prov.default_model || 'Selecione ou digite um modelo'}
                              inputClassName={inp}
                            />
                            <p className="text-[10px] text-[#3d4f65] mt-1">
                              {currentList!.models.length} modelos disponíveis. Clique no campo ou na seta para abrir a lista.
                            </p>
                          </>
                        ) : (
                          <input
                            type={isSecret(key) ? 'password' : 'text'}
                            value={editVars[key] || ''}
                            onChange={(e) => {
                              const v = e.target.value
                              setEditVars(prev => ({ ...prev, [key]: v }))
                              // When the OpenAI API key changes, fetch models (debounced)
                              if (isApiKeyField && prov.id === 'openai') {
                                scheduleOpenAIModelFetch(v)
                              }
                            }}
                            placeholder={key.includes('KEY') ? 'sk-...' : key.includes('MODEL') ? (prov.default_model || '') : key.includes('URL') ? (prov.default_base_url || 'https://...') : ''}
                            className={inp}
                            autoComplete="off"
                          />
                        )}

                        {/* Inline validation feedback for the API key field */}
                        {isApiKeyField && prov.id === 'openai' && currentList && !currentList.loading && (
                          currentList.error ? (
                            <p className="text-[10px] text-[#f87171] mt-1">
                              <AlertCircle size={10} className="inline mr-1" />{currentList.error}
                            </p>
                          ) : currentList.models.length > 0 ? (
                            <p className="text-[10px] text-[#00FFA7] mt-1">
                              <CheckCircle2 size={10} className="inline mr-1" />
                              API key válida — {currentList.models.length} modelos carregados
                            </p>
                          ) : null
                        )}

                        {/* Codex auth hint */}
                        {isModelField && prov.id === 'codex_auth' && currentList?.error && (
                          <p className="text-[10px] text-[#FBBF24] mt-1">
                            <AlertCircle size={10} className="inline mr-1" />{currentList.error}
                          </p>
                        )}
                      </div>
                    )
                  })
                )}

                {prov.default_model && (
                  <p className="text-[10px] text-[#3d4f65]">
                    Default model: <code className="text-[#5a6b7f]">{prov.default_model}</code>
                    {prov.default_base_url && <> | URL: <code className="text-[#5a6b7f]">{prov.default_base_url}</code></>}
                  </p>
                )}

                {prov.requires_logout && (
                  <div className="rounded-lg bg-[#1a1500] border border-[#3a2a00] p-3">
                    <p className="text-xs text-[#FBBF24]">After activating, run <code className="font-bold">/logout</code> in Claude Code if previously logged into Anthropic.</p>
                  </div>
                )}

                {testResults[prov.id] && (
                  <div className={`rounded-lg p-3 text-xs ${testResults[prov.id].success ? 'bg-[#00FFA7]/5 text-[#00FFA7]' : 'bg-[#1a0a0a] text-[#f87171]'}`}>
                    {testResults[prov.id].success ? <CheckCircle2 size={12} className="inline mr-1" /> : <AlertCircle size={12} className="inline mr-1" />}
                    {testResults[prov.id].message}
                  </div>
                )}
              </div>

              <div className="flex items-center justify-between px-6 py-4 border-t border-[#152030]">
                <button onClick={() => handleTest(prov.id)} disabled={testing === prov.id}
                  className="text-[11px] px-3 py-1.5 rounded-md text-[#5a6b7f] border border-[#1e2a3a] hover:text-[#8a9aae] hover:border-[#2e3a4a] transition-colors disabled:opacity-40">
                  {testing === prov.id ? <RefreshCw size={11} className="animate-spin" /> : 'Test connection'}
                </button>
                <div className="flex items-center gap-2">
                  <button onClick={() => setConfigOpen(null)}
                    className="text-[11px] px-4 py-1.5 rounded-md text-[#5a6b7f] border border-[#1e2a3a] hover:text-[#8a9aae] transition-colors">
                    Cancel
                  </button>
                  <button onClick={handleSave} disabled={saving}
                    className="text-[11px] px-4 py-1.5 rounded-md bg-[#00FFA7] text-[#080c14] font-semibold hover:bg-[#00e69a] transition-colors disabled:opacity-40">
                    {saving ? 'Saving...' : 'Save & activate'}
                  </button>
                </div>
              </div>
            </div>
          </div>
        )
      })()}

      {/* OpenAI Auth Modal */}
      {authModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
          <div className="w-full max-w-lg mx-4 rounded-xl border border-[#152030] bg-[#0b1018] shadow-[0_4px_40px_rgba(0,0,0,0.4)]">
            <div className="flex items-center justify-between px-6 py-4 border-b border-[#152030]">
              <h2 className="text-sm font-semibold text-white">Connect to OpenAI</h2>
              <button onClick={() => { setAuthModal(false); setDevicePolling(false) }} className="p-1 rounded text-[#5a6b7f] hover:text-white transition-colors">
                <X size={16} />
              </button>
            </div>

            <div className="flex border-b border-[#152030]">
              <button onClick={() => { setAuthMode('browser'); if (!authUrl) startBrowserAuth() }}
                className={`flex-1 py-2.5 text-[11px] font-medium tracking-wide uppercase transition-colors ${authMode === 'browser' ? 'text-[#10A37F] border-b-2 border-[#10A37F]' : 'text-[#5a6b7f]'}`}>
                Browser OAuth
              </button>
              <button onClick={() => { setAuthMode('device'); if (!deviceCode) startDeviceAuth() }}
                className={`flex-1 py-2.5 text-[11px] font-medium tracking-wide uppercase transition-colors ${authMode === 'device' ? 'text-[#10A37F] border-b-2 border-[#10A37F]' : 'text-[#5a6b7f]'}`}>
                Device Auth
              </button>
            </div>

            <div className="px-6 py-5 space-y-4">
              {authMode === 'browser' ? (
                authUrl ? (
                  <>
                    <div className="space-y-3">
                      <p className="text-xs text-[#5a6b7f]"><span className="text-white font-medium">1.</span> Open this link to login:</p>
                      <a href={authUrl} target="_blank" rel="noopener noreferrer"
                        className="block text-center py-2 rounded-md bg-[#10A37F]/10 text-[#10A37F] border border-[#10A37F]/20 hover:bg-[#10A37F]/20 transition-colors text-sm font-medium">
                        Open OpenAI Login
                      </a>
                    </div>
                    <div className="space-y-2">
                      <p className="text-xs text-[#5a6b7f]"><span className="text-white font-medium">2.</span> Authorize access, then copy the URL from the error page:</p>
                      <input type="text" value={callbackUrl} onChange={(e) => setCallbackUrl(e.target.value)}
                        placeholder="http://localhost:1455/auth/callback?code=..."
                        className={inp} autoComplete="off" />
                    </div>
                  </>
                ) : (
                  <div className="flex items-center justify-center py-8 gap-2 text-[#5a6b7f] text-sm">
                    <RefreshCw size={14} className="animate-spin" /> Generating auth link...
                  </div>
                )
              ) : (
                deviceCode ? (
                  <div className="space-y-4">
                    <p className="text-xs text-[#5a6b7f]"><span className="text-white font-medium">1.</span> Open: <a href={deviceCode.verification_url} target="_blank" rel="noopener noreferrer" className="text-[#10A37F] underline">{deviceCode.verification_url}</a></p>
                    <p className="text-xs text-[#5a6b7f]"><span className="text-white font-medium">2.</span> Enter code:</p>
                    <div className="flex items-center justify-center">
                      <code className="text-xl font-bold text-white tracking-[0.15em] bg-[#0f1520] px-5 py-2.5 rounded-lg border border-[#1e2a3a]">{deviceCode.user_code}</code>
                    </div>
                    {devicePolling && <p className="text-center text-xs text-[#5a6b7f] flex items-center justify-center gap-2"><RefreshCw size={11} className="animate-spin" /> Waiting for authorization...</p>}
                  </div>
                ) : authLoading ? (
                  <div className="flex items-center justify-center py-8 gap-2 text-[#5a6b7f] text-sm"><RefreshCw size={14} className="animate-spin" /> Starting device auth...</div>
                ) : null
              )}

              {authMessage && ((authMode === 'browser') || (authMode === 'device' && authMessage.type === 'error')) && (
                <div className={`rounded-lg p-3 text-xs ${authMessage.type === 'success' ? 'bg-[#00FFA7]/5 text-[#00FFA7]' : 'bg-[#1a0a0a] text-[#f87171]'}`}>
                  {authMessage.text}
                  {authMessage.type === 'error' && authMode === 'device' && <p className="text-[10px] text-[#3d4f65] mt-1">Your organization may not allow Device Auth. Use Browser OAuth instead.</p>}
                </div>
              )}
            </div>

            <div className="flex items-center justify-between px-6 py-4 border-t border-[#152030]">
              <button onClick={() => { setAuthModal(false); setDevicePolling(false) }}
                className="text-[11px] px-4 py-1.5 rounded-md text-[#5a6b7f] border border-[#1e2a3a] hover:text-[#8a9aae] transition-colors">
                Cancel
              </button>
              {authMode === 'browser' && authUrl && (
                <button onClick={completeBrowserAuth} disabled={!callbackUrl || authLoading}
                  className="text-[11px] px-4 py-1.5 rounded-md bg-[#10A37F] text-white font-semibold hover:bg-[#0d8a6a] transition-colors disabled:opacity-40">
                  {authLoading ? 'Verifying...' : 'Confirm'}
                </button>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
