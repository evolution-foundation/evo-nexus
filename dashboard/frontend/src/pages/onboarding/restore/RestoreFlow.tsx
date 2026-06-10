import { useState } from 'react'
import RestoreSelectRepo from './RestoreSelectRepo'
import RestoreSelectSnapshot from './RestoreSelectSnapshot'
import RestoreConfirm from './RestoreConfirm'
import RestoreExecute from './RestoreExecute'

type RestoreStep = 'select-repo' | 'select-snapshot' | 'confirm' | 'execute'

interface SelectedSnapshot {
  ref: string
  label: string
  includeKb: boolean
}

interface RestoreFlowProps {
  onComplete: () => void
  onBack: () => void
}

export default function RestoreFlow({ onComplete, onBack }: RestoreFlowProps) {
  const [step, setStep] = useState<RestoreStep>('select-repo')
  const [repoUrl, setRepoUrl] = useState('')
  // Token + repoUrl flow through every step because /api/brain-repo/snapshots
  // and /api/brain-repo/restore/start need them: in the wizard there is no
  // persisted BrainRepoConfig yet, so the endpoints accept the pair explicitly
  // (catch-22 fix — see routes/brain_repo.py).
  const [token, setToken] = useState('')
  const [snapshot, setSnapshot] = useState<SelectedSnapshot | null>(null)

  if (step === 'select-repo') {
    return (
      <RestoreSelectRepo
        onNext={(url: string, t: string) => {
          setRepoUrl(url)
          setToken(t)
          setStep('select-snapshot')
        }}
        onBack={onBack}
      />
    )
  }

  if (step === 'select-snapshot') {
    return (
      <RestoreSelectSnapshot
        repoUrl={repoUrl}
        token={token}
        onNext={(s: SelectedSnapshot) => {
          setSnapshot(s)
          setStep('confirm')
        }}
        onBack={() => setStep('select-repo')}
      />
    )
  }

  if (step === 'confirm' && snapshot) {
    return (
      <RestoreConfirm
        snapshot={snapshot}
        onConfirm={() => setStep('execute')}
        onBack={() => setStep('select-snapshot')}
      />
    )
  }

  if (step === 'execute' && snapshot) {
    return (
      <RestoreExecute
        snapshot={snapshot}
        token={token}
        repoUrl={repoUrl}
        onComplete={onComplete}
        onRetry={() => setStep('confirm')}
      />
    )
  }

  return null
}
