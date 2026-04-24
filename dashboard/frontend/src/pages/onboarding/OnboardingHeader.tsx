import { useTranslation } from 'react-i18next'

type StepKey = 'step1of3' | 'step2of3' | 'step2aOf3' | 'step2bOf3' | 'step3of3'

interface OnboardingHeaderProps {
  /** Which step indicator label to render (i18n key under onboarding.stepIndicator). */
  step: StepKey
  /** How many of the three progress dots should be filled (1, 2 or 3). */
  filled: 1 | 2 | 3
}

/**
 * Shared header for the inner onboarding screens (Steps 1 → 3).
 *
 * Renders the EvoNexus logo above the step indicator so the user keeps the
 * brand context throughout the wizard — the standalone Welcome screen has its
 * own larger header with the logo + welcome title, so it does NOT use this
 * component.
 */
export default function OnboardingHeader({ step, filled }: OnboardingHeaderProps) {
  const { t } = useTranslation()
  const dot = (active: boolean) => (
    <span className={`h-1.5 w-8 rounded-full ${active ? 'bg-[#00FFA7]' : 'bg-[#152030]'}`} />
  )
  return (
    <div className="flex flex-col items-center gap-4 mb-6">
      <img src="/EVO_NEXUS.webp" alt="EvoNexus" className="h-7 w-auto opacity-90" />
      <div className="flex items-center gap-2">
        <span className="text-[11px] text-[#5a6b7f] uppercase tracking-[0.08em]">
          {t(`onboarding.stepIndicator.${step}`)}
        </span>
        <div className="flex gap-1.5">
          {dot(filled >= 1)}
          {dot(filled >= 2)}
          {dot(filled >= 3)}
        </div>
      </div>
    </div>
  )
}
