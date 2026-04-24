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
  const [snapshot, setSnapshot] = useState<SelectedSnapshot | null>(null)

  if (step === 'select-repo') {
    return (
      <RestoreSelectRepo
        onNext={(url: string) => {
          setRepoUrl(url)
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
        onComplete={onComplete}
        onRetry={() => setStep('confirm')}
      />
    )
  }

  return null
}
