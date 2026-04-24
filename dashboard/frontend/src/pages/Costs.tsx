import { useEffect, useState } from 'react'
import { DollarSign, Zap, Activity, Calculator, Image, type LucideIcon } from 'lucide-react'
import { api } from '../lib/api'
import { useTranslation } from 'react-i18next'
import {
  LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid,
  PieChart, Pie, Cell, Legend,
} from 'recharts'

interface CostData {
  today: number
  week: number
  month_estimate: number
  total_cost: number
  routines_total_cost: number
  heartbeats_total_cost: number
  total_runs: number
  daily: { date: string; cost: number }[]
  by_agent: { agent: string; cost: number }[]
  by_routine: {
    name: string
    runs: number
    tokens: number
    cost: number
    total_cost: number
    avg_cost: number
    agent: string
  }[]
  by_heartbeat: {
    name: string
    agent: string
    runs: number
    total_cost: number
    avg_cost: number
  }[]
}

function normalizeCostData(raw: any): CostData {
  const byRoutine = Array.isArray(raw?.by_routine) ? raw.by_routine : []
  const byHeartbeat = Array.isArray(raw?.by_heartbeat) ? raw.by_heartbeat : []
  const byAgent = Array.isArray(raw?.by_agent) ? raw.by_agent : []
  const totalCost = Number(raw?.total_cost || 0)
  const normalizedByRoutine = byRoutine.map((r: any) => ({
    ...r,
    name: r.name || '',
    runs: Number(r.runs || 0),
    total_cost: Number(r.total_cost || r.cost || 0),
    avg_cost: Number(r.avg_cost || (r.runs ? (Number(r.cost || 0) / Number(r.runs || 1)) : 0)),
  }))
  const normalizedByHeartbeat = byHeartbeat.map((h: any) => ({
    ...h,
    name: h.name || '',
    agent: h.agent || '',
    runs: Number(h.runs || 0),
    total_cost: Number(h.total_cost || 0),
    avg_cost: Number(h.avg_cost || 0),
  }))
  return {
    today: Number(raw?.today || 0),
    week: Number(raw?.week || 0),
    month_estimate: Number(raw?.month_estimate || totalCost),
    total_cost: totalCost,
    routines_total_cost: Number(raw?.routines_total_cost || 0),
    heartbeats_total_cost: Number(raw?.heartbeats_total_cost || 0),
    total_runs: Number(raw?.total_runs || 0),
    daily: Array.isArray(raw?.daily) ? raw.daily : [],
    by_agent: byAgent,
    by_routine: normalizedByRoutine,
    by_heartbeat: normalizedByHeartbeat,
  }
}

const COLORS = ['#00FFA7', '#3B82F6', '#F59E0B', '#EF4444', '#8B5CF6', '#EC4899', '#14B8A6', '#F97316']

