import { useEffect, useState, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import {
  Target, ChevronDown, ChevronRight, Plus, RefreshCw,
  CheckCircle2, Circle, Clock, XCircle, PauseCircle,
  X,
} from 'lucide-react'

// ---- Types ----

interface GoalTask {
  id: number
  goal_id: number | null
  title: string
  description: string | null
  priority: number
  assignee_agent: string | null
  status: 'open' | 'in_progress' | 'done' | 'cancelled'
  due_date: string | null
  created_at: string
  updated_at: string
}

interface Goal {
  id: number
  slug: string
  project_id: number
  title: string
  description: string | null
  target_metric: string | null
  metric_type: 'count' | 'currency' | 'percentage' | 'boolean'
  target_value: number
  current_value: number
  due_date: string | null
  status: 'active' | 'achieved' | 'on-hold' | 'cancelled'
  created_at: string
  updated_at: string
  tasks?: GoalTask[]
}

interface GoalProject {
  id: number
  slug: string
  mission_id: number | null
  title: string
  description: string | null
  workspace_folder_path: string | null
  status: string
  created_at: string
  updated_at: string
  goals?: Goal[]
}

interface Mission {
  id: number
  slug: string
  title: string
  description: string | null
  target_metric: string | null
  target_value: number | null
  current_value: number
  due_date: string | null
  status: 'active' | 'achieved' | 'on-hold' | 'cancelled'
  created_at: string
  updated_at: string
  projects?: GoalProject[]
}

// ---- Helpers ----

const API = import.meta.env.DEV ? 'http://localhost:8080' : ''

async function apiFetch(path: string, opts?: RequestInit) {
  const res = await fetch(API + path, { credentials: 'include', ...opts })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json()
}

function statusIcon(status: string, size = 14) {
  switch (status) {
    case 'achieved': return <CheckCircle2 size={size} className="text-[#00FFA7]" />
    case 'active': return <Circle size={size} className="text-blue-400" />
    case 'on-hold': return <PauseCircle size={size} className="text-yellow-400" />
    case 'cancelled': return <XCircle size={size} className="text-red-400" />
    case 'done': return <CheckCircle2 size={size} className="text-[#00FFA7]" />
    case 'in_progress': return <Clock size={size} className="text-blue-400" />
    case 'open': return <Circle size={size} className="text-[#667085]" />
    default: return <Circle size={size} className="text-[#667085]" />
  }
}

function pct(current: number, target: number) {
  if (target <= 0) return 0
  return Math.min(100, Math.round((current / target) * 100))
}

function formatMetric(val: number, metric_type: string) {
  if (metric_type === 'currency') return `$${val.toLocaleString()}`
  if (metric_type === 'percentage') return `${val}%`
  if (metric_type === 'boolean') return val >= 1 ? 'Done' : 'Pending'
  return String(val)
}

function isDueSoon(due_date: string | null) {
  if (!due_date) return false
  const d = new Date(due_date)
  const now = new Date()
  const diff = (d.getTime() - now.getTime()) / (1000 * 60 * 60 * 24)
  return diff <= 7
}

function isOverdue(due_date: string | null) {
  if (!due_date) return false
  return new Date(due_date) < new Date()
}

// ---- Progress bar ----

function ProgressBar({ current, target, size = 'md' }: { current: number; target: number; size?: 'sm' | 'md' }) {
  const p = pct(current, target)
  const h = size === 'sm' ? 'h-1' : 'h-2'
  return (
    <div className={`w-full bg-[#21262d] rounded-full ${h}`}>
      <div
        className={`${h} rounded-full transition-all duration-300 ${p >= 100 ? 'bg-[#00FFA7]' : 'bg-blue-400'}`}
        style={{ width: `${p}%` }}
      />
    </div>
  )
}

// ---- Modal: Create/Edit ----

function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: React.ReactNode }) {
  return (
    <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4">
      <div className="bg-[#161b22] border border-[#21262d] rounded-xl w-full max-w-lg shadow-2xl">
        <div className="flex items-center justify-between px-6 py-4 border-b border-[#21262d]">
          <h3 className="text-white font-semibold text-sm">{title}</h3>
          <button onClick={onClose} className="text-[#667085] hover:text-white transition-colors">
            <X size={16} />
          </button>
        </div>
        <div className="px-6 py-4">{children}</div>
      </div>
    </div>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="mb-3">
      <label className="block text-xs text-[#667085] mb-1">{label}</label>
      {children}
    </div>
  )
}

