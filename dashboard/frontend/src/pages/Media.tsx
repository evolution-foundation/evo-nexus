import { useEffect, useState, useCallback } from 'react'
import {
  Video, Plus, RefreshCw, Play, X, CheckCircle2, XCircle, AlertCircle,
  Clock, Loader2, Upload, CalendarClock, FileText, Film,
} from 'lucide-react'
import { api } from '../lib/api'

// "Mídias" — MediaJob pipeline management UI (social-media-production,
// briefing Etapa 14). Priority: functionality + consistency with the
// existing dark theme, not a from-scratch design system (briefing:
// "Para o MVP, priorize funcionalidade... Não reescreva todo o frontend").

const PLATFORMS = ['instagram', 'youtube', 'linkedin', 'tiktok'] as const
const FORMATS = ['vertical', 'horizontal', 'square'] as const

interface MediaJob {
  id: string
  project_id: number | null
  campaign_id: string | null
  title: string
  brief: string | null
  platform: string
  format: string
  width: number
  height: number
  fps: number
  duration_seconds: number
  caption: string | null
  publication_mode: 'draft' | 'schedule'
  scheduled_at: string | null
  scheduled_at_utc: string | null
  timezone: string
  status: string
  render_path: string | null
  render_sha256: string | null
  render_size_bytes: number | null
  render_duration_seconds: number | null
  postiz_media_id: string | null
  postiz_post_id: string | null
  attempt_count: number
  last_error: string | null
  reject_reason: string | null
  created_at: string
  updated_at: string
  approved_at: string | null
}

const STATUS_COLOR: Record<string, string> = {
  queued: '#8b949e',
  preparing: '#58a6ff',
  generating: '#58a6ff',
  rendering: '#d29922',
  validating: '#d29922',
  ready_for_review: '#a371f7',
  rejected: '#f85149',
  approved: '#00FFA7',
  uploading: '#58a6ff',
  creating_draft: '#58a6ff',
  draft_created: '#00FFA7',
  scheduling: '#d29922',
  scheduled: '#00FFA7',
  published: '#00FFA7',
  retryable_failure: '#d29922',
  failed: '#f85149',
  cancelled: '#8b949e',
}

function StatusBadge({ status }: { status: string }) {
  const color = STATUS_COLOR[status] || '#8b949e'
  return (
    <span
      className="text-xs px-2 py-0.5 rounded-full border whitespace-nowrap"
      style={{ color, borderColor: `${color}55`, background: `${color}14` }}
    >
      {status}
    </span>
  )
}

