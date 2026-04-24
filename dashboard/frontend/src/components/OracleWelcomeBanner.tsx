import { useState } from 'react'
import { X, BookOpen, Terminal } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { api } from '../lib/api'
import { useAuth } from '../context/AuthContext'

// Extended user type with onboarding fields
interface OnboardingUser {
  id: string
  username: string
  email: string
  display_name: string
  role: string
  onboarding_state?: string | null
  onboarding_completed_agents_visit?: boolean
}

export default function OracleWelcomeBanner() {
  const { user, refreshUser } = useAuth()
  const { t } = useTranslation()
  const extUser = user as OnboardingUser | null
  const [dismissed, setDismissed] = useState(false)

  // Only render if onboarding_completed_agents_visit is explicitly false
  if (!extUser || extUser.onboarding_completed_agents_visit !== false || dismissed) {
    return null
  }

  const handleDismiss = async () => {
    setDismissed(true)
    try {
      await api.post('/auth/mark-agents-visited')
      await refreshUser()
    } catch {
      // ignore — banner is already dismissed locally
    }
  }

  return (
    <div
      className="mb-5 flex items-center gap-4 px-4 py-3 rounded-xl border border-[#00FFA7]/20"
      style={{ backgroundColor: '#0b1a12' }}
    >
      {/* Icon */}
      <div className="flex-shrink-0 flex items-center justify-center w-9 h-9 rounded-xl bg-[#F59E0B]/10 border border-[#F59E0B]/20">
        <BookOpen size={16} className="text-[#F59E0B]" />
      </div>

      {/* Content */}
      <div className="flex-1 min-w-0">
        <p className="text-[13px] font-semibold text-[#e6edf3]">
          {t('agents.welcomeBanner.title')}
        </p>
        <p className="text-[11px] text-[#5a7a5a] mt-0.5 leading-snug">
          {t('agents.welcomeBanner.descriptionPart1')}
          <span className="inline-flex items-center gap-1 font-mono text-[10px] px-1.5 py-0.5 rounded bg-[#0f2010] border border-[#00FFA7]/15 text-[#00FFA7]/80">
            <Terminal size={9} />
            /oracle
          </span>
          {t('agents.welcomeBanner.descriptionPart2')}
        </p>
      </div>

      {/* Dismiss */}
      <button
        onClick={handleDismiss}
        className="flex-shrink-0 p-1.5 rounded-lg text-[#5a6b7f] hover:text-[#e2e8f0] hover:bg-[#152030] transition-colors"
        aria-label={t('common.close')}
      >
        <X size={14} />
      </button>
    </div>
  )
}