function Input(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      {...props}
      className="w-full bg-[#0C111D] border border-[#21262d] rounded-lg px-3 py-2 text-sm text-white placeholder-[#667085] focus:outline-none focus:border-[#00FFA7]/50 transition-colors"
    />
  )
}

function Select(props: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      {...props}
      className="w-full bg-[#0C111D] border border-[#21262d] rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-[#00FFA7]/50 transition-colors"
    />
  )
}

// ---- Task row ----

function TaskRow({ task, onStatusChange }: { task: GoalTask; onStatusChange: (id: number, status: string) => void }) {
  return (
    <div className="flex items-center gap-3 py-1.5 px-2 rounded-lg hover:bg-white/5 group">
      <button
        onClick={() => onStatusChange(task.id, task.status === 'done' ? 'open' : 'done')}
        className="shrink-0 transition-colors"
      >
        {statusIcon(task.status, 14)}
      </button>
      <span className={`text-xs flex-1 ${task.status === 'done' ? 'line-through text-[#667085]' : 'text-[#D0D5DD]'}`}>
        {task.title}
      </span>
      {task.assignee_agent && (
        <span className="text-[10px] text-[#667085] shrink-0">{task.assignee_agent}</span>
      )}
      {task.due_date && (
        <span className={`text-[10px] shrink-0 ${isOverdue(task.due_date) ? 'text-red-400' : isDueSoon(task.due_date) ? 'text-yellow-400' : 'text-[#667085]'}`}>
          {task.due_date}
        </span>
      )}
    </div>
  )
}

// ---- Goal row ----

function GoalRow({
  goal,
  onTaskStatusChange,
  onRecalculate,
  onCreateTask,
}: {
  goal: Goal
  onTaskStatusChange: (taskId: number, status: string) => void
  onRecalculate: (goalId: number) => void
  onCreateTask: (goalId: number) => void
}) {
  const [expanded, setExpanded] = useState(false)
  const p = pct(goal.current_value, goal.target_value)
  const tasks = goal.tasks || []
  const doneTasks = tasks.filter((t) => t.status === 'done').length

  return (
    <div className="border border-[#21262d] rounded-lg mb-2 overflow-hidden">
      <div
        className="flex items-center gap-3 px-4 py-2.5 cursor-pointer hover:bg-white/5 transition-colors"
        onClick={() => setExpanded(!expanded)}
      >
        {expanded ? <ChevronDown size={14} className="text-[#667085] shrink-0" /> : <ChevronRight size={14} className="text-[#667085] shrink-0" />}
        {statusIcon(goal.status)}
        <span className="text-sm text-[#D0D5DD] flex-1 font-medium">{goal.title}</span>

        {/* Progress */}
        <div className="flex items-center gap-2 shrink-0">
          <span className="text-xs text-[#667085]">
            {formatMetric(goal.current_value, goal.metric_type)}/{formatMetric(goal.target_value, goal.metric_type)}
          </span>
          <div className="w-24">
            <ProgressBar current={goal.current_value} target={goal.target_value} size="sm" />
          </div>
          <span className={`text-xs font-mono ${p >= 100 ? 'text-[#00FFA7]' : 'text-[#667085]'}`}>{p}%</span>
        </div>

        {/* Due date */}
        {goal.due_date && (
          <span className={`text-xs shrink-0 ml-2 ${isOverdue(goal.due_date) && goal.status === 'active' ? 'text-red-400' : isDueSoon(goal.due_date) ? 'text-yellow-400' : 'text-[#667085]'}`}>
            {goal.due_date}
          </span>
        )}

        {/* Recalculate button */}
        <button
          onClick={(e) => { e.stopPropagation(); onRecalculate(goal.id) }}
          className="p-1 text-[#667085] hover:text-[#00FFA7] transition-colors ml-1"
          title="Recalculate progress"
        >
          <RefreshCw size={12} />
        </button>
      </div>

      {expanded && (
        <div className="border-t border-[#21262d] bg-[#0C111D]/40 px-4 py-2">
          {tasks.length === 0 ? (
            <p className="text-xs text-[#667085] py-1">No tasks. <button onClick={() => onCreateTask(goal.id)} className="text-[#00FFA7] hover:underline">Add one</button></p>
          ) : (
            <div className="mb-1">
              <div className="text-[10px] text-[#667085] mb-1">{doneTasks}/{tasks.length} tasks done</div>
              {tasks.map((t) => (
                <TaskRow key={t.id} task={t} onStatusChange={onTaskStatusChange} />
              ))}
            </div>
          )}
          <button
            onClick={() => onCreateTask(goal.id)}
            className="flex items-center gap-1 text-xs text-[#667085] hover:text-[#00FFA7] transition-colors mt-1"
          >
            <Plus size={12} /> Add task
          </button>
        </div>
      )}
    </div>
  )
}

