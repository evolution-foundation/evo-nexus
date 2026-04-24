/**
 * Wave 2.5 — SecurityScanSection
 *
 * Renders the security scan gate inside UpdatePreviewModal (and any future
 * install modal). Runs POST /api/plugins/scan on mount and propagates the
 * verdict upward via onVerdict / onOverride callbacks.
 *
 * Dark theme: bg-[#161b22], border-[#344054], accent #00FFA7
 */

import { useEffect, useRef, useState } from 'react'
import {
  AlertTriangle,
  ChevronDown,
  ChevronUp,
  Loader2,
  Shield,
  ShieldAlert,
  ShieldCheck,
  ShieldX,
  SkipForward,
} from 'lucide-react'
import { api } from '../lib/api'
import { useAuth } from '../context/AuthContext'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type ScanVerdict = 'APPROVE' | 'WARN' | 'BLOCK'

interface ScanFinding {
  category: string
  severity: string
  file: string
  line: number
  snippet: string
  description: string
}

export interface ScanResult {
  verdict: ScanVerdict
  severity: string
  scan_duration_ms: number
  scanners_used: string[]
  cache_hit: boolean
  tarball_sha256: string
  findings: ScanFinding[]
  findings_truncated: boolean
  llm_used: boolean
  llm_reasoning: string
  scanner_version: string
}

