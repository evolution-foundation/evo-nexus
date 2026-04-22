import { useEffect, useState, Fragment } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import {
  Heart, Play, RefreshCw, Plus, Trash2, ToggleLeft, ToggleRight,
  ChevronRight, Clock, CheckCircle, XCircle, AlertTriangle, Info,
} from 'lucide-react'
import { api } from '../lib/api'
import { useTranslation } from 'react-i18next'

// ── Types ─────────────────────────────────────────────────────────────────────

interface HeartbeatRun {
  run_id: string
  heartbeat_id: string
  started_at: string
  ended_at: string | null
  duration_ms: number | null
  tokens_in: number | null
  tokens_out: number | null
  cost_usd: number | null
  status: 'running' | 'success' | 'fail' | 'timeout' | 'killed'
  prompt_preview: string | null
  error: string | null
  triggered_by: string | null
}

interface Heartbeat {
  id: string
  agent: string
  interval_seconds: number
  max_turns: number
  timeout_seconds: number
  lock_timeout_seconds: number
  wake_triggers: string[]
  enabled: boolean
  goal_id: string | null
  required_secrets: string[]
  decision_prompt: string
  created_at: string
  updated_at: string
  last_run: HeartbeatRun | null
  run_count: number
  cost_7d: number
}

const STATUS_COLORS: Record<string, string> = {
  running: 'bg-blue-500/10 text-blue-400 border-blue-500/20',
  success: 'bg-[#00FFA7]/10 text-[#00FFA7] border-[#00FFA7]/20',
  fail: 'bg-red-500/10 text-red-400 border-red-500/20',
  timeout: 'bg-orange-500/10 text-orange-400 border-orange-500/20',
  killed: 'bg-red-500/10 text-red-400 border-red-500/20',
}

