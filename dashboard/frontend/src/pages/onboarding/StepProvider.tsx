import { useEffect, useRef, useState } from 'react'
import { Eye, EyeOff, RefreshCw, Terminal, CheckCircle2, AlertCircle, ExternalLink } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { api } from '../../lib/api'
import OnboardingHeader from './OnboardingHeader'

type ProviderId = 'anthropic' | 'openai' | 'openrouter' | 'codex'

interface Provider {
  id: ProviderId
  name: string
  color: string
}

const PROVIDERS: Provider[] = [
  { id: 'anthropic', name: 'Anthropic', color: '#D97706' },
  { id: 'openai', name: 'OpenAI', color: '#10B981' },
  { id: 'openrouter', name: 'OpenRouter', color: '#6366F1' },
  { id: 'codex', name: 'Codex', color: '#8B5CF6' },
]

const OPENROUTER_BASE_URL = 'https://openrouter.ai/api/v1'
const OPENROUTER_DEFAULT_MODEL = 'anthropic/claude-sonnet-4'

const inp = "w-full px-4 py-3 rounded-lg bg-[#0f1520] border border-[#1e2a3a] text-[#e2e8f0] placeholder-[#3d4f65] text-sm transition-colors duration-200 focus:outline-none focus:border-[#00FFA7]/60 focus:ring-1 focus:ring-[#00FFA7]/20"
const lbl = "block text-[11px] font-semibold text-[#5a6b7f] mb-1.5 tracking-[0.08em] uppercase"

interface StepProviderProps {
  onNext: (provider: string) => void
  onBack: () => void
}

type Phase = 'select' | ProviderId

export default function StepProvider({ onNext, onBack }: StepProviderProps) {
  const [phase, setPhase] = useState<Phase>('select')

  const cardShell = "rounded-xl border border-[#152030] bg-[#0b1018] shadow-[0_4px_40px_rgba(0,0,0,0.4)]"

  return (
    <div className="min-h-screen bg-[#080c14] flex items-center justify-center px-4 font-[Inter,-apple-system,sans-serif]">
      <div className="w-full max-w-[480px] relative z-10">
        <OnboardingHeader step="step1of3" filled={1} />

        {phase === 'select' && (
          <ProviderSelect
            shell={cardShell}
            onPick={(id) => setPhase(id)}
            onBack={onBack}
          />
        )}

        {phase === 'anthropic' && (
          <AnthropicSubStep
            shell={cardShell}
            onBackToProviders={() => setPhase('select')}
            onDone={() => onNext('anthropic')}
          />
        )}

        {phase === 'openai' && (
          <OpenAISubStep
            shell={cardShell}
            onBackToProviders={() => setPhase('select')}
            onDone={() => onNext('openai')}
          />
        )}

        {phase === 'openrouter' && (
          <OpenRouterSubStep
            shell={cardShell}
            onBackToProviders={() => setPhase('select')}
            onDone={() => onNext('openrouter')}
          />
        )}

        {phase === 'codex' && (
          <CodexSubStep
            shell={cardShell}
            onBackToProviders={() => setPhase('select')}
            onDone={() => onNext('codex_auth')}
          />
        )}
      </div>
    </div>
  )
}

/* ───────────────────────── Provider select grid ───────────────────────── */