function fmtBytes(n: number | null): string {
  if (!n) return '—'
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`
  return `${(n / (1024 * 1024)).toFixed(1)} MB`
}

const emptyForm = {
  title: '', brief: '', platform: 'instagram' as (typeof PLATFORMS)[number],
  format: 'vertical' as (typeof FORMATS)[number], width: 1080, height: 1920, fps: 30,
  duration_seconds: 20, language: 'pt-BR', caption: '', publication_mode: 'draft' as 'draft' | 'schedule',
  scheduled_at: '', timezone: 'America/Bahia', project_id: '',
}

export default function Media() {
  const [jobs, setJobs] = useState<MediaJob[]>([])
  const [loading, setLoading] = useState(true)
  const [statusFilter, setStatusFilter] = useState<string>('')
  const [error, setError] = useState<string | null>(null)

  const [createOpen, setCreateOpen] = useState(false)
  const [form, setForm] = useState(emptyForm)
  const [creating, setCreating] = useState(false)

  const [selected, setSelected] = useState<MediaJob | null>(null)
  const [logs, setLogs] = useState<{ logs: Record<string, string>; last_error: string | null } | null>(null)
  const [rejectReason, setRejectReason] = useState('')
  const [busy, setBusy] = useState(false)

  const load = useCallback(() => {
    setLoading(true)
    setError(null)
    const qs = statusFilter ? `?status=${statusFilter}` : ''
    api.get(`/media/jobs${qs}`)
      .then((data) => setJobs(Array.isArray(data) ? data : []))
      .catch((e) => setError(e?.message || 'Falha ao carregar mídias'))
      .finally(() => setLoading(false))
  }, [statusFilter])

  useEffect(() => { load() }, [load])

  const openDetail = (job: MediaJob) => {
    setSelected(job)
    setLogs(null)
    api.get(`/media/jobs/${job.id}/logs`).then(setLogs).catch(() => {})
  }

  const refreshSelected = async () => {
    if (!selected) return
    try {
      const fresh = await api.get(`/media/jobs/${selected.id}`)
      setSelected(fresh)
      setJobs((prev) => prev.map((j) => (j.id === fresh.id ? fresh : j)))
      api.get(`/media/jobs/${fresh.id}/logs`).then(setLogs).catch(() => {})
    } catch { /* ignore */ }
  }

  const handleCreate = async () => {
    setCreating(true)
    setError(null)
    try {
      const body: Record<string, unknown> = {
        title: form.title,
        brief: form.brief || undefined,
        platform: form.platform,
        format: form.format,
        width: Number(form.width),
        height: Number(form.height),
        fps: Number(form.fps),
        duration_seconds: Number(form.duration_seconds),
        language: form.language,
        caption: form.caption || undefined,
        publication_mode: form.publication_mode,
        timezone: form.timezone,
      }
      if (form.scheduled_at) body.scheduled_at = form.scheduled_at
      if (form.project_id) body.project_id = Number(form.project_id)
      await api.post('/media/jobs', body)
      setCreateOpen(false)
      setForm(emptyForm)
      load()
    } catch (e: any) {
      setError(e?.message || 'Falha ao criar mídia')
    } finally {
      setCreating(false)
    }
  }

  const action = async (job: MediaJob, path: string, body?: unknown) => {
    setBusy(true)
    setError(null)
    try {
      await api.post(`/media/jobs/${job.id}/${path}`, body)
      load()
      if (selected?.id === job.id) refreshSelected()
    } catch (e: any) {
      setError(e?.message || `Falha em ${path}`)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="p-6 max-w-[1400px] mx-auto">
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-2.5">
          <div className="flex items-center justify-center w-9 h-9 rounded-xl bg-[#00FFA7]/8 border border-[#00FFA7]/15">
            <Video size={18} className="text-[#00FFA7]" />
          </div>
          <div>
            <h1 className="text-lg font-semibold text-[#e6edf3]">Mídias</h1>
            <p className="text-xs text-[#8b949e]">Produção de vídeo social — briefing → HyperFrames → validação → Postiz</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="bg-[#0d1117] border border-[#21262d] rounded-lg px-3 py-1.5 text-sm text-[#e6edf3]"
          >
            <option value="">Todos os status</option>
            {Object.keys(STATUS_COLOR).map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
          <button onClick={load} className="p-2 rounded-lg text-[#8b949e] hover:text-[#e6edf3] hover:bg-[#21262d]">
            <RefreshCw size={16} />
          </button>
          <button
            onClick={() => setCreateOpen(true)}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium bg-[#00FFA7]/10 border border-[#00FFA7]/30 text-[#00FFA7] hover:bg-[#00FFA7]/15"
          >
            <Plus size={14} /> Criar Mídia
          </button>
        </div>
      </div>

      {error && (
        <div className="mb-4 flex items-center gap-2 text-sm text-[#f85149] bg-[#f85149]/10 border border-[#f85149]/30 rounded-lg px-3 py-2">
          <AlertCircle size={14} /> {error}
        </div>
      )}

      <div className="rounded-xl border border-[#21262d] bg-[#161b22] overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-[#21262d] text-[#8b949e] text-xs uppercase tracking-wide">
              <th className="text-left px-4 py-2.5 font-medium">Título</th>
              <th className="text-left px-4 py-2.5 font-medium">Plataforma</th>
              <th className="text-left px-4 py-2.5 font-medium">Status</th>
              <th className="text-left px-4 py-2.5 font-medium">Duração</th>
              <th className="text-left px-4 py-2.5 font-medium">Tamanho</th>
              <th className="text-left px-4 py-2.5 font-medium">Tentativas</th>
              <th className="text-left px-4 py-2.5 font-medium">Postiz</th>
              <th className="text-left px-4 py-2.5 font-medium">Ações</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr><td colSpan={8} className="px-4 py-8 text-center text-[#8b949e]">
                <Loader2 size={16} className="animate-spin inline mr-2" /> Carregando...
              </td></tr>
            ) : jobs.length === 0 ? (
              <tr><td colSpan={8} className="px-4 py-8 text-center text-[#8b949e]">Nenhuma mídia ainda.</td></tr>
            ) : jobs.map((job) => (
              <tr
                key={job.id}
                className="border-b border-[#21262d]/60 hover:bg-[#0d1117]/60 cursor-pointer"
                onClick={() => openDetail(job)}
              >
                <td className="px-4 py-2.5 text-[#e6edf3]">
                  {job.title}
                  {job.last_error && (
                    <div className="text-[11px] text-[#f85149] truncate max-w-[280px]">{job.last_error}</div>
                  )}
                </td>
                <td className="px-4 py-2.5 text-[#8b949e]">{job.platform}</td>
                <td className="px-4 py-2.5"><StatusBadge status={job.status} /></td>
                <td className="px-4 py-2.5 text-[#8b949e]">{job.render_duration_seconds ?? job.duration_seconds}s</td>
                <td className="px-4 py-2.5 text-[#8b949e]">{fmtBytes(job.render_size_bytes)}</td>
                <td className="px-4 py-2.5 text-[#8b949e]">{job.attempt_count}</td>
                <td className="px-4 py-2.5 text-[#8b949e] text-xs">
                  {job.postiz_post_id ? `post: ${job.postiz_post_id.slice(0, 10)}…` : '—'}
                </td>
                <td className="px-4 py-2.5" onClick={(e) => e.stopPropagation()}>
                  <div className="flex items-center gap-1.5">
                    {['queued', 'rejected', 'retryable_failure'].includes(job.status) && (
                      <button
                        disabled={busy}
                        onClick={() => action(job, 'run')}
                        title="Iniciar"
                        className="p-1.5 rounded-lg text-[#00FFA7] hover:bg-[#00FFA7]/10"
                      ><Play size={14} /></button>
                    )}
                    {!['published', 'failed', 'cancelled'].includes(job.status) && (
                      <button
                        disabled={busy}
                        onClick={() => action(job, 'cancel')}
                        title="Cancelar"
                        className="p-1.5 rounded-lg text-[#f85149] hover:bg-[#f85149]/10"
                      ><X size={14} /></button>
                    )}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Create modal */}
      {createOpen && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4" onClick={() => setCreateOpen(false)}>
          <div
            className="bg-[#161b22] border border-[#21262d] rounded-2xl p-6 w-full max-w-2xl max-h-[85vh] overflow-y-auto"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-base font-semibold text-[#e6edf3]">Nova Mídia</h2>
              <button onClick={() => setCreateOpen(false)} className="text-[#8b949e] hover:text-[#e6edf3]"><X size={18} /></button>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="col-span-2">
                <label className="block text-xs text-[#8b949e] mb-1">Título *</label>
                <input value={form.title} onChange={(e) => setForm((f) => ({ ...f, title: e.target.value }))}
                  className="w-full bg-[#0d1117] border border-[#21262d] rounded-lg px-3 py-2 text-sm text-[#e6edf3]" />
              </div>
              <div className="col-span-2">
                <label className="block text-xs text-[#8b949e] mb-1">Briefing</label>
                <textarea value={form.brief} onChange={(e) => setForm((f) => ({ ...f, brief: e.target.value }))} rows={3}
                  className="w-full bg-[#0d1117] border border-[#21262d] rounded-lg px-3 py-2 text-sm text-[#e6edf3]" />
              </div>
              <div>
                <label className="block text-xs text-[#8b949e] mb-1">Plataforma</label>
                <select value={form.platform} onChange={(e) => setForm((f) => ({ ...f, platform: e.target.value as any }))}
                  className="w-full bg-[#0d1117] border border-[#21262d] rounded-lg px-3 py-2 text-sm text-[#e6edf3]">
                  {PLATFORMS.map((p) => <option key={p} value={p}>{p}</option>)}
                </select>
              </div>
              <div>
                <label className="block text-xs text-[#8b949e] mb-1">Formato</label>
                <select value={form.format} onChange={(e) => setForm((f) => ({ ...f, format: e.target.value as any }))}
                  className="w-full bg-[#0d1117] border border-[#21262d] rounded-lg px-3 py-2 text-sm text-[#e6edf3]">
                  {FORMATS.map((f) => <option key={f} value={f}>{f}</option>)}
                </select>
              </div>
              <div>
                <label className="block text-xs text-[#8b949e] mb-1">Largura (px)</label>
                <input type="number" value={form.width} onChange={(e) => setForm((f) => ({ ...f, width: Number(e.target.value) }))}
                  className="w-full bg-[#0d1117] border border-[#21262d] rounded-lg px-3 py-2 text-sm text-[#e6edf3]" />
              </div>
              <div>
                <label className="block text-xs text-[#8b949e] mb-1">Altura (px)</label>
                <input type="number" value={form.height} onChange={(e) => setForm((f) => ({ ...f, height: Number(e.target.value) }))}
                  className="w-full bg-[#0d1117] border border-[#21262d] rounded-lg px-3 py-2 text-sm text-[#e6edf3]" />
              </div>
              <div>
                <label className="block text-xs text-[#8b949e] mb-1">FPS</label>
                <select value={form.fps} onChange={(e) => setForm((f) => ({ ...f, fps: Number(e.target.value) }))}
                  className="w-full bg-[#0d1117] border border-[#21262d] rounded-lg px-3 py-2 text-sm text-[#e6edf3]">
                  {[24, 30, 60].map((f) => <option key={f} value={f}>{f}</option>)}
                </select>
              </div>
              <div>
                <label className="block text-xs text-[#8b949e] mb-1">Duração (s)</label>
                <input type="number" value={form.duration_seconds} onChange={(e) => setForm((f) => ({ ...f, duration_seconds: Number(e.target.value) }))}
                  className="w-full bg-[#0d1117] border border-[#21262d] rounded-lg px-3 py-2 text-sm text-[#e6edf3]" />
              </div>
              <div className="col-span-2">
                <label className="block text-xs text-[#8b949e] mb-1">Legenda sugerida</label>
                <textarea value={form.caption} onChange={(e) => setForm((f) => ({ ...f, caption: e.target.value }))} rows={2}
                  className="w-full bg-[#0d1117] border border-[#21262d] rounded-lg px-3 py-2 text-sm text-[#e6edf3]" />
              </div>
              <div>
                <label className="block text-xs text-[#8b949e] mb-1">Modo de publicação</label>
                <select value={form.publication_mode} onChange={(e) => setForm((f) => ({ ...f, publication_mode: e.target.value as any }))}
                  className="w-full bg-[#0d1117] border border-[#21262d] rounded-lg px-3 py-2 text-sm text-[#e6edf3]">
                  <option value="draft">draft</option>
                  <option value="schedule">schedule</option>
                </select>
              </div>
              <div>
                <label className="block text-xs text-[#8b949e] mb-1">Data desejada ({form.timezone})</label>
                <input type="datetime-local" value={form.scheduled_at} onChange={(e) => setForm((f) => ({ ...f, scheduled_at: e.target.value }))}
                  className="w-full bg-[#0d1117] border border-[#21262d] rounded-lg px-3 py-2 text-sm text-[#e6edf3]" />
              </div>
              <div>
                <label className="block text-xs text-[#8b949e] mb-1">Project ID (opcional)</label>
                <input value={form.project_id} onChange={(e) => setForm((f) => ({ ...f, project_id: e.target.value }))}
                  className="w-full bg-[#0d1117] border border-[#21262d] rounded-lg px-3 py-2 text-sm text-[#e6edf3]" />
              </div>
            </div>
            <div className="flex justify-end gap-2 mt-5">
              <button onClick={() => setCreateOpen(false)} className="px-3 py-1.5 rounded-lg text-sm text-[#8b949e] hover:bg-[#21262d]">Cancelar</button>
              <button
                disabled={creating || !form.title}
                onClick={handleCreate}
                className="px-3 py-1.5 rounded-lg text-sm font-medium bg-[#00FFA7]/10 border border-[#00FFA7]/30 text-[#00FFA7] hover:bg-[#00FFA7]/15 disabled:opacity-50"
              >
                {creating ? 'Criando...' : 'Criar e enfileirar'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Detail drawer */}
      {selected && (
        <div className="fixed inset-0 bg-black/60 flex justify-end z-50" onClick={() => setSelected(null)}>
          <div className="bg-[#161b22] border-l border-[#21262d] w-full max-w-xl h-full overflow-y-auto p-6" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-base font-semibold text-[#e6edf3]">{selected.title}</h2>
              <button onClick={() => setSelected(null)} className="text-[#8b949e] hover:text-[#e6edf3]"><X size={18} /></button>
            </div>

            <div className="flex items-center gap-2 mb-4">
              <StatusBadge status={selected.status} />
              <span className="text-xs text-[#8b949e]">{selected.platform} · {selected.format} · {selected.width}x{selected.height} · {selected.fps}fps</span>
            </div>

            {selected.render_path && ['ready_for_review', 'approved', 'uploading', 'creating_draft', 'draft_created', 'scheduling', 'scheduled', 'published'].includes(selected.status) && (
              <div className="mb-4 rounded-lg overflow-hidden border border-[#21262d] bg-black">
                <video
                  key={selected.id}
                  controls
                  className="w-full max-h-[360px]"
                  src={`/api/media/jobs/${selected.id}/video`}
                >
                  <Film size={24} />
                </video>
              </div>
            )}

            {selected.last_error && (
              <div className="mb-4 text-xs text-[#f85149] bg-[#f85149]/10 border border-[#f85149]/30 rounded-lg px-3 py-2">
                {selected.last_error}
              </div>
            )}
            {selected.reject_reason && (
              <div className="mb-4 text-xs text-[#d29922] bg-[#d29922]/10 border border-[#d29922]/30 rounded-lg px-3 py-2">
                Rejeitado: {selected.reject_reason}
              </div>
            )}

            <div className="grid grid-cols-2 gap-2 text-xs text-[#8b949e] mb-4">
              <div>Tentativas: <span className="text-[#e6edf3]">{selected.attempt_count}</span></div>
              <div>Checksum: <span className="text-[#e6edf3]">{selected.render_sha256 ? selected.render_sha256.slice(0, 12) + '…' : '—'}</span></div>
              <div>Postiz media: <span className="text-[#e6edf3]">{selected.postiz_media_id || '—'}</span></div>
              <div>Postiz post: <span className="text-[#e6edf3]">{selected.postiz_post_id || '—'}</span></div>
              <div>Criado: <span className="text-[#e6edf3]">{selected.created_at}</span></div>
              <div>Atualizado: <span className="text-[#e6edf3]">{selected.updated_at}</span></div>
            </div>

            <div className="flex flex-wrap gap-2 mb-5">
              {selected.status === 'ready_for_review' && (
                <>
                  <button disabled={busy} onClick={() => action(selected, 'approve')}
                    className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium bg-[#00FFA7]/10 border border-[#00FFA7]/30 text-[#00FFA7] hover:bg-[#00FFA7]/15">
                    <CheckCircle2 size={14} /> Aprovar
                  </button>
                  <div className="flex items-center gap-1.5">
                    <input
                      value={rejectReason}
                      onChange={(e) => setRejectReason(e.target.value)}
                      placeholder="Motivo da rejeição"
                      className="bg-[#0d1117] border border-[#21262d] rounded-lg px-2 py-1.5 text-xs text-[#e6edf3] w-40"
                    />
                    <button
                      disabled={busy || !rejectReason.trim()}
                      onClick={() => { action(selected, 'reject', { reason: rejectReason }); setRejectReason('') }}
                      className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium bg-[#f85149]/10 border border-[#f85149]/30 text-[#f85149] hover:bg-[#f85149]/15 disabled:opacity-50"
                    >
                      <XCircle size={14} /> Rejeitar
                    </button>
                  </div>
                </>
              )}
              {selected.status === 'approved' && (
                <button disabled={busy} onClick={() => action(selected, 'create-draft')}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium bg-[#58a6ff]/10 border border-[#58a6ff]/30 text-[#58a6ff] hover:bg-[#58a6ff]/15">
                  <Upload size={14} /> Criar draft no Postiz
                </button>
              )}
              {selected.status === 'draft_created' && selected.publication_mode === 'schedule' && (
                <button disabled={busy} onClick={() => action(selected, 'schedule')}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium bg-[#d29922]/10 border border-[#d29922]/30 text-[#d29922] hover:bg-[#d29922]/15">
                  <CalendarClock size={14} /> Agendar
                </button>
              )}
              {['queued', 'rejected', 'retryable_failure'].includes(selected.status) && (
                <button disabled={busy} onClick={() => action(selected, 'run')}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium bg-[#00FFA7]/10 border border-[#00FFA7]/30 text-[#00FFA7] hover:bg-[#00FFA7]/15">
                  <Play size={14} /> {selected.status === 'rejected' ? 'Recriar' : 'Iniciar'}
                </button>
              )}
              {!['published', 'failed', 'cancelled'].includes(selected.status) && (
                <button disabled={busy} onClick={() => action(selected, 'cancel')}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium bg-[#161b22] border border-[#21262d] text-[#8b949e] hover:text-[#f85149] hover:border-[#f85149]/30">
                  <X size={14} /> Cancelar
                </button>
              )}
            </div>

            <div className="flex items-center gap-2 mb-2">
              <FileText size={14} className="text-[#8b949e]" />
              <h3 className="text-sm font-medium text-[#e6edf3]">Logs</h3>
              <button onClick={refreshSelected} className="ml-auto p-1 rounded text-[#8b949e] hover:text-[#e6edf3]"><RefreshCw size={13} /></button>
            </div>
            {logs?.logs && Object.keys(logs.logs).length > 0 ? (
              Object.entries(logs.logs).map(([name, content]) => (
                <details key={name} className="mb-2 rounded-lg border border-[#21262d] bg-[#0d1117]">
                  <summary className="px-3 py-2 text-xs text-[#8b949e] cursor-pointer">{name}</summary>
                  <pre className="px-3 pb-3 text-[11px] text-[#8b949e] whitespace-pre-wrap max-h-64 overflow-y-auto">{content}</pre>
                </details>
              ))
            ) : (
              <div className="text-xs text-[#8b949e] flex items-center gap-1.5"><Clock size={12} /> Sem logs ainda.</div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
