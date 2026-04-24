import { useState, useEffect } from 'react'
import { Clock, Tag, GitCommit, AlertTriangle, Loader2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { api } from '../../../lib/api'

interface Snapshot {
  ref: string
  label: string
  date?: string
  message?: string
}

interface SnapshotData {
  head: Snapshot | null
  daily: Snapshot[]
  weekly: Snapshot[]
  milestones: Snapshot[]
}

interface SelectedSnapshot {
  ref: string
  label: string
  includeKb: boolean
}

interface RestoreSelectSnapshotProps {
  repoUrl: string
  onNext: (snapshot: SelectedSnapshot) => void
  onBack: () => void
}

function SnapshotItem({
  snapshot,
  selected,
  onClick,
}: {
  snapshot: Snapshot
  selected: boolean
  onClick: () => void
}) {
  return (
    <button
      onClick={onClick}
      className={`w-full flex items-center gap-3 p-2.5 rounded-lg border text-left transition-all ${
        selected
          ? 'border-[#00FFA7]/60 bg-[#00FFA7]/8'
          : 'border-[#1e2a3a] bg-[#0f1520] hover:border-[#2a3a4a]'
      }`}
    >
      <GitCommit size={12} className={selected ? 'text-[#00FFA7]' : 'text-[#5a6b7f]'} />
      <div className="flex-1 min-w-0">
        <p className="text-[12px] font-medium text-[#e2e8f0] truncate">{snapshot.label}</p>
        {snapshot.date && (
          <p className="text-[10px] text-[#5a6b7f]">{snapshot.date}</p>
        )}
        {snapshot.message && (
          <p className="text-[10px] text-[#4a5a6e] truncate">{snapshot.message}</p>
        )}
      </div>
      <code className="text-[9px] font-mono text-[#2d3d4f] flex-shrink-0">{snapshot.ref.slice(0, 8)}</code>
    </button>
  )
}

export default function RestoreSelectSnapshot({ repoUrl, onNext, onBack }: RestoreSelectSnapshotProps) {
  const { t } = useTranslation()
  const [data, setData] = useState<SnapshotData | null>(null)
  const [loading, setLoading] = useState(true)
  const [selected, setSelected] = useState<Snapshot | null>(null)
  const [includeKb, setIncludeKb] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    api.get('/brain-repo/snapshots')
      .then((d: SnapshotData) => setData(d))
      .catch(() => setError(t('restore.selectSnapshot.failed')))
      .finally(() => setLoading(false))
  }, [repoUrl, t])

  const handleNext = () => {
    if (!selected) {
      setError(t('restore.selectSnapshot.selectSnapshot'))
      return
    }
    onNext({ ref: selected.ref, label: selected.label, includeKb })
  }

  const sections: Array<{ key: keyof SnapshotData; label: string; icon: typeof Clock; limit?: number }> = [
    { key: 'head', label: t('restore.selectSnapshot.sectionHead'), icon: GitCommit },
    { key: 'milestones', label: t('restore.selectSnapshot.sectionMilestones'), icon: Tag },
    { key: 'weekly', label: t('restore.selectSnapshot.sectionWeekly'), icon: Clock, limit: 12 },
    { key: 'daily', label: t('restore.selectSnapshot.sectionDaily'), icon: Clock, limit: 30 },
  ]

  return (
    <div className="min-h-screen bg-[#080c14] flex items-center justify-center px-4 font-[Inter,-apple-system,sans-serif]">
      <div className="w-full max-w-[480px] relative z-10">
        <div className="rounded-xl border border-[#152030] bg-[#0b1018] shadow-[0_4px_40px_rgba(0,0,0,0.4)]">
          <div className="px-7 pt-7 pb-5 border-b border-[#152030]">
            <h2 className="text-[16px] font-semibold text-[#e2e8f0]">{t('restore.selectSnapshot.title')}</h2>
            <p className="text-[11px] text-[#4a5a6e] mt-1">{t('restore.selectSnapshot.subtitle')}</p>
          </div>

          <div className="px-7 py-6 space-y-4">
            {error && (
              <div className="px-3 py-2.5 rounded-lg bg-[#1a0a0a] border border-[#3a1515] text-[#f87171] text-xs">
                {error}
              </div>
            )}

            {loading ? (
              <div className="flex items-center justify-center py-8">
                <Loader2 size={24} className="text-[#5a6b7f] animate-spin" />
              </div>
            ) : data ? (
              <div className="space-y-4 max-h-[50vh] overflow-y-auto pr-1">
                {sections.map(({ key, label, icon: Icon, limit }) => {
                  const raw = key === 'head' ? (data.head ? [data.head] : []) : (data[key] as Snapshot[]) || []
                  const items = limit ? raw.slice(0, limit) : raw
                  if (items.length === 0) return null
                  return (
                    <div key={key}>
                      <div className="flex items-center gap-2 mb-2">
                        <Icon size={12} className="text-[#5a6b7f]" />
                        <span className="text-[11px] font-semibold text-[#5a6b7f] uppercase tracking-[0.08em]">{label}</span>
                        <span className="text-[10px] text-[#2d3d4f]">{items.length}</span>
                      </div>
                      <div className="space-y-1">
                        {items.map((s) => (
                          <SnapshotItem
                            key={s.ref}
                            snapshot={s}
                            selected={selected?.ref === s.ref}
                            onClick={() => setSelected(s)}
                          />
                        ))}
                      </div>
                    </div>
                  )
                })}
              </div>
            ) : null}

            {/* Include KB option */}
            {selected && (
              <div>
                <label className="flex items-start gap-3 cursor-pointer group">
                  <input
                    type="checkbox"
                    checked={includeKb}
                    onChange={(e) => setIncludeKb(e.target.checked)}
                    className="mt-0.5 accent-[#00FFA7]"
                  />
                  <div>
                    <p className="text-[12px] font-medium text-[#e2e8f0]">{t('restore.selectSnapshot.includeKb')}</p>
                    <p className="text-[10px] text-[#5a6b7f] mt-0.5">{t('restore.selectSnapshot.includeKbDesc')}</p>
                  </div>
                </label>

                {includeKb && (
                  <div className="mt-2 flex items-start gap-2 p-3 rounded-lg bg-[#1a1400] border border-[#3a3015]">
                    <AlertTriangle size={13} className="text-[#F59E0B] flex-shrink-0 mt-0.5" />
                    <p className="text-[11px] text-[#b89070]">
                      {t('restore.selectSnapshot.kbWarning')}
                    </p>
                  </div>
                )}
              </div>
            )}

            <div className="flex gap-3 pt-2">
              <button
                onClick={onBack}
                className="flex-none py-3 px-4 rounded-lg border border-[#152030] text-[#5a6b7f] hover:border-[#00FFA7]/30 hover:text-[#e2e8f0] text-sm font-medium transition-colors"
              >
                {t('restore.back')}
              </button>
              <button
                onClick={handleNext}
                disabled={!selected}
                className="flex-1 py-3 rounded-lg bg-[#00FFA7] text-[#080c14] hover:bg-[#00e69a] text-sm font-semibold transition-colors disabled:opacity-40"
              >
                {t('restore.next')}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
