import { useState, useRef, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { X, Link2, Eye, Download, CheckCircle, AlertTriangle, Loader2, Lock, Upload, ChevronDown, ChevronUp, Terminal } from 'lucide-react'
import { api } from '../lib/api'
import SecurityScanSection, { type ScanVerdict, type ScanResult } from './SecurityScanSection'

interface PreviewResult {
  manifest: Record<string, unknown>
  warnings: string[]
  // Backend returns conflicts as a list of human-readable strings (not a dict).
  // See plugin_loader.PluginInstaller.preview() — each blocker is a string
  // appended to result["conflicts"].
  conflicts?: string[]
}

interface Props {
  onClose: () => void
  onInstalled: () => void
}

type Step = 1 | 2 | 3

export default function PluginInstallModal({ onClose, onInstalled }: Props) {
  const { t } = useTranslation()
  const [step, setStep] = useState<Step>(1)
  const [sourceUrl, setSourceUrl] = useState('')
  const [authToken, setAuthToken] = useState('')
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [uploadedPath, setUploadedPath] = useState<string | null>(null)
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  const [preview, setPreview] = useState<PreviewResult | null>(null)
  const [loadingPreview, setLoadingPreview] = useState(false)
  const [installing, setInstalling] = useState(false)
  const [previewError, setPreviewError] = useState<string | null>(null)
  const [installError, setInstallError] = useState<string | null>(null)
  const [installedSlug, setInstalledSlug] = useState<string | null>(null)

  // Wave 2.3 — MCP servers installed tracking
  const [mcpServersInstalled, setMcpServersInstalled] = useState<Array<{ effective_name: string }>>([])

  // Wave 2.5 — security scan gate state
  const [scanVerdict, setScanVerdict] = useState<ScanVerdict | null>(null)
  const [, setScanResult] = useState<ScanResult | null>(null)
  const [overrideReason, setOverrideReason] = useState('')
  const [warnConfirmed, setWarnConfirmed] = useState(false)

  // Effective source: uploaded staged path wins over URL input
  const effectiveSource = () => (uploadedPath ?? sourceUrl.trim())
  const canPreview = effectiveSource().length > 0 && !loadingPreview

  const handleScanVerdict = useCallback(
    (verdict: ScanVerdict | null, result: ScanResult | null) => {
      setScanVerdict(verdict)
      setScanResult(result)
    },
    []
  )

  const handleOverride = useCallback((reason: string) => {
    setOverrideReason(reason)
  }, [])

  // Scan gate logic (mirrors UpdatePreviewModal):
  // null = scan not yet completed (wait) | APPROVE = pass | WARN+confirmed = pass |
  // BLOCK+overrideReason(≥20) = admin pass
  const scanGatePassed =
    scanVerdict === null
      ? false // still scanning
      : scanVerdict === 'APPROVE'
        ? true
        : scanVerdict === 'WARN'
          ? warnConfirmed
          : /* BLOCK */ overrideReason.trim().length >= 20

  async function handleFileSelect(file: File) {
    setPreviewError(null)
    setUploading(true)
    try {
      const form = new FormData()
      form.append('file', file)
      const resp = await fetch('/api/plugins/upload', { method: 'POST', body: form, credentials: 'include' })
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}))
        throw new Error(err.message ?? err.error ?? `upload failed (${resp.status})`)
      }
      const result = await resp.json() as { source_path: string }
      setUploadedPath(result.source_path)
      setSourceUrl('')
    } catch (e: unknown) {
      setPreviewError(e instanceof Error ? e.message : t('common.unexpectedError'))
    } finally {
      setUploading(false)
    }
  }

  async function handlePreview() {
    const src = effectiveSource()
    if (!src) return
    setLoadingPreview(true)
    setPreviewError(null)
    // Reset scan state when re-previewing a different source
    setScanVerdict(null)
    setScanResult(null)
    setOverrideReason('')
    setWarnConfirmed(false)
    try {
      const body: Record<string, string> = { source_url: src }
      if (authToken.trim()) body.auth_token = authToken.trim()
      const result = await api.post('/plugins/preview', body) as PreviewResult
      setPreview(result)
      setStep(2)
    } catch (e: unknown) {
      setPreviewError(e instanceof Error ? e.message : t('common.unexpectedError'))
    } finally {
      setLoadingPreview(false)
    }
  }

  async function handleInstall() {
    const src = effectiveSource()
    if (!src) return
    setInstalling(true)
    setInstallError(null)
    try {
      const body: Record<string, unknown> = { source_url: src }
      if (authToken.trim()) body.auth_token = authToken.trim()
      // Wave 2.5 — propagate scan gate state to backend
      if (scanVerdict === 'WARN' && warnConfirmed) body.confirmed_verdict = 'WARN'
      if (scanVerdict === 'BLOCK' && overrideReason.trim().length >= 20) {
        body.override_reason = overrideReason.trim()
      }
      const result = await api.post('/plugins/install', body) as { slug: string; mcp_servers_installed?: Array<{ effective_name: string }> }
      setInstalledSlug(result.slug)
      setMcpServersInstalled(result.mcp_servers_installed ?? [])
      setStep(3)
    } catch (e: unknown) {
      setInstallError(e instanceof Error ? e.message : t('common.unexpectedError'))
    } finally {
      setInstalling(false)
    }
  }

  const manifest = preview?.manifest ?? {}
  const warnings = preview?.warnings ?? []
  const conflicts: string[] = Array.isArray(preview?.conflicts)
    ? (preview!.conflicts as string[]).filter((c): c is string => typeof c === 'string' && c.length > 0)
    : []

  // Install button is amber for WARN, normal green otherwise
  const installBtnClass =
    scanVerdict === 'WARN' && warnConfirmed
      ? 'flex items-center gap-2 px-4 py-2 text-sm font-medium bg-amber-400 text-black rounded-lg hover:bg-amber-300 disabled:opacity-50 disabled:cursor-not-allowed transition-colors'
      : 'flex items-center gap-2 px-4 py-2 text-sm font-medium bg-[#00FFA7] text-black rounded-lg hover:bg-[#00FFA7]/90 disabled:opacity-50 disabled:cursor-not-allowed transition-colors'

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div className="bg-[#161b22] border border-[#344054] rounded-2xl w-full max-w-lg shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-[#21262d]">
          <div>
            <h2 className="text-base font-semibold text-[#e6edf3]">{t('plugins.installPlugin')}</h2>
            <p className="text-xs text-[#667085] mt-0.5">{t('plugins.stepOf', { current: step, total: 3 })}</p>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 rounded-lg text-[#667085] hover:text-[#D0D5DD] hover:bg-white/5 transition-colors"
          >
            <X size={16} />
          </button>
        </div>

        {/* Step indicators */}
        <div className="flex items-center gap-2 px-6 py-3 border-b border-[#21262d]">
          {([1, 2, 3] as Step[]).map((s) => (
            <div key={s} className="flex items-center gap-2">
              <div className={`w-6 h-6 rounded-full flex items-center justify-center text-xs font-bold transition-colors ${
                s < step ? 'bg-[#00FFA7] text-black' :
                s === step ? 'bg-[#00FFA7]/20 text-[#00FFA7] border border-[#00FFA7]/40' :
                'bg-[#21262d] text-[#667085]'
              }`}>
                {s < step ? <CheckCircle size={12} /> : s}
              </div>
              {s < 3 && <div className={`flex-1 h-px w-8 ${s < step ? 'bg-[#00FFA7]/40' : 'bg-[#21262d]'}`} />}
            </div>
          ))}
        </div>

        {/* Body */}
        <div className="px-6 py-5 max-h-[70vh] overflow-y-auto">
          {/* Step 1: URL input */}
          {step === 1 && (
            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-[#D0D5DD] mb-2 flex items-center gap-2">
                  <Link2 size={14} className="text-[#00FFA7]" />
                  {t('plugins.sourceUrl')}
                </label>
                <input
                  type="text"
                  value={sourceUrl}
                  onChange={(e) => { setSourceUrl(e.target.value); setUploadedPath(null) }}
                  placeholder="github:org/plugin-name or https://..."
                  className="w-full bg-[#0C111D] border border-[#344054] rounded-lg px-3 py-2.5 text-sm text-[#e6edf3] placeholder-[#667085] focus:outline-none focus:border-[#00FFA7]/50 transition-colors"
                  onKeyDown={(e) => { if (e.key === 'Enter') handlePreview() }}
                />
                <p className="mt-2 text-xs text-[#667085]">Formatos: github:owner/repo[@ref] · https://…/arquivo.tar.gz</p>
              </div>

              {/* Upload alternative */}
              <div className="relative">
                <div className="flex items-center gap-3 my-1">
                  <div className="flex-1 h-px bg-[#21262d]" />
                  <span className="text-[10px] text-[#667085] uppercase tracking-wider">ou</span>
                  <div className="flex-1 h-px bg-[#21262d]" />
                </div>
                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".zip,.tar.gz,.tgz"
                  className="hidden"
                  onChange={(e) => {
                    const f = e.target.files?.[0]
                    if (f) handleFileSelect(f)
                  }}
                />
                <button
                  onClick={() => fileInputRef.current?.click()}
                  disabled={uploading}
                  className="w-full flex items-center justify-center gap-2 px-3 py-2.5 border border-dashed border-[#344054] rounded-lg text-sm text-[#D0D5DD] hover:border-[#00FFA7]/40 hover:bg-[#00FFA7]/5 disabled:opacity-50 transition-colors"
                >
                  {uploading ? <Loader2 size={14} className="animate-spin" /> : <Upload size={14} />}
                  {uploadedPath ? 'Trocar arquivo' : 'Selecionar arquivo (.zip ou .tar.gz)'}
                </button>
                {uploadedPath && (
                  <p className="mt-2 text-xs text-[#00FFA7] flex items-center gap-1.5">
                    <CheckCircle size={12} /> Arquivo pronto — clique em Visualizar
                  </p>
                )}
              </div>

              {/* Advanced options (auth token) */}
              <div>
                <button
                  onClick={() => setShowAdvanced(!showAdvanced)}
                  className="flex items-center gap-1.5 text-xs text-[#667085] hover:text-[#D0D5DD] transition-colors"
                >
                  {showAdvanced ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
                  Opções avançadas
                </button>
                {showAdvanced && (
                  <div className="mt-3">
                    <label className="block text-xs font-medium text-[#D0D5DD] mb-1.5 flex items-center gap-1.5">
                      <Lock size={12} className="text-[#00FFA7]" />
                      Personal Access Token (repos privados)
                    </label>
                    <input
                      type="password"
                      value={authToken}
                      onChange={(e) => setAuthToken(e.target.value)}
                      placeholder="ghp_..."
                      className="w-full bg-[#0C111D] border border-[#344054] rounded-lg px-3 py-2 text-xs text-[#e6edf3] placeholder-[#667085] focus:outline-none focus:border-[#00FFA7]/50 transition-colors"
                      autoComplete="off"
                    />
                    <p className="mt-1 text-[10px] text-[#667085]">Usado apenas para baixar o arquivo; não é armazenado.</p>
                  </div>
                )}
              </div>

              {previewError && (
                <p className="text-xs text-red-400 flex items-center gap-1.5">
                  <AlertTriangle size={12} /> {previewError}
                </p>
              )}
            </div>
          )}

          {/* Step 2: Security scan + manifest preview */}
          {step === 2 && preview && (
            <div className="space-y-4">
              {/* Wave 2.5 — Security scan gate (runs automatically on mount) */}
              <SecurityScanSection
                sourceUrl={effectiveSource()}
                authToken={authToken.trim() || undefined}
                onVerdict={handleScanVerdict}
                onOverride={handleOverride}
              />

              {/* WARN confirmation checkbox */}
              {scanVerdict === 'WARN' && !warnConfirmed && (
                <label className="flex items-start gap-2 cursor-pointer select-none">
                  <input
                    type="checkbox"
                    checked={warnConfirmed}
                    onChange={(e) => setWarnConfirmed(e.target.checked)}
                    className="mt-0.5 accent-amber-400"
                  />
                  <span className="text-xs text-amber-300">
                    Entendo os riscos apontados acima e desejo prosseguir com a instalação.
                  </span>
                </label>
              )}

              {/* Manifest preview */}
              <div className="bg-[#0C111D] border border-[#21262d] rounded-xl p-4">
                <div className="flex items-center gap-2 mb-3">
                  <Eye size={14} className="text-[#00FFA7]" />
                  <span className="text-sm font-medium text-[#e6edf3]">{t('plugins.manifestPreview')}</span>
                </div>
                <dl className="space-y-1.5 text-xs">
                  {['name', 'version', 'author', 'license', 'description'].map((k) =>
                    manifest[k] ? (
                      <div key={k} className="flex gap-2">
                        <dt className="text-[#667085] capitalize w-20 shrink-0">{k}</dt>
                        <dd className="text-[#e6edf3] break-all">{String(manifest[k])}</dd>
                      </div>
                    ) : null
                  )}
                  {Array.isArray(manifest['capabilities']) && (manifest['capabilities'] as string[]).length > 0 && (
                    <div className="flex gap-2">
                      <dt className="text-[#667085] capitalize w-20 shrink-0">capabilities</dt>
                      <dd className="text-[#e6edf3]">{(manifest['capabilities'] as string[]).join(', ')}</dd>
                    </div>
                  )}
                </dl>
              </div>

              {warnings.length > 0 && (
                <div className="bg-yellow-500/5 border border-yellow-500/20 rounded-xl p-3">
                  <p className="text-xs font-medium text-yellow-400 mb-1.5 flex items-center gap-1.5">
                    <AlertTriangle size={12} /> {t('plugins.warnings')} ({warnings.length})
                  </p>
                  <ul className="space-y-1">
                    {warnings.map((w, i) => (
                      <li key={i} className="text-xs text-yellow-300/80">{w}</li>
                    ))}
                  </ul>
                </div>
              )}

              {conflicts.length > 0 && (
                <div className="bg-red-500/5 border border-red-500/20 rounded-xl p-3">
                  <p className="text-xs font-medium text-red-400 mb-1 flex items-center gap-1.5">
                    <AlertTriangle size={12} /> {t('plugins.conflicts')}
                  </p>
                  <ul className="space-y-0.5 list-disc list-inside">
                    {conflicts.map((c, i) => (
                      <li key={i} className="text-xs text-red-300/80">{c}</li>
                    ))}
                  </ul>
                </div>
              )}

              {installError && (
                <p className="text-xs text-red-400 flex items-center gap-1.5">
                  <AlertTriangle size={12} /> {installError}
                </p>
              )}
            </div>
          )}

          {/* Step 3: Done */}
          {step === 3 && (
            <div className="py-4 space-y-4">
              <div className="text-center">
                <div className="flex items-center justify-center w-14 h-14 rounded-full bg-[#00FFA7]/10 border border-[#00FFA7]/20 mx-auto mb-4">
                  <CheckCircle size={28} className="text-[#00FFA7]" />
                </div>
                <h3 className="text-base font-semibold text-[#e6edf3] mb-1">{t('plugins.installedSuccessTitle')}</h3>
                <p className="text-sm text-[#667085]">
                  {installedSlug && <code className="text-[#00FFA7]">{installedSlug}</code>} {t('plugins.installedDesc')}
                </p>
              </div>

              {/* Wave 2.3 — MCP restart notice */}
              {mcpServersInstalled.length > 0 && (
                <div className="bg-blue-500/5 border border-blue-500/20 rounded-xl p-4">
                  <div className="flex items-start gap-3">
                    <Terminal size={16} className="text-blue-400 mt-0.5 shrink-0" />
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium text-blue-300 mb-1">
                        Restart Claude Code CLI para ativar os MCP servers
                      </p>
                      <p className="text-xs text-blue-400/80 mb-2">
                        {mcpServersInstalled.length} MCP server{mcpServersInstalled.length > 1 ? 's' : ''} instalado{mcpServersInstalled.length > 1 ? 's' : ''}: {mcpServersInstalled.map(s => s.effective_name).join(', ')}
                      </p>
                      <p className="text-xs text-blue-400/60">
                        Cmd+Q no Claude Code e reabra o aplicativo.
                      </p>
                    </div>
                  </div>
                </div>
              )}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-3 px-6 py-4 border-t border-[#21262d]">
          {step < 3 && (
            <button
              onClick={onClose}
              className="px-4 py-2 text-sm text-[#667085] hover:text-[#D0D5DD] transition-colors"
            >
              {t('common.cancel')}
            </button>
          )}

          {step === 1 && (
            <button
              onClick={handlePreview}
              disabled={!canPreview}
              className="flex items-center gap-2 px-4 py-2 text-sm font-medium bg-[#00FFA7] text-black rounded-lg hover:bg-[#00FFA7]/90 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              {loadingPreview ? <Loader2 size={14} className="animate-spin" /> : <Eye size={14} />}
              {t('plugins.preview')}
            </button>
          )}

          {step === 2 && (
            <>
              <button
                onClick={() => setStep(1)}
                className="px-4 py-2 text-sm text-[#667085] hover:text-[#D0D5DD] transition-colors"
              >
                {t('common.back')}
              </button>
              <button
                onClick={handleInstall}
                disabled={installing || conflicts.length > 0 || !scanGatePassed}
                className={installBtnClass}
              >
                {installing ? <Loader2 size={14} className="animate-spin" /> : <Download size={14} />}
                {t('plugins.confirmInstall')}
              </button>
            </>
          )}

          {step === 3 && (
            <button
              onClick={() => { onInstalled(); onClose() }}
              className="px-4 py-2 text-sm font-medium bg-[#00FFA7] text-black rounded-lg hover:bg-[#00FFA7]/90 transition-colors"
            >
              {t('common.close')}
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