// ---- Project card ----

function ProjectCard({
  project,
  onTaskStatusChange,
  onRecalculate,
  onCreateGoal,
  onCreateTask,
}: {
  project: GoalProject
  onTaskStatusChange: (taskId: number, status: string) => void
  onRecalculate: (goalId: number) => void
  onCreateGoal: (projectId: number) => void
  onCreateTask: (goalId: number) => void
}) {
  const [expanded, setExpanded] = useState(true)
  const goals = project.goals || []
  const active = goals.filter((g) => g.status === 'active').length
  const achieved = goals.filter((g) => g.status === 'achieved').length

  // Aggregate project progress
  const totalTarget = goals.reduce((s, g) => s + g.target_value, 0)
  const totalCurrent = goals.reduce((s, g) => s + g.current_value, 0)

  return (
    <div className="bg-[#161b22] border border-[#21262d] rounded-xl mb-4 overflow-hidden">
      <div
        className="flex items-center gap-3 px-5 py-3 cursor-pointer hover:bg-white/5 transition-colors"
        onClick={() => setExpanded(!expanded)}
      >
        {expanded ? <ChevronDown size={16} className="text-[#667085]" /> : <ChevronRight size={16} className="text-[#667085]" />}
        <div className="flex-1">
          <div className="flex items-center gap-2">
            <span className="text-white font-semibold text-sm">{project.title}</span>
            <span className="text-[10px] text-[#667085] bg-[#21262d] px-2 py-0.5 rounded-full">{project.slug}</span>
          </div>
          {project.description && (
            <p className="text-[11px] text-[#667085] mt-0.5">{project.description}</p>
          )}
        </div>
        <div className="flex items-center gap-4 shrink-0">
          <div className="text-right">
            <div className="text-xs text-[#667085]">{active} active · {achieved} achieved</div>
            {totalTarget > 0 && (
              <div className="flex items-center gap-2 mt-1">
                <div className="w-32">
                  <ProgressBar current={totalCurrent} target={totalTarget} />
                </div>
                <span className="text-xs text-[#00FFA7] font-mono">{pct(totalCurrent, totalTarget)}%</span>
              </div>
            )}
          </div>
        </div>
      </div>

      {expanded && (
        <div className="px-5 pb-4 pt-2 border-t border-[#21262d]">
          {goals.length === 0 ? (
            <p className="text-xs text-[#667085] py-2">No goals yet.</p>
          ) : (
            goals.map((g) => (
              <GoalRow
                key={g.id}
                goal={g}
                onTaskStatusChange={onTaskStatusChange}
                onRecalculate={onRecalculate}
                onCreateTask={onCreateTask}
              />
            ))
          )}
          <button
            onClick={() => onCreateGoal(project.id)}
            className="flex items-center gap-1 text-xs text-[#667085] hover:text-[#00FFA7] transition-colors mt-2"
          >
            <Plus size={12} /> Add goal
          </button>
        </div>
      )}
    </div>
  )
}

// ---- Create Goal Modal ----

