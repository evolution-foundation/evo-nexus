import { useState, useEffect, useCallback } from 'react'
import {
  HardDriveDownload, Plus, Download, RotateCcw, Trash2, RefreshCw,
  Cloud, HardDrive, AlertCircle, CheckCircle, Loader2, FileArchive,
  ChevronDown, Eye, EyeOff, Save, Upload,
} from 'lucide-react'
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
  const [expanded, setExpanded] = useState(false)
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
      alert('Erro ao salvar')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="mb-4 rounded-xl border border-[#21262d] bg-[#161b22] overflow-hidden">
      {/* Header — always visible */}
      <button
        onClick={() => setExpanded(prev => !prev)}
        className="w-full flex items-center justify-between px-4 py-3 hover:bg-[#0d1117]/50 transition-colors"
      >
        <div className="flex items-center gap-3 text-sm">
          <Cloud size={16} className={config.s3_configured ? 'text-[#00FFA7]' : 'text-[#667085]'} />
          <span className="text-[#e6edf3] font-medium">Storage Provider</span>
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
        <div className="px-4 pb-4 border-t border-[#21262d]">
          <p className="text-xs text-[#667085] mt-3 mb-4">
            Configure S3 para backup remoto. Compatível com AWS S3, Cloudflare R2, Backblaze B2, MinIO e qualquer storage S3-compatível.
            Para providers não-AWS, preencha o <strong>Endpoint URL</strong>. Deixe tudo vazio para usar apenas backup local.
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

