import { useEffect, useState } from 'react'
import { useToast } from '../components/Toast'
import { useConfirm } from '../components/ConfirmDialog'
import { CheckCircle2, XCircle, Clock, RefreshCw, ExternalLink } from 'lucide-react'
import { api } from '../lib/api'
import { Link } from 'react-router-dom'

interface ApprovalItem {
  id: number
  gate_type: 'publish' | 'decomposition' | 'project_suggestion' | 'goal_suggestion'
  status: 'pending' | 'approved' | 'rejected' | 'expired' | 'published'
  agent: string | null
  ticket_id: string | null
  goal_id: number | null
  mission_id: number | null
  project_id: number | null
  title: string | null
  body: string | null
  context: string | null
  items_preview: string | null
  created_at: string
  expires_at: string
  decided_at: string | null
  decided_by: string | null
}

const GATE_LABELS: Record<string, string> = {
  publish: 'Publicação',
  decomposition: 'Decomposição (Meta → Tickets)',
  project_suggestion: 'Sugestão de Projetos',
  goal_suggestion: 'Sugestão de Metas',
}

const GATE_COLORS: Record<string, string> = {
  publish: 'bg-[#00FFA7]/10 text-[#00FFA7] border-[#00FFA7]/20',
  decomposition: 'bg-blue-500/10 text-blue-400 border-blue-500/20',
  project_suggestion: 'bg-purple-500/10 text-purple-400 border-purple-500/20',
  goal_suggestion: 'bg-orange-500/10 text-orange-400 border-orange-500/20',
}

const STATUS_COLORS: Record<string, string> = {
  pending: 'bg-yellow-500/10 text-yellow-400',
  approved: 'bg-green-500/10 text-green-400',
  rejected: 'bg-red-500/10 text-red-400',
  expired: 'bg-white/10 text-white/40',
  published: 'bg-green-500/10 text-green-400',
}

const STATUS_LABELS: Record<string, string> = {
  pending: 'Pendente', approved: 'Aprovada', rejected: 'Rejeitada',
  expired: 'Expirada', published: 'Publicada',
}

function timeAgo(iso: string): string {
  const diffMs = Date.now() - new Date(iso).getTime()
  const mins = Math.floor(diffMs / 60000)
  if (mins < 1) return 'agora'
  if (mins < 60) return `há ${mins}min`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `há ${hours}h`
  return `há ${Math.floor(hours / 24)}d`
}

function relatedLink(a: ApprovalItem): { to: string; label: string } | null {
  if (a.ticket_id) return { to: `/tickets/${a.ticket_id}`, label: 'Ver ticket' }
  if (a.goal_id) return { to: '/goals', label: 'Ver metas' }
  if (a.project_id) return { to: '/goals', label: 'Ver projeto' }
  if (a.mission_id) return { to: '/goals', label: 'Ver missão' }
  return null
}

