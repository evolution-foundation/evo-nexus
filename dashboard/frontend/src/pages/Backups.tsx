import { useState, useEffect, useCallback, useRef } from 'react'
import { useToast } from '../components/Toast'
import { useConfirm } from '../components/ConfirmDialog'
import {
  HardDriveDownload, Plus, Download, RotateCcw, Trash2, RefreshCw,
  Cloud, HardDrive, AlertCircle, AlertTriangle, CheckCircle, Loader2, FileArchive,
  ChevronDown, Eye, EyeOff, Save, Upload, GitBranch, ExternalLink, Tag,
} from 'lucide-react'
import { Link } from 'react-router-dom'
import { api } from '../lib/api'
import { useTranslation } from 'react-i18next'

interface BackupManifest {
  version: string
  workspace_name: string
  created_at: string
  hostname: string
  file_count: number
  total_size: number
}

interface BackupEntry {
  filename: string
  size: number
  modified: number
  manifest: BackupManifest | null
}

interface BackupConfig {
  s3_configured: boolean
  s3_bucket: string
  boto3_available: boolean
  backups_dir: string
  brain_repo_configured: boolean
  brain_repo: BrainRepoStatus | null
  /** Whether the server has BRAIN_REPO_MASTER_KEY + cryptography available
   *  right now. False means stored tokens cannot be decrypted — UI should
   *  show a danger banner instead of normal "connected" state. */
  brain_crypto_ready: boolean
}

