import { useEffect, useState, useCallback } from 'react'
import { ExternalLink, Play, Square, RefreshCw, Plus, Pencil, Trash2, X, Monitor, Container, Globe, AppWindow } from 'lucide-react'
import { api } from '../lib/api'
import { useTranslation } from 'react-i18next'

interface SystemApp {
  id: number
  name: string
  description: string
  url: string
  container: string
  icon: string
  type: string
  running: boolean | null
  status_detail: string
}

interface SystemForm {
  name: string
  description: string
  url: string
  container: string
  icon: string
  type: string
}

const emptyForm: SystemForm = { name: '', description: '', url: '', container: '', icon: '📦', type: 'docker' }

const ICONS = ['📦', '🌐', '🚀', '🏥', '🎤', '📊', '🔧', '💬', '🤖', '📱', '🎮', '🛒']

const TYPE_CONFIG: Record<string, { icon: typeof Container; label: string; bg: string; text: string; border: string }> = {
  docker: { icon: Container, label: 'Docker', bg: 'rgba(96,165,250,0.10)', text: '#60A5FA', border: 'rgba(96,165,250,0.25)' },
  external: { icon: Globe, label: 'External', bg: 'rgba(168,85,247,0.10)', text: '#A855F7', border: 'rgba(168,85,247,0.25)' },
  iframe: { icon: AppWindow, label: 'Embedded', bg: 'rgba(251,191,36,0.10)', text: '#FBBF24', border: 'rgba(251,191,36,0.25)' },
}

function getTypeConfig(type: string) {
  return TYPE_CONFIG[type] || TYPE_CONFIG.docker
}

