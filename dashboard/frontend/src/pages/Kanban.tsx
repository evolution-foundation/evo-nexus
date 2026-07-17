import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  AlertTriangle,
  ArrowDown,
  ArrowUp,
  CheckCircle,
  Clock,
  Columns3,
  Eye,
  Lock,
  Minus,
  Plus,
  RefreshCw,
  Search,
  Ticket,
  XCircle,
} from 'lucide-react'
import { AgentIcon } from '../components/AgentIcon'
import { useToast } from '../components/Toast'
import { api } from '../lib/api'
import CreateTicketModal, { type CreatedTicket } from '../components/CreateTicketModal'

type TicketStatus = 'open' | 'in_progress' | 'blocked' | 'review' | 'resolved' | 'closed' | 'archived'
type TicketPriority = 'urgent' | 'high' | 'medium' | 'low'

interface TicketItem {
  id: string
  title: string
  description: string | null
  status: TicketStatus
  priority: TicketPriority
  priority_rank: number
  assignee_agent: string | null
  locked_at: string | null
  locked_by: string | null
  updated_at: string
  is_thread: boolean
}

const STATUSES: Array<{ id: TicketStatus; label: string; icon: React.ReactNode; tone: string }> = [
  { id: 'open', label: 'Open', icon: <Clock size={13} />, tone: 'text-blue-400 border-blue-500/25 bg-blue-500/10' },
  { id: 'in_progress', label: 'In Progress', icon: <RefreshCw size={13} />, tone: 'text-[#00FFA7] border-[#00FFA7]/25 bg-[#00FFA7]/10' },
  { id: 'blocked', label: 'Blocked', icon: <AlertTriangle size={13} />, tone: 'text-red-400 border-red-500/25 bg-red-500/10' },
  { id: 'review', label: 'Review', icon: <Eye size={13} />, tone: 'text-purple-400 border-purple-500/25 bg-purple-500/10' },
  { id: 'resolved', label: 'Resolved', icon: <CheckCircle size={13} />, tone: 'text-gray-300 border-gray-500/25 bg-gray-500/10' },
  { id: 'closed', label: 'Closed', icon: <XCircle size={13} />, tone: 'text-[#667085] border-[#344054] bg-[#21262d]' },
]

const PRIORITY_ICON: Record<TicketPriority, React.ReactNode> = {
  urgent: <ArrowUp size={11} />,
  high: <ArrowUp size={11} className="opacity-70" />,
  medium: <Minus size={11} />,
  low: <ArrowDown size={11} />,
}

const PRIORITY_TONE: Record<TicketPriority, string> = {
  urgent: 'text-red-400 border-red-500/25 bg-red-500/10',
  high: 'text-orange-400 border-orange-500/25 bg-orange-500/10',
  medium: 'text-yellow-400 border-yellow-500/25 bg-yellow-500/10',
  low: 'text-[#667085] border-[#344054] bg-[#21262d]',
}

const PRIORITY_RAIL: Record<TicketPriority, string> = {
  urgent: 'bg-red-500',
  high: 'bg-orange-400',
  medium: 'bg-yellow-400',
  low: 'bg-[#344054]',
}

function formatAge(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime()
  const minutes = Math.max(1, Math.floor(diff / 60000))
  if (minutes < 60) return `${minutes}m`
  const hours = Math.floor(minutes / 60)
  if (hours < 48) return `${hours}h`
  return `${Math.floor(hours / 24)}d`
}