interface BrainRepoStatus {
  repo_url?: string
  repo_owner?: string
  repo_name?: string
  last_sync?: string | null
  pending_count?: number
  sync_enabled?: boolean
  last_error?: string | null
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`
}

function formatDate(ts: number | string): string {
  const d = typeof ts === 'number' ? new Date(ts * 1000) : new Date(ts)
  return d.toLocaleDateString('pt-BR', {
    day: '2-digit', month: '2-digit', year: 'numeric',
    hour: '2-digit', minute: '2-digit',
  })
}

const S3_FIELDS = [
  { envKey: 'BACKUP_S3_BUCKET', label: 'S3 Bucket', hint: 'Nome do bucket (ex: my-backups)', required: true, sensitive: false },
  { envKey: 'AWS_ACCESS_KEY_ID', label: 'Access Key ID', hint: 'IAM access key', required: true, sensitive: true },
  { envKey: 'AWS_SECRET_ACCESS_KEY', label: 'Secret Access Key', hint: 'IAM secret key', required: true, sensitive: true },
  { envKey: 'AWS_DEFAULT_REGION', label: 'Region', hint: 'ex: us-east-1, sa-east-1, auto', required: false, sensitive: false },
  { envKey: 'AWS_ENDPOINT_URL', label: 'Endpoint URL', hint: 'Para R2, Backblaze, MinIO (ex: https://xxx.r2.cloudflarestorage.com)', required: false, sensitive: false },
  { envKey: 'BACKUP_S3_PREFIX', label: 'Prefix', hint: 'Prefixo das chaves no bucket (ex: backups/evonexus/)', required: false, sensitive: false },
  { envKey: 'BACKUP_RETAIN_LOCAL', label: 'Retenção local', hint: 'Backups locais a manter (ex: 7). Vazio = sem limite', required: false, sensitive: false },
  { envKey: 'BACKUP_RETAIN_S3', label: 'Retenção S3', hint: 'Backups no S3 a manter (ex: 30). Vazio = sem limite', required: false, sensitive: false },
]

function S3ConfigPanel({ config, onSaved }: { config: BackupConfig; onSaved: () => void }) {
  const { t } = useTranslation()
  const toast = useToast()
  // Open expanded by default — parent only mounts this panel when the user
  // explicitly clicks Configure/Manage on the S3 destination card, so there
  // is no value in starting collapsed.
  const [expanded, setExpanded] = useState(true)
  const [values, setValues] = useState<Record<string, string>>({})
  const [revealed, setRevealed] = useState<Set<string>>(new Set())
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [loaded, setLoaded] = useState(false)

  // Load env values when panel expands
  useEffect(() => {
    if (!expanded || loaded) return
    api.get('/config/env').then((data: { entries: Array<{ type: string; key?: string; value: string }> }) => {
      const vals: Record<string, string> = {}
      const keys = new Set(S3_FIELDS.map(f => f.envKey))
      for (const e of data.entries || []) {
        if (e.type === 'var' && e.key && keys.has(e.key)) {
          vals[e.key] = e.value
        }
      }
      setValues(vals)
      setLoaded(true)
    }).catch(() => setLoaded(true))
  }, [expanded, loaded])

  const handleSave = async () => {
    setSaving(true)
    try {
      const current = await api.get('/config/env')
      const entries = (current.entries || []).map((e: { type: string; key?: string; value: string }) => {
        if (e.type === 'var' && e.key && e.key in values) {
          return { ...e, value: values[e.key!] }
        }
        return e
      })
      // Add new keys not yet in .env
      const existingKeys = new Set(entries.filter((e: { type: string }) => e.type === 'var').map((e: { key?: string }) => e.key))
      for (const field of S3_FIELDS) {
        if (!existingKeys.has(field.envKey) && values[field.envKey]) {
          entries.push({ type: 'var', key: field.envKey, value: values[field.envKey] })
        }
      }
      await api.put('/config/env', { entries })
      setSaved(true)
      setTimeout(() => setSaved(false), 2000)
      onSaved()
    } catch {
      toast.error('Erro ao salvar')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="rounded-xl border border-[#152030] bg-[#0b1018] overflow-hidden shadow-[0_4px_40px_rgba(0,0,0,0.4)]">
      {/* Header — always visible */}
      <button
        onClick={() => setExpanded(prev => !prev)}
        className="w-full flex items-center justify-between px-5 py-4 hover:bg-[#0f1520]/40 transition-colors"
      >
        <div className="flex items-center gap-3 text-sm">
          <Cloud size={16} className={config.s3_configured ? 'text-blue-400' : 'text-[#5a6b7f]'} />
          <span className="text-[#e2e8f0] font-medium">{t('backups.s3Config.title')}</span>
          {config.s3_configured ? (
            <span className="text-xs px-2 py-0.5 rounded-full bg-[#00FFA7]/10 text-[#00FFA7]">
              S3: {config.s3_bucket}
            </span>
          ) : (
            <span className="text-xs px-2 py-0.5 rounded-full bg-[#21262d] text-[#667085]">
              Local only
            </span>
          )}
          {config.s3_configured && !config.boto3_available && (
            <span className="text-xs px-2 py-0.5 rounded-full bg-yellow-500/10 text-yellow-500">
              boto3 not installed
            </span>
          )}
          <span className="flex items-center gap-1 text-xs text-[#667085]">
            <HardDrive size={12} />
            {config.backups_dir}
          </span>
        </div>
        <ChevronDown
          size={16}
          className={`text-[#667085] transition-transform duration-200 ${expanded ? 'rotate-180' : ''}`}
        />
      </button>

      {/* Expandable form */}
      {expanded && (
        <div className="px-5 pb-5 border-t border-[#152030]">
          <p className="text-xs text-[#5a6b7f] mt-4 mb-4 leading-relaxed">
            {t('backups.s3Config.endpointHint')}
          </p>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            {S3_FIELDS.map(field => {
              const isRevealed = revealed.has(field.envKey)
              return (
                <div key={field.envKey}>
                  <label className="block text-xs font-medium text-[#D0D5DD] mb-1">
                    {field.label}
                    {field.required && <span className="text-red-400 ml-0.5">*</span>}
                  </label>
                  <div className="relative">
                    <input
                      type={field.sensitive && !isRevealed ? 'password' : 'text'}
                      value={values[field.envKey] ?? ''}
                      onChange={e => setValues(prev => ({ ...prev, [field.envKey]: e.target.value }))}
                      placeholder={field.hint}
                      className="w-full px-3 py-2 rounded-lg text-sm font-mono bg-[#0d1117] border border-[#21262d] text-[#e6edf3] placeholder-[#667085]/50 focus:outline-none focus:border-[#00FFA7] transition-colors"
                    />
                    {field.sensitive && (
                      <button
                        type="button"
                        onClick={() => setRevealed(prev => {
                          const n = new Set(prev)
                          isRevealed ? n.delete(field.envKey) : n.add(field.envKey)
                          return n
                        })}
                        className="absolute right-2 top-1/2 -translate-y-1/2 p-1 text-[#667085] hover:text-[#D0D5DD] transition-colors"
                      >
                        {isRevealed ? <EyeOff size={14} /> : <Eye size={14} />}
                      </button>
                    )}
                  </div>
                </div>
              )
            })}
          </div>
          <div className="flex justify-end mt-4">
            <button
              onClick={handleSave}
              disabled={saving}
              className={`flex items-center gap-2 px-4 py-2 rounded-lg font-medium text-sm transition-colors ${
                saved
                  ? 'bg-[#00FFA7]/20 text-[#00FFA7] border border-[#00FFA7]/30'
                  : 'bg-[#00FFA7] text-[#0d1117] hover:bg-[#00FFA7]/90'
              } disabled:opacity-50`}
            >
              <Save size={14} />
              {saving ? 'Salvando...' : saved ? 'Salvo!' : 'Salvar'}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

interface S3BackupEntry {
  key: string
  filename: string
  size: number
  modified: string
}

interface BrainSnapshot {
  ref: string              // e.g. "refs/tags/milestone/teste" or "HEAD"
  sha?: string
  label?: string           // human-readable short label (falls back to ref)
  date?: string
  message?: string
}

interface BrainSnapshotsResponse {
  head: BrainSnapshot | null
  milestones: BrainSnapshot[]
  weekly: BrainSnapshot[]
  daily: BrainSnapshot[]
}

type BackupsTab = 'local' | 's3' | 'brain'

/**
 * Backup destinations status panel — makes explicit which targets are
 * available (Local always, S3 + Brain Repo conditionally). Renders 3 cards
 * with concise status so the user knows where backups land before clicking
 * "New Backup" or "Backup + S3" / "Backup + Brain Repo".
 */
/**
 * 3-card panel showing the explicit status of every backup destination.
 * Same palette as /onboarding (#0b1018 cards on #152030 borders, #00FFA7 accent).
 *
 * S3 card "Configure" button toggles the S3ConfigPanel below (parent owns the
 * state) so the env-var form is never shown unsolicited — it only appears
 * when the user explicitly opens it.
 */
function DestinationsPanel({
  config,
  onMilestone,
  milestoneRunning,
  onOpenS3Config,
}: {
  config: BackupConfig
  onMilestone: () => void
  milestoneRunning: boolean
  onOpenS3Config: () => void
}) {
  const { t } = useTranslation()
  const brain = config.brain_repo
  const brainConfigured = config.brain_repo_configured && brain
  // cryptoBroken: config exists but server can't decrypt tokens right now.
  // Treat as more urgent than last_error because it means EVERY sync will
  // fail for the same reason until the admin restores the master key.
  const cryptoBroken = brainConfigured && !config.brain_crypto_ready
  const s3Connected = config.s3_configured && config.boto3_available
  const formatLastSync = (iso?: string | null) => {
    if (!iso) return t('backups.destinations.never')
    try {
      const d = new Date(iso)
      return d.toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })
    } catch { return iso }
  }

  const cardBase = "rounded-xl border bg-[#0b1018] p-5 transition-colors"
  const cardConnected = "border-[#152030] hover:border-[#00FFA7]/30"
  const cardOff = "border-[#152030] hover:border-[#1e2a3a]"

  const badgeOk = "inline-flex items-center gap-1 text-[10px] font-semibold px-2 py-0.5 rounded-full bg-[#00FFA7]/10 text-[#00FFA7] border border-[#00FFA7]/20 uppercase tracking-wider"
  const badgeOff = "inline-flex items-center gap-1 text-[10px] font-semibold px-2 py-0.5 rounded-full bg-[#152030] text-[#5a6b7f] border border-[#1e2a3a] uppercase tracking-wider"
  const pillBtn = "text-[11px] font-medium px-3 py-1.5 rounded-md transition-colors inline-flex items-center gap-1.5"

  return (
    <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
      {/* Local — always available */}
      <div className={`${cardBase} ${cardConnected}`}>
        <div className="flex items-start justify-between gap-2 mb-3">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-xl bg-[#00FFA7]/10 border border-[#00FFA7]/20 flex items-center justify-center">
              <HardDrive size={16} className="text-[#00FFA7]" />
            </div>
            <div>
              <div className="text-[14px] font-semibold text-[#e2e8f0]">{t('backups.destinations.local')}</div>
              <div className="text-[10px] text-[#5a6b7f] mt-0.5">{t('backups.destinations.localDesc')}</div>
            </div>
          </div>
          <span className={badgeOk}>
            <span className="h-1.5 w-1.5 rounded-full bg-[#00FFA7]" />
            {t('backups.destinations.available')}
          </span>
        </div>
        <div className="text-[11px] text-[#5a6b7f] font-mono mt-3 px-2 py-1.5 rounded bg-[#0f1520] border border-[#152030]">
          ./{config.backups_dir}/
        </div>
      </div>

      {/* S3 */}
      <div className={`${cardBase} ${s3Connected ? cardConnected : cardOff}`}>
        <div className="flex items-start justify-between gap-2 mb-3">
          <div className="flex items-center gap-3">
            <div className={`w-9 h-9 rounded-xl flex items-center justify-center border ${
              s3Connected ? 'bg-blue-500/10 border-blue-500/20' : 'bg-[#152030] border-[#1e2a3a]'
            }`}>
              <Cloud size={16} className={s3Connected ? 'text-blue-400' : 'text-[#5a6b7f]'} />
            </div>
            <div>
              <div className="text-[14px] font-semibold text-[#e2e8f0]">{t('backups.destinations.s3')}</div>
              <div className="text-[10px] text-[#5a6b7f] mt-0.5">{t('backups.destinations.s3Desc')}</div>
            </div>
          </div>
          {s3Connected ? (
            <span className="inline-flex items-center gap-1 text-[10px] font-semibold px-2 py-0.5 rounded-full bg-blue-500/10 text-blue-400 border border-blue-500/20 uppercase tracking-wider">
              <span className="h-1.5 w-1.5 rounded-full bg-blue-400" />
              {t('backups.destinations.connected')}
            </span>
          ) : (
            <span className={badgeOff}>{t('backups.destinations.notConfigured')}</span>
          )}
        </div>
        {s3Connected ? (
          <div className="text-[11px] text-[#5a6b7f] font-mono px-2 py-1.5 rounded bg-[#0f1520] border border-[#152030] truncate">
            {config.s3_bucket}
          </div>
        ) : (
          <div className="text-[11px] text-[#5a6b7f] mt-1">
            {t('backups.destinations.s3ConfigureHint')}
          </div>
        )}
        <div className="flex items-center gap-2 mt-3">
          <button
            onClick={onOpenS3Config}
            className={`${pillBtn} ${
              s3Connected
                ? 'border border-[#152030] text-[#5a6b7f] hover:text-[#e2e8f0] hover:border-[#1e2a3a]'
                : 'bg-[#00FFA7]/10 text-[#00FFA7] border border-[#00FFA7]/20 hover:bg-[#00FFA7]/20'
            }`}
          >
            {s3Connected ? t('backups.destinations.manage') : t('backups.destinations.configure')}
          </button>
        </div>
      </div>

      {/* Brain Repo */}
      {/* When crypto is broken (server can't decrypt tokens) or last_error is
          set, the card uses a danger border so the user can't miss that sync
          is broken — plus a Reconnect action that re-runs the onboarding. */}
      <div className={`${cardBase} ${
        cryptoBroken || (brainConfigured && brain?.last_error)
          ? 'border-[#3a1515] hover:border-[#5a2020]'
          : brainConfigured ? cardConnected : cardOff
      }`}>
        <div className="flex items-start justify-between gap-2 mb-3">
          <div className="flex items-center gap-3">
            <div className={`w-9 h-9 rounded-xl flex items-center justify-center border ${
              cryptoBroken || (brainConfigured && brain?.last_error)
                ? 'bg-[#3a1515]/40 border-[#5a2020]'
                : brainConfigured ? 'bg-[#00FFA7]/10 border-[#00FFA7]/20'
                : 'bg-[#152030] border-[#1e2a3a]'
            }`}>
              <GitBranch size={16} className={
                cryptoBroken || (brainConfigured && brain?.last_error) ? 'text-[#f87171]'
                : brainConfigured ? 'text-[#00FFA7]'
                : 'text-[#5a6b7f]'
              } />
            </div>
            <div>
              <div className="text-[14px] font-semibold text-[#e2e8f0]">{t('backups.destinations.brainRepo')}</div>
              <div className="text-[10px] text-[#5a6b7f] mt-0.5">{t('backups.destinations.brainRepoDesc')}</div>
            </div>
          </div>
          {cryptoBroken ? (
            <span className="inline-flex items-center gap-1 text-[10px] font-semibold px-2 py-0.5 rounded-full bg-red-500/10 text-[#f87171] border border-red-500/20 uppercase tracking-wider">
              <AlertTriangle size={9} />
              {t('backups.destinations.cryptoBroken')}
            </span>
          ) : brainConfigured && brain?.last_error ? (
            <span className="inline-flex items-center gap-1 text-[10px] font-semibold px-2 py-0.5 rounded-full bg-red-500/10 text-[#f87171] border border-red-500/20 uppercase tracking-wider">
              <AlertCircle size={9} />
              {t('backups.destinations.syncError')}
            </span>
          ) : brainConfigured ? (
            <span className={badgeOk}>
              <span className="h-1.5 w-1.5 rounded-full bg-[#00FFA7]" />
              {t('backups.destinations.connected')}
            </span>
          ) : (
            <span className={badgeOff}>{t('backups.destinations.notConfigured')}</span>
          )}
        </div>
        {brainConfigured ? (
          <>
            <a
              href={brain!.repo_url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-[11px] text-[#00FFA7]/80 hover:text-[#00FFA7] font-mono inline-flex items-center gap-1 truncate"
            >
              {brain!.repo_owner}/{brain!.repo_name}
              <ExternalLink size={10} />
            </a>
            <div className="text-[11px] text-[#5a6b7f] mt-1.5">
              {t('backups.destinations.lastSync')}: {formatLastSync(brain!.last_sync)}
              {(brain!.pending_count ?? 0) > 0 && (
                <span className="ml-2 text-[#F59E0B]">• {brain!.pending_count} {t('backups.destinations.pending')}</span>
              )}
            </div>
            {cryptoBroken && (
              <div className="mt-2 flex items-start gap-1.5 p-2 rounded-lg bg-[#1a0a0a] border border-[#3a1515]">
                <AlertTriangle size={11} className="text-[#f87171] flex-shrink-0 mt-0.5" />
                <p className="text-[10px] text-[#f87171] leading-tight break-words">
                  {t('backups.destinations.cryptoBrokenDesc')}
                </p>
              </div>
            )}
            {!cryptoBroken && brain?.last_error && (
              <div className="mt-2 flex items-start gap-1.5 p-2 rounded-lg bg-[#1a0a0a] border border-[#3a1515]">
                <AlertTriangle size={11} className="text-[#f87171] flex-shrink-0 mt-0.5" />
                <p className="text-[10px] text-[#f87171] leading-tight break-words">
                  {brain.last_error}
                </p>
              </div>
            )}
            <div className="flex items-center gap-2 mt-3">
              <button
                onClick={onMilestone}
                disabled={milestoneRunning}
                className={`${pillBtn} bg-[#00FFA7]/10 text-[#00FFA7] border border-[#00FFA7]/20 hover:bg-[#00FFA7]/20 disabled:opacity-50`}
              >
                {milestoneRunning ? <Loader2 size={11} className="animate-spin" /> : <Tag size={11} />}
                {t('backups.destinations.createMilestone')}
              </button>
              <Link
                to="/settings/brain-repo"
                className={`${pillBtn} border border-[#152030] text-[#5a6b7f] hover:text-[#e2e8f0] hover:border-[#1e2a3a]`}
              >
                {t('backups.destinations.manage')}
              </Link>
              {(cryptoBroken || brain?.last_error) && (
                <Link
                  to="/onboarding"
                  className={`${pillBtn} bg-[#f87171]/10 text-[#f87171] border border-[#3a1515] hover:bg-[#f87171]/20`}
                >
                  {t('backups.destinations.reconnect')}
                </Link>
              )}
            </div>
          </>
        ) : (
          <div className="mt-1">
            <Link
              to="/onboarding"
              className={`${pillBtn} bg-[#00FFA7]/10 text-[#00FFA7] border border-[#00FFA7]/20 hover:bg-[#00FFA7]/20`}
            >
              {t('backups.destinations.configure')}
            </Link>
          </div>
        )}
      </div>
    </div>
  )
}

/** Import dropdown: upload local .zip (always) + pull from brain repo (when connected). */
function ImportMenu({
  uploading, brainConfigured, onPickZip, onPickBrain,
}: {
  uploading: boolean
  brainConfigured: boolean
  onPickZip: () => void
  onPickBrain: () => void
}) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const wrapperRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const handler = (e: MouseEvent) => {
      if (wrapperRef.current && !wrapperRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  return (
    <div ref={wrapperRef} className="relative">
      <button
        onClick={() => setOpen(o => !o)}
        disabled={uploading}
        className="flex items-center gap-2 px-4 py-2 rounded-lg border border-[#21262d] text-[#D0D5DD] hover:bg-[#161b22] transition-colors text-sm disabled:opacity-50"
      >
        {uploading ? <Loader2 size={16} className="animate-spin" /> : <Upload size={16} />}
        {uploading ? t('backups.importing') : t('backups.import')}
        <ChevronDown size={14} className={`transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>
      {open && (
        <div className="absolute right-0 mt-1 w-64 rounded-lg border border-[#152030] bg-[#0b1018] shadow-[0_4px_40px_rgba(0,0,0,0.4)] z-20 overflow-hidden">
          <button
            onClick={() => { setOpen(false); onPickZip() }}
            className="w-full flex items-start gap-3 px-3 py-2.5 text-left hover:bg-[#0f1520] transition-colors"
          >
            <FileArchive size={14} className="text-[#00FFA7] flex-shrink-0 mt-0.5" />
            <div>
              <div className="text-[12px] text-[#e2e8f0] font-medium">{t('backups.importMenu.zipTitle')}</div>
              <div className="text-[10px] text-[#5a6b7f]">{t('backups.importMenu.zipDesc')}</div>
            </div>
          </button>
          <button
            onClick={() => { setOpen(false); if (brainConfigured) onPickBrain() }}
            disabled={!brainConfigured}
            className="w-full flex items-start gap-3 px-3 py-2.5 text-left hover:bg-[#0f1520] transition-colors disabled:opacity-50 disabled:cursor-not-allowed border-t border-[#152030]"
          >
            <GitBranch size={14} className={`flex-shrink-0 mt-0.5 ${brainConfigured ? 'text-[#00FFA7]' : 'text-[#5a6b7f]'}`} />
            <div>
              <div className="text-[12px] text-[#e2e8f0] font-medium">{t('backups.importMenu.brainTitle')}</div>
              <div className="text-[10px] text-[#5a6b7f]">
                {brainConfigured ? t('backups.importMenu.brainDesc') : t('backups.importMenu.brainDisabled')}
              </div>
            </div>
          </button>
        </div>
      )}
    </div>
  )
}

function TabButton({
  active, onClick, icon, label, count, dimmed = false,
}: {
  active: boolean; onClick: () => void; icon: React.ReactNode; label: string; count?: number; dimmed?: boolean
}) {
  return (
    <button
      onClick={onClick}
      className={`flex items-center gap-2 px-4 py-2.5 text-sm font-medium transition-colors border-b-2 -mb-px ${
        active
          ? 'text-[#00FFA7] border-[#00FFA7]'
          : dimmed
            ? 'text-[#3d4f65] border-transparent hover:text-[#667085]'
            : 'text-[#8a9aae] border-transparent hover:text-[#e2e8f0]'
      }`}
    >
      {icon}
      <span>{label}</span>
      {typeof count === 'number' && (
        <span className={`text-[10px] px-1.5 py-0.5 rounded-full ${
          active ? 'bg-[#00FFA7]/15 text-[#00FFA7]' : 'bg-[#152030] text-[#5a6b7f]'
        }`}>{count}</span>
      )}
    </button>
  )
}

function brainCount(data: BrainSnapshotsResponse): number {
  return (data.head ? 1 : 0) + data.milestones.length + data.weekly.length + data.daily.length
}

/**
 * Renders the 4 categories of brain-repo snapshots (HEAD, milestones, weekly,
 * daily) grouped into collapsible sections. The daily bucket can explode to
 * 30+ items and weekly to 12+ so both default to collapsed.
 */
function BrainRepoSnapshots({
  data, repoUrl, onRestore,
}: {
  data: BrainSnapshotsResponse | null
  repoUrl?: string
  onRestore: (s: BrainSnapshot) => void
}) {
  const { t } = useTranslation()
  const [openWeekly, setOpenWeekly] = useState(false)
  const [openDaily, setOpenDaily] = useState(false)
  if (!data) return null

  const viewOnGitHub = (snapshot: BrainSnapshot) => {
    if (!repoUrl) return
    // Strip "refs/tags/" so the link resolves to GitHub's tag page
    // Guard against malformed snapshots (e.g. legacy HEAD shape without `ref`).
    const rawRef = snapshot.ref ?? snapshot.label ?? ''
    const ref = rawRef.replace(/^refs\/tags\//, '')
    const url = !ref || ref === 'HEAD' ? repoUrl : `${repoUrl}/tree/${encodeURIComponent(ref)}`
    window.open(url, '_blank', 'noopener,noreferrer')
  }

  const SnapshotRow = ({ s, icon, iconClass }: { s: BrainSnapshot; icon: React.ReactNode; iconClass: string }) => (
    <tr className="border-b border-[#152030] last:border-0 hover:bg-[#0f1520]/60 transition-colors">
      <td className="px-4 py-3">
        <div className="flex items-center gap-2">
          <span className={iconClass}>{icon}</span>
          <span className="text-[#e6edf3] font-mono text-xs truncate max-w-[280px] lg:max-w-none">
            {s.label || (s.ref ?? '').replace(/^refs\/tags\//, '') || '(unnamed)'}
          </span>
        </div>
      </td>
      <td className="px-4 py-3 text-[#667085] text-xs font-mono">{s.sha ? s.sha.slice(0, 8) : '-'}</td>
      <td className="px-4 py-3 text-[#667085] text-xs">{s.date || '-'}</td>
      <td className="px-4 py-3">
        <div className="flex items-center justify-end gap-1">
          <button
            onClick={() => onRestore(s)}
            className="p-1.5 rounded-lg text-[#667085] hover:text-blue-400 hover:bg-blue-400/10 transition-colors"
            title={t('backups.action.restoreBtn')}
          ><RotateCcw size={14} /></button>
          {repoUrl && (
            <button
              onClick={() => viewOnGitHub(s)}
              className="p-1.5 rounded-lg text-[#667085] hover:text-[#00FFA7] hover:bg-[#00FFA7]/10 transition-colors"
              title={t('backups.action.viewOnGithub')}
            ><ExternalLink size={14} /></button>
          )}
        </div>
      </td>
    </tr>
  )

  const total = brainCount(data)
  if (total === 0) {
    return (
      <div className="rounded-xl border border-[#152030] bg-[#0b1018] flex flex-col items-center justify-center py-16 text-[#667085]">
        <GitBranch size={48} className="mb-4 opacity-40" />
        <p className="text-sm text-[#8a9aae]">{t('backups.brainTab.noSnapshotsTitle')}</p>
        <p className="text-xs mt-1">{t('backups.brainTab.noSnapshotsHint')}</p>
      </div>
    )
  }

  const section = (
    title: string,
    items: BrainSnapshot[],
    icon: React.ReactNode,
    iconClass: string,
    collapsible?: { open: boolean; onToggle: () => void },
  ) => {
    if (items.length === 0) return null
    return (
      <div className="bg-[#0b1018] border border-[#152030] rounded-xl overflow-hidden">
        <button
          onClick={collapsible?.onToggle}
          disabled={!collapsible}
          className="w-full flex items-center justify-between px-4 py-3 border-b border-[#152030] hover:bg-[#0f1520]/40 transition-colors disabled:cursor-default"
        >
          <div className="flex items-center gap-2">
            <span className="text-[11px] uppercase tracking-wider text-[#8a9aae] font-semibold">{title}</span>
            <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-[#152030] text-[#5a6b7f]">{items.length}</span>
          </div>
          {collapsible && (
            <ChevronDown size={14} className={`text-[#5a6b7f] transition-transform ${collapsible.open ? 'rotate-180' : ''}`} />
          )}
        </button>
        {(!collapsible || collapsible.open) && (
          <table className="w-full text-sm">
            <tbody>
              {items.map(s => <SnapshotRow key={s.ref} s={s} icon={icon} iconClass={iconClass} />)}
            </tbody>
          </table>
        )}
      </div>
    )
  }

  return (
    <div className="space-y-3">
      {data.head && section(t('backups.brainSnapshots.head'), [data.head], <GitBranch size={14} />, 'text-[#00FFA7]')}
      {section(t('backups.brainSnapshots.milestones'), data.milestones, <Tag size={14} />, 'text-[#F59E0B]')}
      {section(
        t('backups.brainSnapshots.weekly'),
        data.weekly,
        <FileArchive size={14} />,
        'text-blue-400',
        { open: openWeekly, onToggle: () => setOpenWeekly(o => !o) },
      )}
      {section(
        t('backups.brainSnapshots.daily'),
        data.daily,
        <FileArchive size={14} />,
        'text-[#8B5CF6]',
        { open: openDaily, onToggle: () => setOpenDaily(o => !o) },
      )}
    </div>
  )
}

export default function Backups() {
  const { t } = useTranslation()
  const toast = useToast()
  const confirm = useConfirm()
  const [backups, setBackups] = useState<BackupEntry[]>([])
  const [s3Backups, setS3Backups] = useState<S3BackupEntry[]>([])
  const [s3Error, setS3Error] = useState<string | null>(null)
  const [s3Loading, setS3Loading] = useState(false)
  const [config, setConfig] = useState<BackupConfig | null>(null)
  const [loading, setLoading] = useState(true)
  const [jobStatus, setJobStatus] = useState<string>('idle')
  const [showRestoreModal, setShowRestoreModal] = useState<string | null>(null)
  const [restoreMode, setRestoreMode] = useState<'merge' | 'replace'>('merge')
  const [uploading, setUploading] = useState(false)
  const [s3ConfigOpen, setS3ConfigOpen] = useState(false)
  const [activeTab, setActiveTab] = useState<BackupsTab>('local')
  const [brainSnapshots, setBrainSnapshots] = useState<BrainSnapshotsResponse | null>(null)
  const [brainLoading, setBrainLoading] = useState(false)
  const [brainError, setBrainError] = useState<string | null>(null)
  // Brain-repo restore modal state — separate from the local/S3 restore
  // modal because the semantics differ (SSE, kb_key_matches, include_kb).
  const [brainRestoreModal, setBrainRestoreModal] = useState<BrainSnapshot | null>(null)
  const [brainRestoreIncludeKb, setBrainRestoreIncludeKb] = useState(false)
  const [brainRestoreKeyMatches, setBrainRestoreKeyMatches] = useState(false)
  const [brainRestoreProgress, setBrainRestoreProgress] = useState<{
    running: boolean; progress: number; message: string; error: boolean
  }>({ running: false, progress: 0, message: '', error: false })
  const uploadRef = { current: null as HTMLInputElement | null }

  const fetchS3 = useCallback(async () => {
    setS3Loading(true)
    try {
      const res = await api.get('/backups/s3')
      setS3Backups(res.backups || [])
      setS3Error(res.error || null)
    } catch {
      setS3Backups([])
      setS3Error('Failed to fetch S3 backups')
    } finally {
      setS3Loading(false)
    }
  }, [])

  const fetchBrainSnapshots = useCallback(async () => {
    setBrainLoading(true)
    setBrainError(null)
    try {
      const res = await api.get('/brain-repo/snapshots') as BrainSnapshotsResponse
      setBrainSnapshots(res)
    } catch (ex: unknown) {
      setBrainError(ex instanceof Error ? ex.message : 'Failed to fetch brain repo snapshots')
      setBrainSnapshots(null)
    } finally {
      setBrainLoading(false)
    }
  }, [])

  const fetchData = useCallback(async () => {
    try {
      const [backupsRes, configRes] = await Promise.all([
        api.get('/backups'),
        api.get('/backups/config'),
      ])
      setBackups(backupsRes.backups)
      setConfig(configRes)
      // Fetch S3 backups if configured
      if (configRes.s3_configured && configRes.boto3_available) {
        fetchS3()
      }
    } catch (err) {
      console.error('Failed to load backups:', err)
    } finally {
      setLoading(false)
    }
  }, [fetchS3])

  useEffect(() => { fetchData() }, [fetchData])

  // Lazy-fetch brain snapshots when user opens that tab and brain repo is configured.
  useEffect(() => {
    if (activeTab === 'brain' && config?.brain_repo_configured && !brainSnapshots && !brainLoading) {
      fetchBrainSnapshots()
    }
  }, [activeTab, config?.brain_repo_configured, brainSnapshots, brainLoading, fetchBrainSnapshots])

  // Poll the backup config every 30s while the user is on this page and
  // brain repo is connected. Surfaces last_error changes from the watcher
  // (auto-sync failures) without requiring a manual refresh. Pauses when
  // the tab is hidden to avoid burning CPU in background tabs.
  useEffect(() => {
    if (!config?.brain_repo_configured) return
    const tick = () => {
      if (document.visibilityState !== 'visible') return
      api.get('/backups/config').then((c) => setConfig(c)).catch(() => { /* ignore transient failures */ })
    }
    const interval = setInterval(tick, 30000)
    return () => clearInterval(interval)
  }, [config?.brain_repo_configured])

  // Poll job status while running
  useEffect(() => {
    if (jobStatus !== 'running') return
    const interval = setInterval(async () => {
      try {
        const status = await api.get('/backups/status')
        if (status.status !== 'running') {
          setJobStatus(status.status)
          fetchData()
        }
      } catch {}
    }, 2000)
    return () => clearInterval(interval)
  }, [jobStatus, fetchData])

  const handleBackup = async (target: 'local' | 's3' = 'local') => {
    try {
      setJobStatus('running')
      await api.post('/backups', { target })
    } catch (err) {
      setJobStatus('error')
      console.error('Backup failed:', err)
    }
  }

  const [milestoneRunning, setMilestoneRunning] = useState(false)
  const handleBrainRepoMilestone = async () => {
    setMilestoneRunning(true)
    try {
      const resp = await api.post('/brain-repo/sync/force') as { ok?: boolean; tag?: string }
      if (resp?.ok) {
        toast.success(t('backups.destinations.milestoneCreated', { tag: resp.tag || '' }))
        // After a successful push, refresh config (last_sync) + snapshots list.
        fetchData()
        if (activeTab === 'brain') fetchBrainSnapshots()
      } else {
        toast.error(t('backups.destinations.milestoneFailed'))
      }
    } catch (ex: unknown) {
      toast.error(ex instanceof Error ? ex.message : t('backups.destinations.milestoneFailed'))
    } finally {
      setMilestoneRunning(false)
    }
  }

  /**
   * Brain-repo restore uses an SSE stream (POST /api/brain-repo/restore/start)
   * with ~10 progress events. We don't reuse the plain JSON api.post — we need
   * the raw Response to read the stream.
   */
  const runBrainRestore = async (snapshot: BrainSnapshot) => {
    setBrainRestoreProgress({ running: true, progress: 0, message: t('backups.brainRestore.starting'), error: false })
    const base = import.meta.env.DEV ? 'http://localhost:8080' : ''
    try {
      const res = await fetch(`${base}/api/brain-repo/restore/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
        credentials: 'include',
        body: JSON.stringify({
          ref: snapshot.ref,
          include_kb: brainRestoreIncludeKb,
          kb_key_matches: brainRestoreKeyMatches,
        }),
      })
      if (!res.ok || !res.body) {
        throw new Error(`${res.status} ${res.statusText}`)
      }
      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() || ''
        for (const line of lines) {
          if (!line.startsWith('data:')) continue
          try {
            const ev = JSON.parse(line.slice(5).trim()) as {
              step?: string; progress?: number; message?: string; error?: boolean
            }
            setBrainRestoreProgress({
              running: !(ev.step === 'complete' || ev.step === 'done' || ev.error),
              progress: ev.progress ?? 0,
              message: ev.message || ev.step || '',
              error: !!ev.error,
            })
          } catch { /* ignore parse errors */ }
        }
      }
      toast.success(t('backups.brainRestore.success'))
      setBrainRestoreModal(null)
      setBrainRestoreIncludeKb(false)
      setBrainRestoreKeyMatches(false)
      fetchData()
    } catch (ex: unknown) {
      const msg = ex instanceof Error ? ex.message : t('backups.brainRestore.failed')
      setBrainRestoreProgress({ running: false, progress: 0, message: msg, error: true })
      toast.error(msg)
    }
  }

  const handleRestore = async (filename: string) => {
    try {
      setJobStatus('running')
      setShowRestoreModal(null)
      await api.post(`/backups/${filename}/restore`, { mode: restoreMode })
    } catch (err) {
      setJobStatus('error')
      console.error('Restore failed:', err)
    }
  }

  const handleDownload = (filename: string) => {
    const base = import.meta.env.DEV ? 'http://localhost:8080' : ''
    window.open(`${base}/api/backups/${filename}/download`, '_blank')
  }

  const handleDelete = async (filename: string) => {
    const ok = await confirm({
      title: t('backups.confirmDelete.title'),
      description: t('backups.confirmDelete.description', { name: filename }),
      confirmText: t('backups.confirmDelete.btn'),
      variant: 'danger',
    })
    if (!ok) return
    try {
      await api.delete(`/backups/${filename}`)
      fetchData()
    } catch (err) {
      console.error('Delete failed:', err)
    }
  }

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    e.target.value = '' // Reset input
    if (!file.name.endsWith('.zip')) {
      toast.warning('Apenas arquivos .zip são aceitos')
      return
    }
    setUploading(true)
    try {
      const formData = new FormData()
      formData.append('file', file)
      const base = import.meta.env.DEV ? 'http://localhost:8080' : ''
      const res = await fetch(`${base}/api/backups/upload`, {
        method: 'POST',
        credentials: 'include',
        body: formData,
      })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        toast.error('Erro ao importar backup', data.error)
        return
      }
      fetchData()
    } catch {
      toast.error('Erro ao importar backup')
    } finally {
      setUploading(false)
    }
  }

  return (
    <div>
      {/* Hidden file input for import */}
      <input
        ref={el => { uploadRef.current = el }}
        type="file"
        accept=".zip"
        onChange={handleUpload}
        className="hidden"
      />

      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-6">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl bg-[#00FFA7]/10 flex items-center justify-center">
            <HardDriveDownload size={20} className="text-[#00FFA7]" />
          </div>
          <div>
            <h1 className="text-xl font-semibold text-[#e6edf3]">{t('backups.title')}</h1>
            <p className="text-sm text-[#667085]">{t('backups.subtitle')}</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => fetchData()}
            className="p-2 rounded-lg border border-[#21262d] text-[#667085] hover:text-[#e6edf3] hover:border-[#344054] transition-colors"
          >
            <RefreshCw size={16} />
          </button>
          <ImportMenu
            uploading={uploading}
            brainConfigured={!!config?.brain_repo_configured}
            onPickZip={() => uploadRef.current?.click()}
            onPickBrain={() => setActiveTab('brain')}
          />
          {config?.s3_configured && config?.boto3_available && (
            <button
              onClick={() => handleBackup('s3')}
              disabled={jobStatus === 'running'}
              className="flex items-center gap-2 px-4 py-2 rounded-lg border border-[#21262d] text-[#D0D5DD] hover:bg-[#161b22] transition-colors text-sm disabled:opacity-50"
            >
              <Cloud size={16} />
              {t('backups.headerBtn.s3')}
            </button>
          )}
          {config?.brain_repo_configured && (
            <button
              onClick={handleBrainRepoMilestone}
              disabled={milestoneRunning}
              className="flex items-center gap-2 px-4 py-2 rounded-lg border border-[#21262d] text-[#D0D5DD] hover:bg-[#161b22] transition-colors text-sm disabled:opacity-50"
            >
              {milestoneRunning ? <Loader2 size={16} className="animate-spin" /> : <GitBranch size={16} />}
              {t('backups.headerBtn.brainRepo')}
            </button>
          )}
          <button
            onClick={() => handleBackup('local')}
            disabled={jobStatus === 'running'}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-[#00FFA7]/10 border border-[#00FFA7]/20 text-[#00FFA7] hover:bg-[#00FFA7]/20 transition-colors font-medium text-sm disabled:opacity-50"
          >
            {jobStatus === 'running' ? <Loader2 size={16} className="animate-spin" /> : <Plus size={16} />}
            {jobStatus === 'running' ? t('backups.headerBtn.running') : t('backups.headerBtn.newLocal')}
          </button>
        </div>
      </div>

      {/* Status banner */}
      {jobStatus === 'done' && (
        <div className="flex items-center gap-2 px-4 py-3 mb-4 rounded-lg bg-[#00FFA7]/10 border border-[#00FFA7]/20 text-[#00FFA7] text-sm">
          <CheckCircle size={16} />
          {t('backups.statusBanner.success')}
          <button onClick={() => setJobStatus('idle')} className="ml-auto text-xs opacity-60 hover:opacity-100">{t('backups.statusBanner.dismiss')}</button>
        </div>
      )}
      {jobStatus === 'error' && (
        <div className="flex items-center gap-2 px-4 py-3 mb-4 rounded-lg bg-red-500/10 border border-red-500/20 text-red-400 text-sm">
          <AlertCircle size={16} />
          {t('backups.statusBanner.failed')}
          <button onClick={() => setJobStatus('idle')} className="ml-auto text-xs opacity-60 hover:opacity-100">{t('backups.statusBanner.dismiss')}</button>
        </div>
      )}

      {/* Backup destinations status — Local, S3, Brain Repo */}
      {config && (
        <DestinationsPanel
          config={config}
          onMilestone={handleBrainRepoMilestone}
          milestoneRunning={milestoneRunning}
          onOpenS3Config={() => setS3ConfigOpen(o => !o)}
        />
      )}

      {/* S3 env-var configuration — only shown after the user opens it
          via the S3 destination card's Configure/Manage button. */}
      {config && s3ConfigOpen && (
        <div className="mb-6">
          <S3ConfigPanel config={config} onSaved={fetchData} />
        </div>
      )}

      {/* Tabs bar */}
      {!loading && (
        <div className="flex items-center gap-1 mb-4 border-b border-[#152030]">
          <TabButton
            active={activeTab === 'local'}
            onClick={() => setActiveTab('local')}
            icon={<HardDrive size={14} />}
            label={t('backups.tabs.local')}
            count={backups.length}
          />
          <TabButton
            active={activeTab === 's3'}
            onClick={() => setActiveTab('s3')}
            icon={<Cloud size={14} />}
            label={t('backups.tabs.s3')}
            count={config?.s3_configured ? s3Backups.length : undefined}
            dimmed={!config?.s3_configured}
          />
          <TabButton
            active={activeTab === 'brain'}
            onClick={() => setActiveTab('brain')}
            icon={<GitBranch size={14} />}
            label={t('backups.tabs.brainRepo')}
            count={brainSnapshots ? brainCount(brainSnapshots) : undefined}
            dimmed={!config?.brain_repo_configured}
          />
          {activeTab === 's3' && config?.s3_configured && (
            <button
              onClick={fetchS3}
              disabled={s3Loading}
              className="ml-auto mb-2 p-1.5 rounded-lg border border-[#152030] text-[#667085] hover:text-[#e2e8f0] hover:border-[#1e2a3a] transition-colors disabled:opacity-50"
              title="Refresh"
            >
              <RefreshCw size={13} className={s3Loading ? 'animate-spin' : ''} />
            </button>
          )}
          {activeTab === 'brain' && config?.brain_repo_configured && (
            <button
              onClick={fetchBrainSnapshots}
              disabled={brainLoading}
              className="ml-auto mb-2 p-1.5 rounded-lg border border-[#152030] text-[#667085] hover:text-[#e2e8f0] hover:border-[#1e2a3a] transition-colors disabled:opacity-50"
              title="Refresh"
            >
              <RefreshCw size={13} className={brainLoading ? 'animate-spin' : ''} />
            </button>
          )}
        </div>
      )}

      {/* Loading skeleton */}
      {loading && (
        <div className="space-y-3">
          {[...Array(3)].map((_, i) => (
            <div key={i} className="skeleton h-20 rounded-xl" />
          ))}
        </div>
      )}

      {/* Local tab content */}
      {!loading && activeTab === 'local' && (
        <>
          {backups.length === 0 ? (
            <div className="rounded-xl border border-[#152030] bg-[#0b1018] flex flex-col items-center justify-center py-16 text-[#667085]">
              <FileArchive size={48} className="mb-4 opacity-40" />
              <p className="text-sm text-[#8a9aae]">{t('backups.empty.title')}</p>
              <p className="text-xs mt-1">{t('backups.empty.hint')}</p>
            </div>
          ) : (
            <div className="bg-[#0b1018] border border-[#152030] rounded-xl overflow-hidden">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-[#152030] text-[#5a6b7f] text-[11px] uppercase tracking-wider">
                    <th className="text-left px-4 py-3 font-medium">{t('backups.table.backup')}</th>
                    <th className="text-left px-4 py-3 font-medium hidden sm:table-cell">{t('backups.table.version')}</th>
                    <th className="text-right px-4 py-3 font-medium hidden sm:table-cell">{t('backups.table.files')}</th>
                    <th className="text-right px-4 py-3 font-medium">{t('backups.table.size')}</th>
                    <th className="text-left px-4 py-3 font-medium">{t('backups.table.date')}</th>
                    <th className="text-right px-4 py-3 font-medium">{t('backups.table.actions')}</th>
                  </tr>
                </thead>
                <tbody>
                  {backups.map((b) => (
                    <tr key={b.filename} className="border-b border-[#152030] last:border-0 hover:bg-[#0f1520]/60 transition-colors">
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-2">
                          <FileArchive size={16} className="text-[#00FFA7] shrink-0" />
                          <span className="text-[#e6edf3] font-mono text-xs truncate max-w-[200px] lg:max-w-none">
                            {b.filename}
                          </span>
                        </div>
                      </td>
                      <td className="px-4 py-3 text-[#D0D5DD] hidden sm:table-cell">{b.manifest?.version || '-'}</td>
                      <td className="px-4 py-3 text-right text-[#D0D5DD] hidden sm:table-cell">{b.manifest?.file_count?.toLocaleString() || '-'}</td>
                      <td className="px-4 py-3 text-right text-[#D0D5DD]">{formatSize(b.size)}</td>
                      <td className="px-4 py-3 text-[#667085]">{formatDate(b.modified)}</td>
                      <td className="px-4 py-3">
                        <div className="flex items-center justify-end gap-1">
                          <button
                            onClick={() => handleDownload(b.filename)}
                            className="p-1.5 rounded-lg text-[#667085] hover:text-[#00FFA7] hover:bg-[#00FFA7]/10 transition-colors"
                            title={t('backups.action.download')}
                          ><Download size={14} /></button>
                          <button
                            onClick={() => { setShowRestoreModal(b.filename); setRestoreMode('merge') }}
                            className="p-1.5 rounded-lg text-[#667085] hover:text-blue-400 hover:bg-blue-400/10 transition-colors"
                            title={t('backups.action.restoreBtn')}
                          ><RotateCcw size={14} /></button>
                          <button
                            onClick={() => handleDelete(b.filename)}
                            className="p-1.5 rounded-lg text-[#667085] hover:text-red-400 hover:bg-red-400/10 transition-colors"
                            title={t('backups.action.delete')}
                          ><Trash2 size={14} /></button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}

      {/* S3 tab content */}
      {!loading && activeTab === 's3' && (
        <>
          {!config?.s3_configured ? (
            <div className="rounded-xl border border-[#152030] bg-[#0b1018] flex flex-col items-center justify-center py-16 text-[#667085]">
              <Cloud size={48} className="mb-4 opacity-40" />
              <p className="text-sm text-[#8a9aae]">{t('backups.s3Tab.notConfiguredTitle')}</p>
              <p className="text-xs mt-1">{t('backups.s3Tab.notConfiguredHint')}</p>
              <button
                onClick={() => setS3ConfigOpen(true)}
                className="mt-4 px-4 py-2 rounded-lg bg-[#00FFA7]/10 text-[#00FFA7] border border-[#00FFA7]/20 hover:bg-[#00FFA7]/20 text-sm font-medium transition-colors"
              >{t('backups.destinations.configure')}</button>
            </div>
          ) : s3Error && !s3Loading && s3Backups.length === 0 ? (
            <div className="flex items-center gap-2 px-4 py-3 rounded-lg bg-yellow-500/10 border border-yellow-500/20 text-yellow-400 text-xs">
              <AlertCircle size={14} />{s3Error}
            </div>
          ) : !s3Loading && s3Backups.length === 0 ? (
            <div className="rounded-xl border border-[#152030] bg-[#0b1018] flex flex-col items-center justify-center py-16 text-[#667085]">
              <Cloud size={48} className="mb-4 opacity-40" />
              <p className="text-sm text-[#8a9aae]">{t('backups.s3List.empty')}</p>
            </div>
          ) : (
            <div className="bg-[#0b1018] border border-[#152030] rounded-xl overflow-hidden">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-[#152030] text-[#5a6b7f] text-[11px] uppercase tracking-wider">
                    <th className="text-left px-4 py-3 font-medium">{t('backups.table.backup')}</th>
                    <th className="text-right px-4 py-3 font-medium">{t('backups.table.size')}</th>
                    <th className="text-left px-4 py-3 font-medium">{t('backups.table.date')}</th>
                    <th className="text-right px-4 py-3 font-medium">{t('backups.table.actions')}</th>
                  </tr>
                </thead>
                <tbody>
                  {s3Backups.map((b) => (
                    <tr key={b.key} className="border-b border-[#152030] last:border-0 hover:bg-[#0f1520]/60 transition-colors">
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-2">
                          <Cloud size={14} className="text-blue-400 shrink-0" />
                          <span className="text-[#e6edf3] font-mono text-xs truncate max-w-[250px] lg:max-w-none">{b.filename}</span>
                        </div>
                      </td>
                      <td className="px-4 py-3 text-right text-[#D0D5DD]">{formatSize(b.size)}</td>
                      <td className="px-4 py-3 text-[#667085]">{formatDate(b.modified)}</td>
                      <td className="px-4 py-3">
                        <div className="flex items-center justify-end gap-1">
                          <button
                            onClick={() => {
                              const base = import.meta.env.DEV ? 'http://localhost:8080' : ''
                              window.open(`${base}/api/backups/s3/${encodeURIComponent(b.key)}/download`, '_blank')
                            }}
                            className="p-1.5 rounded-lg text-[#667085] hover:text-[#00FFA7] hover:bg-[#00FFA7]/10 transition-colors"
                            title={t('backups.action.downloadFromS3')}
                          ><Download size={14} /></button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}

      {/* Brain Repo tab content */}
      {!loading && activeTab === 'brain' && (
        <>
          {!config?.brain_repo_configured ? (
            <div className="rounded-xl border border-[#152030] bg-[#0b1018] flex flex-col items-center justify-center py-16 text-[#667085]">
              <GitBranch size={48} className="mb-4 opacity-40" />
              <p className="text-sm text-[#8a9aae]">{t('backups.brainTab.notConnectedTitle')}</p>
              <p className="text-xs mt-1">{t('backups.brainTab.notConnectedHint')}</p>
              <Link
                to="/onboarding"
                className="mt-4 px-4 py-2 rounded-lg bg-[#00FFA7]/10 text-[#00FFA7] border border-[#00FFA7]/20 hover:bg-[#00FFA7]/20 text-sm font-medium transition-colors"
              >{t('backups.destinations.configure')}</Link>
            </div>
          ) : brainLoading ? (
            <div className="flex items-center justify-center py-16">
              <Loader2 size={24} className="animate-spin text-[#5a6b7f]" />
            </div>
          ) : brainError ? (
            <div className="flex items-center gap-2 px-4 py-3 rounded-lg bg-red-500/10 border border-red-500/20 text-red-400 text-xs">
              <AlertCircle size={14} />{brainError}
            </div>
          ) : (
            <BrainRepoSnapshots
              data={brainSnapshots}
              repoUrl={config.brain_repo?.repo_url}
              onRestore={(snapshot) => {
                setBrainRestoreModal(snapshot)
                setBrainRestoreIncludeKb(false)
                setBrainRestoreKeyMatches(false)
                setBrainRestoreProgress({ running: false, progress: 0, message: '', error: false })
              }}
            />
          )}
        </>
      )}

      {/* Brain-repo restore modal */}
      {brainRestoreModal && (
        <div className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-center justify-center p-4"
          onClick={() => !brainRestoreProgress.running && setBrainRestoreModal(null)}>
          <div className="bg-[#0b1018] border border-[#152030] rounded-xl w-full max-w-md p-6 shadow-[0_4px_40px_rgba(0,0,0,0.4)]"
            onClick={e => e.stopPropagation()}>
            <div className="flex items-center gap-3 mb-3">
              <div className="w-9 h-9 rounded-xl bg-[#00FFA7]/10 border border-[#00FFA7]/20 flex items-center justify-center">
                <GitBranch size={16} className="text-[#00FFA7]" />
              </div>
              <div>
                <h2 className="text-[16px] font-semibold text-[#e2e8f0]">{t('backups.brainRestore.title')}</h2>
                <p className="text-[11px] text-[#5a6b7f] mt-0.5 font-mono truncate">
                  {brainRestoreModal.label || (brainRestoreModal.ref ?? '').replace(/^refs\/tags\//, '') || '(unnamed)'}
                </p>
              </div>
            </div>

            {!brainRestoreProgress.running && !brainRestoreProgress.error && brainRestoreProgress.progress < 100 && (
              <>
                <div className="p-3 rounded-lg bg-[#1a1400] border border-[#3a3015] mb-4">
                  <p className="text-[11px] text-[#b89070] leading-relaxed">
                    <AlertTriangle size={12} className="inline mr-1 mb-0.5 text-[#F59E0B]" />
                    {t('backups.brainRestore.warningNoDb')}
                  </p>
                </div>

                <label className="flex items-start gap-3 p-3 rounded-lg border border-[#152030] mb-2 cursor-pointer hover:border-[#1e2a3a] transition-colors">
                  <input
                    type="checkbox"
                    checked={brainRestoreIncludeKb}
                    onChange={e => setBrainRestoreIncludeKb(e.target.checked)}
                    className="mt-0.5 accent-[#00FFA7]"
                  />
                  <div>
                    <div className="text-[12px] text-[#e2e8f0] font-medium">{t('backups.brainRestore.includeKb')}</div>
                    <div className="text-[10px] text-[#5a6b7f] mt-0.5">{t('backups.brainRestore.includeKbDesc')}</div>
                  </div>
                </label>

                {brainRestoreIncludeKb && (
                  <label className="flex items-start gap-3 p-3 rounded-lg border border-[#152030] mb-4 cursor-pointer hover:border-[#1e2a3a] transition-colors">
                    <input
                      type="checkbox"
                      checked={brainRestoreKeyMatches}
                      onChange={e => setBrainRestoreKeyMatches(e.target.checked)}
                      className="mt-0.5 accent-[#00FFA7]"
                    />
                    <div>
                      <div className="text-[12px] text-[#e2e8f0] font-medium">{t('backups.brainRestore.keyMatches')}</div>
                      <div className="text-[10px] text-[#5a6b7f] mt-0.5">{t('backups.brainRestore.keyMatchesDesc')}</div>
                    </div>
                  </label>
                )}

                <div className="flex justify-end gap-2 mt-2">
                  <button
                    onClick={() => setBrainRestoreModal(null)}
                    className="px-4 py-2 rounded-lg border border-[#152030] text-[#5a6b7f] text-sm hover:text-[#e2e8f0] hover:border-[#1e2a3a] transition-colors"
                  >{t('backups.modal.cancel')}</button>
                  <button
                    onClick={() => runBrainRestore(brainRestoreModal)}
                    className="flex items-center gap-2 px-4 py-2 rounded-lg bg-[#00FFA7] text-[#080c14] hover:bg-[#00e69a] text-sm font-semibold transition-colors"
                  >
                    <RotateCcw size={14} />
                    {t('backups.brainRestore.btn')}
                  </button>
                </div>
              </>
            )}

            {(brainRestoreProgress.running || brainRestoreProgress.error || brainRestoreProgress.progress >= 100) && (
              <div className="space-y-3 mt-2">
                <div>
                  <div className="flex items-center justify-between mb-1.5">
                    <span className="text-[11px] text-[#5a6b7f]">{brainRestoreProgress.message}</span>
                    <span className="text-[11px] text-[#5a6b7f]">{brainRestoreProgress.progress}%</span>
                  </div>
                  <div className="h-1.5 rounded-full bg-[#152030] overflow-hidden">
                    <div
                      className="h-full rounded-full transition-all duration-300"
                      style={{
                        width: `${brainRestoreProgress.progress}%`,
                        backgroundColor: brainRestoreProgress.error ? '#ef4444' : '#00FFA7',
                      }}
                    />
                  </div>
                </div>
                {!brainRestoreProgress.running && (
                  <button
                    onClick={() => {
                      setBrainRestoreModal(null)
                      setBrainRestoreProgress({ running: false, progress: 0, message: '', error: false })
                    }}
                    className="w-full py-2 rounded-lg border border-[#152030] text-[#5a6b7f] text-sm hover:text-[#e2e8f0] hover:border-[#1e2a3a] transition-colors"
                  >{t('common.close')}</button>
                )}
              </div>
            )}
          </div>
        </div>
      )}

      {/* Restore modal */}
      {showRestoreModal && (
        <div className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-center justify-center p-4" onClick={() => setShowRestoreModal(null)}>
          <div className="bg-[#0b1018] border border-[#152030] rounded-xl w-full max-w-md p-6 shadow-[0_4px_40px_rgba(0,0,0,0.4)]" onClick={(e) => e.stopPropagation()}>
            <h2 className="text-lg font-semibold text-[#e2e8f0] mb-1">{t('backups.modal.title')}</h2>
            <p className="text-xs text-[#5a6b7f] mb-5 font-mono truncate">{showRestoreModal}</p>

            <div className="space-y-2 mb-6">
              <label className={`flex items-start gap-3 p-3 rounded-lg border cursor-pointer transition-colors ${
                restoreMode === 'merge'
                  ? 'border-[#00FFA7]/40 bg-[#00FFA7]/5'
                  : 'border-[#152030] hover:border-[#1e2a3a]'
              }`}>
                <input
                  type="radio"
                  name="mode"
                  checked={restoreMode === 'merge'}
                  onChange={() => setRestoreMode('merge')}
                  className="mt-0.5 accent-[#00FFA7]"
                />
                <div>
                  <div className="text-sm font-medium text-[#e2e8f0]">{t('backups.modal.merge')}</div>
                  <div className="text-[11px] text-[#5a6b7f] mt-0.5">{t('backups.modal.mergeDesc')}</div>
                </div>
              </label>
              <label className={`flex items-start gap-3 p-3 rounded-lg border cursor-pointer transition-colors ${
                restoreMode === 'replace'
                  ? 'border-[#3a1515] bg-[#1a0a0a]/40'
                  : 'border-[#152030] hover:border-[#1e2a3a]'
              }`}>
                <input
                  type="radio"
                  name="mode"
                  checked={restoreMode === 'replace'}
                  onChange={() => setRestoreMode('replace')}
                  className="mt-0.5 accent-[#f87171]"
                />
                <div>
                  <div className="text-sm font-medium text-[#e2e8f0]">{t('backups.modal.replace')}</div>
                  <div className="text-[11px] text-[#5a6b7f] mt-0.5">{t('backups.modal.replaceDesc')}</div>
                </div>
              </label>
            </div>

            <div className="flex justify-end gap-2">
              <button
                onClick={() => setShowRestoreModal(null)}
                className="px-4 py-2 rounded-lg border border-[#152030] text-[#5a6b7f] text-sm hover:text-[#e2e8f0] hover:border-[#1e2a3a] transition-colors"
              >
                {t('backups.modal.cancel')}
              </button>
              <button
                onClick={() => handleRestore(showRestoreModal)}
                className="flex items-center gap-2 px-4 py-2 rounded-lg bg-[#00FFA7]/10 border border-[#00FFA7]/20 text-[#00FFA7] hover:bg-[#00FFA7]/20 transition-colors font-medium text-sm"
              >
                <RotateCcw size={14} />
                {t('backups.modal.restoreBtn')} ({restoreMode === 'merge' ? t('backups.modal.merge') : t('backups.modal.replace')})
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