function CreateGoalModal({ projectId, onClose, onCreated }: { projectId: number; onClose: () => void; onCreated: () => void }) {
  const [form, setForm] = useState({
    slug: '', title: '', description: '', target_metric: '', metric_type: 'count', target_value: '1', due_date: '', status: 'active',
  })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const handleSubmit = async () => {
    if (!form.slug || !form.title) { setError('Slug and title are required'); return }
    setLoading(true)
    try {
      await apiFetch('/api/goals', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...form, project_id: projectId, target_value: Number(form.target_value) }),
      })
      onCreated()
      onClose()
    } catch (e: any) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <Modal title="Create Goal" onClose={onClose}>
      {error && <div className="text-red-400 text-xs mb-3">{error}</div>}
      <Field label="Slug (unique identifier)">
        <Input value={form.slug} onChange={(e) => setForm({ ...form, slug: e.target.value })} placeholder="evo-ai-100-customers" />
      </Field>
      <Field label="Title">
        <Input value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} placeholder="100 paying customers by Jun 30" />
      </Field>
      <Field label="Description">
        <Input value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} placeholder="Optional description" />
      </Field>
      <div className="grid grid-cols-2 gap-3">
        <Field label="Metric type">
          <Select value={form.metric_type} onChange={(e) => setForm({ ...form, metric_type: e.target.value })}>
            <option value="count">Count</option>
            <option value="currency">Currency</option>
            <option value="percentage">Percentage</option>
            <option value="boolean">Boolean</option>
          </Select>
        </Field>
        <Field label="Target value">
          <Input type="number" value={form.target_value} onChange={(e) => setForm({ ...form, target_value: e.target.value })} />
        </Field>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <Field label="Due date">
          <Input type="date" value={form.due_date} onChange={(e) => setForm({ ...form, due_date: e.target.value })} />
        </Field>
        <Field label="Status">
          <Select value={form.status} onChange={(e) => setForm({ ...form, status: e.target.value })}>
            <option value="active">Active</option>
            <option value="on-hold">On Hold</option>
          </Select>
        </Field>
      </div>
      <div className="flex justify-end gap-2 mt-4">
        <button onClick={onClose} className="px-4 py-2 text-sm text-[#667085] hover:text-white transition-colors">Cancel</button>
        <button
          onClick={handleSubmit}
          disabled={loading}
          className="px-4 py-2 text-sm bg-[#00FFA7] text-black font-semibold rounded-lg hover:bg-[#00FFA7]/90 transition-colors disabled:opacity-50"
        >
          {loading ? 'Creating...' : 'Create'}
        </button>
      </div>
    </Modal>
  )
}

// ---- Create Task Modal ----

function CreateTaskModal({ goalId, onClose, onCreated }: { goalId: number; onClose: () => void; onCreated: () => void }) {
  const [form, setForm] = useState({ title: '', description: '', priority: '3', assignee_agent: '', due_date: '' })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const handleSubmit = async () => {
    if (!form.title) { setError('Title is required'); return }
    setLoading(true)
    try {
      await apiFetch('/api/goal-tasks', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...form, goal_id: goalId, priority: Number(form.priority) }),
      })
      onCreated()
      onClose()
    } catch (e: any) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <Modal title="Create Task" onClose={onClose}>
      {error && <div className="text-red-400 text-xs mb-3">{error}</div>}
      <Field label="Title">
        <Input value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} placeholder="Task title" />
      </Field>
      <Field label="Description">
        <Input value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} placeholder="Optional" />
      </Field>
      <div className="grid grid-cols-2 gap-3">
        <Field label="Priority (1=high, 5=low)">
          <Input type="number" min={1} max={5} value={form.priority} onChange={(e) => setForm({ ...form, priority: e.target.value })} />
        </Field>
        <Field label="Assignee agent">
          <Input value={form.assignee_agent} onChange={(e) => setForm({ ...form, assignee_agent: e.target.value })} placeholder="atlas" />
        </Field>
      </div>
      <Field label="Due date">
        <Input type="date" value={form.due_date} onChange={(e) => setForm({ ...form, due_date: e.target.value })} />
      </Field>
      <div className="flex justify-end gap-2 mt-4">
        <button onClick={onClose} className="px-4 py-2 text-sm text-[#667085] hover:text-white transition-colors">Cancel</button>
        <button
          onClick={handleSubmit}
          disabled={loading}
          className="px-4 py-2 text-sm bg-[#00FFA7] text-black font-semibold rounded-lg hover:bg-[#00FFA7]/90 transition-colors disabled:opacity-50"
        >
          {loading ? 'Creating...' : 'Create'}
        </button>
      </div>
    </Modal>
  )
}

