import { useEffect, useState, useCallback } from 'react'
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import {
  FileText,
  Clock,
  ArrowRight,
  Activity,
  DollarSign,
  Bot,
  Plug,
  TrendingUp,
  TrendingDown,
  Minus,
  BarChart3,
  GitBranch,
  Settings,
  type LucideIcon,
} from 'lucide-react'
import { api } from '../lib/api'
import HealthBadge from '../components/HealthBadge'

interface OverviewData {
  metrics: {
    label: string
    value: string | number
    delta?: string
    deltaType?: 'up' | 'down' | 'neutral'
  }[]
  recent_reports: {
    title: string
    path: string
    date: string
    area: string
  }[]
  routines: {
    name: string
    last_run: string
    status: 'healthy' | 'warning' | 'critical'
    runs: number
  }[]
}

interface ActiveAgent {
  name: string
  status?: string
}

// --- Area color mapping for report badges ---
const AREA_COLORS: Record<string, { bg: string; text: string; border: string }> = {
  'Operations': { bg: 'rgba(34,211,238,0.10)', text: '#22D3EE', border: 'rgba(34,211,238,0.25)' },
  'Finance': { bg: 'rgba(52,211,153,0.10)', text: '#34D399', border: 'rgba(52,211,153,0.25)' },
  'Projects': { bg: 'rgba(96,165,250,0.10)', text: '#60A5FA', border: 'rgba(96,165,250,0.25)' },
  'Community': { bg: 'rgba(251,191,36,0.10)', text: '#FBBF24', border: 'rgba(251,191,36,0.25)' },
  'Social': { bg: 'rgba(168,85,247,0.10)', text: '#A855F7', border: 'rgba(168,85,247,0.25)' },
  'Strategy': { bg: 'rgba(244,114,182,0.10)', text: '#F472B6', border: 'rgba(244,114,182,0.25)' },
  'Health': { bg: 'rgba(251,113,133,0.10)', text: '#FB7185', border: 'rgba(251,113,133,0.25)' },
  'Licensing': { bg: 'rgba(45,212,191,0.10)', text: '#2DD4BF', border: 'rgba(45,212,191,0.25)' },
}

function getAreaStyle(area: string) {
  const key = Object.keys(AREA_COLORS).find((k) => area.toLowerCase().includes(k.toLowerCase()))
  return key ? AREA_COLORS[key] : { bg: 'rgba(0,255,167,0.08)', text: '#00FFA7', border: 'rgba(0,255,167,0.20)' }
}

// --- Metric card icon mapping ---
const METRIC_ICONS: Record<string, LucideIcon> = {
  'Routines Executed': Activity,
  'Total Cost': DollarSign,
  'Agents': Bot,
  'Active Integrations': Plug,
}

function getMetricIcon(label: string): LucideIcon {
  for (const [key, icon] of Object.entries(METRIC_ICONS)) {
    if (label.toLowerCase().includes(key.toLowerCase())) return icon
  }
  return Activity
}

// --- Relative time helper ---
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

// --- Skeleton Components ---
function SkeletonCard() {
  return <div className="skeleton h-32 rounded-2xl" />
}

function SkeletonRow() {
  return <div className="skeleton h-14 rounded-lg mb-2" />
}

function SkeletonPill() {
  return <div className="skeleton h-8 w-24 rounded-full" />
}