export default function Backups() {
  const { t } = useTranslation()
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
    if (!confirm(`Delete ${filename}?`)) return
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
      alert('Apenas arquivos .zip são aceitos')
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
        alert(data.error || 'Erro ao importar backup')
        return
      }
      fetchData()
    } catch {
      alert('Erro ao importar backup')
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
            <p className="text-sm text-[#667085]">Export and restore workspace data</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => fetchData()}
            className="p-2 rounded-lg border border-[#21262d] text-[#667085] hover:text-[#e6edf3] hover:border-[#344054] transition-colors"
          >
            <RefreshCw size={16} />
          </button>
          <button
            onClick={() => uploadRef.current?.click()}
            disabled={uploading}
            className="flex items-center gap-2 px-4 py-2 rounded-lg border border-[#21262d] text-[#D0D5DD] hover:bg-[#161b22] transition-colors text-sm disabled:opacity-50"
          >
            {uploading ? <Loader2 size={16} className="animate-spin" /> : <Upload size={16} />}
            {uploading ? 'Importando...' : 'Importar'}
          </button>
          {config?.s3_configured && config?.boto3_available && (
            <button
              onClick={() => handleBackup('s3')}
              disabled={jobStatus === 'running'}
              className="flex items-center gap-2 px-4 py-2 rounded-lg border border-[#21262d] text-[#D0D5DD] hover:bg-[#161b22] transition-colors text-sm disabled:opacity-50"
            >
              <Cloud size={16} />
              Backup + S3
            </button>
          )}
          <button
            onClick={() => handleBackup('local')}
            disabled={jobStatus === 'running'}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-[#00FFA7]/10 border border-[#00FFA7]/20 text-[#00FFA7] hover:bg-[#00FFA7]/20 transition-colors font-medium text-sm disabled:opacity-50"
          >
            {jobStatus === 'running' ? <Loader2 size={16} className="animate-spin" /> : <Plus size={16} />}
            {jobStatus === 'running' ? 'Running...' : 'New Backup'}
          </button>
        </div>
      </div>

      {/* Status banner */}
      {jobStatus === 'done' && (
        <div className="flex items-center gap-2 px-4 py-3 mb-4 rounded-lg bg-[#00FFA7]/10 border border-[#00FFA7]/20 text-[#00FFA7] text-sm">
          <CheckCircle size={16} />
          Operation completed successfully.
          <button onClick={() => setJobStatus('idle')} className="ml-auto text-xs opacity-60 hover:opacity-100">dismiss</button>
        </div>
      )}
      {jobStatus === 'error' && (
        <div className="flex items-center gap-2 px-4 py-3 mb-4 rounded-lg bg-red-500/10 border border-red-500/20 text-red-400 text-sm">
          <AlertCircle size={16} />
          Operation failed. Check server logs for details.
          <button onClick={() => setJobStatus('idle')} className="ml-auto text-xs opacity-60 hover:opacity-100">dismiss</button>
        </div>
      )}

      {/* Storage provider config */}
      {config && <S3ConfigPanel config={config} onSaved={fetchData} />}

      {/* Loading */}
      {loading && (
        <div className="space-y-3">
          {[...Array(3)].map((_, i) => (
            <div key={i} className="skeleton h-20 rounded-xl" />
          ))}
        </div>
      )}

      {/* Empty state */}
      {!loading && backups.length === 0 && (
        <div className="flex flex-col items-center justify-center py-16 text-[#667085]">
          <FileArchive size={48} className="mb-4 opacity-40" />
          <p className="text-sm">No backups yet</p>
          <p className="text-xs mt-1">Click "New Backup" to export your workspace data</p>
        </div>
      )}

      {/* Backup list */}
      {!loading && backups.length > 0 && (
        <div className="bg-[#161b22] border border-[#21262d] rounded-xl overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-[#21262d] text-[#667085] text-xs">
                <th className="text-left px-4 py-3 font-medium">Backup</th>
                <th className="text-left px-4 py-3 font-medium hidden sm:table-cell">Version</th>
                <th className="text-right px-4 py-3 font-medium hidden sm:table-cell">Files</th>
                <th className="text-right px-4 py-3 font-medium">Size</th>
                <th className="text-left px-4 py-3 font-medium">Date</th>
                <th className="text-right px-4 py-3 font-medium">Actions</th>
              </tr>
            </thead>
            <tbody>
              {backups.map((b) => (
                <tr key={b.filename} className="border-b border-[#21262d] last:border-0 hover:bg-[#0d1117]/50 transition-colors">
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      <FileArchive size={16} className="text-[#00FFA7] shrink-0" />
                      <span className="text-[#e6edf3] font-mono text-xs truncate max-w-[200px] lg:max-w-none">
                        {b.filename}
                      </span>
                    </div>
                  </td>
                  <td className="px-4 py-3 text-[#D0D5DD] hidden sm:table-cell">
                    {b.manifest?.version || '-'}
                  </td>
                  <td className="px-4 py-3 text-right text-[#D0D5DD] hidden sm:table-cell">
                    {b.manifest?.file_count?.toLocaleString() || '-'}
                  </td>
                  <td className="px-4 py-3 text-right text-[#D0D5DD]">
                    {formatSize(b.size)}
                  </td>
                  <td className="px-4 py-3 text-[#667085]">
                    {formatDate(b.modified)}
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex items-center justify-end gap-1">
                      <button
                        onClick={() => handleDownload(b.filename)}
                        className="p-1.5 rounded-lg text-[#667085] hover:text-[#00FFA7] hover:bg-[#00FFA7]/10 transition-colors"
                        title="Download"
                      >
                        <Download size={14} />
                      </button>
                      <button
                        onClick={() => { setShowRestoreModal(b.filename); setRestoreMode('merge') }}
                        className="p-1.5 rounded-lg text-[#667085] hover:text-blue-400 hover:bg-blue-400/10 transition-colors"
                        title="Restore"
                      >
                        <RotateCcw size={14} />
                      </button>
                      <button
                        onClick={() => handleDelete(b.filename)}
                        className="p-1.5 rounded-lg text-[#667085] hover:text-red-400 hover:bg-red-400/10 transition-colors"
                        title="Delete"
                      >
                        <Trash2 size={14} />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* S3 Remote backups */}
      {config?.s3_configured && config?.boto3_available && (
        <div className="mt-6">
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2">
              <Cloud size={16} className="text-[#667085]" />
              <h2 className="text-sm font-medium text-[#e6edf3]">Remote Backups (S3)</h2>
              {s3Backups.length > 0 && (
                <span className="text-xs px-2 py-0.5 rounded-full bg-[#21262d] text-[#667085]">
                  {s3Backups.length}
                </span>
              )}
            </div>
            <button
              onClick={fetchS3}
              disabled={s3Loading}
              className="p-1.5 rounded-lg border border-[#21262d] text-[#667085] hover:text-[#e6edf3] hover:border-[#344054] transition-colors disabled:opacity-50"
            >
              <RefreshCw size={14} className={s3Loading ? 'animate-spin' : ''} />
            </button>
          </div>

          {s3Error && !s3Loading && s3Backups.length === 0 && (
            <div className="flex items-center gap-2 px-4 py-3 rounded-lg bg-yellow-500/10 border border-yellow-500/20 text-yellow-400 text-xs">
              <AlertCircle size={14} />
              {s3Error}
            </div>
          )}

          {!s3Loading && !s3Error && s3Backups.length === 0 && (
            <div className="text-xs text-[#667085] px-4 py-3 border border-[#21262d] rounded-lg">
              Nenhum backup encontrado no bucket.
            </div>
          )}

          {s3Backups.length > 0 && (
            <div className="bg-[#161b22] border border-[#21262d] rounded-xl overflow-hidden">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-[#21262d] text-[#667085] text-xs">
                    <th className="text-left px-4 py-3 font-medium">Backup</th>
                    <th className="text-right px-4 py-3 font-medium">Size</th>
                    <th className="text-left px-4 py-3 font-medium">Date</th>
                    <th className="text-right px-4 py-3 font-medium">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {s3Backups.map((b) => (
                    <tr key={b.key} className="border-b border-[#21262d] last:border-0 hover:bg-[#0d1117]/50 transition-colors">
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-2">
                          <Cloud size={14} className="text-blue-400 shrink-0" />
                          <span className="text-[#e6edf3] font-mono text-xs truncate max-w-[250px] lg:max-w-none">
                            {b.filename}
                          </span>
                        </div>
                      </td>
                      <td className="px-4 py-3 text-right text-[#D0D5DD]">
                        {formatSize(b.size)}
                      </td>
                      <td className="px-4 py-3 text-[#667085]">
                        {formatDate(b.modified)}
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex items-center justify-end gap-1">
                          <button
                            onClick={() => {
                              const base = import.meta.env.DEV ? 'http://localhost:8080' : ''
                              window.open(`${base}/api/backups/s3/${encodeURIComponent(b.key)}/download`, '_blank')
                            }}
                            className="p-1.5 rounded-lg text-[#667085] hover:text-[#00FFA7] hover:bg-[#00FFA7]/10 transition-colors"
                            title="Download from S3"
                          >
                            <Download size={14} />
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* Restore modal */}
      {showRestoreModal && (
        <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4" onClick={() => setShowRestoreModal(null)}>
          <div className="bg-[#161b22] border border-[#21262d] rounded-xl w-full max-w-md p-6" onClick={(e) => e.stopPropagation()}>
            <h2 className="text-lg font-semibold text-[#e6edf3] mb-1">Restore Backup</h2>
            <p className="text-sm text-[#667085] mb-4 font-mono">{showRestoreModal}</p>

            <div className="space-y-3 mb-6">
              <label className="flex items-start gap-3 p-3 rounded-lg border border-[#21262d] cursor-pointer hover:border-[#344054] transition-colors">
                <input
                  type="radio"
                  name="mode"
                  checked={restoreMode === 'merge'}
                  onChange={() => setRestoreMode('merge')}
                  className="mt-0.5 accent-[#00FFA7]"
                />
                <div>
                  <div className="text-sm font-medium text-[#e6edf3]">Merge</div>
                  <div className="text-xs text-[#667085]">Only restore files that don't exist. Existing files are preserved.</div>
                </div>
              </label>
              <label className="flex items-start gap-3 p-3 rounded-lg border border-[#21262d] cursor-pointer hover:border-[#344054] transition-colors">
                <input
                  type="radio"
                  name="mode"
                  checked={restoreMode === 'replace'}
                  onChange={() => setRestoreMode('replace')}
                  className="mt-0.5 accent-[#00FFA7]"
                />
                <div>
                  <div className="text-sm font-medium text-[#e6edf3]">Replace</div>
                  <div className="text-xs text-[#667085]">Overwrite all files with backup versions. Existing data will be replaced.</div>
                </div>
              </label>
            </div>

            <div className="flex justify-end gap-2">
              <button
                onClick={() => setShowRestoreModal(null)}
                className="px-4 py-2 rounded-lg border border-[#21262d] text-[#D0D5DD] text-sm hover:bg-[#0d1117] transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={() => handleRestore(showRestoreModal)}
                className="flex items-center gap-2 px-4 py-2 rounded-lg bg-[#00FFA7]/10 border border-[#00FFA7]/20 text-[#00FFA7] hover:bg-[#00FFA7]/20 transition-colors font-medium text-sm"
              >
                <RotateCcw size={14} />
                Restore ({restoreMode})
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
