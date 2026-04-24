import { useState, useEffect, useRef } from 'react'
import { CheckCircle, XCircle, Loader2, RefreshCw } from 'lucide-react'
import { useTranslation } from 'react-i18next'

interface SelectedSnapshot {
  ref: string
  label: string
  includeKb: boolean
}

interface RestoreStep {
  id: string
  label: string
  status: 'pending' | 'running' | 'done' | 'error'
  message?: string
}

interface RestoreExecuteProps {
  snapshot: SelectedSnapshot
  onComplete: () => void
  onRetry: () => void
}

const API = import.meta.env.DEV ? 'http://localhost:8080' : ''

export default function RestoreExecute({ snapshot, onComplete, onRetry }: RestoreExecuteProps) {
  const { t } = useTranslation()
  const [progress, setProgress] = useState(0)
  const [steps, setSteps] = useState<RestoreStep[]>([])
  const [failed, setFailed] = useState(false)
  const [done, setDone] = useState(false)
  const [statusMessage, setStatusMessage] = useState(t('restore.execute.starting'))
  const abortRef = useRef<AbortController | null>(null)

  useEffect(() => {
    const ctrl = new AbortController()
    abortRef.current = ctrl

    const run = async () => {
      try {
        const res = await fetch(`${API}/api/brain-repo/restore/start`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
          credentials: 'include',
          body: JSON.stringify({ ref: snapshot.ref, include_kb: snapshot.includeKb }),
          signal: ctrl.signal,
        })

        if (!res.ok) {
          setFailed(true)
          setStatusMessage(`${t('restore.execute.errorPrefix')}${res.status} ${res.statusText}`)
          return
        }

        const reader = res.body?.getReader()
        if (!reader) return

        const decoder = new TextDecoder()
        let buffer = ''

        while (true) {
          const { done: streamDone, value } = await reader.read()
          if (streamDone) break

          buffer += decoder.decode(value, { stream: true })
          const lines = buffer.split('\n')
          buffer = lines.pop() || ''

          for (const line of lines) {
            if (!line.startsWith('data:')) continue
            const raw = line.slice(5).trim()
            if (!raw) continue

            try {
              const event = JSON.parse(raw) as {
                type: string
                step?: string
                label?: string
                status?: string
                message?: string
                progress?: number
              }

              if (event.type === 'step') {
                const stepId = event.step || ''
                setSteps((prev) => {
                  const existing = prev.find((s) => s.id === stepId)
                  if (existing) {
                    return prev.map((s) =>
                      s.id === stepId
                        ? { ...s, status: (event.status as RestoreStep['status']) || 'running', message: event.message }
                        : s
                    )
                  }
                  return [
                    ...prev,
                    {
                      id: stepId,
                      label: event.label || stepId,
                      status: (event.status as RestoreStep['status']) || 'running',
                      message: event.message,
                    },
                  ]
                })
              }

              if (event.type === 'progress' && event.progress !== undefined) {
                setProgress(event.progress)
                if (event.message) setStatusMessage(event.message)
              }

              if (event.type === 'complete') {
                setProgress(100)
                setDone(true)
                setStatusMessage(t('restore.execute.complete'))
                setTimeout(() => onComplete(), 2000)
              }

              if (event.type === 'error') {
                setFailed(true)
                setStatusMessage(event.message || t('restore.execute.failedDefault'))
              }
            } catch {
              // ignore parse errors
            }
          }
        }
      } catch (ex) {
        if ((ex as Error).name !== 'AbortError') {
          setFailed(true)
          setStatusMessage(t('restore.execute.connectionError'))
        }
      }
    }

    run()
    return () => ctrl.abort()
  }, [snapshot, onComplete, t])

  return (
    <div className="min-h-screen bg-[#080c14] flex items-center justify-center px-4 font-[Inter,-apple-system,sans-serif]">
      <div className="w-full max-w-[480px] relative z-10">
        <div className="rounded-xl border border-[#152030] bg-[#0b1018] shadow-[0_4px_40px_rgba(0,0,0,0.4)]">
          <div className="px-7 pt-7 pb-5 border-b border-[#152030]">
            <h2 className="text-[16px] font-semibold text-[#e2e8f0]">
              {done ? t('restore.execute.titleDone') : failed ? t('restore.execute.titleFailed') : t('restore.execute.titleRunning')}
            </h2>
            <p className="text-[11px] text-[#4a5a6e] mt-1">{snapshot.label}</p>
          </div>

          <div className="px-7 py-6 space-y-4">
            {/* Progress bar */}
            <div>
              <div className="flex items-center justify-between mb-1.5">
                <span className="text-[11px] text-[#5a6b7f]">{statusMessage}</span>
                <span className="text-[11px] text-[#5a6b7f]">{progress}%</span>
              </div>
              <div className="h-1.5 rounded-full bg-[#152030] overflow-hidden">
                <div
                  className="h-full rounded-full transition-all duration-300"
                  style={{
                    width: `${progress}%`,
                    backgroundColor: failed ? '#ef4444' : done ? '#00FFA7' : '#00FFA7',
                  }}
                />
              </div>
            </div>

            {/* Steps list */}
            {steps.length > 0 && (
              <div className="space-y-1.5 max-h-52 overflow-y-auto">
                {steps.map((s) => (
                  <div key={s.id} className="flex items-center gap-2.5 py-1.5">
                    {s.status === 'done' && <CheckCircle size={14} className="text-[#00FFA7] flex-shrink-0" />}
                    {s.status === 'error' && <XCircle size={14} className="text-[#f87171] flex-shrink-0" />}
                    {s.status === 'running' && <Loader2 size={14} className="text-[#5a6b7f] animate-spin flex-shrink-0" />}
                    {s.status === 'pending' && <div className="w-3.5 h-3.5 rounded-full border border-[#2d3d4f] flex-shrink-0" />}
                    <div className="min-w-0">
                      <p className={`text-[12px] ${s.status === 'error' ? 'text-[#f87171]' : s.status === 'done' ? 'text-[#e2e8f0]' : 'text-[#8a9ab0]'}`}>
                        {s.label}
                      </p>
                      {s.message && (
                        <p className="text-[10px] text-[#5a6b7f] truncate">{s.message}</p>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}

            {!steps.length && !failed && !done && (
              <div className="flex items-center justify-center py-4">
                <Loader2 size={20} className="text-[#5a6b7f] animate-spin" />
              </div>
            )}

            {/* Done message */}
            {done && (
              <div className="flex items-center gap-2 p-3 rounded-lg bg-[#0a1a12] border border-[#00FFA7]/20">
                <CheckCircle size={16} className="text-[#00FFA7]" />
                <p className="text-[12px] text-[#4a9a6a]">{t('restore.execute.redirecting')}</p>
              </div>
            )}

            {/* Error actions */}
            {failed && (
              <button
                onClick={onRetry}
                className="w-full flex items-center justify-center gap-2 py-3 rounded-lg border border-[#152030] text-[#5a6b7f] hover:border-[#00FFA7]/30 hover:text-[#e2e8f0] text-sm font-medium transition-colors"
              >
                <RefreshCw size={14} />
                {t('restore.execute.tryAgain')}
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
