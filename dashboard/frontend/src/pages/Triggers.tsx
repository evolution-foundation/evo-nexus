import { useEffect, useState } from 'react'
import { useToast } from '../components/Toast'
import { useConfirm } from '../components/ConfirmDialog'
import { Plus, Pencil, Trash2, X, Play, Copy, RefreshCw, KeyRound } from 'lucide-react'
import { api } from '../lib/api'
import { useAuth } from '../context/AuthContext'

interface TriggerItem {
  id: number
  name: string
  slug: string
  type: string
  source: string
  event_filter: Record<string, string>
  action_type: string
  action_payload: string
  agent: string | null
  enabled: boolean
  from_yaml: boolean
  execution_count: number
  created_at: string
  updated_at: string
}

interface Execution {
  id: number
  trigger_id: number
  event_data: Record<string, unknown>
  status: string
  result_summary: string | null
  error: string | null
  duration_seconds: number | null
  started_at: string
  completed_at: string | null
}

const SOURCES = ['github', 'stripe', 'linear', 'telegram', 'discord', 'custom'] as const
const ACTION_TYPES = ['skill', 'prompt', 'script'] as const
const AGENTS = [
  'clawdia-assistant', 'flux-financeiro', 'atlas-project', 'kai-personal-assistant',
  'pulse-community', 'sage-strategy', 'pixel-social-media', 'nex-comercial', 'mentor-courses',
]

const SOURCE_COLORS: Record<string, string> = {
  github: 'bg-[#e6edf3]/10 text-[#e6edf3] border-[#e6edf3]/20',
  stripe: 'bg-[#635bff]/10 text-[#635bff] border-[#635bff]/20',
  linear: 'bg-[#5e6ad2]/10 text-[#5e6ad2] border-[#5e6ad2]/20',
  telegram: 'bg-[#229ed9]/10 text-[#229ed9] border-[#229ed9]/20',
  discord: 'bg-[#5865f2]/10 text-[#5865f2] border-[#5865f2]/20',
  custom: 'bg-[#667085]/10 text-[#667085] border-[#667085]/20',
}

const STATUS_COLORS: Record<string, string> = {
  pending: 'bg-yellow-500/10 text-yellow-400',
  running: 'bg-blue-500/10 text-blue-400',
  completed: 'bg-green-500/10 text-green-400',
  failed: 'bg-red-500/10 text-red-400',
}

const emptyForm = {
  name: '', type: 'webhook' as string, source: 'github' as string,
  event_filter: '{}', action_type: 'skill' as string, action_payload: '',
  agent: '', enabled: true,
}