function ProviderSelect({ shell, onPick, onBack }: { shell: string; onPick: (id: ProviderId) => void; onBack: () => void }) {
  const { t } = useTranslation()
  return (
    <div className={shell}>
      <div className="px-7 pt-7 pb-5 border-b border-[#152030]">
        <h2 className="text-[16px] font-semibold text-[#e2e8f0]">{t('onboarding.provider.title')}</h2>
        <p className="text-[11px] text-[#4a5a6e] mt-1">{t('onboarding.provider.subtitle')}</p>
      </div>

      <div className="px-7 py-6 space-y-4">
        <div className="grid grid-cols-2 gap-2">
          {PROVIDERS.map((p) => (
            <button
              key={p.id}
              onClick={() => onPick(p.id)}
              className="p-3 rounded-lg border text-left transition-all duration-200 border-[#1e2a3a] bg-[#0f1520] hover:border-[#00FFA7]/60 hover:bg-[#00FFA7]/5"
            >
              <div className="flex items-center gap-2 mb-1">
                <span
                  className="h-2 w-2 rounded-full flex-shrink-0"
                  style={{ backgroundColor: p.color }}
                />
                <span className="text-[13px] font-semibold text-[#e2e8f0]">{p.name}</span>
              </div>
              <p className="text-[10px] text-[#5a6b7f] leading-snug">{t(`onboarding.provider.descriptions.${p.id}`)}</p>
            </button>
          ))}
        </div>

        <div className="flex gap-3 pt-2">
          <button
            onClick={onBack}
            className="flex-1 py-3 rounded-lg border border-[#152030] text-[#5a6b7f] hover:border-[#00FFA7]/30 hover:text-[#e2e8f0] text-sm font-medium transition-colors"
          >
            {t('onboarding.back')}
          </button>
        </div>
      </div>
    </div>
  )
}

/* ───────────────────────── Shared back link ───────────────────────── */

function BackLink({ onClick }: { onClick: () => void }) {
  const { t } = useTranslation()
  return (
    <button
      onClick={onClick}
      className="text-[11px] text-[#5a6b7f] hover:text-[#00FFA7] transition-colors"
    >
      {t('onboarding.backToProviders')}
    </button>
  )
}

/* ───────────────────────── Anthropic sub-step ───────────────────────── */

