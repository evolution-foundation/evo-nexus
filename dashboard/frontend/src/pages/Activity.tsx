import { useEffect, useState, useRef, useCallback } from 'react'
import { Clock, Activity, Zap, Loader2, X, ExternalLink, ChevronRight, CheckCircle, XCircle } from 'lucide-react'
import { Link } from 'react-router-dom'
import { api } from '../lib/api'
import { useTranslation } from 'react-i18next'

// ── Types ─────────────────────────────────────────────────────────────────────

type ItemType = 'routine' | 'heartbeat' | 'trigger'
type RunStatus = 'success' | 'error' | 'running' | 'fail' | 'timeout' | 'killed' | 'completed' | 'pending' | string

interface ActivityItem {
  id: string
  type: ItemType
  name: string
  status: RunStatus
  started_at: string
  ended_at: string | null
  duration_ms: number | null
  triggered_by: string | null
  // Heartbeat-specific
  tokens_in?: number | null
  tokens_out?: number | null
  cost_usd?: number | null
  prompt_preview?: string | null
  error?: string | null
  heartbeat_id?: string
  run_id?: string
  // Trigger-specific
  trigger_id?: number | null
  execution_id?: number | null
  result_summary?: string | null
  event_data?: Record<string, unknown> | null
  duration_seconds?: number | null
  // Routine-specific
  routine_name?: string | null
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function relativeTime(dateStr: string): string {
  if (!dateStr) return ''
  try {
    const date = new Date(dateStr)
    const now = new Date()
    const diffMs = now.getTime() - date.getTime()
    const diffMin = Math.floor(diffMs / 60000)
    if (diffMin < 1) return 'just now'
    if (diffMin < 60) return `${diffMin}m ago`
    const diffHr = Math.floor(diffMin / 60)
    if (diffHr < 24) return `${diffHr}h ago`
    const diffDay = Math.floor(diffHr / 24)
    if (diffDay < 7) return `${diffDay}d ago`
    return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
  } catch {
    return dateStr
  }
}

function formatTime(dateStr: string): string {
  if (!dateStr) return ''
  try {
    return new Date(dateStr).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })
  } catch {
    return ''
  }
}

function formatDuration(ms: number | null, secs?: number | null): string {
  const totalMs = ms ?? (secs != null ? secs * 1000 : null)
  if (totalMs == null) return '--'
  if (totalMs < 1000) return `${Math.round(totalMs)}ms`
  if (totalMs < 60000) return `${(totalMs / 1000).toFixed(1)}s`
  const m = Math.floor(totalMs / 60000)
  const s = Math.floor((totalMs % 60000) / 1000)
  return `${m}m ${s}s`
}

function normalizeStatus(status: string): 'success' | 'error' | 'running' {
  if (['success', 'completed'].includes(status)) return 'success'
  if (['fail', 'failed', 'error', 'timeout', 'killed'].includes(status)) return 'error'
  return 'running'
}

function getDateRange(period: 'today' | '7d' | '30d' | 'all'): Date | null {
  const now = new Date()
  if (period === 'today') {
    const d = new Date(now)
    d.setHours(0, 0, 0, 0)
    return d
  }
  if (period === '7d') {
    const d = new Date(now)
    d.setDate(d.getDate() - 7)
    return d
  }
  if (period === '30d') {
    const d = new Date(now)
    d.setDate(d.getDate() - 30)
    return d
  }
  return null
}

// ── Status pill ───────────────────────────────────────────────────────────────

function StatusPill({ status }: { status: RunStatus }) {
  const norm = normalizeStatus(status)
  const cls = {
    success: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20',
    error: 'bg-red-500/10 text-red-400 border-red-500/20',
    running: 'bg-blue-500/10 text-blue-400 border-blue-500/20',
  }[norm]

  return (
    <span className={`inline-flex items-center gap-1 text-[11px] font-medium px-2 py-0.5 rounded-full border ${cls}`}>
      {norm === 'running' && <Loader2 size={10} className="animate-spin" />}
      {norm === 'success' && <CheckCircle size={10} />}
      {norm === 'error' && <XCircle size={10} />}
      {status}
    </span>
  )
}

// ── Type badge ────────────────────────────────────────────────────────────────

