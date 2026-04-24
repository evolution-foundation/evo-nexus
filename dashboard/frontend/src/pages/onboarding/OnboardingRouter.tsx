import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { api } from '../../lib/api'
import { useAuth } from '../../context/AuthContext'
import Welcome from './Welcome'
import StepProvider from './StepProvider'
import StepBrainRepo from './StepBrainRepo'
import StepBrainConnect from './StepBrainConnect'
import StepBrainChoose from './StepBrainChoose'
import StepConfirm from './StepConfirm'
import RestoreFlow from './restore/RestoreFlow'

type Flow = 'first-time' | 'restore' | null

interface OnboardingState {
  onboarding_state: string
  onboarding_completed_agents_visit: boolean
  brain_repo_configured: boolean
  brain_repo: string | null
}

export default function OnboardingRouter() {
  const navigate = useNavigate()
  const { t } = useTranslation()
  const { refreshUser } = useAuth()
  const [flow, setFlow] = useState<Flow>(null)
  const [step, setStep] = useState(0)
  const [patToken, setPatToken] = useState('')
  const [selectedProvider, setSelectedProvider] = useState<string | null>(null)
  const [wantBrainRepo, setWantBrainRepo] = useState<boolean | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    // Allow re-entry into a specific sub-flow even after onboarding is completed.
    // "?reconfigure=brain" enters the brain-repo connect step directly — used by the
    // "Configure" buttons on /backups and /settings/brain-repo when the user already
    // completed onboarding but never connected a brain repo (or disconnected it).
    const params = new URLSearchParams(window.location.search)
    const reconfigure = params.get('reconfigure')

    api.get('/onboarding/state')
      .then((data: OnboardingState) => {
        if (reconfigure === 'brain') {
          setFlow('first-time')
          setWantBrainRepo(true)
          setStep(3)
          return
        }
        // If already completed or skipped, go to agents
        if (data.onboarding_state === 'completed' || data.onboarding_state === 'skipped') {
          navigate('/agents', { replace: true })
          return
        }
        if (data.onboarding_state === 'pending') {
          setFlow('first-time')
          setStep(1)
        }
      })
      .catch(() => {
        // No state yet — show welcome
      })
      .finally(() => setLoading(false))
  }, [navigate])

  const startFirstTime = async () => {
    try {
      await api.post('/onboarding/start')
    } catch {
      // ignore
    }
    setFlow('first-time')
    setStep(1)
  }

  const startRestore = () => {
    setFlow('restore')
    setStep(0)
  }

  const handleComplete = async () => {
    try {
      await api.post('/onboarding/complete')
    } catch {
      // ignore
    }
    // Refresh React user state BEFORE navigating, otherwise the App.tsx onboarding
    // guard still sees onboarding_state = 'pending' from cache and bounces us back
    // to /onboarding, creating an infinite loop with the OnboardingRouter useEffect.
    await refreshUser()
    navigate('/agents', { replace: true })
  }

  const handleSkip = async () => {
    try {
      await api.post('/onboarding/skip')
    } catch {
      // ignore
    }
    await refreshUser()
    navigate('/agents', { replace: true })
  }

  if (loading) {
    return (
      <div className="min-h-screen bg-[#080c14] flex items-center justify-center">
        <div className="text-[#5a6b7f] text-sm">{t('onboarding.loading')}</div>
      </div>
    )
  }

  // Welcome screen (no flow chosen yet)
  if (flow === null) {
    return <Welcome onFirstTime={startFirstTime} onRestore={startRestore} />
  }

  // Restore flow
  if (flow === 'restore') {
    return <RestoreFlow onComplete={handleComplete} onBack={() => setFlow(null)} />
  }

  // First-time flow steps
  if (step === 1) {
    return (
      <StepProvider
        onNext={(provider: string) => {
          setSelectedProvider(provider)
          setStep(2)
        }}
        onBack={() => setFlow(null)}
      />
    )
  }

  if (step === 2) {
    return (
      <StepBrainRepo
        onYes={() => {
          setWantBrainRepo(true)
          setStep(3)
        }}
        onNo={() => {
          setWantBrainRepo(false)
          setStep(5)
        }}
        onBack={() => setStep(1)}
      />
    )
  }

  if (step === 3) {
    return (
      <StepBrainConnect
        onNext={(token: string) => {
          setPatToken(token)
          setStep(4)
        }}
        onBack={() => setStep(2)}
      />
    )
  }

  if (step === 4) {
    return (
      <StepBrainChoose
        token={patToken}
        onNext={() => setStep(5)}
        onBack={() => setStep(3)}
      />
    )
  }

  if (step === 5) {
    return (
      <StepConfirm
        provider={selectedProvider}
        wantBrainRepo={wantBrainRepo}
        onComplete={handleComplete}
        onSkip={handleSkip}
        onBack={() => setStep(wantBrainRepo ? 4 : 2)}
      />
    )
  }

  return null
}
