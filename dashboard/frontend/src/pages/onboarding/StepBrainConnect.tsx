import { useState } from 'react'
import { Eye, EyeOff, ExternalLink } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { api } from '../../lib/api'
import OnboardingHeader from './OnboardingHeader'

const inp = "w-full px-4 py-3 rounded-lg bg-[#0f1520] border border-[#1e2a3a] text-[#e2e8f0] placeholder-[#3d4f65] text-sm transition-colors duration-200 focus:outline-none focus:border-[#00FFA7]/60 focus:ring-1 focus:ring-[#00FFA7]/20"

interface StepBrainConnectProps {
  onNext: (token: string) => void
  onBack: () => void
}

export default function StepBrainConnect({ onNext, onBack }: StepBrainConnectProps) {
  const { t } = useTranslation()
  const [token, setToken] = useState('')
  const [showToken, setShowToken] = useState(false)
  const [connecting, setConnecting] = useState(false)
  const [error, setError] = useState('')

  const handleNext = async () => {
    if (!token.trim()) {
      setError(t('onboarding.connect.tokenRequired'))
      return
    }
    setError('')
    setConnecting(true)
    try {
      // Validate the token — response shape: { ok, scopes, username }
      const resp = await api.post('/brain-repo/validate-token', { token: token.trim() }) as { ok?: boolean }
      if (!resp?.ok) {
        setError(t('onboarding.connect.invalidToken'))
        return
      }
      onNext(token.trim())
    } catch (ex: unknown) {
      const msg = ex instanceof Error ? ex.message : t('onboarding.connect.failed')
      if (msg.includes('401') || msg.includes('403')) {
        setError(t('onboarding.connect.invalidToken'))
      } else {
        setError(msg)
      }
    } finally {
      setConnecting(false)
    }
  }

  return (
    <div className="min-h-screen bg-[#080c14] flex items-center justify-center px-4 font-[Inter,-apple-system,sans-serif]">
      <div className="w-full max-w-[480px] relative z-10">
        <OnboardingHeader step="step2aOf3" filled={2} />

        <div className="rounded-xl border border-[#152030] bg-[#0b1018] shadow-[0_4px_40px_rgba(0,0,0,0.4)]">
          <div className="px-7 pt-7 pb-5 border-b border-[#152030]">
            <h2 className="text-[16px] font-semibold text-[#e2e8f0]">{t('onboarding.connect.title')}</h2>
            <p className="text-[11px] text-[#4a5a6e] mt-1">{t('onboarding.connect.subtitle')}</p>
          </div>

          <div className="px-7 py-6 space-y-4">
            {error && (
              <div className="px-3 py-2.5 rounded-lg bg-[#1a0a0a] border border-[#3a1515] text-[#f87171] text-xs">
                {error}
              </div>
            )}

            <div>
              <div className="flex items-center justify-between mb-1.5">
                <label className="block text-[11px] font-semibold text-[#5a6b7f] tracking-[0.08em] uppercase">
                  {t('onboarding.connect.pat')}
                </label>
                <a
                  href="https://github.com/settings/tokens/new?scopes=repo"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex items-center gap-1 text-[10px] text-[#00FFA7]/70 hover:text-[#00FFA7] transition-colors"
                >
                  <ExternalLink size={10} />
                  {t('onboarding.connect.createPat')}
                </a>
              </div>
              <div className="relative">
                <input
                  type={showToken ? 'text' : 'password'}
                  value={token}
                  onChange={(e) => setToken(e.target.value)}
                  className={`${inp} pr-10`}
                  placeholder="ghp_xxxxxxxxxxxxxxxxxxxx"
                  autoFocus
                  onKeyDown={(e) => e.key === 'Enter' && handleNext()}
                />
                <button
                  type="button"
                  onClick={() => setShowToken(!showToken)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-[#5a6b7f] hover:text-[#e2e8f0] transition-colors"
                >
                  {showToken ? <EyeOff size={16} /> : <Eye size={16} />}
                </button>
              </div>
            </div>

            <div className="p-3 rounded-lg bg-[#0a1220] border border-[#1e2a3a]">
              <p className="text-[11px] text-[#5a6b7f] leading-relaxed">
                {t('onboarding.connect.patHintPart1')}
                <code className="text-[#00FFA7]/80 bg-[#0f1520] px-1 py-0.5 rounded text-[10px]">{t('onboarding.connect.patHintScope')}</code>
                {t('onboarding.connect.patHintPart2')}
              </p>
            </div>

            <div className="flex gap-3 pt-2">
              <button
                onClick={onBack}
                className="flex-none py-3 px-4 rounded-lg border border-[#152030] text-[#5a6b7f] hover:border-[#00FFA7]/30 hover:text-[#e2e8f0] text-sm font-medium transition-colors"
              >
                {t('onboarding.back')}
              </button>
              <button
                onClick={handleNext}
                disabled={connecting || !token.trim()}
                className="flex-1 py-3 rounded-lg bg-[#00FFA7] text-[#080c14] hover:bg-[#00e69a] text-sm font-semibold transition-colors disabled:opacity-40"
              >
                {connecting ? t('onboarding.connect.connecting') : t('onboarding.connect.connect')}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