function TypeBadge({ type }: { type: ItemType }) {
  return (
    <span className="text-[10px] text-white/50 border border-white/10 rounded px-1.5 py-0.5 capitalize">
      {type}
    </span>
  )
}

// ── Row icon ──────────────────────────────────────────────────────────────────

function RowIcon({ type }: { type: ItemType }) {
  if (type === 'heartbeat') return (
    <div className="flex items-center justify-center w-9 h-9 rounded-xl bg-emerald-500/8 border border-emerald-500/15 shrink-0">
      <Activity size={16} className="text-emerald-400" />
    </div>
  )
  if (type === 'trigger') return (
    <div className="flex items-center justify-center w-9 h-9 rounded-xl bg-amber-500/8 border border-amber-500/15 shrink-0">
      <Zap size={16} className="text-amber-400" />
    </div>
  )
  return (
    <div className="flex items-center justify-center w-9 h-9 rounded-xl bg-[#21262d] border border-[#21262d] shrink-0">
      <Clock size={16} className="text-[#667085]" />
    </div>
  )
}

// ── Skeleton ──────────────────────────────────────────────────────────────────

function SkeletonRow() {
  return (
    <div className="flex items-center gap-4 px-4 py-3 border-b border-[#21262d]/60 animate-pulse">
      <div className="w-9 h-9 rounded-xl bg-[#21262d] shrink-0" />
      <div className="flex-1 min-w-0 space-y-2">
        <div className="h-3.5 bg-[#21262d] rounded w-48" />
        <div className="h-3 bg-[#21262d]/60 rounded w-32" />
      </div>
      <div className="h-5 bg-[#21262d] rounded-full w-16" />
      <div className="h-3 bg-[#21262d]/60 rounded w-14" />
    </div>
  )
}

// ── Drawer ────────────────────────────────────────────────────────────────────

interface DrawerProps {
  item: ActivityItem | null
  onClose: () => void
}

