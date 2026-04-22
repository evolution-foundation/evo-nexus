import { useEffect, useState } from 'react'
import {
  Clock,
  ArrowUpDown,
  Play,
  Loader2,
  CheckCircle2,
  XCircle,
  Activity,
  DollarSign,
  Zap,
  TrendingUp,
  Bot,
  Brain,
  FolderKanban,
  Heart,
  GraduationCap,
  Target,
  Camera,
  Users,
  Compass,
  Cog,
  type LucideIcon,
} from 'lucide-react'
import { api } from '../lib/api'
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from 'recharts'
import { useTranslation } from 'react-i18next'

interface Routine {
  name: string
  agent: string
  runs: number
  success_pct: number
  avg_time: string
  total_tokens: number
  total_cost: number
  avg_cost: number
  last_run: string
  status: 'healthy' | 'warning' | 'critical'
}

// Agent color mapping (same as Agents page)
const AGENT_META: Record<string, { icon: LucideIcon; color: string; colorMuted: string; label: string }> = {
  'atlas': { icon: FolderKanban, color: '#60A5FA', colorMuted: 'rgba(96,165,250,0.12)', label: 'Atlas' },
  'clawdia': { icon: Brain, color: '#22D3EE', colorMuted: 'rgba(34,211,238,0.12)', label: 'Clawdia' },
  'flux': { icon: DollarSign, color: '#34D399', colorMuted: 'rgba(52,211,153,0.12)', label: 'Flux' },
  'kai': { icon: Heart, color: '#F472B6', colorMuted: 'rgba(244,114,182,0.12)', label: 'Kai' },
  'mentor': { icon: GraduationCap, color: '#FBBF24', colorMuted: 'rgba(251,191,36,0.12)', label: 'Mentor' },
  'nex': { icon: Target, color: '#FB923C', colorMuted: 'rgba(251,146,60,0.12)', label: 'Nex' },
  'pixel': { icon: Camera, color: '#A78BFA', colorMuted: 'rgba(167,139,250,0.12)', label: 'Pixel' },
  'pulse': { icon: Users, color: '#2DD4BF', colorMuted: 'rgba(45,212,191,0.12)', label: 'Pulse' },
  'sage': { icon: Compass, color: '#818CF8', colorMuted: 'rgba(129,140,248,0.12)', label: 'Sage' },
}

const SYSTEM_META = { icon: Cog, color: '#8b949e', colorMuted: 'rgba(139,148,158,0.12)', label: 'systematic' }
const DEFAULT_AGENT_META = { icon: Bot, color: '#8b949e', colorMuted: 'rgba(139,148,158,0.12)', label: '' }

function getAgentMeta(agentName: string) {
  if (!agentName) return DEFAULT_AGENT_META
  if (agentName.toLowerCase() === 'system') return SYSTEM_META
  const key = Object.keys(AGENT_META).find((k) => agentName.toLowerCase().includes(k))
  return key ? AGENT_META[key] : DEFAULT_AGENT_META
}