interface Props {
  sourceUrl: string
  authToken?: string
  /** Called when a scan result is available (or when scan is skipped). */
  onVerdict: (verdict: ScanVerdict | null, result: ScanResult | null) => void
  /** Called when admin overrides a BLOCK. */
  onOverride: (reason: string) => void
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const SEVERITY_ORDER: Record<string, number> = {
  CRITICAL: 4,
  HIGH: 3,
  MEDIUM: 2,
  LOW: 1,
  INFO: 0,
}

function sortedFindings(findings: ScanFinding[]): ScanFinding[] {
  return [...findings].sort(
    (a, b) => (SEVERITY_ORDER[b.severity] ?? 0) - (SEVERITY_ORDER[a.severity] ?? 0)
  )
}

function severityColor(sev: string): string {
  switch (sev.toUpperCase()) {
    case 'CRITICAL': return 'text-red-400'
    case 'HIGH':     return 'text-orange-400'
    case 'MEDIUM':   return 'text-yellow-400'
    case 'LOW':      return 'text-blue-400'
    default:         return 'text-[#667085]'
  }
}

function verdictLabel(v: ScanVerdict): string {
  switch (v) {
    case 'APPROVE': return 'APPROVED'
    case 'WARN':    return 'WARNING'
    case 'BLOCK':   return 'BLOCKED'
  }
}

function verdictColors(v: ScanVerdict) {
  switch (v) {
    case 'APPROVE':
      return {
        badge: 'bg-[#00FFA7]/10 text-[#00FFA7] border-[#00FFA7]/30',
        icon: <ShieldCheck size={14} className="text-[#00FFA7]" />,
      }
    case 'WARN':
      return {
        badge: 'bg-yellow-500/10 text-yellow-400 border-yellow-500/30',
        icon: <ShieldAlert size={14} className="text-yellow-400" />,
      }
    case 'BLOCK':
      return {
        badge: 'bg-red-500/10 text-red-400 border-red-500/30',
        icon: <ShieldX size={14} className="text-red-400" />,
      }
  }
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export default function SecurityScanSection({ sourceUrl, authToken, onVerdict, onOverride }: Props) {
  const { user } = useAuth()
  const isAdmin = user?.role === 'admin'

  const [scanning, setScanning] = useState(false)
  const [result, setResult] = useState<ScanResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [findingsOpen, setFindingsOpen] = useState(false)
  const [skipScan, setSkipScan] = useState(false)
  const [skipReason, setSkipReason] = useState('')
  const [overrideReason, setOverrideReason] = useState('')
  const [showOverrideInput, setShowOverrideInput] = useState(false)

  // Run once on mount
  const hasRun = useRef(false)
  useEffect(() => {
    if (hasRun.current) return
    hasRun.current = true

    setScanning(true)
    setError(null)

    api
      .post('/plugins/scan', { source_url: sourceUrl, auth_token: authToken || undefined })
      .then((res: unknown) => {
        const r = res as ScanResult
        setResult(r)
        onVerdict(r.verdict, r)
        // Auto-expand findings if WARN or BLOCK
        if (r.verdict !== 'APPROVE') setFindingsOpen(true)
      })
      .catch((e: unknown) => {
        const msg = e instanceof Error ? e.message : 'Scan request failed'
        setError(msg)
        // Scan error → treat as WARN so install isn't blocked by infra failure
        onVerdict('WARN', null)
      })
      .finally(() => {
        setScanning(false)
      })
  }, [sourceUrl, authToken, onVerdict])

  // Handle skip checkbox
  function handleSkipToggle(checked: boolean) {
    setSkipScan(checked)
    if (checked) {
      setResult(null)
      setError(null)
      onVerdict(null, null)
    } else {
      // Re-run scan when unchecked
      hasRun.current = false
      setScanning(true)
      api
        .post('/plugins/scan', { source_url: sourceUrl, auth_token: authToken || undefined })
        .then((res: unknown) => {
          const r = res as ScanResult
          setResult(r)
          onVerdict(r.verdict, r)
          if (r.verdict !== 'APPROVE') setFindingsOpen(true)
        })
        .catch((e: unknown) => {
          const msg = e instanceof Error ? e.message : 'Scan request failed'
          setError(msg)
          onVerdict('WARN', null)
        })
        .finally(() => setScanning(false))
    }
  }

  function handleOverrideSubmit() {
    if (!overrideReason || overrideReason.trim().length < 20) return
    onOverride(overrideReason.trim())
    setShowOverrideInput(false)
  }

  return (
    <div className="border border-[#344054] rounded-xl overflow-hidden">
      {/* Section header */}
      <div className="flex items-center gap-2 px-4 py-3 bg-white/3 border-b border-[#21262d]">
        <Shield size={14} className="text-[#00FFA7] shrink-0" />
        <span className="text-xs font-semibold text-[#D0D5DD]">Security Scan</span>
        {result?.cache_hit && (
          <span className="ml-auto text-[10px] text-[#667085] bg-white/5 rounded px-1.5 py-0.5">
            cached
          </span>
        )}
        {result && (
          <span className="text-[10px] text-[#667085] ml-auto">
            {result.scan_duration_ms}ms · {result.scanners_used.join('+')}
          </span>
        )}
      </div>

      <div className="px-4 py-3 space-y-3">
        {/* Scanning spinner */}
        {scanning && (
          <div className="flex items-center gap-2 text-xs text-[#667085]">
            <Loader2 size={13} className="animate-spin shrink-0" />
            <span>Scanning for security issues…</span>
          </div>
        )}

        {/* Scan error (infra failure) */}
        {!scanning && error && (
          <div className="flex items-start gap-2 text-xs text-yellow-400 bg-yellow-500/5 border border-yellow-500/20 rounded-lg px-3 py-2">
            <AlertTriangle size={13} className="shrink-0 mt-0.5" />
            <span>Scan unavailable: {error}. Proceeding at your discretion.</span>
          </div>
        )}

        {/* Scan result */}
        {!scanning && !error && result && (
          <div className="space-y-2">
            {/* Verdict badge */}
            <div className="flex items-center gap-2">
              {verdictColors(result.verdict).icon}
              <span
                className={`text-xs font-semibold px-2 py-0.5 rounded border ${verdictColors(result.verdict).badge}`}
              >
                {verdictLabel(result.verdict)}
              </span>
              {result.findings.length > 0 && (
                <span className="text-[10px] text-[#667085]">
                  {result.findings.length}{result.findings_truncated ? '+' : ''} finding
                  {result.findings.length !== 1 ? 's' : ''}
                </span>
              )}
              {result.verdict === 'APPROVE' && result.findings.length === 0 && (
                <span className="text-[10px] text-[#667085]">No issues found</span>
              )}
            </div>

            {/* LLM reasoning (if any) */}
            {result.llm_used && result.llm_reasoning && (
              <p className="text-[10px] text-[#667085] italic leading-relaxed">
                {result.llm_reasoning}
              </p>
            )}

            {/* Findings collapsible */}
            {result.findings.length > 0 && (
              <div className="border border-[#21262d] rounded-lg overflow-hidden">
                <button
                  onClick={() => setFindingsOpen((o) => !o)}
                  className="w-full flex items-center justify-between px-3 py-2 text-[10px] font-medium text-[#667085] hover:text-[#D0D5DD] hover:bg-white/3 transition-colors"
                >
                  <span>Findings</span>
                  {findingsOpen ? <ChevronUp size={11} /> : <ChevronDown size={11} />}
                </button>
                {findingsOpen && (
                  <div className="divide-y divide-[#21262d]">
                    {sortedFindings(result.findings).map((f, i) => (
                      <div key={i} className="px-3 py-2 space-y-0.5">
                        <div className="flex items-center gap-1.5">
                          <span className={`text-[10px] font-semibold uppercase ${severityColor(f.severity)}`}>
                            {f.severity}
                          </span>
                          <span className="text-[10px] text-[#667085] font-mono">{f.category}</span>
                        </div>
                        <p className="text-xs text-[#D0D5DD] leading-relaxed">{f.description}</p>
                        {f.file && (
                          <p className="text-[10px] text-[#667085] font-mono truncate">
                            {f.file}{f.line > 0 ? `:${f.line}` : ''}
                          </p>
                        )}
                        {f.snippet && (
                          <p className="text-[10px] text-[#667085] font-mono bg-black/20 rounded px-2 py-1 truncate">
                            {f.snippet}
                          </p>
                        )}
                      </div>
                    ))}
                    {result.findings_truncated && (
                      <p className="px-3 py-2 text-[10px] text-[#667085] text-center">
                        More findings not shown. Review the full scan report.
                      </p>
                    )}
                  </div>
                )}
              </div>
            )}

            {/* Admin BLOCK override */}
            {result.verdict === 'BLOCK' && isAdmin && (
              <div className="space-y-2">
                {!showOverrideInput ? (
                  <button
                    onClick={() => setShowOverrideInput(true)}
                    className="text-xs text-red-400 underline hover:text-red-300 transition-colors"
                  >
                    Override BLOCK (audit logged)
                  </button>
                ) : (
                  <div className="space-y-1.5">
                    <label className="text-[10px] text-[#667085]">
                      Override reason (min 20 chars, required)
                    </label>
                    <textarea
                      value={overrideReason}
                      onChange={(e) => setOverrideReason(e.target.value)}
                      rows={2}
                      placeholder="Explain why this BLOCK is being overridden…"
                      className="w-full bg-black/20 border border-[#344054] rounded-lg px-3 py-2 text-xs text-[#D0D5DD] placeholder-[#667085] resize-none focus:outline-none focus:border-red-500/50"
                    />
                    <div className="flex gap-2">
                      <button
                        onClick={handleOverrideSubmit}
                        disabled={overrideReason.trim().length < 20}
                        className="px-3 py-1.5 text-xs bg-red-500/20 text-red-400 border border-red-500/30 rounded-lg disabled:opacity-50 disabled:cursor-not-allowed hover:bg-red-500/30 transition-colors"
                      >
                        Confirm Override
                      </button>
                      <button
                        onClick={() => { setShowOverrideInput(false); setOverrideReason('') }}
                        className="px-3 py-1.5 text-xs text-[#667085] hover:text-[#D0D5DD] transition-colors"
                      >
                        Cancel
                      </button>
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        {/* Skip scan checkbox — admin only */}
        {isAdmin && !scanning && (
          <div className="pt-1 border-t border-[#21262d]">
            <label className="flex items-start gap-2 cursor-pointer group">
              <div className="relative mt-0.5">
                <input
                  type="checkbox"
                  checked={skipScan}
                  onChange={(e) => handleSkipToggle(e.target.checked)}
                  className="sr-only"
                />
                <div
                  className={`w-3.5 h-3.5 rounded border transition-colors ${
                    skipScan
                      ? 'bg-yellow-500 border-yellow-500'
                      : 'border-[#344054] group-hover:border-[#667085]'
                  }`}
                >
                  {skipScan && (
                    <SkipForward size={9} className="text-black absolute top-0.5 left-0.5" />
                  )}
                </div>
              </div>
              <div className="space-y-0.5">
                <span className="text-[10px] text-[#667085]">
                  Skip scan — will be logged to audit
                </span>
                {skipScan && (
                  <div className="space-y-1">
                    <textarea
                      value={skipReason}
                      onChange={(e) => setSkipReason(e.target.value)}
                      rows={1}
                      placeholder="Reason for skipping (required)"
                      className="w-full bg-black/20 border border-[#344054] rounded px-2 py-1 text-[10px] text-[#D0D5DD] placeholder-[#667085] resize-none focus:outline-none focus:border-yellow-500/50"
                      onClick={(e) => e.stopPropagation()}
                    />
                  </div>
                )}
              </div>
            </label>
          </div>
        )}
      </div>
    </div>
  )
}