export default function Systems() {
  const { t } = useTranslation()
  const [apps, setApps] = useState<SystemApp[]>([])
  const [loading, setLoading] = useState(true)
  const [actionLoading, setActionLoading] = useState<number | null>(null)
  const [viewApp, setViewApp] = useState<SystemApp | null>(null)
  const [modalOpen, setModalOpen] = useState(false)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [form, setForm] = useState<SystemForm>(emptyForm)
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)

  const fetchApps = useCallback(() => {
    api.get('/systems')
      .then((data: SystemApp[]) => setApps(Array.isArray(data) ? data : []))
      .catch(() => setApps([]))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => { fetchApps() }, [fetchApps])

  const openCreate = () => {
    setEditingId(null)
    setForm(emptyForm)
    setError('')
    setModalOpen(true)
  }

  const openEdit = (app: SystemApp) => {
    setEditingId(app.id)
    setForm({
      name: app.name,
      description: app.description || '',
      url: app.url || '',
      container: app.container || '',
      icon: app.icon || '📦',
      type: app.type || 'docker',
    })
    setError('')
    setModalOpen(true)
  }

  const handleSubmit = async () => {
    setError('')
    if (!form.name.trim()) { setError('Name is required'); return }
    setSubmitting(true)
    try {
      if (editingId) {
        await api.put(`/systems/${editingId}`, form)
      } else {
        await api.post('/systems', form)
      }
      setModalOpen(false)
      fetchApps()
    } catch (ex: unknown) {
      setError(ex instanceof Error ? ex.message : 'Failed')
    } finally {
      setSubmitting(false)
    }
  }

  const handleDelete = async (app: SystemApp) => {
    if (!confirm(`Delete "${app.name}"?`)) return
    try {
      await api.delete(`/systems/${app.id}`)
      fetchApps()
    } catch { /* ignore */ }
  }

  const handleAction = async (app: SystemApp, action: 'start' | 'stop' | 'update') => {
    setActionLoading(app.id)
    try {
      await api.post(`/systems/${app.id}/${action}`)
      setTimeout(() => { fetchApps(); setActionLoading(null) }, 2000)
    } catch {
      setActionLoading(null)
    }
  }

  if (viewApp) {
    return (
      <div className="flex flex-col" style={{ height: 'calc(100vh - 64px)' }}>
        <div className="flex items-center justify-between mb-4 flex-shrink-0">
          <div className="flex items-center gap-3">
            <button onClick={() => setViewApp(null)} className="text-[#00FFA7] text-sm hover:underline">
              &larr; Back
            </button>
            <h1 className="text-xl font-bold text-[#e6edf3]">{viewApp.name}</h1>
            {viewApp.running !== null && (
              <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${viewApp.running ? 'bg-[#00FFA7]/10 text-[#00FFA7]' : 'bg-red-500/10 text-red-400'}`}>
                {viewApp.running ? 'Running' : 'Stopped'}
              </span>
            )}
          </div>
          <a href={viewApp.url} target="_blank" rel="noopener noreferrer" className="flex items-center gap-1.5 text-xs text-[#667085] hover:text-[#00FFA7] transition-colors">
            Open in new tab <ExternalLink size={12} />
          </a>
        </div>
        <div className="flex-1 bg-[#161b22] border border-[#21262d] rounded-xl overflow-hidden">
          <iframe src={viewApp.url} className="w-full h-full border-0" title={viewApp.name} />
        </div>
      </div>
    )
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-bold text-[#e6edf3]">{t('systems.title')}</h1>
          <p className="text-[#667085] mt-1">Registered applications and services</p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => { setLoading(true); fetchApps() }}
            className="flex items-center gap-2 px-3 py-2 rounded-lg border border-[#21262d] bg-[#161b22] text-[#667085] hover:text-[#00FFA7] hover:border-[#00FFA7]/30 transition-colors"
          >
            <RefreshCw size={16} /> Refresh
          </button>
          <button
            onClick={openCreate}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-[#00FFA7] text-[#0d1117] font-semibold text-sm hover:bg-[#00FFA7]/90 transition-colors"
          >
            <Plus size={16} /> Add System
          </button>
        </div>
      </div>

      {loading ? (
        <div className="space-y-3">
          {[...Array(2)].map((_, i) => <div key={i} className="skeleton h-24 rounded-xl" />)}
        </div>
      ) : apps.length === 0 ? (
        <div className="text-center py-16">
          <div className="w-16 h-16 rounded-2xl bg-[#161b22] border border-[#21262d] flex items-center justify-center mx-auto mb-4">
            <Monitor size={28} className="text-[#667085]" />
          </div>
          <p className="text-[#667085] mb-4">No systems registered yet.</p>
          <button onClick={openCreate} className="text-[#00FFA7] text-sm hover:underline">
            Add your first system
          </button>
        </div>
      ) : (
        <div className="space-y-3">
          {apps.map(app => {
            const tc = getTypeConfig(app.type)
            const TypeIcon = tc.icon
            return (
              <div key={app.id} className="bg-[#161b22] border border-[#21262d] rounded-xl p-5 hover:border-[#00FFA7]/30 transition-all group">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-4">
                    <div className="w-11 h-11 rounded-xl flex items-center justify-center text-2xl" style={{ background: tc.bg, border: `1px solid ${tc.border}` }}>
                      {app.icon || <TypeIcon size={20} style={{ color: tc.text }} />}
                    </div>
                    <div>
                      <div className="flex items-center gap-2.5">
                        <h3 className="text-[15px] font-semibold text-[#e6edf3]">{app.name}</h3>
                        <span
                          className="inline-flex items-center gap-1 text-[10px] font-medium px-2 py-0.5 rounded-full border"
                          style={{ background: tc.bg, color: tc.text, borderColor: tc.border }}
                        >
                          <TypeIcon size={10} />
                          {tc.label}
                        </span>
                      </div>
                      <p className="text-sm text-[#667085] mt-0.5">{app.description}</p>
                    </div>
                  </div>

                  <div className="flex items-center gap-2">
                    {/* Status */}
                    {app.running !== null && (
                      <span className={`inline-flex items-center gap-1.5 text-xs font-medium px-2.5 py-1 rounded-full ${
                        app.running ? 'bg-[#00FFA7]/10 text-[#00FFA7]' : 'bg-red-500/10 text-red-400'
                      }`}>
                        <span className={`w-1.5 h-1.5 rounded-full ${app.running ? 'bg-[#00FFA7] animate-pulse' : 'bg-red-400'}`} />
                        {app.running ? 'Running' : 'Stopped'}
                      </span>
                    )}

                    {/* Open */}
                    {app.url && (
                      <button
                        onClick={() => setViewApp(app)}
                        className="flex items-center gap-1.5 text-sm px-3 py-1.5 rounded-lg bg-[#00FFA7]/10 text-[#00FFA7] hover:bg-[#00FFA7]/20 border border-[#00FFA7]/20 transition-colors font-medium"
                      >
                        <ExternalLink size={13} /> Open
                      </button>
                    )}

                    {/* Docker actions */}
                    {app.container && (
                      <>
                        <button
                          onClick={() => handleAction(app, 'update')}
                          disabled={actionLoading === app.id}
                          className="flex items-center gap-1 text-xs px-3 py-1.5 rounded-lg bg-blue-500/10 text-blue-400 hover:bg-blue-500/20 border border-blue-500/20 transition-colors disabled:opacity-50"
                        >
                          {actionLoading === app.id ? <RefreshCw size={13} className="animate-spin" /> : <RefreshCw size={13} />} Update
                        </button>

                        <button
                          onClick={() => handleAction(app, app.running ? 'stop' : 'start')}
                          disabled={actionLoading === app.id}
                          className={`flex items-center gap-1 text-xs px-3 py-1.5 rounded-lg transition-colors disabled:opacity-50 border ${
                            app.running
                              ? 'bg-red-500/10 text-red-400 hover:bg-red-500/20 border-red-500/20'
                              : 'bg-[#00FFA7]/10 text-[#00FFA7] hover:bg-[#00FFA7]/20 border-[#00FFA7]/20'
                          }`}
                        >
                          {app.running ? <><Square size={13} /> Stop</> : <><Play size={13} /> Start</>}
                        </button>
                      </>
                    )}

                    {/* Edit / Delete */}
                    <button onClick={() => openEdit(app)} className="p-1.5 rounded-lg text-[#667085] hover:text-[#e6edf3] hover:bg-white/5 transition-colors" title="Edit">
                      <Pencil size={14} />
                    </button>
                    <button onClick={() => handleDelete(app)} className="p-1.5 rounded-lg text-[#667085] hover:text-red-400 hover:bg-red-500/10 transition-colors" title="Delete">
                      <Trash2 size={14} />
                    </button>
                  </div>
                </div>

                {/* Details */}
                <div className="mt-4 pt-3 border-t border-[#21262d]/60 flex items-center gap-4 text-xs text-[#667085]">
                  {app.container && (
                    <span>Container: <code className="text-[#e6edf3] bg-black/30 px-1.5 py-0.5 rounded font-mono">{app.container}</code></span>
                  )}
                  {app.url && (
                    <span>URL: <a href={app.url} target="_blank" rel="noopener noreferrer" className="text-[#00FFA7] hover:underline">{app.url}</a></span>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      )}

      {/* Modal */}
      {modalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm px-4">
          <div className="bg-[#161b22] rounded-2xl border border-[#21262d] p-6 w-full max-w-md shadow-2xl">
            <div className="flex items-center justify-between mb-5">
              <h2 className="text-lg font-bold text-[#e6edf3]">{editingId ? 'Edit System' : 'Add System'}</h2>
              <button onClick={() => setModalOpen(false)} className="text-[#667085] hover:text-[#e6edf3] transition-colors"><X size={18} /></button>
            </div>

            {error && (
              <div className="mb-4 px-4 py-3 rounded-lg bg-red-500/10 border border-red-500/30 text-red-400 text-sm">{error}</div>
            )}

            <div className="space-y-3">
              <div className="flex gap-3">
                <div className="flex-1">
                  <label className="block text-sm font-medium text-[#e6edf3] mb-1">Name *</label>
                  <input type="text" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })}
                    className="w-full px-3 py-2 rounded-lg bg-[#0d1117] border border-[#21262d] text-[#e6edf3] text-sm focus:outline-none focus:border-[#00FFA7] transition-colors"
                    placeholder="My App" />
                </div>
                <div className="w-20">
                  <label className="block text-sm font-medium text-[#e6edf3] mb-1">Icon</label>
                  <div className="relative">
                    <select value={form.icon} onChange={(e) => setForm({ ...form, icon: e.target.value })}
                      className="w-full px-2 py-2 rounded-lg bg-[#0d1117] border border-[#21262d] text-white text-lg text-center focus:outline-none focus:border-[#00FFA7] appearance-none transition-colors">
                      {ICONS.map(i => <option key={i} value={i}>{i}</option>)}
                    </select>
                  </div>
                </div>
              </div>

              <div>
                <label className="block text-sm font-medium text-[#e6edf3] mb-1">Description</label>
                <input type="text" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })}
                  className="w-full px-3 py-2 rounded-lg bg-[#0d1117] border border-[#21262d] text-[#e6edf3] text-sm focus:outline-none focus:border-[#00FFA7] transition-colors"
                  placeholder="What this system does" />
              </div>

              <div>
                <label className="block text-sm font-medium text-[#e6edf3] mb-1">URL</label>
                <input type="text" value={form.url} onChange={(e) => setForm({ ...form, url: e.target.value })}
                  className="w-full px-3 py-2 rounded-lg bg-[#0d1117] border border-[#21262d] text-[#e6edf3] text-sm focus:outline-none focus:border-[#00FFA7] transition-colors"
                  placeholder="http://localhost:3000" />
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-sm font-medium text-[#e6edf3] mb-1">Docker Container</label>
                  <input type="text" value={form.container} onChange={(e) => setForm({ ...form, container: e.target.value })}
                    className="w-full px-3 py-2 rounded-lg bg-[#0d1117] border border-[#21262d] text-[#e6edf3] text-sm focus:outline-none focus:border-[#00FFA7] transition-colors"
                    placeholder="my-container" />
                </div>
                <div>
                  <label className="block text-sm font-medium text-[#e6edf3] mb-1">Type</label>
                  <select value={form.type} onChange={(e) => setForm({ ...form, type: e.target.value })}
                    className="w-full px-3 py-2 rounded-lg bg-[#0d1117] border border-[#21262d] text-[#e6edf3] text-sm focus:outline-none focus:border-[#00FFA7] transition-colors">
                    <option value="docker">Docker</option>
                    <option value="external">External URL</option>
                    <option value="iframe">Embedded (iframe)</option>
                  </select>
                </div>
              </div>
            </div>

            <div className="flex justify-end gap-3 mt-6">
              <button onClick={() => setModalOpen(false)}
                className="px-4 py-2 rounded-lg text-[#667085] text-sm hover:text-[#e6edf3] hover:bg-white/5 transition-colors">
                Cancel
              </button>
              <button onClick={handleSubmit} disabled={submitting}
                className="px-4 py-2 rounded-lg bg-[#00FFA7] text-[#0d1117] font-semibold text-sm hover:bg-[#00FFA7]/90 transition-colors disabled:opacity-50">
                {submitting ? 'Saving...' : editingId ? 'Save' : 'Add System'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