export default function Approvals() {
  const toast = useToast()
  const confirm = useConfirm()
  const [approvals, setApprovals] = useState<ApprovalItem[]>([])
  const [loading, setLoading] = useState(true)
  const [statusFilter, setStatusFilter] = useState('pending')
  const [actingId, setActingId] = useState<number | null>(null)

  const fetchApprovals = () => {
    setLoading(true)
    api.get(`/approvals?status=${statusFilter}`)
      .then((data: { approvals: ApprovalItem[] }) => setApprovals(data.approvals || []))
      .catch(() => setApprovals([]))
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    fetchApprovals()
  }, [statusFilter])

  const decide = async (a: ApprovalItem, decision: 'approve' | 'reject') => {
    const verb = decision === 'approve' ? 'Aprovar' : 'Rejeitar'
    const ok = await confirm({
      title: `${verb} ${GATE_LABELS[a.gate_type] || a.gate_type}`,
      description: a.context
        ? `${a.context}\n\n${a.title || ''}`
        : (a.title || 'Confirmar decisão?'),
      confirmText: verb,
      variant: decision === 'reject' ? 'danger' : 'default',
    })
    if (!ok) return

    setActingId(a.id)
    try {
      await api.post(`/approvals/${a.id}/dashboard-decision`, { decision })
      toast.success(`${verb === 'Aprovar' ? 'Aprovado' : 'Rejeitado'} com sucesso`)
      fetchApprovals()
    } catch (e) {
      toast.error(`Erro ao ${decision === 'approve' ? 'aprovar' : 'rejeitar'}`, String(e))
    } finally {
      setActingId(null)
    }
  }

  return (
    <div className="p-6 max-w-4xl mx-auto">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-xl font-semibold text-white">Aprovações</h1>
          <p className="text-sm text-white/50 mt-1">
            Fallback do dashboard para as aprovações que normalmente chegam no Telegram —
            decidir aqui exige sessão de admin logada, nunca token de API.
          </p>
        </div>
        <button
          onClick={fetchApprovals}
          className="flex items-center gap-2 px-3 py-2 rounded-lg text-sm text-white/60 hover:text-white hover:bg-white/5 transition"
        >
          <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
          Atualizar
        </button>
      </div>

      <div className="flex gap-2 mb-4">
        {['pending', 'approved', 'rejected', 'expired', 'published', 'all'].map((s) => (
          <button
            key={s}
            onClick={() => setStatusFilter(s)}
            className={`px-3 py-1.5 rounded-full text-xs font-medium transition border ${
              statusFilter === s
                ? 'bg-[#00FFA7]/10 text-[#00FFA7] border-[#00FFA7]/30'
                : 'text-white/50 border-white/10 hover:text-white/80'
            }`}
          >
            {STATUS_LABELS[s] || 'Todas'}
          </button>
        ))}
      </div>

      {loading && approvals.length === 0 && (
        <div className="text-white/40 text-sm py-12 text-center">Carregando…</div>
      )}

      {!loading && approvals.length === 0 && (
        <div className="text-white/40 text-sm py-12 text-center border border-white/5 rounded-xl">
          Nenhuma aprovação {statusFilter !== 'all' ? STATUS_LABELS[statusFilter]?.toLowerCase() : ''} agora.
        </div>
      )}

      <div className="flex flex-col gap-3">
        {approvals.map((a) => {
          const link = relatedLink(a)
          return (
            <div key={a.id} className="rounded-xl border border-white/10 bg-white/[0.02] p-4">
              <div className="flex items-center justify-between mb-2">
                <div className="flex items-center gap-2">
                  <span className={`px-2 py-0.5 rounded-md text-xs font-medium border ${GATE_COLORS[a.gate_type] || ''}`}>
                    {GATE_LABELS[a.gate_type] || a.gate_type}
                  </span>
                  <span className={`px-2 py-0.5 rounded-md text-xs font-medium ${STATUS_COLORS[a.status] || ''}`}>
                    {STATUS_LABELS[a.status] || a.status}
                  </span>
                </div>
                <span className="flex items-center gap-1 text-xs text-white/40">
                  <Clock size={12} />
                  {timeAgo(a.created_at)}
                </span>
              </div>

              {a.context && (
                <div className="text-xs text-white/50 mb-1">{a.context}</div>
              )}
              <div className="text-sm text-white/90 font-medium mb-1">{a.title || `Aprovação #${a.id}`}</div>
              {a.body && <div className="text-sm text-white/60 whitespace-pre-wrap mb-2">{a.body}</div>}
              {a.items_preview && (
                <pre className="text-xs text-white/50 whitespace-pre-wrap bg-white/[0.02] rounded-lg p-2 mb-2">
                  {a.items_preview}
                </pre>
              )}

              <div className="flex items-center justify-between mt-3">
                <div className="flex items-center gap-3 text-xs text-white/40">
                  {a.agent && <span>via @{a.agent}</span>}
                  {a.decided_by && <span>decidido por {a.decided_by}</span>}
                  {link && (
                    <Link to={link.to} className="flex items-center gap-1 text-[#00FFA7] hover:underline">
                      <ExternalLink size={12} /> {link.label}
                    </Link>
                  )}
                </div>

                {a.status === 'pending' && (
                  <div className="flex gap-2">
                    <button
                      disabled={actingId === a.id}
                      onClick={() => decide(a, 'reject')}
                      className="flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-medium text-red-400 border border-red-500/20 hover:bg-red-500/10 transition disabled:opacity-50"
                    >
                      <XCircle size={14} /> Rejeitar
                    </button>
                    <button
                      disabled={actingId === a.id}
                      onClick={() => decide(a, 'approve')}
                      className="flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-medium text-[#00FFA7] border border-[#00FFA7]/30 hover:bg-[#00FFA7]/10 transition disabled:opacity-50"
                    >
                      <CheckCircle2 size={14} /> Aprovar
                    </button>
                  </div>
                )}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