// --- Stat Card ---
function StatCard({
  label,
  value,
  delta,
  deltaType = 'neutral',
  icon: Icon,
}: {
  label: string
  value: string | number
  delta?: string
  deltaType?: 'up' | 'down' | 'neutral'
  icon: LucideIcon
}) {
  const deltaColor = {
    up: 'text-[#00FFA7]',
    down: 'text-red-400',
    neutral: 'text-[#667085]',
  }[deltaType]

  const deltaBg = {
    up: 'bg-[#00FFA7]/10',
    down: 'bg-red-400/10',
    neutral: 'bg-[#667085]/10',
  }[deltaType]

  const DeltaIcon = {
    up: TrendingUp,
    down: TrendingDown,
    neutral: Minus,
  }[deltaType]

  return (
    <div className="group relative bg-[#161b22] border border-[#21262d] rounded-2xl p-5 transition-all duration-300 hover:border-[#00FFA7]/40 hover:shadow-[0_0_24px_rgba(0,255,167,0.06)]">
      {/* Subtle top gradient accent */}
      <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-[#00FFA7]/20 to-transparent rounded-t-2xl" />

      <div className="flex items-start justify-between mb-3">
        <div className="flex items-center justify-center w-9 h-9 rounded-xl bg-[#00FFA7]/8 border border-[#00FFA7]/15">
          <Icon size={18} className="text-[#00FFA7]" />
        </div>
        {delta && (
          <div className={`flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${deltaColor} ${deltaBg}`}>
            <DeltaIcon size={12} />
            <span>{delta}</span>
          </div>
        )}
      </div>

      <p className="text-3xl font-bold text-[#e6edf3] tracking-tight">{value}</p>
      <p className="text-sm text-[#667085] mt-1">{label}</p>
    </div>
  )
}

// --- Active Agents Bar ---
function ActiveAgentsBar({ agents, loading }: { agents: ActiveAgent[]; loading: boolean }) {
  if (loading) {
    return (
      <div className="flex items-center gap-3 mb-8 px-1">
        <SkeletonPill />
        <SkeletonPill />
        <SkeletonPill />
      </div>
    )
  }

  return (
    <div className="flex items-center gap-3 mb-8 flex-wrap">
      <span className="text-xs font-medium text-[#667085] uppercase tracking-wider mr-1">Active Agents</span>
      {agents.length === 0 ? (
        <span className="text-xs text-[#667085]/60 italic">No agents running</span>
      ) : (
        agents.map((agent, i) => (
          <span
            key={i}
            className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full text-xs font-medium bg-[#161b22] border border-[#21262d] text-[#e6edf3]"
          >
            <span className="relative flex h-2 w-2">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-[#00FFA7] opacity-75" />
              <span className="relative inline-flex rounded-full h-2 w-2 bg-[#00FFA7]" />
            </span>
            {agent.name}
          </span>
        ))
      )}
    </div>
  )
}

// --- Quick Actions ---
const QUICK_ACTIONS = [
  { label: 'Agents', icon: Bot, to: '/agents', hint: 'Talk to agents' },
  { label: 'Providers', icon: Settings, to: '/providers', hint: 'AI configuration' },
  { label: 'View Costs', icon: BarChart3, to: '/costs', hint: 'Financial overview' },
  { label: 'Check GitHub', icon: GitBranch, to: '/integrations', hint: 'Repo status' },
]

// --- Main Component ---
export default function Overview() {
  const { t } = useTranslation()
  const [data, setData] = useState<OverviewData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [activeAgents, setActiveAgents] = useState<ActiveAgent[]>([])
  const [agentsLoading, setAgentsLoading] = useState(true)

  useEffect(() => {
    api.get('/overview')
      .then(setData)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  const fetchActiveAgents = useCallback(() => {
    api.get('/agents/active')
      .then((agents: ActiveAgent[]) => {
        setActiveAgents(Array.isArray(agents) ? agents : [])
      })
      .catch(() => {
        setActiveAgents([])
      })
      .finally(() => setAgentsLoading(false))
  }, [])

  useEffect(() => {
    fetchActiveAgents()
    const interval = setInterval(fetchActiveAgents, 5000)
    return () => clearInterval(interval)
  }, [fetchActiveAgents])

  if (error) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="text-center">
          <p className="text-red-400 text-lg mb-2">Failed to load overview</p>
          <p className="text-[#667085] text-sm">{error}</p>
        </div>
      </div>
    )
  }

  const recentReports = data?.recent_reports?.slice(0, 5) ?? []
  const routines = data?.routines?.slice(0, 8) ?? []

  return (
    <div className="max-w-[1400px] mx-auto">
      {/* Header */}
      <div className="mb-8">
        <h1 className="text-2xl font-bold text-[#e6edf3] tracking-tight">{t('overview.title')}</h1>
        <p className="text-[#667085] text-sm mt-1">{t('overview.subtitle')}</p>
      </div>

      {/* Stat Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
        {loading ? (
          <>
            <SkeletonCard />
            <SkeletonCard />
            <SkeletonCard />
            <SkeletonCard />
          </>
        ) : (
          data?.metrics?.map((m, i) => (
            <StatCard
              key={i}
              label={m.label}
              value={m.value}
              delta={m.delta}
              deltaType={m.deltaType}
              icon={getMetricIcon(m.label)}
            />
          ))
        )}
      </div>

      {/* Active Agents Bar */}
      <ActiveAgentsBar agents={activeAgents} loading={agentsLoading} />

      {/* Main Content Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-8">
        {/* Recent Reports */}
        <div className="bg-[#161b22] border border-[#21262d] rounded-2xl p-6 transition-all duration-300 hover:border-[#21262d] hover:shadow-[0_0_32px_rgba(0,255,167,0.04)]">
          <div className="flex items-center justify-between mb-5">
            <h2 className="text-base font-semibold text-[#e6edf3] flex items-center gap-2.5">
              <div className="flex items-center justify-center w-7 h-7 rounded-lg bg-[#00FFA7]/8 border border-[#00FFA7]/15">
                <FileText size={14} className="text-[#00FFA7]" />
              </div>
              Recent Reports
            </h2>
            <Link to="/workspace" className="text-xs font-medium text-[#667085] hover:text-[#00FFA7] transition-colors flex items-center gap-1">
              View all <ArrowRight size={12} />
            </Link>
          </div>
          {loading ? (
            <div className="space-y-2">
              <SkeletonRow />
              <SkeletonRow />
              <SkeletonRow />
            </div>
          ) : recentReports.length ? (
            <div className="space-y-1">
              {recentReports.map((r, i) => {
                const areaStyle = getAreaStyle(r.area)
                return (
                  <Link
                    key={i}
                    to={`/workspace/${r.path?.replace(/^workspace\//, '') || ''}`}
                    className="flex items-center gap-3 p-3 rounded-xl hover:bg-white/[0.03] transition-all group"
                  >
                    <div className="flex items-center justify-center w-8 h-8 rounded-lg bg-white/[0.04] shrink-0">
                      <FileText size={14} className="text-[#667085] group-hover:text-[#e6edf3] transition-colors" />
                    </div>
                    <div className="flex-1 min-w-0">
                      <p className="text-sm text-[#e6edf3] group-hover:text-white transition-colors truncate">{r.title}</p>
                      <p className="text-xs text-[#667085] mt-0.5">{relativeTime(r.date)}</p>
                    </div>
                    <span
                      className="text-[10px] font-medium px-2 py-0.5 rounded-full border shrink-0"
                      style={{
                        backgroundColor: areaStyle.bg,
                        color: areaStyle.text,
                        borderColor: areaStyle.border,
                      }}
                    >
                      {r.area}
                    </span>
                  </Link>
                )
              })}
            </div>
          ) : (
            <div className="flex items-center justify-center h-32">
              <p className="text-[#667085] text-sm">No recent reports</p>
            </div>
          )}
        </div>

        {/* Routines */}
        <div className="bg-[#161b22] border border-[#21262d] rounded-2xl p-6 transition-all duration-300 hover:border-[#21262d] hover:shadow-[0_0_32px_rgba(0,255,167,0.04)]">
          <div className="flex items-center justify-between mb-5">
            <h2 className="text-base font-semibold text-[#e6edf3] flex items-center gap-2.5">
              <div className="flex items-center justify-center w-7 h-7 rounded-lg bg-[#00FFA7]/8 border border-[#00FFA7]/15">
                <Clock size={14} className="text-[#00FFA7]" />
              </div>
              Routines
            </h2>
            <Link to="/routines" className="text-xs font-medium text-[#667085] hover:text-[#00FFA7] transition-colors flex items-center gap-1">
              View all <ArrowRight size={12} />
            </Link>
          </div>
          {loading ? (
            <div className="space-y-2">
              <SkeletonRow />
              <SkeletonRow />
              <SkeletonRow />
              <SkeletonRow />
            </div>
          ) : routines.length ? (
            <div className="overflow-x-auto -mx-6 px-6">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-[#667085] text-[11px] uppercase tracking-wider font-medium">
                    <th className="text-left pb-3 pr-4">Routine</th>
                    <th className="text-left pb-3 pr-4">Status</th>
                    <th className="text-right pb-3 pr-4">Runs</th>
                    <th className="text-right pb-3">Last Run</th>
                  </tr>
                </thead>
                <tbody>
                  {routines.map((r, i) => (
                    <tr
                      key={i}
                      className="border-t border-[#21262d]/60 hover:bg-white/[0.02] transition-colors"
                    >
                      <td className="py-2.5 pr-4 text-[#e6edf3] text-[13px] font-medium">{r.name}</td>
                      <td className="py-2.5 pr-4">
                        <HealthBadge status={r.status} label={r.status} />
                      </td>
                      <td className="py-2.5 pr-4 text-right text-[#D0D5DD] tabular-nums text-[13px]">{r.runs}</td>
                      <td className="py-2.5 text-right text-[#667085] text-[13px]">{relativeTime(r.last_run)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="flex items-center justify-center h-32">
              <p className="text-[#667085] text-sm">No routines data</p>
            </div>
          )}
        </div>
      </div>

      {/* Quick Actions */}
      <div className="mb-4">
        <h3 className="text-xs font-medium text-[#667085] uppercase tracking-wider mb-3">Quick Actions</h3>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          {QUICK_ACTIONS.map((action) => {
            const Icon = action.icon
            return (
              <Link
                key={action.label}
                to={action.to}
                className="group flex items-center gap-3 bg-[#161b22] border border-[#21262d] rounded-xl px-4 py-3 transition-all duration-200 hover:border-[#00FFA7]/30 hover:bg-[#00FFA7]/[0.03]"
              >
                <div className="flex items-center justify-center w-8 h-8 rounded-lg bg-white/[0.04] group-hover:bg-[#00FFA7]/10 transition-colors">
                  <Icon size={15} className="text-[#667085] group-hover:text-[#00FFA7] transition-colors" />
                </div>
                <div className="min-w-0">
                  <p className="text-[13px] font-medium text-[#e6edf3] group-hover:text-white truncate">{action.label}</p>
                  <p className="text-[11px] text-[#667085] truncate">{action.hint}</p>
                </div>
              </Link>
            )
          })}
        </div>
      </div>
    </div>
  )
}