// ---- Filters ----

type StatusFilter = 'all' | 'active' | 'achieved' | 'on-hold' | 'cancelled'
type DueFilter = 'all' | 'overdue' | 'this-week' | 'this-month'

// ---- Main component ----

export default function Goals() {
  const { t } = useTranslation()
  const [missions, setMissions] = useState<Mission[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all')
  const [dueFilter, setDueFilter] = useState<DueFilter>('all')
  const [createGoalForProject, setCreateGoalForProject] = useState<number | null>(null)
  const [createTaskForGoal, setCreateTaskForGoal] = useState<number | null>(null)

  const load = useCallback(async () => {
    try {
      setError('')
      const data = await apiFetch('/api/missions')
      // Load tasks for each goal
      const enriched = await Promise.all(
        data.map(async (mission: Mission) => ({
          ...mission,
          projects: await Promise.all(
            (mission.projects || []).map(async (proj: GoalProject) => ({
              ...proj,
              goals: await Promise.all(
                (proj.goals || []).map(async (goal: Goal) => {
                  const tasks = await apiFetch(`/api/goal-tasks?goal_id=${goal.id}`)
                  return { ...goal, tasks }
                })
              ),
            }))
          ),
        }))
      )
      setMissions(enriched)
    } catch (e: any) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const handleTaskStatusChange = async (taskId: number, status: string) => {
    try {
      await apiFetch(`/api/goal-tasks/${taskId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status }),
      })
      await load()
    } catch (e: any) {
      console.error('Failed to update task:', e.message)
    }
  }

  const handleRecalculate = async (goalId: number) => {
    try {
      await apiFetch(`/api/goals/${goalId}/recalculate`, { method: 'POST' })
      await load()
    } catch (e: any) {
      console.error('Failed to recalculate:', e.message)
    }
  }

  // Filter goals within missions
  function filterGoal(goal: Goal): boolean {
    if (statusFilter !== 'all' && goal.status !== statusFilter) return false
    if (dueFilter === 'overdue') return isOverdue(goal.due_date) && goal.status === 'active'
    if (dueFilter === 'this-week') return isDueSoon(goal.due_date)
    if (dueFilter === 'this-month') {
      if (!goal.due_date) return false
      const monthEnd = new Date(); monthEnd.setDate(monthEnd.getDate() + 30)
      return new Date(goal.due_date) <= monthEnd && new Date(goal.due_date) >= new Date()
    }
    return true
  }

  function filterMission(mission: Mission): Mission {
    return {
      ...mission,
      projects: (mission.projects || []).map((proj) => ({
        ...proj,
        goals: (proj.goals || []).filter(filterGoal),
      })).filter((proj) => (proj.goals?.length ?? 0) > 0 || statusFilter === 'all'),
    }
  }

  const filteredMissions = missions.map(filterMission)

  // Compute top urgent goals for header stats
  const allGoals = missions.flatMap((m) =>
    (m.projects || []).flatMap((p) => (p.goals || []))
  )
  const urgentGoals = allGoals
    .filter((g) => g.status === 'active')
    .sort((a, b) => (a.due_date || '9999') < (b.due_date || '9999') ? -1 : 1)
    .slice(0, 3)

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="text-[#667085] text-sm">Loading goals...</div>
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
          <Target size={20} className="text-[#00FFA7]" />
          <div>
            <h1 className="text-white font-semibold text-lg">{t('goals.title')}</h1>
            <p className="text-xs text-[#667085]">Mission → Project → Goal → Task hierarchy</p>
          </div>
        </div>
        <button
          onClick={load}
          className="flex items-center gap-2 px-3 py-1.5 text-xs text-[#667085] hover:text-white border border-[#21262d] rounded-lg hover:border-[#344054] transition-colors"
        >
          <RefreshCw size={12} /> Refresh
        </button>
      </div>

      {/* Top urgent goals stats */}
      {urgentGoals.length > 0 && (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3 mb-6">
          {urgentGoals.map((g) => (
            <div key={g.id} className="bg-[#161b22] border border-[#21262d] rounded-xl p-4">
              <div className="flex items-center gap-2 mb-2">
                {statusIcon(g.status)}
                <span className="text-xs text-white font-medium truncate">{g.title}</span>
              </div>
              <ProgressBar current={g.current_value} target={g.target_value} />
              <div className="flex justify-between mt-1.5">
                <span className="text-[10px] text-[#667085]">{formatMetric(g.current_value, g.metric_type)} / {formatMetric(g.target_value, g.metric_type)}</span>
                {g.due_date && (
                  <span className={`text-[10px] ${isOverdue(g.due_date) ? 'text-red-400' : isDueSoon(g.due_date) ? 'text-yellow-400' : 'text-[#667085]'}`}>
                    {g.due_date}
                  </span>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Filters */}
      <div className="flex items-center gap-3 mb-5 flex-wrap">
        <div className="flex items-center gap-1.5">
          <span className="text-xs text-[#667085]">Status:</span>
          {(['all', 'active', 'achieved', 'on-hold', 'cancelled'] as StatusFilter[]).map((s) => (
            <button
              key={s}
              onClick={() => setStatusFilter(s)}
              className={`px-2.5 py-1 text-xs rounded-lg transition-colors ${statusFilter === s ? 'bg-[#00FFA7]/15 text-[#00FFA7] border border-[#00FFA7]/30' : 'text-[#667085] border border-[#21262d] hover:border-[#344054]'}`}
            >
              {s}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-1.5">
          <span className="text-xs text-[#667085]">Due:</span>
          {(['all', 'overdue', 'this-week', 'this-month'] as DueFilter[]).map((d) => (
            <button
              key={d}
              onClick={() => setDueFilter(d)}
              className={`px-2.5 py-1 text-xs rounded-lg transition-colors ${dueFilter === d ? 'bg-[#00FFA7]/15 text-[#00FFA7] border border-[#00FFA7]/30' : 'text-[#667085] border border-[#21262d] hover:border-[#344054]'}`}
            >
              {d}
            </button>
          ))}
        </div>
      </div>

      {/* Mission tree */}
      {filteredMissions.length === 0 ? (
        <div className="text-center py-16 text-[#667085] text-sm">
          No missions found. Run the backend migration to seed initial data.
        </div>
      ) : (
        filteredMissions.map((mission) => (
          <div key={mission.id} className="mb-8">
            {/* Mission header */}
            <div className="bg-gradient-to-r from-[#00FFA7]/10 to-transparent border border-[#00FFA7]/20 rounded-xl p-5 mb-4">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <Target size={18} className="text-[#00FFA7]" />
                  <div>
                    <h2 className="text-white font-bold text-base">{mission.title}</h2>
                    {mission.description && <p className="text-xs text-[#667085] mt-0.5">{mission.description}</p>}
                  </div>
                </div>
                <div className="text-right shrink-0">
                  {mission.target_value && (
                    <div className="flex items-center gap-2">
                      <span className="text-xs text-[#667085]">{mission.current_value} / {mission.target_value}</span>
                      <div className="w-32">
                        <ProgressBar current={mission.current_value} target={mission.target_value} />
                      </div>
                    </div>
                  )}
                  {mission.due_date && (
                    <div className="text-xs text-[#667085] mt-1">Due {mission.due_date}</div>
                  )}
                </div>
              </div>
            </div>

            {/* Projects */}
            {(mission.projects || []).length === 0 ? (
              <p className="text-xs text-[#667085] px-2">No projects in this mission.</p>
            ) : (
              (mission.projects || []).map((project) => (
                <ProjectCard
                  key={project.id}
                  project={project}
                  onTaskStatusChange={handleTaskStatusChange}
                  onRecalculate={handleRecalculate}
                  onCreateGoal={(pid) => setCreateGoalForProject(pid)}
                  onCreateTask={(gid) => setCreateTaskForGoal(gid)}
                />
              ))
            )}
          </div>
        ))
      )}

      {/* Modals */}
      {createGoalForProject !== null && (
        <CreateGoalModal
          projectId={createGoalForProject}
          onClose={() => setCreateGoalForProject(null)}
          onCreated={load}
        />
      )}
      {createTaskForGoal !== null && (
        <CreateTaskModal
          goalId={createTaskForGoal}
          onClose={() => setCreateTaskForGoal(null)}
          onCreated={load}
        />
      )}
    </div>
  )
}
