import { useState, useEffect } from 'react'
import { GitBranch, RefreshCw, Tag, Unlink, AlertTriangle, CheckCircle, Loader2, Clock } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { api } from '../../lib/api'

interface BrainRepoStatus {
  connected: boolean
  repo_url: string | null
  last_sync: string | null
  pending_count: number
  sync_enabled: boolean
  branch?: string
  /** Server has cryptography + BRAIN_REPO_MASTER_KEY available right now.
   *  False means stored tokens cannot be decrypted; UI should warn. */
  crypto_ready?: boolean
  last_error?: string | null
}

const inp = "w-full px-4 py-3 rounded-lg bg-[#0f1520] border border-[#1e2a3a] text-[#e2e8f0] placeholder-[#3d4f65] text-sm transition-colors duration-200 focus:outline-none focus:border-[#00FFA7]/60 focus:ring-1 focus:ring-[#00FFA7]/20"

export default function BrainRepo() {
  const { t } = useTranslation()
  const [status, setStatus] = useState<BrainRepoStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [syncing, setSyncing] = useState(false)
  const [syncMsg, setSyncMsg] = useState<{ type: 'ok' | 'err'; text: string } | null>(null)
  const [milestoneInput, setMilestoneInput] = useState('')
  const [milestoning, setMilestoning] = useState(false)
  const [milestoneMsg, setMilestoneMsg] = useState<{ type: 'ok' | 'err'; text: string } | null>(null)
  const [confirmDisconnect, setConfirmDisconnect] = useState(false)
  const [disconnecting, setDisconnecting] = useState(false)

  const formatDate = (iso: string | null): string => {
    if (!iso) return t('brainRepoSettings.formatNever')
    const d = new Date(iso)
    const now = new Date()
    const diff = Math.floor((now.getTime() - d.getTime()) / 1000)
    if (diff < 60) return t('brainRepoSettings.formatJustNow')
    if (diff < 3600) return t('brainRepoSettings.formatMinAgo', { n: Math.floor(diff / 60) })
    if (diff < 86400) return t('brainRepoSettings.formatHourAgo', { n: Math.floor(diff / 3600) })
    return t('brainRepoSettings.formatDayAgo', { n: Math.floor(diff / 86400) })
  }

  const loadStatus = () => {
    setLoading(true)
    api.get('/brain-repo/status')
      .then((d: BrainRepoStatus) => setStatus(d))
      .catch(() => setStatus(null))
      .finally(() => setLoading(false))
  }

  useEffect(() => { loadStatus() }, [])

  const handleSync = async () => {
    setSyncing(true)
    setSyncMsg(null)
    try {
      await api.post('/brain-repo/sync/force')
      setSyncMsg({ type: 'ok', text: t('brainRepoSettings.sync.success') })
      setTimeout(loadStatus, 2000)
    } catch (ex: unknown) {
      setSyncMsg({ type: 'err', text: ex instanceof Error ? ex.message : t('brainRepoSettings.sync.failed') })
    } finally {
      setSyncing(false)
    }
  }

  const handleMilestone = async () => {
    if (!milestoneInput.trim()) return
    setMilestoning(true)
    setMilestoneMsg(null)
    try {
      const res = await api.post('/brain-repo/tag/milestone', { name: milestoneInput.trim() }) as { tag: string }
      setMilestoneMsg({ type: 'ok', text: t('brainRepoSettings.milestone.success', { tag: res.tag }) })
      setMilestoneInput('')
    } catch (ex: unknown) {
      setMilestoneMsg({ type: 'err', text: ex instanceof Error ? ex.message : t('brainRepoSettings.milestone.failed') })
    } finally {
      setMilestoning(false)
    }
  }

  const handleDisconnect = async () => {
    setDisconnecting(true)
    try {
      await api.post('/brain-repo/disconnect')
      setConfirmDisconnect(false)
      loadStatus()
    } catch {
      // ignore
    } finally {
      setDisconnecting(false)
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center py-16">
        <Loader2 size={24} className="text-[#5a6b7f] animate-spin" />
      </div>
    )
  }

  const cryptoBroken = status?.connected && status.crypto_ready === false

  return (
    <div className="max-w-2xl">
      {/* Header */}
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-[#e6edf3]">{t('brainRepoSettings.title')}</h1>
        <p className="text-[#667085] mt-1">{t('brainRepoSettings.subtitle')}</p>
      </div>

      {/* Crypto-broken banner — master key missing or cryptography module
          unavailable on the server. Every sync/tag will fail until fixed,
          so surface it above the status card so users can't miss it. */}
      {cryptoBroken && (
        <div className="rounded-xl border border-[#3a1515] bg-[#1a0a0a] px-5 py-4 mb-4 flex items-start gap-3">
          <AlertTriangle size={18} className="text-[#f87171] flex-shrink-0 mt-0.5" />
          <div className="flex-1">
            <p className="text-[13px] font-semibold text-[#f87171]">{t('brainRepoSettings.cryptoBroken.title')}</p>
            <p className="text-[11px] text-[#f87171]/80 mt-1 leading-relaxed">{t('brainRepoSettings.cryptoBroken.desc')}</p>
          </div>
        </div>
      )}

      {/* Status card */}
      <div className="rounded-xl border border-[#152030] bg-[#0b1018] shadow-[0_4px_40px_rgba(0,0,0,0.4)] mb-4">
        <div className="px-6 py-5 border-b border-[#152030] flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className={`flex items-center justify-center w-9 h-9 rounded-xl border ${
              status?.connected
                ? 'bg-[#00FFA7]/10 border-[#00FFA7]/20'
                : 'bg-[#5a6b7f]/10 border-[#5a6b7f]/20'
            }`}>
              <GitBranch size={16} className={status?.connected ? 'text-[#00FFA7]' : 'text-[#5a6b7f]'} />
            </div>
            <div>
              <p className="text-[14px] font-semibold text-[#e2e8f0]">
                {status?.connected ? t('brainRepoSettings.status.connected') : t('brainRepoSettings.status.notConnected')}
              </p>
              {status?.repo_url && (
                <a
                  href={status.repo_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-[11px] text-[#00FFA7]/70 hover:text-[#00FFA7] transition-colors truncate max-w-xs block"
                >
                  {status.repo_url}
                </a>
              )}
            </div>
          </div>
          {status?.connected && (
            <span className="flex items-center gap-1.5 px-2 py-1 rounded-full bg-[#00FFA7]/10 border border-[#00FFA7]/20 text-[10px] font-semibold uppercase tracking-wider text-[#00FFA7]">
              <span className="h-1.5 w-1.5 rounded-full bg-[#00FFA7]" />
              {t('brainRepoSettings.status.active')}
            </span>
          )}
        </div>

        {status?.connected && (
          <div className="px-6 py-4 grid grid-cols-3 gap-4">
            <div>
              <p className="text-[10px] font-semibold text-[#5a6b7f] uppercase tracking-[0.08em]">{t('brainRepoSettings.status.lastSync')}</p>
              <div className="flex items-center gap-1.5 mt-1">
                <Clock size={12} className="text-[#5a6b7f]" />
                <p className="text-[13px] text-[#e2e8f0]">{formatDate(status.last_sync)}</p>
              </div>
            </div>
            <div>
              <p className="text-[10px] font-semibold text-[#5a6b7f] uppercase tracking-[0.08em]">{t('brainRepoSettings.status.pending')}</p>
              <p className={`text-[13px] mt-1 font-medium ${status.pending_count > 0 ? 'text-[#F59E0B]' : 'text-[#e2e8f0]'}`}>
                {t('brainRepoSettings.status.pendingCount', { count: status.pending_count })}
              </p>
            </div>
            <div>
              <p className="text-[10px] font-semibold text-[#5a6b7f] uppercase tracking-[0.08em]">{t('brainRepoSettings.status.autoSync')}</p>
              <p className={`text-[13px] mt-1 ${status.sync_enabled ? 'text-[#00FFA7]' : 'text-[#5a6b7f]'}`}>
                {status.sync_enabled ? t('brainRepoSettings.status.enabled') : t('brainRepoSettings.status.disabled')}
              </p>
            </div>
          </div>
        )}
      </div>

      {status?.connected && (
        <>
          {/* Sync now */}
          <div className="rounded-xl border border-[#152030] bg-[#0b1018] px-6 py-5 mb-4">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-[14px] font-semibold text-[#e2e8f0]">{t('brainRepoSettings.sync.title')}</p>
                <p className="text-[11px] text-[#5a6b7f] mt-0.5">{t('brainRepoSettings.sync.desc')}</p>
              </div>
              <button
                onClick={handleSync}
                disabled={syncing}
                className="flex items-center gap-2 px-4 py-2 rounded-lg bg-[#00FFA7] text-[#080c14] hover:bg-[#00e69a] text-sm font-semibold transition-colors disabled:opacity-40"
              >
                {syncing ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
                {syncing ? t('brainRepoSettings.sync.running') : t('brainRepoSettings.sync.btn')}
              </button>
            </div>
            {syncMsg && (
              <div className={`mt-3 flex items-center gap-2 px-3 py-2 rounded-lg border text-xs ${
                syncMsg.type === 'ok'
                  ? 'bg-[#0a1a12] border-[#00FFA7]/20 text-[#4a9a6a]'
                  : 'bg-[#1a0a0a] border-[#3a1515] text-[#f87171]'
              }`}>
                {syncMsg.type === 'ok' ? <CheckCircle size={12} /> : <AlertTriangle size={12} />}
                {syncMsg.text}
              </div>
            )}
          </div>

          {/* Create milestone */}
          <div className="rounded-xl border border-[#152030] bg-[#0b1018] px-6 py-5 mb-4">
            <p className="text-[14px] font-semibold text-[#e2e8f0] mb-1">{t('brainRepoSettings.milestone.title')}</p>
            <p className="text-[11px] text-[#5a6b7f] mb-3">{t('brainRepoSettings.milestone.desc')}</p>
            <div className="flex gap-2">
              <input
                type="text"
                value={milestoneInput}
                onChange={(e) => setMilestoneInput(e.target.value)}
                className={`${inp} flex-1`}
                placeholder={t('brainRepoSettings.milestone.placeholder')}
                onKeyDown={(e) => e.key === 'Enter' && handleMilestone()}
              />
              <button
                onClick={handleMilestone}
                disabled={milestoning || !milestoneInput.trim()}
                className="flex items-center gap-2 px-4 py-2 rounded-lg border border-[#152030] text-[#5a6b7f] hover:border-[#00FFA7]/30 hover:text-[#e2e8f0] text-sm font-medium transition-colors disabled:opacity-40 flex-shrink-0"
              >
                {milestoning ? <Loader2 size={14} className="animate-spin" /> : <Tag size={14} />}
                {milestoning ? t('brainRepoSettings.milestone.running') : t('brainRepoSettings.milestone.btn')}
              </button>
            </div>
            {milestoneMsg && (
              <div className={`mt-3 flex items-center gap-2 px-3 py-2 rounded-lg border text-xs ${
                milestoneMsg.type === 'ok'
                  ? 'bg-[#0a1a12] border-[#00FFA7]/20 text-[#4a9a6a]'
                  : 'bg-[#1a0a0a] border-[#3a1515] text-[#f87171]'
              }`}>
                {milestoneMsg.type === 'ok' ? <CheckCircle size={12} /> : <AlertTriangle size={12} />}
                {milestoneMsg.text}
              </div>
            )}
          </div>

          {/* Disconnect */}
          <div className="rounded-xl border border-[#3a1515] bg-[#0b1018] px-6 py-5">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-[14px] font-semibold text-[#e2e8f0]">{t('brainRepoSettings.disconnect.title')}</p>
                <p className="text-[11px] text-[#5a6b7f] mt-0.5">{t('brainRepoSettings.disconnect.desc')}</p>
              </div>
              <button
                onClick={() => setConfirmDisconnect(true)}
                className="flex items-center gap-2 px-4 py-2 rounded-lg border border-[#3a1515] text-[#f87171] hover:bg-[#1a0a0a] text-sm font-medium transition-colors"
              >
                <Unlink size={14} />
                {t('brainRepoSettings.disconnect.btn')}
              </button>
            </div>

            {confirmDisconnect && (
              <div className="mt-4 p-3 rounded-lg bg-[#1a0a0a] border border-[#3a1515]">
                <p className="text-[12px] text-[#f87171] mb-3">
                  {t('brainRepoSettings.disconnect.confirmText')}
                </p>
                <div className="flex gap-2">
                  <button
                    onClick={() => setConfirmDisconnect(false)}
                    className="flex-1 py-2 rounded-lg border border-[#152030] text-[#5a6b7f] text-sm font-medium transition-colors hover:text-[#e2e8f0]"
                  >
                    {t('brainRepoSettings.disconnect.cancel')}
                  </button>
                  <button
                    onClick={handleDisconnect}
                    disabled={disconnecting}
                    className="flex-1 py-2 rounded-lg bg-[#f87171] text-[#1a0a0a] hover:bg-[#ef4444] text-sm font-semibold transition-colors disabled:opacity-40"
                  >
                    {disconnecting ? t('brainRepoSettings.disconnect.running') : t('brainRepoSettings.disconnect.confirm')}
                  </button>
                </div>
              </div>
            )}
          </div>
        </>
      )}

      {!status?.connected && (
        <div className="rounded-xl border border-[#152030] bg-[#0b1018] px-6 py-8 text-center">
          <GitBranch size={32} className="text-[#2d3d4f] mx-auto mb-3" />
          <p className="text-[14px] text-[#5a6b7f]">{t('brainRepoSettings.empty.title')}</p>
          <p className="text-[11px] text-[#2d3d4f] mt-1">
            {t('brainRepoSettings.empty.desc')}
          </p>
          <a
            href="/onboarding?reconfigure=brain"
            className="inline-block mt-4 px-4 py-2 rounded-lg bg-[#00FFA7] text-[#080c14] hover:bg-[#00e69a] text-sm font-semibold transition-colors"
          >
            {t('brainRepoSettings.empty.btn')}
          </a>
        </div>
      )}
    </div>
  )
}
