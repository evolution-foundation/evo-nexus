import { useState } from 'react'
import { Eye, EyeOff, ExternalLink, GitBranch, Loader2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { api } from '../../../lib/api'

const inp = "w-full px-4 py-3 rounded-lg bg-[#0f1520] border border-[#1e2a3a] text-[#e2e8f0] placeholder-[#3d4f65] text-sm transition-colors duration-200 focus:outline-none focus:border-[#00FFA7]/60 focus:ring-1 focus:ring-[#00FFA7]/20"

interface Repo {
  name: string
  full_name: string
  html_url: string
}

interface RestoreSelectRepoProps {
  onNext: (repoUrl: string, token: string) => void
  onBack: () => void
}

export default function RestoreSelectRepo({ onNext, onBack }: RestoreSelectRepoProps) {
  const { t } = useTranslation()
  const [token, setToken] = useState('')
  const [showToken, setShowToken] = useState(false)
  const [tokenConnected, setTokenConnected] = useState(false)
  const [connecting, setConnecting] = useState(false)
  const [repos, setRepos] = useState<Repo[]>([])
  const [loadingRepos, setLoadingRepos] = useState(false)
  const [selectedRepo, setSelectedRepo] = useState<Repo | null>(null)
  const [error, setError] = useState('')

  const connectToken = async () => {
    if (!token.trim()) {
      setError(t('restore.selectRepo.tokenRequired'))
      return
    }
    setError('')
    setConnecting(true)
    try {
      // Validate the PAT without persisting — same endpoint StepBrainConnect uses.
      // Then call /detect with the token as a query param so backend doesn't
      // need a stored config to enumerate the user's brain repos.
      const resp = await api.post('/brain-repo/validate-token', { token: token.trim() }) as { ok?: boolean }
      if (!resp?.ok) {
        setError(t('restore.selectRepo.invalidToken'))
        return
      }
      setTokenConnected(true)
      setLoadingRepos(true)
      const data = await api.get(`/brain-repo/detect?token=${encodeURIComponent(token.trim())}`) as { repos: Repo[] }
      setRepos(data.repos || [])
    } catch (ex: unknown) {
      const msg = ex instanceof Error ? ex.message : t('restore.selectRepo.failed')
      if (msg.includes('401') || msg.includes('403')) {
        setError(t('restore.selectRepo.invalidToken'))
      } else {
        setError(msg)
      }
    } finally {
      setConnecting(false)
      setLoadingRepos(false)
    }
  }

  const handleNext = () => {
    if (!selectedRepo) {
      setError(t('restore.selectRepo.selectRepo'))
      return
    }
    onNext(selectedRepo.html_url, token.trim())
  }

  return (
    <div className="min-h-screen bg-[#080c14] flex items-center justify-center px-4 font-[Inter,-apple-system,sans-serif]">
      <div className="w-full max-w-[480px] relative z-10">
        <div className="rounded-xl border border-[#152030] bg-[#0b1018] shadow-[0_4px_40px_rgba(0,0,0,0.4)]">
          <div className="px-7 pt-7 pb-5 border-b border-[#152030]">
            <h2 className="text-[16px] font-semibold text-[#e2e8f0]">{t('restore.selectRepo.title')}</h2>
            <p className="text-[11px] text-[#4a5a6e] mt-1">{t('restore.selectRepo.subtitle')}</p>
          </div>

          <div className="px-7 py-6 space-y-4">
            {error && (
              <div className="px-3 py-2.5 rounded-lg bg-[#1a0a0a] border border-[#3a1515] text-[#f87171] text-xs">
                {error}
              </div>
            )}

            {/* Token input */}
            {!tokenConnected && (
              <div>
                <div className="flex items-center justify-between mb-1.5">
                  <label className="block text-[11px] font-semibold text-[#5a6b7f] tracking-[0.08em] uppercase">
                    {t('restore.selectRepo.pat')}
                  </label>
                  <a
                    href="https://github.com/settings/tokens/new?scopes=repo"
                    target="_blank"
                    rel="noopener noreferrer"
                    className="flex items-center gap-1 text-[10px] text-[#00FFA7]/70 hover:text-[#00FFA7] transition-colors"
                  >
                    <ExternalLink size={10} />
                    {t('restore.selectRepo.createPat')}
                  </a>
                </div>
                <div className="relative flex gap-2">
                  <div className="relative flex-1">
                    <input
                      type={showToken ? 'text' : 'password'}
                      value={token}
                      onChange={(e) => setToken(e.target.value)}
                      className={`${inp} pr-10`}
                      placeholder="ghp_xxxxxxxxxxxxxxxxxxxx"
                      onKeyDown={(e) => e.key === 'Enter' && connectToken()}
                    />
                    <button
                      type="button"
                      onClick={() => setShowToken(!showToken)}
                      className="absolute right-3 top-1/2 -translate-y-1/2 text-[#5a6b7f] hover:text-[#e2e8f0] transition-colors"
                    >
                      {showToken ? <EyeOff size={16} /> : <Eye size={16} />}
                    </button>
                  </div>
                  <button
                    onClick={connectToken}
                    disabled={connecting || !token.trim()}
                    className="px-4 py-3 rounded-lg bg-[#00FFA7] text-[#080c14] hover:bg-[#00e69a] text-sm font-semibold transition-colors disabled:opacity-40 flex-shrink-0"
                  >
                    {connecting ? <Loader2 size={14} className="animate-spin" /> : t('restore.selectRepo.connect')}
                  </button>
                </div>
              </div>
            )}

            {/* Repo list */}
            {tokenConnected && (
              <div>
                <label className="block text-[11px] font-semibold text-[#5a6b7f] mb-1.5 tracking-[0.08em] uppercase">
                  {t('restore.selectRepo.detectedRepos')}
                </label>
                {loadingRepos ? (
                  <div className="flex items-center justify-center py-6">
                    <Loader2 size={18} className="text-[#5a6b7f] animate-spin" />
                  </div>
                ) : repos.length === 0 ? (
                  <div className="py-4 text-center">
                    <p className="text-[12px] text-[#5a6b7f]">{t('restore.selectRepo.noReposFound')}</p>
                    <p className="text-[11px] text-[#2d3d4f] mt-1">{t('restore.selectRepo.noReposHint')}</p>
                  </div>
                ) : (
                  <div className="space-y-1.5 max-h-52 overflow-y-auto">
                    {repos.map((repo) => (
                      <button
                        key={repo.full_name}
                        onClick={() => setSelectedRepo(repo)}
                        className={`w-full flex items-center gap-3 p-3 rounded-lg border text-left transition-all ${
                          selectedRepo?.full_name === repo.full_name
                            ? 'border-[#00FFA7]/60 bg-[#00FFA7]/8'
                            : 'border-[#1e2a3a] bg-[#0f1520] hover:border-[#2a3a4a]'
                        }`}
                      >
                        <GitBranch size={13} className="text-[#5a6b7f] flex-shrink-0" />
                        <div className="min-w-0">
                          <p className="text-[12px] font-medium text-[#e2e8f0] truncate">{repo.name}</p>
                          <p className="text-[10px] text-[#5a6b7f] truncate">{repo.full_name}</p>
                        </div>
                      </button>
                    ))}
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
                disabled={!selectedRepo}
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
