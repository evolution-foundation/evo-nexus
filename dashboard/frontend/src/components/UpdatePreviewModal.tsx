import { useEffect, useState, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import {
  X, AlertTriangle, CheckCircle, Loader2, ChevronDown, ChevronUp, Download, Terminal,
} from 'lucide-react'
import { api } from '../lib/api'
import SecurityScanSection, { type ScanVerdict, type ScanResult } from './SecurityScanSection'

interface DiffSection {
  [capType: string]: string[]
}

interface McpDiff {
  added: string[]
  removed: string[]
  modified: string[]
}

interface PreviewResult {
  from_version: string
  to_version: string
  added: DiffSection
  removed: DiffSection
  modified: DiffSection
  sql_migrations_blocked: boolean
  breaking_changes: string[]
  tarball_sha7?: string
  up_to_date?: boolean
  mcp_diff?: McpDiff | null
}

interface Props {
  slug: string
  sourceUrl: string
  onClose: () => void
  onApplied: () => void
}

function CollapsibleSection({
  label,
  items,
  defaultOpen,
  accent,
}: {
  label: string
  items: [string, string[]][]
  defaultOpen: boolean
  accent: string
}) {
  const [open, setOpen] = useState(defaultOpen)
  if (items.length === 0) return null
  return (
    <div className="border border-[#21262d] rounded-xl overflow-hidden">
      <button
        onClick={() => setOpen((o) => !o)}
        className={`w-full flex items-center justify-between px-4 py-2.5 text-xs font-medium ${accent} hover:bg-white/5 transition-colors`}
      >
        <span>{label}</span>
        {open ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
      </button>
      {open && (
        <div className="px-4 pb-3 pt-1 space-y-2">
          {items.map(([capType, ids]) => (
            <div key={capType}>
              <p className="text-[10px] text-[#667085] uppercase tracking-wider mb-1">{capType}</p>
              <ul className="space-y-0.5">
                {ids.map((id) => (
                  <li key={id} className="text-xs text-[#D0D5DD] font-mono truncate">{id}</li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function sectionEntries(section: DiffSection): [string, string[]][] {
  return Object.entries(section).filter(([, ids]) => ids.length > 0)
}

function totalCount(section: DiffSection): number {
  return Object.values(section).reduce((acc, ids) => acc + ids.length, 0)
}

export default function UpdatePreviewModal({
  slug,
  sourceUrl,
  onClose,
  onApplied,
}: Props) {
  const { t } = useTranslation()
  const [preview, setPreview] = useState<PreviewResult | null>(null)
  const [loading, setLoading] = useState(true)
  const [previewError, setPreviewError] = useState<string | null>(null)
  const [applying, setApplying] = useState(false)
  const [applyError, setApplyError] = useState<string | null>(null)

  // Wave 2.5 — security scan state
  const [scanVerdict, setScanVerdict] = useState<ScanVerdict | null>(null)
  const [, setScanResult] = useState<ScanResult | null>(null)
  const [overrideReason, setOverrideReason] = useState<string | null>(null)
  const [confirmed, setConfirmed] = useState(false) // user confirmed WARN

  const handleScanVerdict = useCallback(
    (verdict: ScanVerdict | null, result: ScanResult | null) => {
      setScanVerdict(verdict)
      setScanResult(result)
      setConfirmed(false) // reset confirmation on new verdict
    },
    []
  )

  const handleOverride = useCallback((reason: string) => {
    setOverrideReason(reason)
  }, [])

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setPreviewError(null)
    api
      .get(`/plugins/${slug}/update/preview?source=${encodeURIComponent(sourceUrl)}`)
      .then((result) => {
        if (!cancelled) setPreview(result as PreviewResult)
      })
      .catch((e: unknown) => {
        if (!cancelled) setPreviewError(e instanceof Error ? e.message : t('plugins.updatePreviewError'))
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => { cancelled = true }
  }, [slug, sourceUrl, t])

  async function handleApply() {
    if (!preview || preview.up_to_date || preview.sql_migrations_blocked) return
    setApplying(true)
    setApplyError(null)
    try {
      const body: Record<string, unknown> = { source_url: sourceUrl }
      // Wave 2.5 — attach scan confirmation info
      if (scanVerdict === 'WARN' && confirmed) {
        body.confirmed_verdict = 'WARN'
      }
      if (scanVerdict === 'BLOCK' && overrideReason) {
        body.override_reason = overrideReason
      }
      if (scanVerdict === null) {
        // skip_scan was checked by admin
        body.skip_scan = true
      }
      await api.post(`/plugins/${slug}/update`, body)
      onApplied()
      onClose()
    } catch (e: unknown) {
      setApplyError(e instanceof Error ? e.message : t('common.unexpectedError'))
    } finally {
      setApplying(false)
    }
  }

  // Wave 2.5 — canApply depends on scan verdict
  const scanGatePassed =
    scanVerdict === 'APPROVE' ||
    scanVerdict === null /* skip */ ||
    (scanVerdict === 'WARN' && confirmed) ||
    (scanVerdict === 'BLOCK' && !!overrideReason)

  const canApply = !!(
    preview &&
    !preview.up_to_date &&
    !preview.sql_migrations_blocked &&
    !applying &&
    scanGatePassed
  )

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div className="bg-[#161b22] border border-[#344054] rounded-2xl w-full max-w-lg shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-[#21262d]">
          <h2 className="text-base font-semibold text-[#e6edf3]">
            {t('plugins.updatePreviewTitle', { slug })}
          </h2>
          <button
            onClick={onClose}
            className="p-1.5 rounded-lg text-[#667085] hover:text-[#D0D5DD] hover:bg-white/5 transition-colors"
          >
            <X size={16} />
          </button>
        </div>

        {/* Body */}
        <div className="px-6 py-5 space-y-4 max-h-[60vh] overflow-y-auto">
          {/* Loading */}
          {loading && (
            <div className="flex items-center justify-center gap-2 py-8 text-sm text-[#667085]">
              <Loader2 size={16} className="animate-spin" />
              {t('plugins.updatePreviewLoading')}
            </div>
          )}

          {/* Error fetching preview */}
          {!loading && previewError && (
            <div className="flex items-start gap-2 text-xs text-red-400 bg-red-500/5 border border-red-500/20 rounded-xl px-4 py-3">
              <AlertTriangle size={14} className="shrink-0 mt-0.5" />
              <span>{previewError}</span>
            </div>
          )}

          {/* Up to date */}
          {!loading && !previewError && preview?.up_to_date && (
            <div className="flex items-center justify-center gap-2 py-8 text-sm text-[#00FFA7]">
              <CheckCircle size={18} />
              {t('plugins.updatePreviewUpToDate')}
            </div>
          )}

          {/* Wave 2.5 — Security Scan (first section, always visible once preview loaded) */}
          {!loading && !previewError && preview && !preview.up_to_date && (
            <SecurityScanSection
              sourceUrl={sourceUrl}
              onVerdict={handleScanVerdict}
              onOverride={handleOverride}
            />
          )}

          {/* WARN confirmation checkbox */}
          {!loading && !previewError && scanVerdict === 'WARN' && !confirmed && (
            <label className="flex items-center gap-2 cursor-pointer text-xs text-yellow-400">
              <input
                type="checkbox"
                checked={confirmed}
                onChange={(e) => setConfirmed(e.target.checked)}
                className="rounded border-yellow-500/50 text-yellow-400 focus:ring-yellow-500/30"
              />
              I acknowledge the security warnings and want to proceed
            </label>
          )}

          {/* Diff content */}
          {!loading && !previewError && preview && !preview.up_to_date && (
            <>
              {/* Version line */}
              <p className="text-xs text-[#667085]">
                {t('plugins.updatePreviewVersionLine', {
                  from: preview.from_version,
                  to: preview.to_version,
                  sha: preview.tarball_sha7 ?? '—',
                })}
              </p>

              {/* SQL blocked banner */}
              {preview.sql_migrations_blocked && (
                <div className="flex items-start gap-2 bg-red-500/5 border border-red-500/30 rounded-xl px-4 py-3">
                  <AlertTriangle size={14} className="text-red-400 shrink-0 mt-0.5" />
                  <div className="flex-1 min-w-0">
                    <p className="text-xs font-medium text-red-400">
                      {t('plugins.updatePreviewSqlBlocked')}
                    </p>
                    <a
                      href="/docs/plugins/migration-chain"
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-xs text-red-300/70 hover:text-red-300 underline mt-0.5 inline-block"
                    >
                      {t('plugins.updatePreviewSqlBlockedDocs')}
                    </a>
                  </div>
                </div>
              )}

              {/* Other breaking changes */}
              {preview.breaking_changes.filter((msg) => !msg.startsWith('install.sql')).length > 0 && (
                <div className="bg-yellow-500/5 border border-yellow-500/20 rounded-xl px-4 py-3">
                  <p className="text-xs font-medium text-yellow-400 mb-1.5 flex items-center gap-1.5">
                    <AlertTriangle size={12} />
                    {t('plugins.updatePreviewBreakingChanges')}
                  </p>
                  <ul className="space-y-1">
                    {preview.breaking_changes
                      .filter((msg) => !msg.startsWith('install.sql'))
                      .map((msg, i) => (
                        <li key={i} className="text-xs text-yellow-300/80">{msg}</li>
                      ))}
                  </ul>
                </div>
              )}

              {/* Added */}
              <CollapsibleSection
                label={t('plugins.updatePreviewAdded', { count: totalCount(preview.added) })}
                items={sectionEntries(preview.added)}
                defaultOpen={totalCount(preview.added) > 0}
                accent="text-[#00FFA7]"
              />

              {/* Removed */}
              <CollapsibleSection
                label={t('plugins.updatePreviewRemoved', { count: totalCount(preview.removed) })}
                items={sectionEntries(preview.removed)}
                defaultOpen={totalCount(preview.removed) > 0}
                accent="text-red-400"
              />

              {/* Modified */}
              <CollapsibleSection
                label={t('plugins.updatePreviewModified', { count: totalCount(preview.modified) })}
                items={sectionEntries(preview.modified)}
                defaultOpen={false}
                accent="text-yellow-400"
              />

              {/* Wave 2.3 — MCP server changes */}
              {preview.mcp_diff && (
                <div className="bg-blue-500/5 border border-blue-500/20 rounded-xl px-4 py-3">
                  <p className="text-xs font-medium text-blue-300 mb-2 flex items-center gap-1.5">
                    <Terminal size={12} />
                    MCP Servers — reiniciar Claude Code CLI necessário
                  </p>
                  {preview.mcp_diff.added.length > 0 && (
                    <div className="mb-1.5">
                      <p className="text-[10px] text-blue-400/60 uppercase tracking-wider mb-0.5">Adicionados</p>
                      {preview.mcp_diff.added.map((name) => (
                        <p key={name} className="text-xs text-blue-300 font-mono">{name}</p>
                      ))}
                    </div>
                  )}
                  {preview.mcp_diff.removed.length > 0 && (
                    <div className="mb-1.5">
                      <p className="text-[10px] text-blue-400/60 uppercase tracking-wider mb-0.5">Removidos</p>
                      {preview.mcp_diff.removed.map((name) => (
                        <p key={name} className="text-xs text-red-400 font-mono">{name}</p>
                      ))}
                    </div>
                  )}
                  {preview.mcp_diff.modified.length > 0 && (
                    <div>
                      <p className="text-[10px] text-blue-400/60 uppercase tracking-wider mb-0.5">Modificados</p>
                      {preview.mcp_diff.modified.map((name) => (
                        <p key={name} className="text-xs text-yellow-400 font-mono">{name}</p>
                      ))}
                    </div>
                  )}
                </div>
              )}

              {/* Empty state */}
              {totalCount(preview.added) === 0 &&
                totalCount(preview.removed) === 0 &&
                totalCount(preview.modified) === 0 && (
                  <p className="text-xs text-[#667085] text-center py-2">
                    No capability changes detected.
                  </p>
                )}

              {/* Apply error */}
              {applyError && (
                <div className="flex items-start gap-2 text-xs text-red-400 bg-red-500/5 border border-red-500/20 rounded-xl px-4 py-3">
                  <AlertTriangle size={14} className="shrink-0 mt-0.5" />
                  <span>{applyError}</span>
                </div>
              )}
            </>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-3 px-6 py-4 border-t border-[#21262d]">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm text-[#667085] hover:text-[#D0D5DD] transition-colors"
          >
            {t('common.cancel')}
          </button>

          {preview && !preview.up_to_date && (
            <button
              onClick={handleApply}
              disabled={!canApply}
              title={
                preview.sql_migrations_blocked
                  ? t('plugins.updatePreviewSqlBlocked')
                  : scanVerdict === 'BLOCK' && !overrideReason
                  ? 'Installation blocked by security scan'
                  : undefined
              }
              className={`flex items-center gap-2 px-4 py-2 text-sm font-medium rounded-lg disabled:opacity-50 disabled:cursor-not-allowed transition-colors ${
                scanVerdict === 'WARN'
                  ? 'bg-yellow-500 text-black hover:bg-yellow-400'
                  : 'bg-[#00FFA7] text-black hover:bg-[#00FFA7]/90'
              }`}
            >
              {applying ? (
                <Loader2 size={14} className="animate-spin" />
              ) : (
                <Download size={14} />
              )}
              {scanVerdict === 'WARN' && confirmed
                ? 'Install Anyway'
                : t('plugins.updatePreviewApply', {
                    from: preview.from_version,
                    to: preview.to_version,
                  })}
            </button>
          )}

          {preview?.up_to_date && (
            <button
              onClick={onClose}
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
