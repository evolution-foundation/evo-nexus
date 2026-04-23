import { useEffect, useState } from 'react'
import { useToast } from '../components/Toast'
import { useConfirm } from '../components/ConfirmDialog'
import { CalendarClock, Plus, Play, X, Eye, RefreshCw, Pencil, Trash2 } from 'lucide-react'
import { api } from '../lib/api'
import { useTranslation } from 'react-i18next'

interface Task {
  id: number
  name: string
  description: string | null
  type: string
  payload: string
  agent: string | null
  scheduled_at: string
  status: string
  created_at: string
  started_at: string | null
  completed_at: string | null
  result_summary: string | null
  error: string | null
  created_by: number | null
}

const STATUS_STYLES: Record<string, { bg: string; text: string; dot: string }> = {
  pending: { bg: 'bg-yellow-500/10 border-yellow-500/20', text: 'text-yellow-400', dot: 'bg-yellow-400' },
  running: { bg: 'bg-blue-500/10 border-blue-500/20', text: 'text-blue-400', dot: 'bg-blue-400 animate-pulse' },
  completed: { bg: 'bg-[#00FFA7]/10 border-[#00FFA7]/20', text: 'text-[#00FFA7]', dot: 'bg-[#00FFA7]' },
  failed: { bg: 'bg-red-500/10 border-red-500/20', text: 'text-red-400', dot: 'bg-red-400' },
  cancelled: { bg: 'bg-[#667085]/10 border-[#667085]/20', text: 'text-[#667085]', dot: 'bg-[#667085]' },
}

const TYPE_STYLES: Record<string, string> = {
  skill: 'bg-purple-500/10 border-purple-500/20 text-purple-400',
  prompt: 'bg-blue-500/10 border-blue-500/20 text-blue-400',
  script: 'bg-orange-500/10 border-orange-500/20 text-orange-400',
}

const AGENTS = [
  '', 'clawdia-assistant', 'flux-financeiro', 'atlas-project', 'kai-personal-assistant',
  'pulse-community', 'sage-strategy', 'pixel-social-media', 'nex-comercial', 'mentor-courses',
]

const AGENT_LABELS: Record<string, string> = {
  '': 'None',
  'clawdia-assistant': '@clawdia',
  'flux-financeiro': '@flux',
  'atlas-project': '@atlas',
  'kai-personal-assistant': '@kai',
  'pulse-community': '@pulse',
  'sage-strategy': '@sage',
  'pixel-social-media': '@pixel',
  'nex-comercial': '@nex',
  'mentor-courses': '@mentor',
}

function formatDate(iso: string | null): string {
  if (!iso) return '--'
  const d = new Date(iso)
  return d.toLocaleString('pt-BR', { timeZone: 'America/Sao_Paulo', day: '2-digit', month: '2-digit', year: '2-digit', hour: '2-digit', minute: '2-digit' })
}

const emptyForm = { name: '', description: '', type: 'skill', payload: '', agent: '', scheduled_at: '' }