export default function Triggers() {
  const { hasPermission } = useAuth()
  const toast = useToast()
  const confirm = useConfirm()
  const [triggers, setTriggers] = useState<TriggerItem[]>([])
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState('all')
  const [showModal, setShowModal] = useState(false)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [form, setForm] = useState(emptyForm)
  const [saving, setSaving] = useState(false)
  const [copied, setCopied] = useState<number | null>(null)
  const [execModal, setExecModal] = useState<{ triggerId: number; name: string } | null>(null)
  const [executions, setExecutions] = useState<Execution[]>([])
  const [execLoading, setExecLoading] = useState(false)
  const [newSecret, setNewSecret] = useState<{ id: number; secret: string } | null>(null)

  const fetchTriggers = () => {
    let url = '/triggers'
    if (filter === 'webhooks') url += '?type=webhook'
    else if (filter === 'events') url += '?type=event'
    else if (filter === 'enabled') url += '?enabled=true'
    else if (filter === 'disabled') url += '?enabled=false'

    api.get(url)
      .then((data: { triggers: TriggerItem[] }) => setTriggers(data.triggers || []))
      .catch(() => setTriggers([]))
      .finally(() => setLoading(false))
  }

  // F11: inline fetch in useEffect to avoid stale closure
  useEffect(() => {
    let url = '/triggers'
    if (filter === 'webhooks') url += '?type=webhook'
    else if (filter === 'events') url += '?type=event'
    else if (filter === 'enabled') url += '?enabled=true'
    else if (filter === 'disabled') url += '?enabled=false'

    api.get(url)
      .then((data: { triggers: TriggerItem[] }) => setTriggers(data.triggers || []))
      .catch(() => setTriggers([]))
      .finally(() => setLoading(false))
  }, [filter])

  const openCreate = () => {
    setEditingId(null)
    setForm({ ...emptyForm })  // F12: fresh copy
    setShowModal(true)
  }

  const openEdit = (t: TriggerItem) => {
    setEditingId(t.id)
    setForm({
      name: t.name, type: t.type, source: t.source,
      event_filter: JSON.stringify(t.event_filter, null, 2),
      action_type: t.action_type, action_payload: t.action_payload,
      agent: t.agent || '', enabled: t.enabled,
    })
    setShowModal(true)
  }

  const handleSave = async () => {
    setSaving(true)
    try {
      let ef: Record<string, string>
      try { ef = JSON.parse(form.event_filter) } catch { toast.error('JSON inválido no event filter'); setSaving(false); return }

      const body = { ...form, event_filter: ef, agent: form.agent || null }
      if (editingId) {
        await api.put(`/triggers/${editingId}`, body)
      } else {
        await api.post('/triggers', body)
      }
      setShowModal(false)
      fetchTriggers()
    } catch (e) {
      toast.error('Erro ao salvar trigger', String(e))
    }
    setSaving(false)
  }

  const handleDelete = async (id: number) => {
    const ok = await confirm({
      title: 'Deletar trigger',
      description: 'Deletar este trigger e todas as suas execuções?',
      confirmText: 'Deletar',
      variant: 'danger',
    })
    if (!ok) return
    try {
      await api.delete(`/triggers/${id}`)
      fetchTriggers()
    } catch (e) {
      toast.error('Erro ao deletar', String(e))
    }
  }

  const handleTest = async (id: number) => {
    try {
      await api.post(`/triggers/${id}/test`)
      fetchTriggers()
    } catch (e) {
      toast.error('Erro ao testar trigger', String(e))
    }
  }

  const handleToggle = async (t: TriggerItem) => {
    try {
      await api.put(`/triggers/${t.id}`, { enabled: !t.enabled })
      fetchTriggers()
    } catch (e) {
      toast.error('Erro ao alternar trigger', String(e))
    }
  }

  const copyWebhookUrl = (t: TriggerItem) => {
    const base = window.location.origin.includes('localhost:5173')
      ? 'http://localhost:8080'
      : window.location.origin
    navigator.clipboard.writeText(`${base}/api/triggers/webhook/${t.id}`)
    setCopied(t.id)
    setTimeout(() => setCopied(null), 2000)
  }

  const openExecutions = async (triggerId: number, name: string) => {
    setExecModal({ triggerId, name })
    setExecLoading(true)
    try {
      const data = await api.get(`/triggers/${triggerId}/executions`)
      setExecutions(data.executions || [])
    } catch {
      setExecutions([])
    }
    setExecLoading(false)
  }

  const handleRegenerateSecret = async (id: number) => {
    const ok = await confirm({
      title: 'Regenerar webhook secret',
      description: 'O secret atual deixará de funcionar imediatamente.',
      confirmText: 'Regenerar',
      variant: 'danger',
    })
    if (!ok) return
    try {
      const data = await api.post(`/triggers/${id}/regenerate-secret`)
      setNewSecret({ id, secret: data.secret })
    } catch (e) {
      toast.error('Erro ao regenerar secret', String(e))
    }
  }

  const filters = [
    { value: 'all', label: 'All' },
    { value: 'webhooks', label: 'Webhooks' },
    { value: 'events', label: 'Events' },
    { value: 'enabled', label: 'Enabled' },
    { value: 'disabled', label: 'Disabled' },
  ]

  const relativeTime = (iso: string) => {
    const diff = Date.now() - new Date(iso).getTime()
    const mins = Math.floor(diff / 60000)
    if (mins < 1) return 'just now'
    if (mins < 60) return `${mins}m ago`
    const hours = Math.floor(mins / 60)
    if (hours < 24) return `${hours}h ago`
    const days = Math.floor(hours / 24)
    return `${days}d ago`
  }

  if (loading) {
    return (
      <div>
        <h1 className="text-2xl font-bold text-[#e6edf3] mb-8">Triggers</h1>
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
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#00FFA7" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M18 16.98h-5.99c-1.1 0-1.95.94-2.48 1.9A4 4 0 0 1 2 17c.01-.7.2-1.4.57-2"/><path d="m6 7 3 5"/><path d="m12 6 1.97 3.44"/><path d="M22 17c0 2.21-1.79 4-4 4-1.29 0-2.44-.62-3.16-1.58"/><path d="M2 12c1.9-3.31 5.57-5 9.24-5a11.57 11.57 0 0 1 6.93 2.34"/><circle cx="18" cy="17" r="1"/></svg>
          </div>
          <div>
            <h1 className="text-2xl font-bold text-[#e6edf3]">Triggers</h1>
            <p className="text-[#667085] mt-0.5 text-sm">Reactive event triggers — webhook & event-based</p>
          </div>
        </div>
        {hasPermission('triggers', 'execute') && (
          <button onClick={openCreate}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-[#00FFA7]/10 border border-[#00FFA7]/20 text-[#00FFA7] hover:bg-[#00FFA7]/20 transition-colors font-medium text-sm"
          >
            <Plus size={16} /> New Trigger
          </button>
        )}
      </div>

      {/* Filters */}
      <div className="flex items-center gap-2 mb-4 flex-wrap">
        {filters.map(f => (
          <button key={f.value}
            onClick={() => setFilter(f.value)}
            className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors border ${
              filter === f.value
                ? 'bg-[#00FFA7]/10 border-[#00FFA7]/20 text-[#00FFA7]'
                : 'bg-[#161b22] border-[#21262d] text-[#667085] hover:text-[#D0D5DD] hover:border-[#344054]'
            }`}
          >
            {f.label}
          </button>
        ))}
        <span className="text-[11px] text-[#667085] ml-2">{triggers.length} triggers</span>
        <button onClick={fetchTriggers} className="ml-auto p-1.5 rounded-lg hover:bg-white/5 text-[#667085] hover:text-[#e6edf3] transition-colors" title="Refresh">
          <RefreshCw size={14} />
        </button>
      </div>

      {/* Table */}
      {triggers.length === 0 ? (
        <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-12 text-center">
          <p className="text-[#667085] text-sm">No triggers yet. Create one to get started.</p>
        </div>
      ) : (
        <div className="bg-[#161b22] border border-[#21262d] rounded-xl overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-[#667085] text-xs uppercase tracking-wider bg-[#0d1117]/50 border-b border-[#21262d]">
                <th className="text-left p-4 font-medium">Name</th>
                <th className="text-left p-4 font-medium">Type</th>
                <th className="text-left p-4 font-medium">Source</th>
                <th className="text-left p-4 font-medium hidden xl:table-cell">Action</th>
                <th className="text-left p-4 font-medium hidden lg:table-cell">Agent</th>
                <th className="text-center p-4 font-medium">Status</th>
                <th className="text-center p-4 font-medium">Runs</th>
                <th className="text-right p-4 font-medium">Actions</th>
              </tr>
            </thead>
            <tbody>
              {triggers.map(t => (
                <tr key={t.id} className="border-t border-[#21262d]/50 hover:bg-white/[0.02]">
                  <td className="p-4">
                    <div className="text-[#e6edf3] font-medium">{t.name}</div>
                    <div className="text-[#667085] text-xs mt-0.5">
                      {Object.entries(t.event_filter).map(([k, v]) => `${k}:${v}`).join(', ') || 'no filter'}
                    </div>
                  </td>
                  <td className="p-4">
                    <span className={`inline-block px-2 py-0.5 rounded text-[10px] font-medium border ${
                      t.type === 'webhook' ? 'bg-[#00FFA7]/10 text-[#00FFA7] border-[#00FFA7]/20' : 'bg-purple-500/10 text-purple-400 border-purple-500/20'
                    }`}>
                      {t.type}
                    </span>
                  </td>
                  <td className="p-4">
                    <span className={`inline-block px-2 py-0.5 rounded text-[10px] font-medium border ${SOURCE_COLORS[t.source] || SOURCE_COLORS.custom}`}>
                      {t.source}
                    </span>
                  </td>
                  <td className="p-4 hidden xl:table-cell">
                    <span className="text-[#667085] text-xs">{t.action_type}:</span>{' '}
                    <span className="text-[#e6edf3] text-xs">{t.action_payload.length > 40 ? t.action_payload.slice(0, 40) + '...' : t.action_payload}</span>
                  </td>
                  <td className="p-4 hidden lg:table-cell">
                    <span className="text-[#667085] text-xs">{t.agent ? `@${t.agent.replace('-assistant', '').replace('-financeiro', '').replace('-community', '').replace('-strategy', '').replace('-social-media', '').replace('-comercial', '').replace('-courses', '').replace('-personal', '')}` : '--'}</span>
                  </td>
                  <td className="p-4 text-center">
                    <button onClick={() => handleToggle(t)} title={t.enabled ? 'Disable' : 'Enable'}
                      className={`inline-block w-8 h-4 rounded-full relative cursor-pointer transition-colors ${t.enabled ? 'bg-[#00FFA7]/30' : 'bg-[#344054]'}`}
                    >
                      <span className={`absolute top-0.5 w-3 h-3 rounded-full transition-all ${t.enabled ? 'left-4 bg-[#00FFA7]' : 'left-0.5 bg-[#667085]'}`} />
                    </button>
                  </td>
                  <td className="p-4 text-center">
                    <button onClick={() => openExecutions(t.id, t.name)}
                      className="text-[#667085] hover:text-[#e6edf3] text-xs transition-colors"
                    >
                      {t.execution_count}
                    </button>
                  </td>
                  <td className="p-4">
                    <div className="flex items-center gap-1 justify-end">
                      {t.type === 'webhook' && (
                        <button onClick={() => copyWebhookUrl(t)}
                          className={`p-1.5 rounded-lg transition-colors ${copied === t.id ? 'text-[#00FFA7]' : 'hover:bg-white/5 text-[#667085] hover:text-[#e6edf3]'}`}
                          title="Copy webhook URL"
                        >
                          <Copy size={14} />
                        </button>
                      )}
                      {hasPermission('triggers', 'execute') && (
                        <button onClick={() => handleTest(t.id)} className="p-1.5 rounded-lg hover:bg-white/5 text-[#667085] hover:text-[#e6edf3] transition-colors" title="Test">
                          <Play size={14} />
                        </button>
                      )}
                      {hasPermission('triggers', 'execute') && (
                        <button onClick={() => openEdit(t)} className="p-1.5 rounded-lg hover:bg-white/5 text-[#667085] hover:text-[#e6edf3] transition-colors" title="Edit">
                          <Pencil size={14} />
                        </button>
                      )}
                      {hasPermission('triggers', 'manage') && (
                        <>
                          <button onClick={() => handleRegenerateSecret(t.id)} className="p-1.5 rounded-lg hover:bg-white/5 text-[#667085] hover:text-yellow-400 transition-colors" title="Regenerate Secret">
                            <KeyRound size={14} />
                          </button>
                          <button onClick={() => handleDelete(t.id)} className="p-1.5 rounded-lg hover:bg-red-500/10 text-[#667085] hover:text-red-400 transition-colors" title="Delete">
                            <Trash2 size={14} />
                          </button>
                        </>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Create/Edit Modal */}
      {showModal && (
        <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4" onClick={() => setShowModal(false)}>
          <div className="bg-[#161b22] border border-[#21262d] rounded-xl w-full max-w-lg max-h-[90vh] overflow-y-auto" onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between px-6 py-4 border-b border-[#21262d]">
              <h2 className="text-lg font-semibold text-[#e6edf3]">{editingId ? 'Edit Trigger' : 'New Trigger'}</h2>
              <button onClick={() => setShowModal(false)} className="p-1 rounded-lg hover:bg-white/5 text-[#667085] hover:text-[#e6edf3]"><X size={18} /></button>
            </div>

            <div className="p-6 space-y-4">
              <div>
                <label className="block text-xs font-medium text-[#667085] mb-1.5">Name *</label>
                <input type="text" value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} placeholder="Deploy Notification"
                  className="w-full px-3 py-2 bg-[#0d1117] border border-[#21262d] rounded-lg text-sm text-[#e6edf3] placeholder-[#667085] focus:border-[#00FFA7]/50 focus:outline-none" />
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-xs font-medium text-[#667085] mb-1.5">Type *</label>
                  <select value={form.type} onChange={e => setForm({ ...form, type: e.target.value })}
                    className="w-full px-3 py-2 bg-[#0d1117] border border-[#21262d] rounded-lg text-sm text-[#e6edf3] focus:border-[#00FFA7]/50 focus:outline-none">
                    <option value="webhook">Webhook</option>
                    <option value="event">Event</option>
                  </select>
                </div>
                <div>
                  <label className="block text-xs font-medium text-[#667085] mb-1.5">Source *</label>
                  <select value={form.source} onChange={e => setForm({ ...form, source: e.target.value })}
                    className="w-full px-3 py-2 bg-[#0d1117] border border-[#21262d] rounded-lg text-sm text-[#e6edf3] focus:border-[#00FFA7]/50 focus:outline-none">
                    {SOURCES.map(s => <option key={s} value={s}>{s}</option>)}
                  </select>
                </div>
              </div>

              <div>
                <label className="block text-xs font-medium text-[#667085] mb-1.5">Event Filter (JSON)</label>
                <textarea value={form.event_filter} onChange={e => setForm({ ...form, event_filter: e.target.value })}
                  placeholder='{"event": "push", "branch": "main"}' rows={3}
                  className="w-full px-3 py-2 bg-[#0d1117] border border-[#21262d] rounded-lg text-sm text-[#e6edf3] placeholder-[#667085] focus:border-[#00FFA7]/50 focus:outline-none resize-none font-mono" />
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-xs font-medium text-[#667085] mb-1.5">Action Type *</label>
                  <select value={form.action_type} onChange={e => setForm({ ...form, action_type: e.target.value })}
                    className="w-full px-3 py-2 bg-[#0d1117] border border-[#21262d] rounded-lg text-sm text-[#e6edf3] focus:border-[#00FFA7]/50 focus:outline-none">
                    {ACTION_TYPES.map(at => <option key={at} value={at}>{at}</option>)}
                  </select>
                </div>
                <div>
                  <label className="block text-xs font-medium text-[#667085] mb-1.5">Agent</label>
                  <select value={form.agent} onChange={e => setForm({ ...form, agent: e.target.value })}
                    className="w-full px-3 py-2 bg-[#0d1117] border border-[#21262d] rounded-lg text-sm text-[#e6edf3] focus:border-[#00FFA7]/50 focus:outline-none">
                    <option value="">None</option>
                    {AGENTS.map(a => <option key={a} value={a}>{a}</option>)}
                  </select>
                </div>
              </div>

              <div>
                <label className="block text-xs font-medium text-[#667085] mb-1.5">Action Payload *</label>
                <textarea value={form.action_payload} onChange={e => setForm({ ...form, action_payload: e.target.value })}
                  placeholder="discord-send-message Deploy detectado na main" rows={2}
                  className="w-full px-3 py-2 bg-[#0d1117] border border-[#21262d] rounded-lg text-sm text-[#e6edf3] placeholder-[#667085] focus:border-[#00FFA7]/50 focus:outline-none resize-none" />
              </div>

              <div className="flex items-center gap-2">
                <input type="checkbox" checked={form.enabled} onChange={e => setForm({ ...form, enabled: e.target.checked })}
                  className="rounded border-[#21262d] bg-[#0d1117] text-[#00FFA7] focus:ring-[#00FFA7]/50" />
                <label className="text-xs text-[#667085]">Enabled</label>
              </div>
            </div>

            <div className="flex items-center justify-end gap-3 px-6 py-4 border-t border-[#21262d]">
              <button onClick={() => setShowModal(false)}
                className="px-4 py-2 rounded-lg border border-[#21262d] text-[#667085] hover:text-[#e6edf3] hover:border-[#344054] transition-colors text-sm">
                Cancel
              </button>
              <button onClick={handleSave} disabled={saving || !form.name || !form.action_payload}
                className="px-4 py-2 rounded-lg bg-[#00FFA7]/10 border border-[#00FFA7]/20 text-[#00FFA7] hover:bg-[#00FFA7]/20 transition-colors text-sm font-medium disabled:opacity-50 disabled:cursor-not-allowed">
                {saving ? 'Saving...' : editingId ? 'Update' : 'Create'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Executions Modal */}
      {execModal && (
        <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4" onClick={() => setExecModal(null)}>
          <div className="bg-[#161b22] border border-[#21262d] rounded-xl w-full max-w-2xl max-h-[80vh] overflow-hidden flex flex-col" onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between px-6 py-4 border-b border-[#21262d]">
              <h2 className="text-lg font-semibold text-[#e6edf3]">Executions — {execModal.name}</h2>
              <button onClick={() => setExecModal(null)} className="p-1 rounded-lg hover:bg-white/5 text-[#667085] hover:text-[#e6edf3]"><X size={18} /></button>
            </div>
            <div className="overflow-y-auto flex-1">
              {execLoading ? (
                <div className="p-6 text-center text-[#667085] text-sm">Loading...</div>
              ) : executions.length === 0 ? (
                <div className="p-6 text-center text-[#667085] text-sm">No executions yet</div>
              ) : (
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-[#667085] text-xs uppercase tracking-wider bg-[#0d1117]/50 border-b border-[#21262d]">
                      <th className="text-left p-3 font-medium">Status</th>
                      <th className="text-left p-3 font-medium">Event</th>
                      <th className="text-left p-3 font-medium">Duration</th>
                      <th className="text-left p-3 font-medium">Time</th>
                    </tr>
                  </thead>
                  <tbody>
                    {executions.map(ex => (
                      <tr key={ex.id} className="border-t border-[#21262d]/50 hover:bg-white/[0.02]">
                        <td className="p-3">
                          <span className={`inline-block px-2 py-0.5 rounded text-[10px] font-medium ${STATUS_COLORS[ex.status] || ''}`}>
                            {ex.status}
                          </span>
                        </td>
                        <td className="p-3 text-[#667085] text-xs max-w-[200px] truncate">
                          {(ex.event_data as Record<string, unknown>)?._test ? 'test' : (String((ex.event_data as Record<string, unknown>)?.event_type || '--'))}
                          {ex.error && <span className="text-red-400 ml-2" title={ex.error}>error</span>}
                        </td>
                        <td className="p-3 text-[#667085] text-xs">
                          {ex.duration_seconds != null ? `${ex.duration_seconds.toFixed(1)}s` : '--'}
                        </td>
                        <td className="p-3 text-[#667085] text-xs">
                          {ex.started_at ? relativeTime(ex.started_at) : '--'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </div>
        </div>
      )}

      {/* New Secret Modal */}
      {newSecret && (
        <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4" onClick={() => setNewSecret(null)}>
          <div className="bg-[#161b22] border border-[#21262d] rounded-xl w-full max-w-md" onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between px-6 py-4 border-b border-[#21262d]">
              <h2 className="text-lg font-semibold text-[#e6edf3]">New Secret Generated</h2>
              <button onClick={() => setNewSecret(null)} className="p-1 rounded-lg hover:bg-white/5 text-[#667085] hover:text-[#e6edf3]"><X size={18} /></button>
            </div>
            <div className="p-6">
              <p className="text-[#667085] text-xs mb-3">Copy this secret now — it won't be shown again.</p>
              <div className="flex items-center gap-2">
                <code className="flex-1 px-3 py-2 bg-[#0d1117] border border-[#21262d] rounded-lg text-xs text-[#e6edf3] font-mono break-all">
                  {newSecret.secret}
                </code>
                <button onClick={() => { navigator.clipboard.writeText(newSecret.secret); }}
                  className="p-2 rounded-lg hover:bg-white/5 text-[#667085] hover:text-[#00FFA7] transition-colors" title="Copy">
                  <Copy size={16} />
                </button>
              </div>
            </div>
            <div className="flex justify-end px-6 py-4 border-t border-[#21262d]">
              <button onClick={() => setNewSecret(null)}
                className="px-4 py-2 rounded-lg bg-[#00FFA7]/10 border border-[#00FFA7]/20 text-[#00FFA7] hover:bg-[#00FFA7]/20 transition-colors text-sm font-medium">
                Done
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
