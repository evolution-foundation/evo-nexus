import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { FolderKanban, RefreshCw, Target } from 'lucide-react'

// ---- Types (mirrors Goals.tsx) ----

interface Goal {
  id: number
  status: 'active' | 'achieved' | 'on-hold' | 'cancelled'
  target_value: number
  current_value: number
}

interface GoalProject {
  id: number
  slug: string
  mission_id: number | null
  title: string
  description: string | null
  status: string
  goals?: Goal[]
}

interface Mission {
  id: number
  title: string
  projects?: GoalProject[]
}

interface ProjectRow {
  project: GoalProject
  missionTitle: string
  goalCount: number
  totalTarget: number
  totalCurrent: number
}

// ---- Helpers ----

const API = import.meta.env.DEV ? 'http://localhost:8080' : ''

async function apiFetch(path: string) {
  const res = await fetch(API + path, { credentials: 'include' })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json()
}

function pct(current: number, target: number) {
  if (target <= 0) return 0
  return Math.min(100, Math.round((current / target) * 100))
}

function ProgressBar({ current, target }: { current: number; target: number }) {
  const p = pct(current, target)
  return (
    <div className="w-full bg-[#21262d] rounded-full h-2">
      <div
        className={`h-2 rounded-full transition-all duration-300 ${p >= 100 ? 'bg-[#00FFA7]' : 'bg-blue-400'}`}
        style={{ width: `${p}%` }}
      />
    </div>
  )
}

// ---- Main component ----

export default function ProjectsOverview() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [rows, setRows] = useState<ProjectRow[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = async () => {
    try {
      setError('')
      setLoading(true)
      const missions: Mission[] = await apiFetch('/api/missions')
      const flattened: ProjectRow[] = missions.flatMap((mission) =>
        (mission.projects || []).map((project) => {
          const goals = project.goals || []
          return {
            project,
            missionTitle: mission.title,
            goalCount: goals.length,
            totalTarget: goals.reduce((s, g) => s + g.target_value, 0),
            totalCurrent: goals.reduce((s, g) => s + g.current_value, 0),
          }
        })
      )
      setRows(flattened)
    } catch (e: any) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="text-[#667085] text-sm">Loading projects...</div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="text-red-400 text-sm">Error: {error}</div>
      </div>
    )
  }

  return (
    <div className="max-w-5xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-3">
          <FolderKanban size={20} className="text-[#00FFA7]" />
          <div>
            <h1 className="text-white font-semibold text-lg">{t('nav.projects')}</h1>
            <p className="text-xs text-[#667085]">Read-only overview — click a project to open it in Goals</p>
          </div>
        </div>
        <button
          onClick={load}
          className="flex items-center gap-2 px-3 py-1.5 text-xs text-[#667085] hover:text-white border border-[#21262d] rounded-lg hover:border-[#344054] transition-colors"
        >
          <RefreshCw size={12} /> Refresh
        </button>
      </div>

      {rows.length === 0 ? (
        <div className="text-center py-16 px-4">
          <p className="text-sm text-[#e6edf3]">No projects yet.</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {rows.map(({ project, missionTitle, goalCount, totalTarget, totalCurrent }) => (
            <button
              key={project.id}
              onClick={() => navigate(`/goals?project=${project.id}`)}
              className="text-left bg-[#161b22] border border-[#21262d] rounded-xl p-5 hover:border-[#00FFA7]/40 transition-colors"
            >
              <div className="flex items-center gap-1.5 text-[10px] text-[#667085] mb-2">
                <Target size={10} />
                <span className="truncate">{missionTitle}</span>
              </div>
              <div className="flex items-center gap-2 mb-1">
                <span className="text-white font-semibold text-sm">{project.title}</span>
                <span className="text-[10px] text-[#667085] bg-[#21262d] px-2 py-0.5 rounded-full">{project.slug}</span>
              </div>
              {project.description && (
                <p className="text-xs text-[#667085] mb-3 line-clamp-2">{project.description}</p>
              )}
              <div className="flex items-center justify-between text-[11px] text-[#667085] mb-1.5">
                <span>{goalCount} goal{goalCount === 1 ? '' : 's'}</span>
                {totalTarget > 0 && <span className="text-[#00FFA7] font-mono">{pct(totalCurrent, totalTarget)}%</span>}
              </div>
              {totalTarget > 0 && <ProgressBar current={totalCurrent} target={totalTarget} />}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