export default function Tasks() {
  const { t } = useTranslation()
  const toast = useToast()
  const confirm = useConfirm()
  const [tasks, setTasks] = useState<Task[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState<string>('')
  const [showModal, setShowModal] = useState(false)
  const [editingTask, setEditingTask] = useState<Task | null>(null)
  const [form, setForm] = useState(emptyForm)
  const [saving, setSaving] = useState(false)
  const [viewTask, setViewTask] = useState<Task | null>(null)

  const fetchTasks = () => {
    const params = filter ? `?status=${filter}` : ''
    api.get(`/tasks${params}`)
      .then((data) => {
        setTasks(data.tasks || [])
        setTotal(data.total || 0)
      })
      .catch(() => setTasks([]))
      .finally(() => setLoading(false))
  }

  useEffect(() => { fetchTasks() }, [filter])

  const openCreate = () => {
    setEditingTask(null)
    setForm(emptyForm)
    setShowModal(true)
  }

  const openEdit = (task: Task) => {
    setEditingTask(task)
    setForm({
      name: task.name,
      description: task.description || '',
      type: task.type,
      payload: task.payload,
      agent: task.agent || '',
      scheduled_at: task.scheduled_at ? task.scheduled_at.slice(0, 16) : '',
    })
    setShowModal(true)
  }

  const handleSave = async () => {
    setSaving(true)
    try {
      const body = {
        ...form,
        scheduled_at: form.scheduled_at ? new Date(form.scheduled_at).toISOString() : '',
        agent: form.agent || null,
        description: form.description || null,
      }
      if (editingTask) {
        await api.put(`/tasks/${editingTask.id}`, body)
      } else {
        await api.post('/tasks', body)
      }
      setShowModal(false)
      setLoading(true)
      fetchTasks()
    } catch (e) {
      toast.error('Erro ao salvar tarefa', String(e))
    }
    setSaving(false)
  }

  const handleCancel = async (id: number) => {
    const ok = await confirm({
      title: 'Cancelar tarefa',
      description: 'Cancelar esta tarefa agendada?',
      confirmText: 'Cancelar tarefa',
      variant: 'danger',
    })
    if (!ok) return
    try {
      await api.delete(`/tasks/${id}`)
      fetchTasks()
    } catch (e) {
      toast.error('Erro ao cancelar', String(e))
    }
  }

  const handleRunNow = async (id: number) => {
    try {
      await api.post(`/tasks/${id}/run`)
      fetchTasks()
    } catch (e) {
      toast.error('Erro ao executar', String(e))
    }
  }

  const handleDelete = async (id: number) => {
    const ok = await confirm({
      title: 'Deletar tarefa',
      description: 'Deletar esta tarefa permanentemente?',
      confirmText: 'Deletar',
      variant: 'danger',
    })
    if (!ok) return
    try {
      await api.delete(`/tasks/${id}`)
      fetchTasks()
    } catch (e) {
      toast.error('Erro ao deletar', String(e))
    }
  }

  const filters = [
    { label: 'All', value: '' },
    { label: 'Pending', value: 'pending' },
    { label: 'Running', value: 'running' },
    { label: 'Completed', value: 'completed' },
    { label: 'Failed', value: 'failed' },
  ]

  if (loading) {
    return (
      <div>
        <div className="mb-8">
          <h1 className="text-2xl font-bold text-[#e6edf3]">{t('tasks.title')}</h1>
        </div>
        <div className="space-y-3">
          {[...Array(3)].map((_, i) => <div key={i} className="skeleton h-16 rounded-xl" />)}
        </div>
      </div>
    )
  }

  return (
    <div>
      {/* Header */}
      <div className="flex items-center justify-between mb-8">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl bg-[#161b22] border border-[#21262d] flex items-center justify-center">
            <CalendarClock size={20} className="text-[#00FFA7]" />
          </div>
          <div>
            <h1 className="text-2xl font-bold text-[#e6edf3]">{t('tasks.title')}</h1>
            <p className="text-[#667085] mt-0.5 text-sm">One-off scheduled actions</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => { setLoading(true); fetchTasks() }}
            className="flex items-center gap-2 px-3 py-2 rounded-lg border border-[#21262d] bg-[#161b22] text-[#667085] hover:text-[#00FFA7] hover:border-[#00FFA7]/30 transition-colors"
          >
            <RefreshCw size={16} />
          </button>
          <button
            onClick={openCreate}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-[#00FFA7]/10 border border-[#00FFA7]/20 text-[#00FFA7] hover:bg-[#00FFA7]/20 transition-colors font-medium text-sm"
          >
            <Plus size={16} /> New Task
          </button>
        </div>
      </div>

      {/* Filters */}
      <div className="flex items-center gap-2 mb-4">
        {filters.map((f) => (
          <button
            key={f.value}
            onClick={() => { setFilter(f.value); setLoading(true) }}
            className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors border ${
              filter === f.value
                ? 'bg-[#00FFA7]/10 border-[#00FFA7]/20 text-[#00FFA7]'
                : 'bg-[#161b22] border-[#21262d] text-[#667085] hover:text-[#e6edf3] hover:border-[#344054]'
            }`}
          >
            {f.label}
          </button>
        ))}
        <span className="text-[11px] text-[#667085] ml-2">{total} tasks</span>
      </div>

      {/* Table */}
      {tasks.length === 0 ? (
        <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-12 text-center">
          <CalendarClock size={32} className="text-[#667085] mx-auto mb-3" />
          <p className="text-[#667085] text-sm">No scheduled tasks yet</p>
          <button onClick={openCreate} className="mt-3 text-[#00FFA7] text-sm hover:underline">
            Create your first task
          </button>
        </div>
      ) : (
        <div className="bg-[#161b22] border border-[#21262d] rounded-xl overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-[#667085] text-xs uppercase tracking-wider bg-[#0d1117]/50 border-b border-[#21262d]">
                <th className="text-left p-4 font-medium">Task</th>
                <th className="text-left p-4 font-medium">Type</th>
                <th className="text-left p-4 font-medium">Agent</th>
                <th className="text-left p-4 font-medium">Scheduled</th>
                <th className="text-left p-4 font-medium">Status</th>
                <th className="text-right p-4 font-medium">Actions</th>
              </tr>
            </thead>
            <tbody>
              {tasks.map((task) => {
                const st = STATUS_STYLES[task.status] || STATUS_STYLES.pending
                return (
                  <tr key={task.id} className="border-t border-[#21262d]/50 hover:bg-white/[0.02] transition-colors">
                    <td className="p-4">
                      <div className="text-[#e6edf3] font-medium">{task.name}</div>
                      {task.description && (
                        <div className="text-[#667085] text-xs mt-0.5 truncate max-w-[300px]">{task.description}</div>
                      )}
                    </td>
                    <td className="p-4">
                      <span className={`inline-flex text-[10px] font-medium px-2 py-0.5 rounded-full border ${TYPE_STYLES[task.type] || ''}`}>
                        {task.type}
                      </span>
                    </td>
                    <td className="p-4">
                      {task.agent ? (
                        <span className="inline-flex text-[11px] font-medium px-2.5 py-0.5 rounded-full bg-[#00FFA7]/8 border border-[#00FFA7]/20 text-[#00FFA7]">
                          {AGENT_LABELS[task.agent] || `@${task.agent}`}
                        </span>
                      ) : (
                        <span className="text-[#667085]">--</span>
                      )}
                    </td>
                    <td className="p-4 text-[#e6edf3] text-xs font-mono">{formatDate(task.scheduled_at)}</td>
                    <td className="p-4">
                      <span className={`inline-flex items-center gap-1.5 text-[11px] font-medium px-2.5 py-0.5 rounded-full border ${st.bg} ${st.text}`}>
                        <span className={`w-1.5 h-1.5 rounded-full ${st.dot}`} />
                        {task.status}
                      </span>
                    </td>
                    <td className="p-4">
                      <div className="flex items-center gap-1 justify-end">
                        {task.status === 'pending' && (
                          <>
                            <button onClick={() => handleRunNow(task.id)} className="p-1.5 rounded-lg hover:bg-[#00FFA7]/10 text-[#667085] hover:text-[#00FFA7] transition-colors" title="Run Now">
                              <Play size={14} />
                            </button>
                            <button onClick={() => openEdit(task)} className="p-1.5 rounded-lg hover:bg-white/5 text-[#667085] hover:text-[#e6edf3] transition-colors" title="Edit">
                              <Pencil size={14} />
                            </button>
                            <button onClick={() => handleCancel(task.id)} className="p-1.5 rounded-lg hover:bg-red-500/10 text-[#667085] hover:text-red-400 transition-colors" title="Cancel">
                              <X size={14} />
                            </button>
                          </>
                        )}
                        {(task.status === 'completed' || task.status === 'failed') && (
                          <>
                            <button onClick={() => setViewTask(task)} className="p-1.5 rounded-lg hover:bg-white/5 text-[#667085] hover:text-[#e6edf3] transition-colors" title="View Result">
                              <Eye size={14} />
                            </button>
                            <button onClick={() => handleDelete(task.id)} className="p-1.5 rounded-lg hover:bg-red-500/10 text-[#667085] hover:text-red-400 transition-colors" title="Delete">
                              <Trash2 size={14} />
                            </button>
                          </>
                        )}
                        {task.status === 'failed' && (
                          <button onClick={() => handleRunNow(task.id)} className="p-1.5 rounded-lg hover:bg-[#00FFA7]/10 text-[#667085] hover:text-[#00FFA7] transition-colors" title="Retry">
                            <RefreshCw size={14} />
                          </button>
                        )}
                        {task.status === 'cancelled' && (
                          <button onClick={() => handleDelete(task.id)} className="p-1.5 rounded-lg hover:bg-red-500/10 text-[#667085] hover:text-red-400 transition-colors" title="Delete">
                            <Trash2 size={14} />
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* Create/Edit Modal */}
      {showModal && (
        <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4" onClick={() => setShowModal(false)}>
          <div className="bg-[#161b22] border border-[#21262d] rounded-xl w-full max-w-lg" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between px-6 py-4 border-b border-[#21262d]">
              <h2 className="text-lg font-semibold text-[#e6edf3]">
                {editingTask ? 'Edit Task' : 'New Scheduled Task'}
              </h2>
              <button onClick={() => setShowModal(false)} className="p-1 rounded-lg hover:bg-white/5 text-[#667085] hover:text-[#e6edf3]">
                <X size={18} />
              </button>
            </div>

            <div className="p-6 space-y-4">
              <div>
                <label className="block text-xs font-medium text-[#667085] mb-1.5">Name *</label>
                <input
                  type="text"
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                  placeholder="e.g. Post LinkedIn about Summit"
                  className="w-full px-3 py-2 bg-[#0d1117] border border-[#21262d] rounded-lg text-sm text-[#e6edf3] placeholder-[#667085] focus:border-[#00FFA7]/50 focus:outline-none"
                />
              </div>

              <div>
                <label className="block text-xs font-medium text-[#667085] mb-1.5">Description</label>
                <input
                  type="text"
                  value={form.description}
                  onChange={(e) => setForm({ ...form, description: e.target.value })}
                  placeholder="Optional context"
                  className="w-full px-3 py-2 bg-[#0d1117] border border-[#21262d] rounded-lg text-sm text-[#e6edf3] placeholder-[#667085] focus:border-[#00FFA7]/50 focus:outline-none"
                />
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-xs font-medium text-[#667085] mb-1.5">Type *</label>
                  <select
                    value={form.type}
                    onChange={(e) => setForm({ ...form, type: e.target.value })}
                    className="w-full px-3 py-2 bg-[#0d1117] border border-[#21262d] rounded-lg text-sm text-[#e6edf3] focus:border-[#00FFA7]/50 focus:outline-none"
                  >
                    <option value="skill">Skill</option>
                    <option value="prompt">Prompt</option>
                    <option value="script">Script</option>
                  </select>
                </div>
                <div>
                  <label className="block text-xs font-medium text-[#667085] mb-1.5">Agent</label>
                  <select
                    value={form.agent}
                    onChange={(e) => setForm({ ...form, agent: e.target.value })}
                    className="w-full px-3 py-2 bg-[#0d1117] border border-[#21262d] rounded-lg text-sm text-[#e6edf3] focus:border-[#00FFA7]/50 focus:outline-none"
                  >
                    {AGENTS.map((a) => (
                      <option key={a} value={a}>{AGENT_LABELS[a] || a}</option>
                    ))}
                  </select>
                </div>
              </div>

              <div>
                <label className="block text-xs font-medium text-[#667085] mb-1.5">
                  {form.type === 'skill' ? 'Skill name + args' : form.type === 'prompt' ? 'Prompt text' : 'Script path (relative to ADWs/routines/)'} *
                </label>
                <textarea
                  value={form.payload}
                  onChange={(e) => setForm({ ...form, payload: e.target.value })}
                  placeholder={
                    form.type === 'skill' ? 'social-post-writer LinkedIn post about...'
                      : form.type === 'prompt' ? 'Generate a summary of...'
                        : 'custom/my_script.py'
                  }
                  rows={3}
                  className="w-full px-3 py-2 bg-[#0d1117] border border-[#21262d] rounded-lg text-sm text-[#e6edf3] placeholder-[#667085] focus:border-[#00FFA7]/50 focus:outline-none font-mono resize-none"
                />
              </div>

              <div>
                <label className="block text-xs font-medium text-[#667085] mb-1.5">Scheduled At *</label>
                <input
                  type="datetime-local"
                  value={form.scheduled_at}
                  onChange={(e) => setForm({ ...form, scheduled_at: e.target.value })}
                  className="w-full px-3 py-2 bg-[#0d1117] border border-[#21262d] rounded-lg text-sm text-[#e6edf3] focus:border-[#00FFA7]/50 focus:outline-none"
                />
              </div>
            </div>

            <div className="flex items-center justify-end gap-3 px-6 py-4 border-t border-[#21262d]">
              <button
                onClick={() => setShowModal(false)}
                className="px-4 py-2 rounded-lg border border-[#21262d] text-[#667085] hover:text-[#e6edf3] hover:border-[#344054] transition-colors text-sm"
              >
                Cancel
              </button>
              <button
                onClick={handleSave}
                disabled={saving || !form.name || !form.payload || !form.scheduled_at}
                className="px-4 py-2 rounded-lg bg-[#00FFA7]/10 border border-[#00FFA7]/20 text-[#00FFA7] hover:bg-[#00FFA7]/20 transition-colors text-sm font-medium disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {saving ? 'Saving...' : editingTask ? 'Update' : 'Schedule'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* View Result Modal */}
      {viewTask && (
        <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4" onClick={() => setViewTask(null)}>
          <div className="bg-[#161b22] border border-[#21262d] rounded-xl w-full max-w-2xl max-h-[80vh] flex flex-col" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between px-6 py-4 border-b border-[#21262d]">
              <div>
                <h2 className="text-lg font-semibold text-[#e6edf3]">{viewTask.name}</h2>
                <p className="text-xs text-[#667085] mt-0.5">
                  {viewTask.status === 'completed' ? 'Completed' : 'Failed'} at {formatDate(viewTask.completed_at)}
                  {viewTask.started_at && viewTask.completed_at && (
                    <> — {Math.round((new Date(viewTask.completed_at).getTime() - new Date(viewTask.started_at).getTime()) / 1000)}s</>
                  )}
                </p>
              </div>
              <button onClick={() => setViewTask(null)} className="p-1 rounded-lg hover:bg-white/5 text-[#667085] hover:text-[#e6edf3]">
                <X size={18} />
              </button>
            </div>

            <div className="flex-1 overflow-y-auto p-6 space-y-4">
              <div>
                <label className="block text-xs font-medium text-[#667085] mb-1">Payload</label>
                <pre className="text-xs text-[#e6edf3] bg-[#0d1117] border border-[#21262d] rounded-lg p-3 whitespace-pre-wrap font-mono">{viewTask.payload}</pre>
              </div>

              {viewTask.result_summary && (
                <div>
                  <label className="block text-xs font-medium text-[#00FFA7] mb-1">Result</label>
                  <pre className="text-xs text-[#e6edf3] bg-[#0d1117] border border-[#21262d] rounded-lg p-3 whitespace-pre-wrap font-mono max-h-64 overflow-y-auto">{viewTask.result_summary}</pre>
                </div>
              )}

              {viewTask.error && (
                <div>
                  <label className="block text-xs font-medium text-red-400 mb-1">Error</label>
                  <pre className="text-xs text-red-300 bg-red-500/5 border border-red-500/20 rounded-lg p-3 whitespace-pre-wrap font-mono max-h-48 overflow-y-auto">{viewTask.error}</pre>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