// Relative time helper
function relativeTime(dateStr: string): string {
  if (!dateStr || dateStr === '-') return '-'
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

function transformRoutineMetrics(data: any): Routine[] {
  if (Array.isArray(data)) return data
  const metrics = data?.metrics || {}
  return Object.entries(metrics).map(([name, m]: [string, any]) => {
    const runs = Number(m.runs || 0)
    const successes = Number(m.successes || 0)
    const successRate = Number(m.success_rate || (runs > 0 ? (successes / runs) * 100 : 0))
    const totalTokens = Number(m.total_input_tokens || 0) + Number(m.total_output_tokens || 0)
    const totalCost = Number(m.total_cost_usd || 0)
    const avgCost = Number(m.avg_cost_usd || 0)
    const avgSeconds = Number(m.avg_seconds || 0)
    const status: 'healthy' | 'warning' | 'critical' =
      successRate >= 90 ? 'healthy' : successRate >= 70 ? 'warning' : 'critical'
    return {
      name,
      agent: m.agent || '',
      runs,
      success_pct: Math.round(successRate),
      avg_time: avgSeconds > 0 ? `${avgSeconds.toFixed(1)}s` : '-',
      total_tokens: totalTokens,
      total_cost: totalCost,
      avg_cost: avgCost,
      last_run: m.last_run || '-',
      status,
    }
  })
}

type SortKey = keyof Routine
type SortDir = 'asc' | 'desc'
type RunStatus = 'idle' | 'running' | 'success' | 'error'

// Stat Card (matches Overview design)
function StatCard({ label, value, icon: Icon }: { label: string; value: string | number; icon: LucideIcon }) {
  return (
    <div className="group relative bg-[#161b22] border border-[#21262d] rounded-2xl p-5 transition-all duration-300 hover:border-[#00FFA7]/40 hover:shadow-[0_0_24px_rgba(0,255,167,0.06)]">
      <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-[#00FFA7]/20 to-transparent rounded-t-2xl" />
      <div className="flex items-start justify-between mb-3">
        <div className="flex items-center justify-center w-9 h-9 rounded-xl bg-[#00FFA7]/8 border border-[#00FFA7]/15">
          <Icon size={18} className="text-[#00FFA7]" />
        </div>
      </div>
      <p className="text-3xl font-bold text-[#e6edf3] tracking-tight">{value}</p>
      <p className="text-sm text-[#667085] mt-1">{label}</p>
    </div>
  )
}

function SkeletonStat() {
  return <div className="skeleton h-32 rounded-2xl" />
}

function SkeletonRow() {
  return <div className="skeleton h-14 rounded-lg mb-2" />
}

export default function Routines() {
  const { t } = useTranslation()
  const [routines, setRoutines] = useState<Routine[]>([])
  const [loading, setLoading] = useState(true)
  const [sortKey, setSortKey] = useState<SortKey>('name')
  const [sortDir, setSortDir] = useState<SortDir>('asc')
  const [runStatus, setRunStatus] = useState<Record<string, RunStatus>>({})

  useEffect(() => {
    api.get('/routines')
      .then((data) => setRoutines(transformRoutineMetrics(data)))
      .catch(() => setRoutines([]))
      .finally(() => setLoading(false))
  }, [])

  const handleSort = (key: SortKey) => {
    if (sortKey === key) {
      setSortDir(sortDir === 'asc' ? 'desc' : 'asc')
    } else {
      setSortKey(key)
      setSortDir('asc')
    }
  }

  const sorted = [...routines].sort((a, b) => {
    const av = a[sortKey]
    const bv = b[sortKey]
    const dir = sortDir === 'asc' ? 1 : -1
    if (typeof av === 'string') return av.localeCompare(bv as string) * dir
    return ((av as number) - (bv as number)) * dir
  })

  const totals = (routines || []).reduce(
    (acc, r) => ({
      runs: acc.runs + Number(r.runs || 0),
      total_tokens: acc.total_tokens + Number(r.total_tokens || 0),
      total_cost: acc.total_cost + Number(r.total_cost || 0),
    }),
    { runs: 0, total_tokens: 0, total_cost: 0 }
  )

  const avgSuccessRate = routines.length > 0
    ? Math.round(routines.reduce((sum, r) => sum + r.success_pct, 0) / routines.length)
    : 0

  const chartData = (routines || []).map((r) => ({
    name: (r.name || '').length > 15 ? r.name.slice(0, 15) + '...' : (r.name || ''),
    cost: Number(r.total_cost || 0),
  }))

  const SortHeader = ({ label, field, align = 'left' }: { label: string; field: SortKey; align?: 'left' | 'right' }) => (
    <th
      className={`pb-3 pr-4 cursor-pointer hover:text-[#e6edf3] transition-colors select-none ${align === 'right' ? 'text-right' : 'text-left'}`}
      onClick={() => handleSort(field)}
    >
      <span className={`inline-flex items-center gap-1 ${align === 'right' ? 'justify-end' : ''}`}>
        {label}
        <ArrowUpDown size={11} className={sortKey === field ? 'text-[#00FFA7]' : 'opacity-40'} />
      </span>
    </th>
  )

  return (
    <div className="max-w-[1400px] mx-auto">
      {/* Header */}
      <div className="mb-8">
        <h1 className="text-2xl font-bold text-[#e6edf3] tracking-tight">{t('routines.title')}</h1>
        <p className="text-[#667085] text-sm mt-1">Automated routine performance</p>
      </div>

      {/* Stats Bar */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
        {loading ? (
          <>
            <SkeletonStat />
            <SkeletonStat />
            <SkeletonStat />
            <SkeletonStat />
          </>
        ) : (
          <>
            <StatCard label="Total Routines" value={routines.length} icon={Clock} />
            <StatCard label="Total Runs" value={totals.runs.toLocaleString()} icon={Activity} />
            <StatCard label="Avg Success Rate" value={`${avgSuccessRate}%`} icon={TrendingUp} />
            <StatCard label="Total Cost" value={`$${totals.total_cost.toFixed(2)}`} icon={DollarSign} />
          </>
        )}
      </div>

      {loading ? (
        <div className="space-y-2">
          {[...Array(6)].map((_, i) => <SkeletonRow key={i} />)}
        </div>
      ) : routines.length === 0 ? (
        <div className="text-center py-16">
          <div className="mx-auto mb-4 flex h-16 w-16 items-center justify-center rounded-2xl bg-[#161b22] border border-[#21262d]">
            <Clock size={32} className="text-[#3F3F46]" />
          </div>
          <p className="text-[#667085] text-lg">No routines data</p>
          <p className="text-[#3F3F46] text-sm mt-1">Run some routines to see metrics here</p>
        </div>
      ) : (
        <>
          {/* Cost Chart */}
          <div className="bg-[#161b22] border border-[#21262d] rounded-2xl p-6 mb-6 transition-all duration-300 hover:shadow-[0_0_32px_rgba(0,255,167,0.04)]">
            <div className="flex items-center gap-2.5 mb-4">
              <div className="flex items-center justify-center w-7 h-7 rounded-lg bg-[#00FFA7]/8 border border-[#00FFA7]/15">
                <Zap size={14} className="text-[#00FFA7]" />
              </div>
              <h2 className="text-base font-semibold text-[#e6edf3]">Cost per Routine</h2>
            </div>
            <ResponsiveContainer width="100%" height={250}>
              <BarChart data={chartData}>
                <CartesianGrid strokeDasharray="3 3" stroke="#21262d" />
                <XAxis dataKey="name" tick={{ fill: '#667085', fontSize: 11 }} angle={-30} textAnchor="end" height={60} />
                <YAxis tick={{ fill: '#667085', fontSize: 11 }} tickFormatter={(v) => `$${v.toFixed(2)}`} />
                <Tooltip
                  contentStyle={{ background: '#161b22', border: '1px solid #21262d', borderRadius: '12px', color: '#e6edf3' }}
                  formatter={(value: unknown) => [`$${Number(value).toFixed(4)}`, 'Cost']}
                />
                <Bar dataKey="cost" fill="#00FFA7" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>

          {/* Table */}
          <div className="bg-[#161b22] border border-[#21262d] rounded-2xl overflow-hidden transition-all duration-300 hover:shadow-[0_0_32px_rgba(0,255,167,0.04)]">
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-[#667085] text-[11px] uppercase tracking-wider font-medium">
                    <th className="p-4 w-8"><span className="sr-only">Status</span></th>
                    <SortHeader label="Name" field="name" />
                    <SortHeader label="Agent" field="agent" />
                    <SortHeader label="Runs" field="runs" align="right" />
                    <SortHeader label="Success %" field="success_pct" align="right" />
                    <SortHeader label="Avg Time" field="avg_time" align="right" />
                    <SortHeader label="Total Tokens" field="total_tokens" align="right" />
                    <SortHeader label="Total Cost" field="total_cost" align="right" />
                    <SortHeader label="Avg Cost" field="avg_cost" align="right" />
                    <th className="pb-3 pr-4 text-right">Last Run</th>
                    <th className="p-4 text-right">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {sorted.map((r, i) => {
                    const agentMeta = getAgentMeta(r.agent)
                    return (
                      <tr key={i} className="border-t border-[#21262d]/60 hover:bg-[#161b22] transition-colors">
                        {/* Status dot */}
                        <td className="p-4">
                          <span
                            className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium border"
                            style={{
                              backgroundColor:
                                r.status === 'healthy' ? 'rgba(0,255,167,0.10)' :
                                r.status === 'warning' ? 'rgba(251,191,36,0.10)' :
                                'rgba(239,68,68,0.10)',
                              color:
                                r.status === 'healthy' ? '#00FFA7' :
                                r.status === 'warning' ? '#FBBF24' :
                                '#EF4444',
                              borderColor:
                                r.status === 'healthy' ? 'rgba(0,255,167,0.25)' :
                                r.status === 'warning' ? 'rgba(251,191,36,0.25)' :
                                'rgba(239,68,68,0.25)',
                            }}
                          >
                            <span
                              className="w-1.5 h-1.5 rounded-full"
                              style={{
                                backgroundColor:
                                  r.status === 'healthy' ? '#00FFA7' :
                                  r.status === 'warning' ? '#FBBF24' :
                                  '#EF4444',
                              }}
                            />
                            {r.status}
                          </span>
                        </td>
                        <td className="py-3 pr-4 text-[#e6edf3] text-[13px] font-medium">{r.name}</td>
                        {/* Agent badge with color */}
                        <td className="py-3 pr-4">
                          {r.agent ? (
                            <span
                              className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[11px] font-medium border"
                              style={{
                                backgroundColor: agentMeta.colorMuted,
                                color: agentMeta.color,
                                borderColor: `${agentMeta.color}33`,
                              }}
                            >
                              {(() => { const AgentIcon = agentMeta.icon; return <AgentIcon size={11} />; })()}
                              {r.agent}
                            </span>
                          ) : (
                            <span className="text-[#667085] text-[13px]">-</span>
                          )}
                        </td>
                        <td className="py-3 pr-4 text-right text-[#D0D5DD] tabular-nums text-[13px]">{r.runs}</td>
                        <td className="py-3 pr-4 text-right">
                          <span className={`text-[13px] font-medium ${
                            r.success_pct >= 90 ? 'text-[#00FFA7]' : r.success_pct >= 70 ? 'text-[#FBBF24]' : 'text-red-400'
                          }`}>
                            {r.success_pct}%
                          </span>
                        </td>
                        <td className="py-3 pr-4 text-[#667085] text-right text-[13px]">{r.avg_time}</td>
                        <td className="py-3 pr-4 text-[#D0D5DD] text-right tabular-nums text-[13px]">{Number(r.total_tokens || 0).toLocaleString()}</td>
                        <td className="py-3 pr-4 text-[#D0D5DD] text-right tabular-nums text-[13px]">${Number(r.total_cost || 0).toFixed(4)}</td>
                        <td className="py-3 pr-4 text-[#667085] text-right tabular-nums text-[13px]">${Number(r.avg_cost || 0).toFixed(4)}</td>
                        <td className="py-3 pr-4 text-right text-[#667085] text-[13px] whitespace-nowrap">{relativeTime(r.last_run)}</td>
                        <td className="py-3 pr-4 text-right">
                          {(() => {
                            const status = runStatus[r.name] || 'idle'
                            if (status === 'running') return (
                              <span className="inline-flex items-center gap-1 text-xs px-2.5 py-1 rounded-full bg-[#FBBF24]/10 text-[#FBBF24] border border-[#FBBF24]/20">
                                <Loader2 size={10} className="animate-spin" /> Running
                              </span>
                            )
                            if (status === 'success') return (
                              <span className="inline-flex items-center gap-1 text-xs px-2.5 py-1 rounded-full bg-[#00FFA7]/10 text-[#00FFA7] border border-[#00FFA7]/20">
                                <CheckCircle2 size={10} /> Started
                              </span>
                            )
                            if (status === 'error') return (
                              <span className="inline-flex items-center gap-1 text-xs px-2.5 py-1 rounded-full bg-red-500/10 text-red-400 border border-red-500/20">
                                <XCircle size={10} /> Failed
                              </span>
                            )
                            return (
                              <button
                                onClick={async () => {
                                  const routineId = r.name.replace(/ /g, '-').toLowerCase()
                                  setRunStatus(prev => ({ ...prev, [r.name]: 'running' }))
                                  try {
                                    await api.post(`/routines/${routineId}/run`)
                                    setRunStatus(prev => ({ ...prev, [r.name]: 'success' }))
                                    setTimeout(() => setRunStatus(prev => ({ ...prev, [r.name]: 'idle' })), 5000)
                                  } catch {
                                    setRunStatus(prev => ({ ...prev, [r.name]: 'error' }))
                                    setTimeout(() => setRunStatus(prev => ({ ...prev, [r.name]: 'idle' })), 5000)
                                  }
                                }}
                                className="inline-flex items-center gap-1 text-xs px-2.5 py-1 rounded-full bg-[#00FFA7]/10 text-[#00FFA7] border border-[#00FFA7]/20 hover:bg-[#00FFA7]/20 hover:shadow-[0_0_12px_rgba(0,255,167,0.10)] transition-all"
                                title={`Run ${r.name}`}
                              >
                                <Play size={10} /> Run
                              </button>
                            )
                          })()}
                        </td>
                      </tr>
                    )
                  })}
                  {/* Totals row */}
                  <tr className="border-t-2 border-[#00FFA7]/20 bg-[#00FFA7]/[0.03]">
                    <td className="p-4" />
                    <td className="py-3 pr-4 text-[#00FFA7] text-[13px] font-semibold">TOTAL</td>
                    <td className="py-3 pr-4" />
                    <td className="py-3 pr-4 text-[#e6edf3] text-right text-[13px] font-semibold tabular-nums">{totals.runs}</td>
                    <td className="py-3 pr-4" />
                    <td className="py-3 pr-4" />
                    <td className="py-3 pr-4 text-[#e6edf3] text-right text-[13px] font-semibold tabular-nums">{Number(totals.total_tokens || 0).toLocaleString()}</td>
                    <td className="py-3 pr-4 text-[#e6edf3] text-right text-[13px] font-semibold tabular-nums">${Number(totals.total_cost || 0).toFixed(4)}</td>
                    <td className="py-3 pr-4" />
                    <td className="py-3 pr-4" />
                    <td className="py-3 pr-4" />
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