function formatDuration(ms: number | null): string {
  if (!ms) return '--'
  if (ms < 1000) return `${ms}ms`
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`
  return `${Math.floor(ms / 60000)}m ${Math.floor((ms % 60000) / 1000)}s`
}

function formatInterval(secs: number): string {
  if (secs < 3600) return `${Math.round(secs / 60)}m`
  if (secs % 3600 === 0) return `${secs / 3600}h`
  return `${secs}s`
}

function formatCost(usd: number | null): string {
  if (!usd) return '$0.00'
  return `$${usd.toFixed(4)}`
}

function StatusBadge({ status }: { status: string }) {
  const cls = STATUS_COLORS[status] || 'bg-[#21262d] text-[#667085] border-[#21262d]'
  return (
    <span className={`inline-flex items-center gap-1 text-[11px] font-medium px-2 py-0.5 rounded-full border ${cls}`}>
      {status === 'running' && <span className="w-1.5 h-1.5 rounded-full bg-blue-400 animate-pulse" />}
      {status === 'success' && <CheckCircle size={10} />}
      {status === 'fail' && <XCircle size={10} />}
      {status === 'timeout' && <AlertTriangle size={10} />}
      {status}
    </span>
  )
}

// ── Heartbeat List ────────────────────────────────────────────────────────────

export function HeartbeatsList() {
  const { t } = useTranslation()
  const [heartbeats, setHeartbeats] = useState<Heartbeat[]>([])
  const [loading, setLoading] = useState(true)
  const [running, setRunning] = useState<string | null>(null)
  const [showCreate, setShowCreate] = useState(false)
  const navigate = useNavigate()

  const load = async () => {
    try {
      const data = await api.get('/heartbeats')
      setHeartbeats(data.heartbeats || [])
    } catch {
      setHeartbeats([])
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  const handleToggle = async (hb: Heartbeat) => {
    try {
      await api.patch(`/heartbeats/${hb.id}`, { enabled: !hb.enabled })
      load()
    } catch (e: any) {
      alert(e?.message || 'Failed to toggle')
    }
  }

  const handleDelete = async (hb: Heartbeat) => {
    if (!confirm(`Delete heartbeat "${hb.id}"? This will remove all run history.`)) return
    try {
      await api.delete(`/heartbeats/${hb.id}`)
      load()
    } catch (e: any) {
      if (e?.status === 409) {
        if (confirm(`A run is in progress. Force delete?`)) {
          await api.delete(`/heartbeats/${hb.id}?force=true`)
          load()
        }
      } else {
        alert(e?.message || 'Failed to delete')
      }
    }
  }

  const handleRunNow = async (hb: Heartbeat) => {
    setRunning(hb.id)
    try {
      const result = await api.post(`/heartbeats/${hb.id}/run`)
      alert(`Run dispatched: ${result.run_id || 'ok'}`)
      setTimeout(load, 2000)
    } catch (e: any) {
      alert(e?.message || 'Failed to run')
    } finally {
      setRunning(null)
    }
  }

  if (loading) {
    return (
      <div>
        <div className="mb-8">
          <h1 className="text-2xl font-bold text-[#e6edf3]">{t('heartbeats.title')}</h1>
        </div>
        <div className="space-y-3">
          {[...Array(3)].map((_, i) => <div key={i} className="skeleton h-20 rounded-xl" />)}
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
            <Heart size={20} className="text-[#00FFA7]" />
          </div>
          <div>
            <h1 className="text-2xl font-bold text-[#e6edf3]">{t('heartbeats.title')}</h1>
            <p className="text-[#667085] mt-0.5 text-sm">Proactive agents — wake on trigger, decide, act or sleep</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => { setLoading(true); load() }}
            className="flex items-center gap-2 px-3 py-2 rounded-lg border border-[#21262d] bg-[#161b22] text-[#667085] hover:text-[#00FFA7] hover:border-[#00FFA7]/30 transition-colors"
          >
            <RefreshCw size={16} />
          </button>
          <button
            onClick={() => setShowCreate(true)}
            className="flex items-center gap-2 px-3 py-2 rounded-lg border border-[#00FFA7]/30 bg-[#00FFA7]/10 text-[#00FFA7] hover:bg-[#00FFA7]/20 transition-colors text-sm font-medium"
          >
            <Plus size={16} /> New Heartbeat
          </button>
        </div>
      </div>

      {/* Table */}
      {heartbeats.length === 0 ? (
        <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-12 text-center">
          <Heart size={32} className="text-[#667085] mx-auto mb-3" />
          <p className="text-[#e6edf3] font-medium mb-1">No heartbeats configured</p>
          <p className="text-[#667085] text-sm mb-4">Create one or run <code className="font-mono">make heartbeat-lint</code> to sync from YAML</p>
          <button
            onClick={() => setShowCreate(true)}
            className="inline-flex items-center gap-2 px-4 py-2 rounded-lg border border-[#00FFA7]/30 bg-[#00FFA7]/10 text-[#00FFA7] hover:bg-[#00FFA7]/20 transition-colors text-sm font-medium"
          >
            <Plus size={16} /> Create Heartbeat
          </button>
        </div>
      ) : (
        <div className="bg-[#161b22] border border-[#21262d] rounded-xl overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-[#667085] text-xs uppercase tracking-wider bg-[#0d1117]/50 border-b border-[#21262d]">
                <th className="text-left p-4 font-medium">Heartbeat</th>
                <th className="text-left p-4 font-medium">Agent</th>
                <th className="text-left p-4 font-medium">Interval</th>
                <th className="text-left p-4 font-medium">Last Run</th>
                <th className="text-left p-4 font-medium">Status</th>
                <th className="text-left p-4 font-medium">Cost 7d</th>
                <th className="text-left p-4 font-medium">Enabled</th>
                <th className="text-right p-4 font-medium">Actions</th>
              </tr>
            </thead>
            <tbody>
              {heartbeats.map((hb) => (
                <tr key={hb.id} className="border-t border-[#21262d]/50 hover:bg-white/[0.02] transition-colors">
                  <td className="p-4">
                    <button
                      onClick={() => navigate(`/heartbeats/${hb.id}`)}
                      className="flex items-center gap-1.5 text-[#e6edf3] font-medium hover:text-[#00FFA7] transition-colors"
                    >
                      {hb.id}
                      <ChevronRight size={14} className="text-[#667085]" />
                    </button>
                    <p className="text-[10px] text-[#667085] mt-0.5">{hb.run_count} runs total</p>
                  </td>
                  <td className="p-4">
                    <span className="inline-flex items-center gap-1 text-[11px] font-medium px-2.5 py-0.5 rounded-full bg-[#00FFA7]/8 border border-[#00FFA7]/20 text-[#00FFA7]">
                      @{hb.agent}
                    </span>
                  </td>
                  <td className="p-4">
                    <code className="text-[11px] bg-[#0d1117] border border-[#21262d] px-2 py-1 rounded text-[#e6edf3] font-mono">
                      {formatInterval(hb.interval_seconds)}
                    </code>
                  </td>
                  <td className="p-4 text-[#667085] text-xs">
                    {hb.last_run ? (
                      <div>
                        <div>{new Date(hb.last_run.started_at).toLocaleDateString()}</div>
                        <div className="text-[10px]">{formatDuration(hb.last_run.duration_ms)}</div>
                      </div>
                    ) : '--'}
                  </td>
                  <td className="p-4">
                    {hb.last_run ? <StatusBadge status={hb.last_run.status} /> : <span className="text-[#667085] text-xs">never</span>}
                  </td>
                  <td className="p-4 text-[#667085] text-xs font-mono">
                    {formatCost(hb.cost_7d)}
                  </td>
                  <td className="p-4">
                    <button
                      onClick={() => handleToggle(hb)}
                      className={`transition-colors ${hb.enabled ? 'text-[#00FFA7] hover:text-[#00FFA7]/70' : 'text-[#667085] hover:text-[#e6edf3]'}`}
                      title={hb.enabled ? 'Disable' : 'Enable'}
                    >
                      {hb.enabled ? <ToggleRight size={22} /> : <ToggleLeft size={22} />}
                    </button>
                  </td>
                  <td className="p-4">
                    <div className="flex items-center justify-end gap-2">
                      <button
                        onClick={() => handleRunNow(hb)}
                        disabled={running === hb.id}
                        className="flex items-center gap-1 text-xs px-2.5 py-1.5 rounded-lg border border-[#21262d] bg-[#0d1117] text-[#667085] hover:text-[#00FFA7] hover:border-[#00FFA7]/30 transition-colors disabled:opacity-50"
                        title="Run Now"
                      >
                        {running === hb.id ? <RefreshCw size={12} className="animate-spin" /> : <Play size={12} />}
                        Run
                      </button>
                      <button
                        onClick={() => navigate(`/heartbeats/${hb.id}`)}
                        className="p-1.5 rounded-lg border border-[#21262d] bg-[#0d1117] text-[#667085] hover:text-[#e6edf3] hover:border-[#21262d] transition-colors"
                        title="View details"
                      >
                        <Info size={14} />
                      </button>
                      <button
                        onClick={() => handleDelete(hb)}
                        className="p-1.5 rounded-lg border border-[#21262d] bg-[#0d1117] text-[#667085] hover:text-red-400 hover:border-red-500/20 transition-colors"
                        title="Delete"
                      >
                        <Trash2 size={14} />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Create modal */}
      {showCreate && (
        <HeartbeatCreateModal
          onClose={() => setShowCreate(false)}
          onCreated={() => { setShowCreate(false); load() }}
        />
      )}
    </div>
  )
}

// ── Create Modal ──────────────────────────────────────────────────────────────

const WAKE_TRIGGER_OPTIONS = ['interval', 'new_task', 'mention', 'manual', 'approval_decision']

function HeartbeatCreateModal({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const { t } = useTranslation()
  const [form, setForm] = useState({
    id: '',
    agent: '',
    interval_seconds: 3600,
    max_turns: 10,
    timeout_seconds: 300,
    lock_timeout_seconds: 1800,
    wake_triggers: ['interval', 'manual'] as string[],
    enabled: false,
    decision_prompt: '',
    required_secrets: '',
  })
  const [agents, setAgents] = useState<string[]>([])
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    api.get('/agents').then((data: any[]) => {
      setAgents(data.map((a: any) => a.name || a.id || a.slug).filter(Boolean))
    }).catch(() => {})
  }, [])

  const toggleTrigger = (t: string) => {
    setForm(f => ({
      ...f,
      wake_triggers: f.wake_triggers.includes(t)
        ? f.wake_triggers.filter(x => x !== t)
        : [...f.wake_triggers, t],
    }))
  }

  const validate = (): boolean => {
    const e: Record<string, string> = {}
    if (!form.id.match(/^[a-z0-9-]+$/)) e.id = 'ID must be lowercase alphanumeric with dashes'
    if (!form.agent) e.agent = 'Agent is required'
    if (form.interval_seconds < 60) e.interval_seconds = 'Minimum 60 seconds'
    if (form.wake_triggers.length === 0) e.wake_triggers = 'At least one trigger required'
    if (form.decision_prompt.length < 20) e.decision_prompt = 'Decision prompt must be at least 20 characters'
    setErrors(e)
    return Object.keys(e).length === 0
  }

  const handleSubmit = async () => {
    if (!validate()) return
    setSaving(true)
    try {
      const payload = {
        ...form,
        required_secrets: form.required_secrets.split(',').map(s => s.trim()).filter(Boolean),
      }
      await api.post('/heartbeats', payload)
      onCreated()
    } catch (e: any) {
      const details = e?.details
      if (details) {
        const errs: Record<string, string> = {}
        for (const d of details) {
          errs[d.loc?.join('.') || 'general'] = d.msg
        }
        setErrors(errs)
      } else {
        alert(e?.message || 'Failed to create heartbeat')
      }
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-6 w-full max-w-lg max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between mb-6">
          <h2 className="text-lg font-bold text-[#e6edf3]">{t('heartbeats.createHeartbeat')}</h2>
          <button onClick={onClose} className="text-[#667085] hover:text-[#e6edf3] transition-colors">✕</button>
        </div>

        <div className="space-y-4">
          {/* ID */}
          <div>
            <label className="block text-xs font-medium text-[#667085] uppercase tracking-wider mb-1.5">ID (slug)</label>
            <input
              type="text"
              value={form.id}
              onChange={e => setForm(f => ({ ...f, id: e.target.value }))}
              placeholder="atlas-4h"
              className="w-full bg-[#0d1117] border border-[#21262d] rounded-lg px-3 py-2 text-sm text-[#e6edf3] focus:outline-none focus:border-[#00FFA7]/50"
            />
            {errors.id && <p className="text-red-400 text-xs mt-1">{errors.id}</p>}
          </div>

          {/* Agent */}
          <div>
            <label className="block text-xs font-medium text-[#667085] uppercase tracking-wider mb-1.5">Agent</label>
            <input
              type="text"
              value={form.agent}
              onChange={e => setForm(f => ({ ...f, agent: e.target.value }))}
              list="agent-options"
              placeholder="atlas-project"
              className="w-full bg-[#0d1117] border border-[#21262d] rounded-lg px-3 py-2 text-sm text-[#e6edf3] focus:outline-none focus:border-[#00FFA7]/50"
            />
            <datalist id="agent-options">
              {agents.map(a => <option key={a} value={a} />)}
            </datalist>
            {errors.agent && <p className="text-red-400 text-xs mt-1">{errors.agent}</p>}
          </div>

          {/* Interval */}
          <div>
            <label className="block text-xs font-medium text-[#667085] uppercase tracking-wider mb-1.5">Interval (seconds)</label>
            <input
              type="number"
              min={60}
              value={form.interval_seconds}
              onChange={e => setForm(f => ({ ...f, interval_seconds: parseInt(e.target.value) || 60 }))}
              className="w-full bg-[#0d1117] border border-[#21262d] rounded-lg px-3 py-2 text-sm text-[#e6edf3] focus:outline-none focus:border-[#00FFA7]/50"
            />
            <p className="text-[10px] text-[#667085] mt-1">= {formatInterval(form.interval_seconds)} — minimum 60s</p>
            {errors.interval_seconds && <p className="text-red-400 text-xs mt-1">{errors.interval_seconds}</p>}
          </div>

          {/* Max turns + Timeout */}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-medium text-[#667085] uppercase tracking-wider mb-1.5">Max turns</label>
              <input
                type="number"
                min={1}
                max={100}
                value={form.max_turns}
                onChange={e => setForm(f => ({ ...f, max_turns: parseInt(e.target.value) || 10 }))}
                className="w-full bg-[#0d1117] border border-[#21262d] rounded-lg px-3 py-2 text-sm text-[#e6edf3] focus:outline-none focus:border-[#00FFA7]/50"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-[#667085] uppercase tracking-wider mb-1.5">Timeout (s)</label>
              <input
                type="number"
                min={30}
                max={3600}
                value={form.timeout_seconds}
                onChange={e => setForm(f => ({ ...f, timeout_seconds: parseInt(e.target.value) || 300 }))}
                className="w-full bg-[#0d1117] border border-[#21262d] rounded-lg px-3 py-2 text-sm text-[#e6edf3] focus:outline-none focus:border-[#00FFA7]/50"
              />
            </div>
          </div>

          {/* Wake triggers */}
          <div>
            <label className="block text-xs font-medium text-[#667085] uppercase tracking-wider mb-1.5">Wake triggers</label>
            <div className="flex flex-wrap gap-2">
              {WAKE_TRIGGER_OPTIONS.map(t => (
                <button
                  key={t}
                  type="button"
                  onClick={() => toggleTrigger(t)}
                  className={`text-xs px-3 py-1.5 rounded-lg border transition-colors ${
                    form.wake_triggers.includes(t)
                      ? 'border-[#00FFA7]/30 bg-[#00FFA7]/10 text-[#00FFA7]'
                      : 'border-[#21262d] bg-[#0d1117] text-[#667085] hover:text-[#e6edf3]'
                  }`}
                >
                  {t}
                </button>
              ))}
            </div>
            {errors.wake_triggers && <p className="text-red-400 text-xs mt-1">{errors.wake_triggers}</p>}
          </div>

          {/* Required secrets */}
          <div>
            <label className="block text-xs font-medium text-[#667085] uppercase tracking-wider mb-1.5">Required secrets (comma-separated, optional)</label>
            <input
              type="text"
              value={form.required_secrets}
              onChange={e => setForm(f => ({ ...f, required_secrets: e.target.value }))}
              placeholder="STRIPE_KEY, OMIE_APP_KEY"
              className="w-full bg-[#0d1117] border border-[#21262d] rounded-lg px-3 py-2 text-sm text-[#e6edf3] focus:outline-none focus:border-[#00FFA7]/50"
            />
          </div>

          {/* Decision prompt */}
          <div>
            <label className="block text-xs font-medium text-[#667085] uppercase tracking-wider mb-1.5">Decision prompt</label>
            <textarea
              value={form.decision_prompt}
              onChange={e => setForm(f => ({ ...f, decision_prompt: e.target.value }))}
              rows={4}
              placeholder="You are X. Check Y. Decide: should you act or skip? Respond with JSON: {action: 'work'|'skip', reason: '...'}"
              className="w-full bg-[#0d1117] border border-[#21262d] rounded-lg px-3 py-2 text-sm text-[#e6edf3] focus:outline-none focus:border-[#00FFA7]/50 resize-none font-mono"
            />
            {errors.decision_prompt && <p className="text-red-400 text-xs mt-1">{errors.decision_prompt}</p>}
          </div>

          {/* Enabled */}
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={() => setForm(f => ({ ...f, enabled: !f.enabled }))}
              className={`transition-colors ${form.enabled ? 'text-[#00FFA7]' : 'text-[#667085]'}`}
            >
              {form.enabled ? <ToggleRight size={22} /> : <ToggleLeft size={22} />}
            </button>
            <span className="text-sm text-[#667085]">
              {form.enabled ? 'Enabled (will run on schedule)' : 'Disabled (safe — enable via toggle when ready)'}
            </span>
          </div>
        </div>

        <div className="flex justify-end gap-3 mt-6 pt-4 border-t border-[#21262d]">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm text-[#667085] hover:text-[#e6edf3] transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={handleSubmit}
            disabled={saving}
            className="flex items-center gap-2 px-4 py-2 rounded-lg border border-[#00FFA7]/30 bg-[#00FFA7]/10 text-[#00FFA7] hover:bg-[#00FFA7]/20 transition-colors text-sm font-medium disabled:opacity-50"
          >
            {saving ? <RefreshCw size={14} className="animate-spin" /> : <Plus size={14} />}
            Create
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Heartbeat Detail ──────────────────────────────────────────────────────────

export function HeartbeatDetail() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [hb, setHb] = useState<Heartbeat | null>(null)
  const [runs, setRuns] = useState<HeartbeatRun[]>([])
  const [loading, setLoading] = useState(true)
  const [running, setRunning] = useState(false)
  const [expandedRun, setExpandedRun] = useState<string | null>(null)

  const load = async () => {
    if (!id) return
    try {
      const [detail, runsData] = await Promise.all([
        api.get(`/heartbeats/${id}`),
        api.get(`/heartbeats/${id}/runs?limit=50`),
      ])
      setHb(detail)
      setRuns(runsData.runs || [])
    } catch {
      setHb(null)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [id])

  const handleRunNow = async () => {
    if (!hb) return
    setRunning(true)
    try {
      const result = await api.post(`/heartbeats/${id}/run`)
      alert(`Run dispatched: ${result.run_id}`)
      setTimeout(load, 2000)
    } catch (e: any) {
      alert(e?.message || 'Failed to run')
    } finally {
      setRunning(false)
    }
  }

  const handleToggle = async () => {
    if (!hb) return
    await api.patch(`/heartbeats/${id}`, { enabled: !hb.enabled })
    load()
  }

  if (loading) {
    return (
      <div className="space-y-3">
        {[...Array(4)].map((_, i) => <div key={i} className="skeleton h-16 rounded-xl" />)}
      </div>
    )
  }

  if (!hb) {
    return (
      <div className="text-center py-20">
        <p className="text-[#667085]">Heartbeat not found</p>
        <button onClick={() => navigate('/heartbeats')} className="mt-4 text-[#00FFA7] hover:underline text-sm">
          Back to list
        </button>
      </div>
    )
  }

  return (
    <div>
      {/* Header */}
      <div className="flex items-center justify-between mb-8">
        <div className="flex items-center gap-3">
          <button onClick={() => navigate('/heartbeats')} className="text-[#667085] hover:text-[#e6edf3] transition-colors text-sm">
            Heartbeats
          </button>
          <ChevronRight size={14} className="text-[#667085]" />
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-[#161b22] border border-[#21262d] flex items-center justify-center">
              <Heart size={20} className="text-[#00FFA7]" />
            </div>
            <div>
              <h1 className="text-2xl font-bold text-[#e6edf3]">{hb.id}</h1>
              <p className="text-[#667085] mt-0.5 text-sm">@{hb.agent} · every {formatInterval(hb.interval_seconds)}</p>
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={handleToggle}
            className={`flex items-center gap-2 px-3 py-2 rounded-lg border transition-colors text-sm font-medium ${
              hb.enabled
                ? 'border-[#00FFA7]/30 bg-[#00FFA7]/10 text-[#00FFA7] hover:bg-[#00FFA7]/20'
                : 'border-[#21262d] bg-[#161b22] text-[#667085] hover:text-[#e6edf3]'
            }`}
          >
            {hb.enabled ? <ToggleRight size={16} /> : <ToggleLeft size={16} />}
            {hb.enabled ? 'Enabled' : 'Disabled'}
          </button>
          <button
            onClick={handleRunNow}
            disabled={running}
            className="flex items-center gap-2 px-3 py-2 rounded-lg border border-[#21262d] bg-[#161b22] text-[#667085] hover:text-[#00FFA7] hover:border-[#00FFA7]/30 transition-colors text-sm disabled:opacity-50"
          >
            {running ? <RefreshCw size={16} className="animate-spin" /> : <Play size={16} />}
            Run Now
          </button>
          <button
            onClick={() => { setLoading(true); load() }}
            className="flex items-center gap-2 px-3 py-2 rounded-lg border border-[#21262d] bg-[#161b22] text-[#667085] hover:text-[#e6edf3] transition-colors"
          >
            <RefreshCw size={16} />
          </button>
        </div>
      </div>

      {/* Config */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-8">
        <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-5">
          <h3 className="text-xs font-medium text-[#667085] uppercase tracking-wider mb-4">Configuration</h3>
          <dl className="space-y-2">
            {[
              ['Agent', `@${hb.agent}`],
              ['Interval', formatInterval(hb.interval_seconds)],
              ['Max turns', String(hb.max_turns)],
              ['Timeout', `${hb.timeout_seconds}s`],
              ['Lock timeout', `${hb.lock_timeout_seconds}s`],
              ['Wake triggers', hb.wake_triggers.join(', ')],
              ['Cost 7d', formatCost(hb.cost_7d)],
              ['Total runs', String(hb.run_count)],
            ].map(([label, value]) => (
              <div key={label} className="flex items-center justify-between text-sm">
                <dt className="text-[#667085]">{label}</dt>
                <dd className="text-[#e6edf3] font-mono text-xs">{value}</dd>
              </div>
            ))}
          </dl>
        </div>
        <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-5">
          <h3 className="text-xs font-medium text-[#667085] uppercase tracking-wider mb-4">Decision Prompt</h3>
          <pre className="text-xs text-[#e6edf3] whitespace-pre-wrap font-mono leading-5 max-h-48 overflow-y-auto">
            {hb.decision_prompt}
          </pre>
        </div>
      </div>

      {/* Runs */}
      <div className="flex items-center gap-3 mb-4">
        <h2 className="text-base font-semibold text-[#e6edf3]">Run History</h2>
        <span className="text-[11px] font-medium text-[#667085] bg-[#21262d] px-2 py-0.5 rounded-full">{runs.length}</span>
      </div>
      {runs.length === 0 ? (
        <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-8 text-center">
          <Clock size={24} className="text-[#667085] mx-auto mb-3" />
          <p className="text-[#667085] text-sm">No runs yet</p>
        </div>
      ) : (
        <div className="bg-[#161b22] border border-[#21262d] rounded-xl overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-[#667085] text-xs uppercase tracking-wider bg-[#0d1117]/50 border-b border-[#21262d]">
                <th className="text-left p-4 font-medium">Started</th>
                <th className="text-left p-4 font-medium">Status</th>
                <th className="text-left p-4 font-medium">Duration</th>
                <th className="text-left p-4 font-medium">Triggered by</th>
                <th className="text-left p-4 font-medium">Cost</th>
                <th className="text-left p-4 font-medium">Details</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((run) => (
                <Fragment key={run.run_id}>
                  <tr className="border-t border-[#21262d]/50 hover:bg-white/[0.02] transition-colors">
                    <td className="p-4 text-[#667085] text-xs">
                      {new Date(run.started_at).toLocaleString()}
                    </td>
                    <td className="p-4"><StatusBadge status={run.status} /></td>
                    <td className="p-4 text-[#667085] text-xs font-mono">{formatDuration(run.duration_ms)}</td>
                    <td className="p-4">
                      <span className="text-[11px] text-[#667085]">{run.triggered_by || '--'}</span>
                    </td>
                    <td className="p-4 text-[#667085] text-xs font-mono">{formatCost(run.cost_usd)}</td>
                    <td className="p-4">
                      {(run.prompt_preview || run.error) && (
                        <button
                          onClick={() => setExpandedRun(expandedRun === run.run_id ? null : run.run_id)}
                          className="text-xs text-[#667085] hover:text-[#e6edf3] transition-colors"
                        >
                          {expandedRun === run.run_id ? 'hide' : 'expand'}
                        </button>
                      )}
                    </td>
                  </tr>
                  {expandedRun === run.run_id && (
                    <tr className="border-t border-[#21262d]/50 bg-[#0d1117]/30">
                      <td colSpan={6} className="p-4">
                        {run.error && (
                          <div className="mb-3">
                            <p className="text-xs font-medium text-red-400 mb-1">Error</p>
                            <pre className="text-xs text-red-300 whitespace-pre-wrap bg-red-500/5 border border-red-500/20 rounded-lg p-3 font-mono leading-5">
                              {run.error}
                            </pre>
                          </div>
                        )}
                        {run.prompt_preview && (
                          <div>
                            <p className="text-xs font-medium text-[#667085] mb-1">Prompt preview</p>
                            <pre className="text-xs text-[#e6edf3] whitespace-pre-wrap bg-[#0d1117] border border-[#21262d] rounded-lg p-3 font-mono leading-5 max-h-40 overflow-y-auto">
                              {run.prompt_preview}
                            </pre>
                          </div>
                        )}
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

// Default export for list page
export default HeartbeatsList
