import { Check, GitBranch, Cpu } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import OnboardingHeader from './OnboardingHeader'

interface StepConfirmProps {
  provider: string | null
  wantBrainRepo: boolean | null
  onComplete: () => void
  onSkip: () => void
  onBack: () => void
}

export default function StepConfirm({ provider, wantBrainRepo, onComplete, onSkip, onBack }: StepConfirmProps) {
  const { t } = useTranslation()
  const providerLabel = provider
    ? t(`onboarding.providerLabels.${provider}`, { defaultValue: provider })
    : t('onboarding.confirm.notConfigured')
  return (
    <div className="min-h-screen bg-[#080c14] flex items-center justify-center px-4 font-[Inter,-apple-system,sans-serif]">
      <div className="w-full max-w-[480px] relative z-10">
        <OnboardingHeader step="step3of3" filled={3} />

        <div className="rounded-xl border border-[#152030] bg-[#0b1018] shadow-[0_4px_40px_rgba(0,0,0,0.4)]">
          <div className="px-7 pt-7 pb-5 border-b border-[#152030]">
            <h2 className="text-[16px] font-semibold text-[#e2e8f0]">{t('onboarding.confirm.title')}</h2>
            <p className="text-[11px] text-[#4a5a6e] mt-1">{t('onboarding.confirm.subtitle')}</p>
          </div>

          <div className="px-7 py-6 space-y-4">
            {/* Summary */}
            <div className="space-y-2">
              <p className="text-[11px] font-semibold text-[#5a6b7f] uppercase tracking-[0.08em] mb-3">
                {t('onboarding.confirm.summary')}
              </p>

              <div className="flex items-center gap-3 p-3 rounded-lg bg-[#0f1520] border border-[#1e2a3a]">
                <div className="flex items-center justify-center w-7 h-7 rounded-lg bg-[#00FFA7]/10">
                  <Cpu size={14} className="text-[#00FFA7]" />
                </div>
                <div className="flex-1">
                  <p className="text-[11px] text-[#5a6b7f]">{t('onboarding.confirm.aiProvider')}</p>
                  <p className="text-[13px] font-medium text-[#e2e8f0]">{providerLabel}</p>
                </div>
                <Check size={14} className="text-[#00FFA7] flex-shrink-0" />
              </div>

              <div className="flex items-center gap-3 p-3 rounded-lg bg-[#0f1520] border border-[#1e2a3a]">
                <div className="flex items-center justify-center w-7 h-7 rounded-lg bg-[#00FFA7]/10">
                  <GitBranch size={14} className="text-[#00FFA7]" />
                </div>
                <div className="flex-1">
                  <p className="text-[11px] text-[#5a6b7f]">{t('onboarding.confirm.brainRepoLabel')}</p>
                  <p className="text-[13px] font-medium text-[#e2e8f0]">
                    {wantBrainRepo ? t('onboarding.confirm.connected') : t('onboarding.confirm.skipped')}
                  </p>
                </div>
                {wantBrainRepo ? (
                  <Check size={14} className="text-[#00FFA7] flex-shrink-0" />
                ) : (
                  <span className="text-[10px] text-[#5a6b7f]">{t('onboarding.confirm.optional')}</span>
                )}
              </div>
            </div>

            <div className="p-3 rounded-lg bg-[#0a1a12] border border-[#00FFA7]/15">
              <p className="text-[11px] text-[#4a7a5a] leading-relaxed">
                {t('onboarding.confirm.settingsHintPart1')}
                <span className="text-[#00FFA7]/70">{t('onboarding.confirm.settingsHintPath')}</span>
                {t('onboarding.confirm.settingsHintPart2')}
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
                onClick={onComplete}
                className="flex-1 py-3 rounded-lg bg-[#00FFA7] text-[#080c14] hover:bg-[#00e69a] text-sm font-semibold transition-colors"
              >
                {t('onboarding.confirm.finish')}
              </button>
            </div>

            <button
              onClick={onSkip}
              className="w-full py-2 text-[11px] text-[#2d3d4f] hover:text-[#5a6b7f] transition-colors"
            >
              {t('onboarding.confirm.skipAll')}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