function AnthropicSubStep({ shell, onBackToProviders, onDone }: { shell: string; onBackToProviders: () => void; onDone: () => void }) {
  const { t } = useTranslation()
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const handleContinue = async () => {
    setError('')
    setSaving(true)
    try {
      await api.post('/onboarding/provider', { provider: 'anthropic' })
      onDone()
    } catch (ex: unknown) {
      setError(ex instanceof Error ? ex.message : t('onboarding.provider.saveError'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className={shell}>
      <div className="px-7 pt-5 pb-4 border-b border-[#152030]">
        <BackLink onClick={onBackToProviders} />
        <h2 className="text-[16px] font-semibold text-[#e2e8f0] mt-2">{t('onboarding.providerAnthropic.title')}</h2>
      </div>

      <div className="px-7 py-6 space-y-4">
        {error && (
          <div className="px-3 py-2.5 rounded-lg bg-[#1a0a0a] border border-[#3a1515] text-[#f87171] text-xs">
            {error}
          </div>
        )}

        <p className="text-[12px] text-[#8a9aae] leading-relaxed">
          {t('onboarding.providerAnthropic.bodyPart1')}
          <code className="inline-flex items-center gap-1 font-mono text-[11px] px-1.5 py-0.5 rounded bg-[#0f1520] border border-[#00FFA7]/20 text-[#00FFA7] mx-1">
            <Terminal size={10} />
            {t('onboarding.providerAnthropic.codeChip')}
          </code>
          {t('onboarding.providerAnthropic.bodyPart2')}
        </p>

        <div className="p-3 rounded-lg bg-[#0a1220] border border-[#1e2a3a]">
          <p className="text-[11px] text-[#5a6b7f] leading-relaxed">
            {t('onboarding.providerAnthropic.noKeyNote')}
          </p>
        </div>

        <div className="flex gap-3 pt-2">
          <button
            onClick={onBackToProviders}
            className="flex-none py-3 px-4 rounded-lg border border-[#152030] text-[#5a6b7f] hover:border-[#00FFA7]/30 hover:text-[#e2e8f0] text-sm font-medium transition-colors"
          >
            {t('onboarding.back')}
          </button>
          <button
            onClick={handleContinue}
            disabled={saving}
            className="flex-1 py-3 rounded-lg bg-[#00FFA7] text-[#080c14] hover:bg-[#00e69a] text-sm font-semibold transition-colors disabled:opacity-40"
          >
            {saving ? t('onboarding.provider.saving') : t('onboarding.providerAnthropic.continue')}
          </button>
        </div>
      </div>
    </div>
  )
}

/* ───────────────────────── OpenAI sub-step ───────────────────────── */

interface ModelOption { id: string; description?: string; owned_by?: string }

function OpenAISubStep({ shell, onBackToProviders, onDone }: { shell: string; onBackToProviders: () => void; onDone: () => void }) {
  const { t } = useTranslation()
  const [apiKey, setApiKey] = useState('')
  const [showKey, setShowKey] = useState(false)
  const [validating, setValidating] = useState(false)
  const [models, setModels] = useState<ModelOption[]>([])
  const [model, setModel] = useState('')
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)

  const handleValidate = async () => {
    if (!apiKey.trim()) {
      setError(t('onboarding.providerOpenAI.invalidKey'))
      return
    }
    setError('')
    setValidating(true)
    setModels([])
    try {
      const r = await api.post('/providers/openai/models', { api_key: apiKey.trim() }) as { valid?: boolean; models?: ModelOption[]; error?: string }
      if (r.valid && r.models && r.models.length > 0) {
        setModels(r.models)
        setModel(r.models[0]?.id || '')
      } else {
        setError(r.error || t('onboarding.providerOpenAI.invalidKey'))
      }
    } catch (ex: unknown) {
      setError(ex instanceof Error ? ex.message : t('onboarding.providerOpenAI.invalidKey'))
    } finally {
      setValidating(false)
    }
  }

  const handleSave = async () => {
    if (!apiKey.trim() || !model) return
    setError('')
    setSaving(true)
    try {
      await api.post('/onboarding/provider', {
        provider: 'openai',
        env_vars: {
          OPENAI_API_KEY: apiKey.trim(),
          OPENAI_MODEL: model,
        },
      })
      onDone()
    } catch (ex: unknown) {
      setError(ex instanceof Error ? ex.message : t('onboarding.provider.saveError'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className={shell}>
      <div className="px-7 pt-5 pb-4 border-b border-[#152030]">
        <BackLink onClick={onBackToProviders} />
        <h2 className="text-[16px] font-semibold text-[#e2e8f0] mt-2">{t('onboarding.providerOpenAI.title')}</h2>
      </div>

      <div className="px-7 py-6 space-y-4">
        {error && (
          <div className="px-3 py-2.5 rounded-lg bg-[#1a0a0a] border border-[#3a1515] text-[#f87171] text-xs">
            <AlertCircle size={12} className="inline mr-1" />{error}
          </div>
        )}

        <div>
          <label className={lbl}>{t('onboarding.providerOpenAI.apiKey')}</label>
          <div className="relative">
            <input
              type={showKey ? 'text' : 'password'}
              value={apiKey}
              onChange={(e) => { setApiKey(e.target.value); setModels([]); setModel('') }}
              className={`${inp} pr-10`}
              placeholder={t('onboarding.providerOpenAI.apiKeyPlaceholder')}
              autoFocus
              autoComplete="off"
            />
            <button
              type="button"
              onClick={() => setShowKey(!showKey)}
              className="absolute right-3 top-1/2 -translate-y-1/2 text-[#5a6b7f] hover:text-[#e2e8f0] transition-colors"
            >
              {showKey ? <EyeOff size={16} /> : <Eye size={16} />}
            </button>
          </div>
        </div>

        <button
          onClick={handleValidate}
          disabled={validating || !apiKey.trim()}
          className="w-full py-2.5 rounded-lg border border-[#00FFA7]/30 text-[#00FFA7] hover:bg-[#00FFA7]/10 text-xs font-semibold tracking-[0.08em] uppercase transition-colors disabled:opacity-40"
        >
          {validating ? (
            <span className="inline-flex items-center gap-2"><RefreshCw size={12} className="animate-spin" /> {t('onboarding.providerOpenAI.validating')}</span>
          ) : (
            t('onboarding.providerOpenAI.validate')
          )}
        </button>

        {models.length > 0 && (
          <div>
            <label className={lbl}>
              {t('onboarding.providerOpenAI.modelLabel')}
              <span className="ml-2 text-[#00FFA7] normal-case tracking-normal font-normal">
                <CheckCircle2 size={10} className="inline mr-1" />{models.length} models
              </span>
            </label>
            <select
              value={model}
              onChange={(e) => setModel(e.target.value)}
              className={inp}
            >
              <option value="" disabled>{t('onboarding.providerOpenAI.modelPlaceholder')}</option>
              {models.map((m) => (
                <option key={m.id} value={m.id}>{m.id}</option>
              ))}
            </select>
          </div>
        )}

        <div className="p-3 rounded-lg bg-[#0a1220] border border-[#1e2a3a]">
          <p className="text-[11px] text-[#5a6b7f] leading-relaxed">
            {t('onboarding.providerOpenAI.storageNote')}
          </p>
        </div>

        <div className="flex gap-3 pt-2">
          <button
            onClick={onBackToProviders}
            className="flex-none py-3 px-4 rounded-lg border border-[#152030] text-[#5a6b7f] hover:border-[#00FFA7]/30 hover:text-[#e2e8f0] text-sm font-medium transition-colors"
          >
            {t('onboarding.back')}
          </button>
          <button
            onClick={handleSave}
            disabled={saving || !apiKey.trim() || !model}
            className="flex-1 py-3 rounded-lg bg-[#00FFA7] text-[#080c14] hover:bg-[#00e69a] text-sm font-semibold transition-colors disabled:opacity-40"
          >
            {saving ? t('onboarding.providerOpenAI.saving') : t('onboarding.providerOpenAI.save')}
          </button>
        </div>
      </div>
    </div>
  )
}

/* ───────────────────────── OpenRouter sub-step ───────────────────────── */

function OpenRouterSubStep({ shell, onBackToProviders, onDone }: { shell: string; onBackToProviders: () => void; onDone: () => void }) {
  const { t } = useTranslation()
  const [apiKey, setApiKey] = useState('')
  const [showKey, setShowKey] = useState(false)
  const [model, setModel] = useState(OPENROUTER_DEFAULT_MODEL)
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)

  const handleSave = async () => {
    if (!apiKey.trim() || !model.trim()) return
    setError('')
    setSaving(true)
    try {
      await api.post('/onboarding/provider', {
        provider: 'openrouter',
        env_vars: {
          OPENAI_API_KEY: apiKey.trim(),
          OPENAI_BASE_URL: OPENROUTER_BASE_URL,
          OPENAI_MODEL: model.trim(),
        },
      })
      onDone()
    } catch (ex: unknown) {
      setError(ex instanceof Error ? ex.message : t('onboarding.provider.saveError'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className={shell}>
      <div className="px-7 pt-5 pb-4 border-b border-[#152030]">
        <BackLink onClick={onBackToProviders} />
        <h2 className="text-[16px] font-semibold text-[#e2e8f0] mt-2">{t('onboarding.providerOpenRouter.title')}</h2>
      </div>

      <div className="px-7 py-6 space-y-4">
        {error && (
          <div className="px-3 py-2.5 rounded-lg bg-[#1a0a0a] border border-[#3a1515] text-[#f87171] text-xs">
            <AlertCircle size={12} className="inline mr-1" />{error}
          </div>
        )}

        <div>
          <label className={lbl}>{t('onboarding.providerOpenRouter.baseUrlLabel')}</label>
          <div className="px-3 py-2.5 rounded-lg bg-[#0f1520] border border-[#1e2a3a] font-mono text-[12px] text-[#6366F1] flex items-center gap-2">
            <ExternalLink size={12} />
            {OPENROUTER_BASE_URL}
          </div>
        </div>

        <div>
          <label className={lbl}>{t('onboarding.providerOpenAI.apiKey')}</label>
          <div className="relative">
            <input
              type={showKey ? 'text' : 'password'}
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              className={`${inp} pr-10`}
              placeholder="sk-or-..."
              autoFocus
              autoComplete="off"
            />
            <button
              type="button"
              onClick={() => setShowKey(!showKey)}
              className="absolute right-3 top-1/2 -translate-y-1/2 text-[#5a6b7f] hover:text-[#e2e8f0] transition-colors"
            >
              {showKey ? <EyeOff size={16} /> : <Eye size={16} />}
            </button>
          </div>
        </div>

        <div>
          <label className={lbl}>{t('onboarding.providerOpenRouter.modelLabel')}</label>
          <input
            type="text"
            value={model}
            onChange={(e) => setModel(e.target.value)}
            className={inp}
            placeholder={t('onboarding.providerOpenRouter.modelPlaceholder')}
            autoComplete="off"
          />
        </div>

        <div className="p-3 rounded-lg bg-[#0a1220] border border-[#1e2a3a]">
          <p className="text-[11px] text-[#5a6b7f] leading-relaxed">
            {t('onboarding.providerOpenAI.storageNote')}
          </p>
        </div>

        <div className="flex gap-3 pt-2">
          <button
            onClick={onBackToProviders}
            className="flex-none py-3 px-4 rounded-lg border border-[#152030] text-[#5a6b7f] hover:border-[#00FFA7]/30 hover:text-[#e2e8f0] text-sm font-medium transition-colors"
          >
            {t('onboarding.back')}
          </button>
          <button
            onClick={handleSave}
            disabled={saving || !apiKey.trim() || !model.trim()}
            className="flex-1 py-3 rounded-lg bg-[#00FFA7] text-[#080c14] hover:bg-[#00e69a] text-sm font-semibold transition-colors disabled:opacity-40"
          >
            {saving ? t('onboarding.providerOpenAI.saving') : t('onboarding.providerOpenAI.save')}
          </button>
        </div>
      </div>
    </div>
  )
}

/* ───────────────────────── Codex sub-step (OAuth) ───────────────────────── */

type CodexTab = 'browser' | 'device'
type AuthMessage = { type: 'success' | 'error'; text: string } | null

interface DeviceCode {
  user_code: string
  verification_url: string
  interval: number
  expires_in: number
}

function CodexSubStep({ shell, onBackToProviders, onDone }: { shell: string; onBackToProviders: () => void; onDone: () => void }) {
  const { t } = useTranslation()
  const [tab, setTab] = useState<CodexTab>('browser')

  // Browser OAuth state
  const [authUrl, setAuthUrl] = useState('')
  const [callbackUrl, setCallbackUrl] = useState('')
  const [authLoading, setAuthLoading] = useState(false)

  // Device auth state
  const [deviceCode, setDeviceCode] = useState<DeviceCode | null>(null)
  const [devicePolling, setDevicePolling] = useState(false)

  const [authMessage, setAuthMessage] = useState<AuthMessage>(null)

  // Track the active phase so the polling loop can bail out on unmount / tab switch.
  const activeRef = useRef(true)
  useEffect(() => {
    activeRef.current = true
    return () => { activeRef.current = false }
  }, [])

  const startBrowserAuth = async () => {
    setAuthLoading(true)
    setAuthMessage(null)
    try {
      const d = await api.post('/providers/openai/auth-start') as { authorize_url?: string }
      if (d.authorize_url) setAuthUrl(d.authorize_url)
      else setAuthMessage({ type: 'error', text: t('onboarding.providerCodex.authError') })
    } catch {
      setAuthMessage({ type: 'error', text: t('onboarding.providerCodex.authError') })
    } finally {
      setAuthLoading(false)
    }
  }

  const completeBrowserAuth = async () => {
    if (!callbackUrl.includes('code=')) {
      setAuthMessage({ type: 'error', text: 'Invalid URL - must contain ?code=' })
      return
    }
    setAuthLoading(true)
    try {
      const r = await api.post('/providers/openai/auth-complete', { callback_url: callbackUrl }) as { status?: string; message?: string; error?: string }
      if (r.status === 'ok') {
        setAuthMessage({ type: 'success', text: r.message || t('onboarding.providerCodex.successMessage') })
        onDone()
      } else {
        setAuthMessage({ type: 'error', text: r.error || t('onboarding.providerCodex.authError') })
      }
    } catch {
      setAuthMessage({ type: 'error', text: t('onboarding.providerCodex.authError') })
    } finally {
      setAuthLoading(false)
    }
  }

  const startDeviceAuth = async () => {
    setAuthLoading(true)
    setAuthMessage(null)
    try {
      const d = await api.post('/providers/openai/device-start') as (DeviceCode & { error?: string })
      if (d.error) {
        setAuthMessage({ type: 'error', text: d.error })
      } else {
        setDeviceCode({ user_code: d.user_code, verification_url: d.verification_url, interval: d.interval, expires_in: d.expires_in })
        setDevicePolling(true)
      }
    } catch {
      setAuthMessage({ type: 'error', text: t('onboarding.providerCodex.authError') })
    } finally {
      setAuthLoading(false)
    }
  }

  const pollDeviceAuth = async () => {
    try {
      const r = await api.post('/providers/openai/device-poll') as { status?: string }
      if (!activeRef.current) return
      if (r.status === 'authorized') {
        setDevicePolling(false)
        setDeviceCode(null)
        setAuthMessage({ type: 'success', text: t('onboarding.providerCodex.successMessage') })
        onDone()
      }
    } catch {
      // swallow — device-poll transient errors are expected while waiting
    }
  }

  // Start browser auth immediately when the Codex sub-step mounts
  useEffect(() => {
    if (tab === 'browser' && !authUrl && !authLoading) {
      startBrowserAuth()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab])

  // Auto-start device auth when user switches to the device tab
  useEffect(() => {
    if (tab === 'device' && !deviceCode && !authLoading) {
      startDeviceAuth()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab])

  // Polling loop
  useEffect(() => {
    if (!devicePolling) return
    const interval = (deviceCode?.interval || 5) * 1000
    const timer = setInterval(pollDeviceAuth, interval)
    return () => clearInterval(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [devicePolling, deviceCode?.interval])

  return (
    <div className={shell}>
      <div className="px-7 pt-5 pb-4 border-b border-[#152030]">
        <BackLink onClick={onBackToProviders} />
        <h2 className="text-[16px] font-semibold text-[#e2e8f0] mt-2">{t('onboarding.providerCodex.title')}</h2>
      </div>

      <div className="flex border-b border-[#152030]">
        <button
          onClick={() => setTab('browser')}
          className={`flex-1 py-2.5 text-[11px] font-medium tracking-wide uppercase transition-colors ${tab === 'browser' ? 'text-[#00FFA7] border-b-2 border-[#00FFA7]' : 'text-[#5a6b7f]'}`}
        >
          {t('onboarding.providerCodex.tabBrowser')}
        </button>
        <button
          onClick={() => setTab('device')}
          className={`flex-1 py-2.5 text-[11px] font-medium tracking-wide uppercase transition-colors ${tab === 'device' ? 'text-[#00FFA7] border-b-2 border-[#00FFA7]' : 'text-[#5a6b7f]'}`}
        >
          {t('onboarding.providerCodex.tabDevice')}
        </button>
      </div>

      <div className="px-7 py-6 space-y-4">
        {tab === 'browser' ? (
          authUrl ? (
            <>
              <div className="space-y-3">
                <p className="text-xs text-[#5a6b7f]">{t('onboarding.providerCodex.step1Open')}</p>
                <a
                  href={authUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="block text-center py-2.5 rounded-md bg-[#00FFA7]/10 text-[#00FFA7] border border-[#00FFA7]/20 hover:bg-[#00FFA7]/20 transition-colors text-sm font-medium"
                >
                  {t('onboarding.providerCodex.generateLink')}
                </a>
              </div>
              <div className="space-y-2">
                <p className="text-xs text-[#5a6b7f]">{t('onboarding.providerCodex.step2Paste')}</p>
                <input
                  type="text"
                  value={callbackUrl}
                  onChange={(e) => setCallbackUrl(e.target.value)}
                  placeholder={t('onboarding.providerCodex.callbackPlaceholder')}
                  className={inp}
                  autoComplete="off"
                />
              </div>
            </>
          ) : (
            <div className="flex items-center justify-center py-8 gap-2 text-[#5a6b7f] text-sm">
              <RefreshCw size={14} className="animate-spin" /> {t('onboarding.providerCodex.generateLink')}...
            </div>
          )
        ) : (
          deviceCode ? (
            <div className="space-y-4">
              <p className="text-xs text-[#5a6b7f]">
                {t('onboarding.providerCodex.device1')}:{' '}
                <a
                  href={deviceCode.verification_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-[#00FFA7] underline"
                >
                  {deviceCode.verification_url}
                </a>
              </p>
              <p className="text-xs text-[#5a6b7f]">{t('onboarding.providerCodex.device2')}:</p>
              <div className="flex items-center justify-center">
                <code className="text-xl font-bold text-white tracking-[0.15em] bg-[#0f1520] px-5 py-2.5 rounded-lg border border-[#1e2a3a]">
                  {deviceCode.user_code}
                </code>
              </div>
              {devicePolling && (
                <p className="text-center text-xs text-[#5a6b7f] flex items-center justify-center gap-2">
                  <RefreshCw size={11} className="animate-spin" /> {t('onboarding.providerCodex.waitingAuth')}
                </p>
              )}
            </div>
          ) : authLoading ? (
            <div className="flex items-center justify-center py-8 gap-2 text-[#5a6b7f] text-sm">
              <RefreshCw size={14} className="animate-spin" /> {t('onboarding.providerCodex.startingDevice')}
            </div>
          ) : null
        )}

        {authMessage && (
          <div className={`rounded-lg p-3 text-xs ${authMessage.type === 'success' ? 'bg-[#00FFA7]/5 text-[#00FFA7]' : 'bg-[#1a0a0a] text-[#f87171]'}`}>
            {authMessage.text}
            {authMessage.type === 'error' && tab === 'device' && (
              <p className="text-[10px] text-[#3d4f65] mt-1">{t('onboarding.providerCodex.deviceOrgWarning')}</p>
            )}
          </div>
        )}

        <div className="flex gap-3 pt-2">
          <button
            onClick={onBackToProviders}
            className="flex-none py-3 px-4 rounded-lg border border-[#152030] text-[#5a6b7f] hover:border-[#00FFA7]/30 hover:text-[#e2e8f0] text-sm font-medium transition-colors"
          >
            {t('onboarding.back')}
          </button>
          {tab === 'browser' && authUrl && (
            <button
              onClick={completeBrowserAuth}
              disabled={!callbackUrl || authLoading}
              className="flex-1 py-3 rounded-lg bg-[#00FFA7] text-[#080c14] hover:bg-[#00e69a] text-sm font-semibold transition-colors disabled:opacity-40"
            >
              {authLoading ? t('onboarding.providerCodex.verifying') : t('onboarding.providerCodex.confirm')}
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