function ActivityDrawer({ item, onClose }: DrawerProps) {
  // Close on Escape
  useEffect(() => {
    if (!item) return
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [item, onClose])

  if (!item) return null

  const norm = normalizeStatus(item.status)
  const dedicatedHref = item.type === 'heartbeat' && item.heartbeat_id
    ? `/heartbeats/${item.heartbeat_id}`
    : item.type === 'trigger' && item.trigger_id
    ? `/triggers`
    : `/routines`

  const tokensTotal = (item.tokens_in ?? 0) + (item.tokens_out ?? 0)
  const hasCost = item.cost_usd != null && item.cost_usd > 0

  const logContent = item.type === 'heartbeat'
    ? item.prompt_preview ?? item.error ?? '(no output)'
    : item.type === 'trigger'
    ? (item.result_summary ?? item.error ?? (item.event_data ? JSON.stringify(item.event_data, null, 2) : '(no output)'))
    : item.error ?? '(no output)'

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 bg-black/50 z-40"
        onClick={onClose}
        aria-hidden="true"
      />

      {/* Drawer */}
      <aside
        role="dialog"
        aria-modal="true"
        aria-label="Execution details"
        className="fixed right-0 top-0 bottom-0 w-full sm:w-[480px] bg-[#0d1117] border-l border-[#21262d] flex flex-col z-50 shadow-2xl"
        style={{ animation: 'slideInRight 180ms ease-out' }}
      >
        {/* Header */}
        <div className="flex items-start gap-3 px-5 py-4 border-b border-[#21262d]">
          <RowIcon type={item.type} />
          <div className="flex-1 min-w-0">
            <h2 className="text-sm font-semibold text-[#e6edf3] truncate">{item.name}</h2>
            <div className="flex items-center gap-2 mt-1">
              <TypeBadge type={item.type} />
              <StatusPill status={item.status} />
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 rounded-lg text-[#667085] hover:text-[#e6edf3] hover:bg-white/5 transition-colors shrink-0"
            aria-label="Close"
          >
            <X size={16} />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-5">
          {/* Metadata grid */}
          <div className="grid grid-cols-2 gap-3">
            {[
              { label: 'Started', value: item.started_at ? formatTime(item.started_at) : '--' },
              { label: 'Finished', value: item.ended_at ? formatTime(item.ended_at) : norm === 'running' ? 'Still running' : '--' },
              { label: 'Duration', value: formatDuration(item.duration_ms, item.duration_seconds) },
              { label: 'Triggered by', value: item.triggered_by ?? 'schedule' },
            ].map(({ label, value }) => (
              <div key={label} className="bg-[#161b22] border border-[#21262d] rounded-xl p-3">
                <p className="text-[10px] uppercase tracking-wider text-[#667085] mb-1">{label}</p>
                <p className="text-sm text-[#e6edf3] font-medium">{value}</p>
              </div>
            ))}
          </div>

          {/* Cost section — heartbeats only */}
          {item.type === 'heartbeat' && (hasCost || tokensTotal > 0) && (
            <div>
              <p className="text-[10px] uppercase tracking-wider text-[#667085] mb-2">Cost</p>
              <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-3 flex items-center gap-6">
                <div>
                  <p className="text-[10px] text-[#667085] mb-0.5">Tokens in</p>
                  <p className="text-sm text-[#e6edf3] font-mono">{(item.tokens_in ?? 0).toLocaleString()}</p>
                </div>
                <div>
                  <p className="text-[10px] text-[#667085] mb-0.5">Tokens out</p>
                  <p className="text-sm text-[#e6edf3] font-mono">{(item.tokens_out ?? 0).toLocaleString()}</p>
                </div>
                {hasCost && (
                  <div>
                    <p className="text-[10px] text-[#667085] mb-0.5">Cost</p>
                    <p className="text-sm text-[#00FFA7] font-mono">${item.cost_usd!.toFixed(4)}</p>
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Output / log */}
          <div>
            <p className="text-[10px] uppercase tracking-wider text-[#667085] mb-2">Output</p>
            <pre className="bg-[#0a0e14] border border-[#21262d] rounded-xl p-3 text-xs text-[#8b949e] font-mono overflow-auto max-h-64 whitespace-pre-wrap break-words">
              {logContent}
            </pre>
          </div>

          {/* Error detail */}
          {item.error && norm === 'error' && (
            <div>
              <p className="text-[10px] uppercase tracking-wider text-red-400/70 mb-2">Error</p>
              <div className="bg-red-500/5 border border-red-500/20 rounded-xl p-3">
                <p className="text-xs text-red-400 font-mono break-words">{item.error}</p>
              </div>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="px-5 py-4 border-t border-[#21262d]">
          <Link
            to={dedicatedHref}
            onClick={onClose}
            className="flex items-center justify-center gap-2 w-full px-4 py-2.5 rounded-xl bg-[#161b22] border border-[#21262d] text-sm text-[#667085] hover:text-[#e6edf3] hover:border-[#00FFA7]/30 transition-all"
          >
            <ExternalLink size={14} />
            Open in dedicated page
          </Link>
        </div>
      </aside>
    </>
  )
}

// ── Main ──────────────────────────────────────────────────────────────────────

type TypeFilter = 'routines' | 'heartbeats' | 'triggers'
type StatusFilter = 'all' | 'success' | 'error' | 'running'
type PeriodFilter = 'today' | '7d' | '30d' | 'all'

const TYPE_FILTERS: TypeFilter[] = ['routines', 'heartbeats', 'triggers']
const PERIOD_TABS: { key: PeriodFilter; label: string }[] = [
  { key: 'today', label: 'Today' },
  { key: '7d', label: '7d' },
  { key: '30d', label: '30d' },
  { key: 'all', label: 'All' },
]

export default function ActivityPage() {
  const { t } = useTranslation()
  const [items, setItems] = useState<ActivityItem[]>([])
  const [loading, setLoading] = useState(true)
  const [typeFilters, setTypeFilters] = useState<Set<TypeFilter>>(new Set(['routines', 'heartbeats', 'triggers']))
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all')
  const [period, setPeriod] = useState<PeriodFilter>('today')
  const [search, setSearch] = useState('')
  const [selectedItem, setSelectedItem] = useState<ActivityItem | null>(null)
  const [paused, setPaused] = useState(false)
  const [page, setPage] = useState(0)
  const PAGE_SIZE = 50

  const searchDebounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const [displaySearch, setDisplaySearch] = useState('')

  const handleSearchChange = (val: string) => {
    setDisplaySearch(val)
    if (searchDebounceRef.current) clearTimeout(searchDebounceRef.current)
    searchDebounceRef.current = setTimeout(() => setSearch(val), 300)
  }

  // ── Fetch ─────────────────────────────────────────────────────────────────

  const fetchAll = useCallback(async () => {
    const results: ActivityItem[] = []

    // 1. Routines — logs by date
    if (typeFilters.has('routines')) {
      try {
        const today = new Date().toISOString().slice(0, 10)
        const logs: any[] = await api.get(`/routines/logs?date=${today}`)
        for (const log of Array.isArray(logs) ? logs : []) {
          results.push({
            id: `routine-${log.routine_name || log.name || 'unknown'}-${log.started_at || log.timestamp || Date.now()}`,
            type: 'routine',
            name: log.routine_name || log.name || log.script || 'Unknown Routine',
            status: log.status || (log.exit_code === 0 ? 'success' : 'error'),
            started_at: log.started_at || log.timestamp || '',
            ended_at: log.ended_at || log.completed_at || null,
            duration_ms: log.duration_ms ?? (log.duration_seconds != null ? log.duration_seconds * 1000 : null),
            triggered_by: log.triggered_by || 'schedule',
            error: log.error || log.stderr || null,
            routine_name: log.routine_name || log.name || null,
          })
        }
      } catch { /* routines may have no logs */ }
    }

    // 2. Heartbeats — list all, then runs per heartbeat
    if (typeFilters.has('heartbeats')) {
      try {
        const hbData = await api.get('/heartbeats')
        const heartbeats: any[] = hbData.heartbeats || []

        await Promise.all(
          heartbeats.map(async (hb: any) => {
            try {
              const runsData = await api.get(`/heartbeats/${hb.id}/runs?limit=10`)
              const runs: any[] = runsData.runs || []
              for (const run of runs) {
                results.push({
                  id: `heartbeat-${run.run_id}`,
                  type: 'heartbeat',
                  name: hb.agent || hb.id,
                  status: run.status,
                  started_at: run.started_at || '',
                  ended_at: run.ended_at || null,
                  duration_ms: run.duration_ms ?? null,
                  triggered_by: run.triggered_by || 'interval',
                  tokens_in: run.tokens_in,
                  tokens_out: run.tokens_out,
                  cost_usd: run.cost_usd,
                  prompt_preview: run.prompt_preview,
                  error: run.error,
                  heartbeat_id: hb.id,
                  run_id: run.run_id,
                })
              }
            } catch { /* skip this heartbeat */ }
          })
        )
      } catch { /* no heartbeats */ }
    }

    // 3. Triggers — list all triggers, get executions from each detail
    if (typeFilters.has('triggers')) {
      try {
        const trigData = await api.get('/triggers?per_page=50')
        const triggers: any[] = trigData.triggers || []

        await Promise.all(
          triggers.map(async (tr: any) => {
            try {
              const detail = await api.get(`/triggers/${tr.id}`)
              const execs: any[] = detail.executions || []
              for (const exec of execs) {
                results.push({
                  id: `trigger-exec-${exec.id}`,
                  type: 'trigger',
                  name: tr.name || tr.slug,
                  status: exec.status === 'completed' ? 'success' : exec.status,
                  started_at: exec.started_at || '',
                  ended_at: exec.completed_at || null,
                  duration_ms: exec.duration_seconds != null ? exec.duration_seconds * 1000 : null,
                  triggered_by: tr.type === 'webhook' ? 'webhook' : 'event',
                  error: exec.error,
                  result_summary: exec.result_summary,
                  event_data: exec.event_data,
                  trigger_id: tr.id,
                  execution_id: exec.id,
                  duration_seconds: exec.duration_seconds,
                })
              }
            } catch { /* skip this trigger */ }
          })
        )
      } catch { /* no triggers */ }
    }

    // Sort descending by started_at
    results.sort((a, b) => {
      const ta = a.started_at ? new Date(a.started_at).getTime() : 0
      const tb = b.started_at ? new Date(b.started_at).getTime() : 0
      return tb - ta
    })

    setItems(results)
    setLoading(false)
  }, [typeFilters])

  useEffect(() => {
    setLoading(true)
    setPage(0)
    fetchAll()
  }, [fetchAll])

  // Auto-refresh 30s, paused when hidden or manually paused
  useEffect(() => {
    if (paused) return
    const id = setInterval(() => {
      if (document.visibilityState !== 'hidden') fetchAll()
    }, 30000)
    return () => clearInterval(id)
  }, [paused, fetchAll])

  // ── Filter ────────────────────────────────────────────────────────────────

  const cutoff = getDateRange(period)

  const filtered = items.filter((item) => {
    // Period
    if (cutoff && item.started_at) {
      const d = new Date(item.started_at)
      if (d < cutoff) return false
    }
    // Status
    if (statusFilter !== 'all') {
      const norm = normalizeStatus(item.status)
      if (norm !== statusFilter) return false
    }
    // Search
    if (search && !item.name.toLowerCase().includes(search.toLowerCase())) return false
    return true
  })

  const paginated = filtered.slice(0, (page + 1) * PAGE_SIZE)
  const hasMore = filtered.length > paginated.length

  // ── Toggle type filter ────────────────────────────────────────────────────

  const toggleType = (type: TypeFilter) => {
    setTypeFilters((prev) => {
      const next = new Set(prev)
      if (next.has(type)) {
        if (next.size === 1) return next // keep at least one
        next.delete(type)
      } else {
        next.add(type)
      }
      return next
    })
  }

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <>
      <style>{`
        @keyframes slideInRight {
          from { transform: translateX(100%); opacity: 0; }
          to { transform: translateX(0); opacity: 1; }
        }
      `}</style>

      <div className="max-w-[1200px] mx-auto">
        {/* Header */}
        <div className="mb-6">
          <h1 className="text-2xl font-bold text-[#e6edf3] tracking-tight">
            {t('nav.activity')}
          </h1>
          <p className="text-[#667085] text-sm mt-1">
            Execution log across routines, heartbeats and triggers
          </p>
        </div>

        {/* Filters — sticky after header */}
        <div className="sticky top-0 z-20 bg-[#0C111D] pb-4 pt-1">
          <div className="flex flex-wrap items-center gap-3">
            {/* Type chips */}
            <div className="flex items-center gap-1.5">
              {TYPE_FILTERS.map((type) => {
                const active = typeFilters.has(type)
                return (
                  <button
                    key={type}
                    onClick={() => toggleType(type)}
                    className={`flex items-center gap-1.5 text-[12px] font-medium px-3 py-1.5 rounded-full border transition-all ${
                      active
                        ? 'bg-[#00FFA7]/10 text-[#00FFA7] border-[#00FFA7]/30'
                        : 'bg-transparent text-[#667085] border-[#344054] hover:border-[#667085] hover:text-[#D0D5DD]'
                    }`}
                  >
                    {type === 'routines' && <Clock size={12} />}
                    {type === 'heartbeats' && <Activity size={12} />}
                    {type === 'triggers' && <Zap size={12} />}
                    <span className="capitalize">{type}</span>
                  </button>
                )
              })}
            </div>

            {/* Divider */}
            <span className="text-[#344054] hidden sm:block">|</span>

            {/* Status select */}
            <select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value as StatusFilter)}
              className="bg-[#161b22] border border-[#21262d] text-[#D0D5DD] text-[12px] rounded-lg px-3 py-1.5 focus:outline-none focus:border-[#00FFA7]/40 cursor-pointer"
            >
              <option value="all">All statuses</option>
              <option value="success">Success</option>
              <option value="error">Error</option>
              <option value="running">Running</option>
            </select>

            {/* Period tabs */}
            <div className="flex items-center bg-[#161b22] border border-[#21262d] rounded-lg overflow-hidden">
              {PERIOD_TABS.map((p) => (
                <button
                  key={p.key}
                  onClick={() => setPeriod(p.key)}
                  className={`text-[12px] px-3 py-1.5 transition-colors ${
                    period === p.key
                      ? 'bg-[#00FFA7]/10 text-[#00FFA7]'
                      : 'text-[#667085] hover:text-[#D0D5DD]'
                  }`}
                >
                  {p.label}
                </button>
              ))}
            </div>

            {/* Search */}
            <input
              type="text"
              value={displaySearch}
              onChange={(e) => handleSearchChange(e.target.value)}
              placeholder="Search by name..."
              className="bg-[#161b22] border border-[#21262d] text-[#D0D5DD] text-[12px] rounded-lg px-3 py-1.5 w-48 focus:outline-none focus:border-[#00FFA7]/40 placeholder-[#667085]"
            />

            {/* Pause */}
            <button
              onClick={() => setPaused((p) => !p)}
              className={`ml-auto text-[12px] px-3 py-1.5 rounded-lg border transition-all ${
                paused
                  ? 'bg-amber-500/10 text-amber-400 border-amber-500/20'
                  : 'bg-transparent text-[#667085] border-[#344054] hover:text-[#D0D5DD]'
              }`}
            >
              {paused ? 'Paused' : 'Auto-refresh on'}
            </button>
          </div>
        </div>

        {/* List */}
        <div className="bg-[#161b22] border border-[#21262d] rounded-2xl overflow-hidden">
          {loading ? (
            <div>
              {[...Array(6)].map((_, i) => <SkeletonRow key={i} />)}
            </div>
          ) : filtered.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-20">
              <div className="flex items-center justify-center w-14 h-14 rounded-2xl bg-[#21262d] border border-[#344054] mb-4">
                <Activity size={24} className="text-[#3F3F46]" />
              </div>
              <p className="text-[#667085] text-sm">No activity in this period.</p>
              <p className="text-[#3F3F46] text-xs mt-1">Try expanding the range.</p>
            </div>
          ) : (
            <>
              {paginated.map((item, idx) => {
                const isLast = idx === paginated.length - 1
                return (
                  <button
                    key={item.id}
                    onClick={() => setSelectedItem(item)}
                    className={`w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-white/[0.03] cursor-pointer transition-colors ${
                      !isLast ? 'border-b border-[#21262d]/60' : ''
                    } ${selectedItem?.id === item.id ? 'bg-white/[0.02]' : ''}`}
                  >
                    <RowIcon type={item.type} />

                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-0.5">
                        <span className="text-sm font-medium text-[#e6edf3] truncate">{item.name}</span>
                      </div>
                      <div className="flex items-center gap-2 flex-wrap">
                        <TypeBadge type={item.type} />
                        <span className="text-[#667085] text-[11px]">·</span>
                        <span className="text-[#667085] text-[11px]">
                          {formatDuration(item.duration_ms, item.duration_seconds)}
                        </span>
                        {item.triggered_by && (
                          <>
                            <span className="text-[#667085] text-[11px]">·</span>
                            <span className="text-[#667085] text-[11px]">{item.triggered_by}</span>
                          </>
                        )}
                      </div>
                    </div>

                    <div className="flex items-center gap-3 shrink-0">
                      <StatusPill status={item.status} />
                      <div className="text-right hidden sm:block">
                        <p className="text-[#667085] text-[11px]">{relativeTime(item.started_at)}</p>
                        <p className="text-[#3F3F46] text-[10px]">{formatTime(item.started_at)}</p>
                      </div>
                      <ChevronRight size={14} className="text-[#3F3F46]" />
                    </div>
                  </button>
                )
              })}

              {hasMore && (
                <div className="px-4 py-3 border-t border-[#21262d]/60">
                  <button
                    onClick={() => setPage((p) => p + 1)}
                    className="text-[12px] text-[#667085] hover:text-[#00FFA7] transition-colors"
                  >
                    Load more ({filtered.length - paginated.length} remaining)
                  </button>
                </div>
              )}
            </>
          )}
        </div>

        {/* Summary line */}
        {!loading && filtered.length > 0 && (
          <p className="text-[#667085] text-xs mt-3 px-1">
            {filtered.length} execution{filtered.length !== 1 ? 's' : ''} · refreshes every 30s
            {paused ? ' (paused)' : ''}
          </p>
        )}
      </div>

      {/* Drawer */}
      <ActivityDrawer item={selectedItem} onClose={() => setSelectedItem(null)} />
    </>
  )
}