// --- Stat Card (matches Overview.tsx) ---
function StatCard({
  label,
  value,
  subtitle,
  icon: Icon,
}: {
  label: string
  value: string | number
  subtitle?: string
  icon: LucideIcon
}) {
  return (
    <div className="group relative bg-[#161b22] border border-[#21262d] rounded-2xl p-5 transition-all duration-300 hover:border-[#00FFA7]/40 hover:shadow-[0_0_24px_rgba(0,255,167,0.06)]">
      {/* Subtle top gradient accent */}
      <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-[#00FFA7]/20 to-transparent rounded-t-2xl" />

      <div className="flex items-start justify-between mb-3">
        <div className="flex items-center justify-center w-9 h-9 rounded-xl bg-[#00FFA7]/8 border border-[#00FFA7]/15">
          <Icon size={18} className="text-[#00FFA7]" />
        </div>
      </div>

      <p className="text-3xl font-bold text-[#e6edf3] tracking-tight">{value}</p>
      <p className="text-sm text-[#667085] mt-1">{label}</p>
      {subtitle && <p className="text-xs text-[#667085]/60 mt-0.5">{subtitle}</p>}
    </div>
  )
}

// --- Skeleton ---
function SkeletonCard() {
  return (
    <div className="rounded-2xl border border-[#21262d] bg-[#161b22] p-5">
      <div className="flex items-start justify-between mb-3">
        <div className="h-9 w-9 rounded-xl bg-[#21262d] animate-pulse" />
      </div>
      <div className="h-8 w-24 rounded bg-[#21262d] animate-pulse mb-2" />
      <div className="h-4 w-20 rounded bg-[#21262d] animate-pulse" />
    </div>
  )
}

interface ImageCostEntry {
  timestamp: string
  model: string
  provider: string
  mode: string
  output_file: string
  size_bytes: number
  elapsed_seconds: number
  token_usage: { prompt_tokens: number; completion_tokens: number; total_tokens: number }
  estimated_cost_usd?: number
}

interface ImageCosts {
  entries: ImageCostEntry[]
  totals: { count: number; total_tokens: number; total_seconds: number; total_bytes: number; total_cost_usd?: number }
}

function relativeTime(ts: string): string {
  try {
    const diff = Date.now() - new Date(ts).getTime()
    const min = Math.floor(diff / 60000)
    if (min < 1) return 'just now'
    if (min < 60) return `${min}m ago`
    const hr = Math.floor(min / 60)
    if (hr < 24) return `${hr}h ago`
    return `${Math.floor(hr / 24)}d ago`
  } catch { return ts }
}

function formatBytes(b: number): string {
  if (b < 1024) return `${b} B`
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(0)} KB`
  return `${(b / (1024 * 1024)).toFixed(1)} MB`
}

export default function Costs() {
  const { t } = useTranslation()
  const [data, setData] = useState<CostData | null>(null)
  const [imageCosts, setImageCosts] = useState<ImageCosts | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    Promise.all([
      api.get('/costs').catch(() => null),
      api.get('/routines/image-costs').catch(() => null),
    ]).then(([costRaw, imgRaw]) => {
      if (costRaw) setData(normalizeCostData(costRaw))
      if (imgRaw) setImageCosts(imgRaw)
    }).finally(() => setLoading(false))
  }, [])

  if (loading) {
    return (
      <div className="max-w-[1400px] mx-auto">
        <div className="mb-8">
          <h1 className="text-2xl font-bold text-[#e6edf3] tracking-tight">{t('costs.title')}</h1>
          <p className="text-[#667085] text-sm mt-1">AI usage cost analysis</p>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
          <SkeletonCard />
          <SkeletonCard />
          <SkeletonCard />
          <SkeletonCard />
        </div>
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
          <div className="skeleton h-80 rounded-2xl" />
          <div className="skeleton h-80 rounded-2xl" />
        </div>
      </div>
    )
  }

  if (!data) {
    return (
      <div className="text-center py-16">
        <div className="mx-auto mb-4 flex h-16 w-16 items-center justify-center rounded-2xl bg-[#161b22] border border-[#21262d]">
          <DollarSign size={32} className="text-[#3F3F46]" />
        </div>
        <p className="text-[#667085] text-lg">No cost data available</p>
        <p className="text-[#3F3F46] text-sm mt-1">Cost data will appear after routines run</p>
      </div>
    )
  }

  const totalRuns = Number(data.total_runs || 0) || (data.by_routine || []).reduce((sum, r) => sum + Number(r.runs || 0), 0)
  const imageTotalCost = imageCosts?.totals?.total_cost_usd || 0
  const grandTotal = Number(data.total_cost || 0) + imageTotalCost
  const avgCostPerRun = totalRuns > 0 ? grandTotal / totalRuns : 0

  return (
    <div className="max-w-[1400px] mx-auto">
      {/* Header */}
      <div className="mb-8">
        <h1 className="text-2xl font-bold text-[#e6edf3] tracking-tight">{t('costs.title')}</h1>
        <p className="text-[#667085] text-sm mt-1">AI usage cost analysis</p>
      </div>

      {/* KPI Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
        <StatCard
          label="Today"
          value={`$${Number(data.today || 0).toFixed(2)}`}
          subtitle="Current day spend"
          icon={DollarSign}
        />
        <StatCard
          label="This Week"
          value={`$${Number(data.week || 0).toFixed(2)}`}
          subtitle="Rolling 7 days"
          icon={Activity}
        />
        <StatCard
          label="Total (All)"
          value={`$${grandTotal.toFixed(2)}`}
          subtitle={imageTotalCost > 0 ? `Routines + Heartbeats + ${imageCosts?.totals?.count || 0} images` : 'Routines + Heartbeats'}
          icon={Zap}
        />
        <StatCard
          label="Avg Cost / Run"
          value={`$${avgCostPerRun.toFixed(4)}`}
          subtitle={`${totalRuns} total runs`}
          icon={Calculator}
        />
      </div>

      {/* Charts */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
        {/* Line Chart: Cost per day */}
        <div className="bg-[#161b22] border border-[#21262d] rounded-2xl p-6 transition-all duration-300 hover:shadow-[0_0_32px_rgba(0,255,167,0.04)]">
          <h2 className="text-base font-semibold text-[#e6edf3] mb-4 flex items-center gap-2.5">
            <div className="flex items-center justify-center w-7 h-7 rounded-lg bg-[#00FFA7]/8 border border-[#00FFA7]/15">
              <Activity size={14} className="text-[#00FFA7]" />
            </div>
            Cost per Day
          </h2>
          <ResponsiveContainer width="100%" height={250}>
            <LineChart data={data.daily}>
              <CartesianGrid strokeDasharray="3 3" stroke="#21262d" />
              <XAxis dataKey="date" tick={{ fill: '#667085', fontSize: 11 }} />
              <YAxis tick={{ fill: '#667085', fontSize: 11 }} tickFormatter={(v) => `$${v.toFixed(2)}`} />
              <Tooltip
                contentStyle={{ background: '#161b22', border: '1px solid #21262d', borderRadius: '12px', color: '#e6edf3' }}
                formatter={(value: unknown) => [`$${Number(value).toFixed(4)}`, 'Cost']}
              />
              <Line type="monotone" dataKey="cost" stroke="#00FFA7" strokeWidth={2} dot={{ fill: '#00FFA7', r: 3 }} />
            </LineChart>
          </ResponsiveContainer>
        </div>

        {/* Pie Chart: Cost per agent */}
        <div className="bg-[#161b22] border border-[#21262d] rounded-2xl p-6 transition-all duration-300 hover:shadow-[0_0_32px_rgba(0,255,167,0.04)]">
          <h2 className="text-base font-semibold text-[#e6edf3] mb-4 flex items-center gap-2.5">
            <div className="flex items-center justify-center w-7 h-7 rounded-lg bg-[#00FFA7]/8 border border-[#00FFA7]/15">
              <DollarSign size={14} className="text-[#00FFA7]" />
            </div>
            Cost per Agent
          </h2>
          <ResponsiveContainer width="100%" height={250}>
            <PieChart>
              <Pie
                data={data.by_agent}
                dataKey="cost"
                nameKey="agent"
                cx="50%"
                cy="50%"
                outerRadius={90}
                label={({ name, percent }: any) => `${name || ''} ${((percent || 0) * 100).toFixed(0)}%`}
                labelLine={false}
              >
                {(data.by_agent || []).map((_, i) => (
                  <Cell key={i} fill={COLORS[i % COLORS.length]} />
                ))}
              </Pie>
              <Tooltip
                contentStyle={{ background: '#161b22', border: '1px solid #21262d', borderRadius: '12px', color: '#e6edf3' }}
                formatter={(value: unknown) => [`$${Number(value).toFixed(4)}`, 'Cost']}
              />
              <Legend wrapperStyle={{ color: '#8b949e', fontSize: '12px' }} />
            </PieChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Per Routine Table */}
      <div className="bg-[#161b22] border border-[#21262d] rounded-2xl overflow-hidden transition-all duration-300 hover:shadow-[0_0_32px_rgba(0,255,167,0.04)]">
        <div className="p-5 border-b border-[#21262d]">
          <h2 className="text-base font-semibold text-[#e6edf3] flex items-center gap-2.5">
            <div className="flex items-center justify-center w-7 h-7 rounded-lg bg-[#00FFA7]/8 border border-[#00FFA7]/15">
              <Calculator size={14} className="text-[#00FFA7]" />
            </div>
            Per Routine Breakdown
          </h2>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-[#667085] text-[11px] uppercase tracking-wider font-medium">
                <th className="text-left p-4 pb-3">Routine</th>
                <th className="text-right p-4 pb-3">Runs</th>
                <th className="text-right p-4 pb-3">Total Cost</th>
                <th className="text-right p-4 pb-3">Avg Cost</th>
              </tr>
            </thead>
            <tbody>
              {(data.by_routine || []).map((r, i) => (
                <tr
                  key={i}
                  className="border-t border-[#21262d]/60 hover:bg-white/[0.02] transition-colors group"
                >
                  <td className="p-4 text-[#e6edf3] text-[13px] font-medium group-hover:text-white transition-colors">{r.name}</td>
                  <td className="p-4 text-right text-[#8b949e] tabular-nums text-[13px]">{Number(r.runs || 0)}</td>
                  <td className="p-4 text-right text-[#8b949e] tabular-nums text-[13px]">
                    <span className="text-[#e6edf3]">${Number(r.total_cost || 0).toFixed(4)}</span>
                  </td>
                  <td className="p-4 text-right text-[#667085] tabular-nums text-[13px]">${Number(r.avg_cost || 0).toFixed(4)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Per Heartbeat Table */}
      <div className="bg-[#161b22] border border-[#21262d] rounded-2xl overflow-hidden mt-6 transition-all duration-300 hover:shadow-[0_0_32px_rgba(0,255,167,0.04)]">
        <div className="p-5 border-b border-[#21262d]">
          <h2 className="text-base font-semibold text-[#e6edf3] flex items-center gap-2.5">
            <div className="flex items-center justify-center w-7 h-7 rounded-lg bg-[#00FFA7]/8 border border-[#00FFA7]/15">
              <Activity size={14} className="text-[#00FFA7]" />
            </div>
            Per Heartbeat Breakdown
          </h2>
        </div>
        {(data.by_heartbeat || []).length === 0 ? (
          <div className="p-8 text-center text-[#667085] text-sm">No heartbeat runs yet</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-[#667085] text-[11px] uppercase tracking-wider font-medium">
                  <th className="text-left p-4 pb-3">Heartbeat</th>
                  <th className="text-left p-4 pb-3">Agent</th>
                  <th className="text-right p-4 pb-3">Runs</th>
                  <th className="text-right p-4 pb-3">Total Cost</th>
                  <th className="text-right p-4 pb-3">Avg Cost</th>
                </tr>
              </thead>
              <tbody>
                {(data.by_heartbeat || []).map((h, i) => (
                  <tr
                    key={i}
                    className="border-t border-[#21262d]/60 hover:bg-white/[0.02] transition-colors group"
                  >
                    <td className="p-4 text-[#e6edf3] text-[13px] font-medium group-hover:text-white transition-colors">{h.name}</td>
                    <td className="p-4 text-[#8b949e] text-[13px]">{h.agent}</td>
                    <td className="p-4 text-right text-[#8b949e] tabular-nums text-[13px]">{Number(h.runs || 0)}</td>
                    <td className="p-4 text-right text-[#8b949e] tabular-nums text-[13px]">
                      <span className="text-[#e6edf3]">${Number(h.total_cost || 0).toFixed(4)}</span>
                    </td>
                    <td className="p-4 text-right text-[#667085] tabular-nums text-[13px]">${Number(h.avg_cost || 0).toFixed(4)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Image Generation Costs */}
      {imageCosts && imageCosts.entries.length > 0 && (
        <div className="bg-[#161b22] border border-[#21262d] rounded-2xl overflow-hidden mt-6 transition-all duration-300 hover:shadow-[0_0_32px_rgba(0,255,167,0.04)]">
          <div className="p-5 border-b border-[#21262d] flex items-center justify-between">
            <h2 className="text-base font-semibold text-[#e6edf3] flex items-center gap-2.5">
              <div className="flex items-center justify-center w-7 h-7 rounded-lg bg-[#F472B6]/10 border border-[#F472B6]/20">
                <Image size={14} className="text-[#F472B6]" />
              </div>
              Image Generation
            </h2>
            <div className="flex items-center gap-4 text-[11px] text-[#667085]">
              <span>{imageCosts.totals.count} images</span>
              <span>{imageCosts.totals.total_tokens.toLocaleString()} tokens</span>
              <span>{formatBytes(imageCosts.totals.total_bytes)}</span>
              <span>{imageCosts.totals.total_seconds}s total</span>
              {imageCosts.totals.total_cost_usd !== undefined && (
                <span className="text-[#00FFA7] font-medium">${imageCosts.totals.total_cost_usd.toFixed(2)}</span>
              )}
            </div>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-[#667085] text-[11px] uppercase tracking-wider font-medium">
                  <th className="text-left p-4 pb-3">Model</th>
                  <th className="text-left p-4 pb-3">Provider</th>
                  <th className="text-left p-4 pb-3">Output</th>
                  <th className="text-right p-4 pb-3">Tokens</th>
                  <th className="text-right p-4 pb-3">Size</th>
                  <th className="text-right p-4 pb-3">Time</th>
                  <th className="text-right p-4 pb-3">Est. Cost</th>
                  <th className="text-right p-4 pb-3">When</th>
                </tr>
              </thead>
              <tbody>
                {[...imageCosts.entries].reverse().map((e, i) => (
                  <tr key={i} className="border-t border-[#21262d]/60 hover:bg-white/[0.02] transition-colors group">
                    <td className="p-4">
                      <code className="text-[11px] text-[#F472B6] font-mono bg-[#F472B6]/8 px-2 py-0.5 rounded border border-[#F472B6]/15">
                        {e.model.split('/').pop()}
                      </code>
                    </td>
                    <td className="p-4 text-[#8b949e] text-[13px]">{e.provider} <span className="text-[#667085]">({e.mode})</span></td>
                    <td className="p-4 text-[#e6edf3] text-[13px] font-medium group-hover:text-white truncate max-w-[200px]" title={e.output_file}>
                      {e.output_file.split('/').pop()}
                    </td>
                    <td className="p-4 text-right text-[#8b949e] tabular-nums text-[13px]">{e.token_usage.total_tokens.toLocaleString()}</td>
                    <td className="p-4 text-right text-[#8b949e] tabular-nums text-[13px]">{formatBytes(e.size_bytes)}</td>
                    <td className="p-4 text-right text-[#667085] tabular-nums text-[13px]">{e.elapsed_seconds.toFixed(1)}s</td>
                    <td className="p-4 text-right tabular-nums text-[13px] text-[#00FFA7]">
                      {e.estimated_cost_usd !== undefined ? `$${e.estimated_cost_usd.toFixed(4)}` : '—'}
                    </td>
                    <td className="p-4 text-right text-[#667085] text-[13px] whitespace-nowrap">{relativeTime(e.timestamp)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}