export default function Kanban() {
  const navigate = useNavigate()
  const toast = useToast()
  const [tickets, setTickets] = useState<TicketItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [updating, setUpdating] = useState<string | null>(null)
  const [showCreateModal, setShowCreateModal] = useState(false)

  const fetchTickets = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const params = new URLSearchParams()
      params.set('limit', '500')
      params.set('display_mode', 'all')
      if (query.trim()) params.set('q', query.trim())
      const data = await api.get(`/tickets?${params.toString()}`)
      setTickets((data.tickets || []).filter((t: TicketItem) => t.status !== 'archived'))
    } catch (err: any) {
      setError(err?.message || 'Failed to load Kanban')
    } finally {
      setLoading(false)
    }
  }, [query])

  useEffect(() => {
    fetchTickets()
  }, [fetchTickets])

  const grouped = useMemo(() => {
    const base = Object.fromEntries(STATUSES.map((s) => [s.id, [] as TicketItem[]])) as Record<TicketStatus, TicketItem[]>
    tickets.forEach((ticket) => {
      if (base[ticket.status]) base[ticket.status].push(ticket)
    })
    return base
  }, [tickets])

  const moveTicket = async (ticket: TicketItem, status: TicketStatus) => {
    if (ticket.status === status) return
    const previous = tickets
    setUpdating(ticket.id)
    setTickets((current) => current.map((item) => item.id === ticket.id ? { ...item, status } : item))
    try {
      await api.patch(`/tickets/${ticket.id}`, { status })
    } catch (err: any) {
      setTickets(previous)
      toast.error('Falha ao mover ticket', err?.message)
    } finally {
      setUpdating(null)
    }
  }

  const handleTicketCreated = (ticket: CreatedTicket) => {
    setTickets((current) => [ticket as unknown as TicketItem, ...current])
  }

  return (
    <div className="min-h-screen bg-[#0C111D]">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between mb-6">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl bg-[#161b22] border border-[#21262d] flex items-center justify-center">
            <Columns3 size={20} className="text-[#00FFA7]" />
          </div>
          <div>
            <h1 className="text-2xl font-bold text-[#e6edf3]">Kanban</h1>
            <p className="text-sm text-[#667085]">{tickets.length} tickets ativos por status operacional</p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <div className="relative">
            <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-[#667085]" />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Buscar tickets..."
              className="w-56 bg-[#161b22] border border-[#21262d] rounded-lg pl-9 pr-3 py-2 text-sm text-[#e6edf3] placeholder-[#667085] focus:outline-none focus:border-[#00FFA7]/50 transition-colors"
            />
          </div>
          <button
            onClick={fetchTickets}
            className="flex items-center gap-2 px-3 py-2 text-xs border border-[#21262d] bg-[#161b22] text-[#667085] hover:text-[#00FFA7] hover:border-[#00FFA7]/30 rounded-lg transition-colors"
          >
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
            Atualizar
          </button>
          <button
            onClick={() => setShowCreateModal(true)}
            className="flex items-center gap-2 px-3 py-2 text-xs font-semibold bg-[#00FFA7] text-black rounded-lg hover:bg-[#00FFA7]/90 transition-colors"
          >
            <Plus size={14} />
            Novo ticket
          </button>
        </div>
      </div>

      {error && (
        <div className="mb-4 px-4 py-3 bg-red-500/10 border border-red-500/20 rounded-xl text-sm text-red-400">
          {error}
        </div>
      )}

      {/* Mobile/tablet (<xl): horizontal snap-scroll, one column at a time — avoids
          stacking 6 full-height sections vertically (~3000px of scroll). At xl+
          (1280px), enough width exists for all 6 columns side by side without
          scrolling, so we drop the scroll/snap behavior and let the grid share
          the viewport naturally instead of forcing a hardcoded min-width that
          exceeded common 1280-1439px desktop/laptop viewports and cropped them. */}
      <div className="flex xl:grid xl:grid-cols-6 gap-3 pb-4 overflow-x-auto xl:overflow-visible snap-x snap-mandatory xl:snap-none -mx-4 px-4 xl:mx-0 xl:px-0">
        {STATUSES.map((column) => {
          const items = grouped[column.id] || []
          return (
            <section
              key={column.id}
              className="bg-[#161b22] border border-[#21262d] rounded-xl min-h-[420px] xl:min-h-[520px] w-[85vw] sm:w-[320px] xl:w-auto shrink-0 xl:shrink flex flex-col snap-start xl:snap-align-none"
            >
              <header className="flex items-center justify-between px-3 py-3 border-b border-[#21262d]">
                <span className={`inline-flex items-center gap-1.5 px-2 py-1 rounded-full border text-xs font-medium ${column.tone}`}>
                  {column.icon}
                  {column.label}
                </span>
                <span className="text-xs text-[#667085]">{items.length}</span>
              </header>

              <div className="flex-1 p-2 space-y-2">
                {loading ? (
                  <div className="text-xs text-[#667085] px-2 py-6 text-center">Carregando...</div>
                ) : items.length === 0 ? (
                  <div className="text-xs text-[#667085] px-2 py-6 text-center border border-dashed border-[#21262d] rounded-lg">
                    Vazio
                  </div>
                ) : (
                  items.map((ticket) => (
                    <article
                      key={ticket.id}
                      className="group relative overflow-hidden bg-[#0C111D] border border-[#21262d] hover:border-[#344054] rounded-lg pl-3.5 pr-3 py-3 transition-colors"
                    >
                      <span
                        aria-hidden="true"
                        className={`absolute left-0 top-0 bottom-0 w-[3px] ${PRIORITY_RAIL[ticket.priority]}`}
                      />

                      <button
                        onClick={() => navigate(`/tickets/${ticket.id}`)}
                        className="w-full text-left"
                      >
                        <div className="flex items-start gap-2">
                          {ticket.is_thread ? (
                            ticket.assignee_agent ? <AgentIcon agent={ticket.assignee_agent} size={18} /> : <Ticket size={14} className="text-[#00FFA7] mt-0.5" />
                          ) : (
                            <Ticket size={14} className="text-[#667085] mt-0.5" />
                          )}
                          <div className="min-w-0 flex-1">
                            <div className="flex items-start gap-1.5">
                              <h2 className="font-display text-[13.5px] font-semibold text-[#e6edf3] leading-snug tracking-tight line-clamp-2">
                                {ticket.title}
                              </h2>
                              {ticket.locked_at && <Lock size={12} className="text-orange-400 shrink-0 mt-0.5" aria-label={`Locked by ${ticket.locked_by || 'agent'}`} />}
                            </div>
                            {ticket.description && (
                              <p className="mt-1 text-xs text-[#8b949e] leading-relaxed line-clamp-2">{ticket.description}</p>
                            )}
                          </div>
                        </div>
                      </button>

                      <div className="mt-3 flex items-center justify-between gap-2">
                        <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded border font-ticket-mono text-[10px] font-medium uppercase tracking-wide ${PRIORITY_TONE[ticket.priority]}`}>
                          {PRIORITY_ICON[ticket.priority]}
                          {ticket.priority}
                        </span>
                        <span className="font-ticket-mono text-[10px] text-[#667085]">{formatAge(ticket.updated_at)}</span>
                      </div>

                      <div className="mt-2.5 pt-2.5 border-t border-[#21262d]/60 flex items-center justify-between gap-2">
                        <span className="font-ticket-mono text-[10px] text-[#667085] truncate">
                          {ticket.assignee_agent ? `@${ticket.assignee_agent}` : 'sem agente'}
                        </span>
                        <select
                          value={ticket.status}
                          disabled={updating === ticket.id}
                          onChange={(e) => moveTicket(ticket, e.target.value as TicketStatus)}
                          className="max-w-[112px] bg-[#161b22] border border-[#21262d] rounded-md px-2 py-1 text-[11px] text-[#e6edf3] focus:outline-none focus:border-[#00FFA7]/50 disabled:opacity-60"
                          aria-label="Mover ticket"
                        >
                          {STATUSES.map((s) => (
                            <option key={s.id} value={s.id}>{s.label}</option>
                          ))}
                        </select>
                      </div>
                    </article>
                  ))
                )}
              </div>
            </section>
          )
        })}
      </div>

      {tickets.length === 0 && !loading && (
        <div className="mt-2 flex items-center justify-center gap-3 text-xs text-[#667085]">
          Nenhum ticket ativo ainda.
          <button
            onClick={() => setShowCreateModal(true)}
            className="flex items-center gap-1.5 text-[#00FFA7] hover:underline"
          >
            <Plus size={12} /> Criar o primeiro ticket
          </button>
        </div>
      )}

      {showCreateModal && (
        <CreateTicketModal
          onClose={() => setShowCreateModal(false)}
          onCreated={handleTicketCreated}
        />
      )}
    </div>
  )
}
