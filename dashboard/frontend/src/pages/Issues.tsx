import { useEffect, useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Ticket, Plus, RefreshCw, Filter, Search, Download, CheckSquare, Square,
  X, AlertTriangle, ArrowUp, Minus, ArrowDown,
  Lock, Clock, CheckCircle, XCircle, Eye,
} from 'lucide-react'
import { api } from '../lib/api'
import { useTranslation } from 'react-i18next'

// ── Types ─────────────────────────────────────────────────────────────────────

type TicketStatus = 'open' | 'in_progress' | 'blocked' | 'review' | 'resolved' | 'closed'
type TicketPriority = 'urgent' | 'high' | 'medium' | 'low'

interface TicketItem {
  id: string
  title: string
  description: string | null
  status: TicketStatus
  priority: TicketPriority
  priority_rank: number
  assignee_agent: string | null
  project_id: number | null
  goal_id: number | null
  locked_at: string | null
  locked_by: string | null
  created_by: string
  created_at: string
  updated_at: string
  resolved_at: string | null
}

// ── Style maps ────────────────────────────────────────────────────────────────

const STATUS_STYLES: Record<TicketStatus, string> = {
  open: 'bg-blue-500/10 text-blue-400 border-blue-500/20',
  in_progress: 'bg-[#00FFA7]/10 text-[#00FFA7] border-[#00FFA7]/20',
  blocked: 'bg-red-500/10 text-red-400 border-red-500/20',
  review: 'bg-purple-500/10 text-purple-400 border-purple-500/20',
  resolved: 'bg-gray-500/10 text-gray-400 border-gray-500/20',
  closed: 'bg-[#21262d] text-[#667085] border-[#21262d]',
}

const STATUS_ICON: Record<TicketStatus, React.ReactNode> = {
  open: <Clock size={10} />,
  in_progress: <RefreshCw size={10} className="animate-spin" />,
  blocked: <AlertTriangle size={10} />,
  review: <Eye size={10} />,
  resolved: <CheckCircle size={10} />,
  closed: <XCircle size={10} />,
}

const PRIORITY_STYLES: Record<TicketPriority, string> = {
  urgent: 'bg-red-500/10 text-red-400 border-red-500/20',
  high: 'bg-orange-500/10 text-orange-400 border-orange-500/20',
  medium: 'bg-yellow-500/10 text-yellow-400 border-yellow-500/20',
  low: 'bg-[#21262d] text-[#667085] border-[#344054]',
}

const PRIORITY_ICON: Record<TicketPriority, React.ReactNode> = {
  urgent: <ArrowUp size={10} />,
  high: <ArrowUp size={10} className="opacity-60" />,
  medium: <Minus size={10} />,
  low: <ArrowDown size={10} />,
}

const ALL_STATUSES: TicketStatus[] = ['open', 'in_progress', 'blocked', 'review', 'resolved', 'closed']
const ALL_PRIORITIES: TicketPriority[] = ['urgent', 'high', 'medium', 'low']

function formatDate(iso: string | null): string {
  if (!iso) return '--'
  const d = new Date(iso)
  return d.toLocaleString('pt-BR', {
    timeZone: 'America/Sao_Paulo',
    day: '2-digit', month: '2-digit', year: '2-digit',
    hour: '2-digit', minute: '2-digit',
  })
}

// ── StatusBadge ───────────────────────────────────────────────────────────────

function StatusBadge({ status }: { status: TicketStatus }) {
  return (
    <span className={`inline-flex items-center gap-1 text-[11px] font-medium px-2 py-0.5 rounded-full border ${STATUS_STYLES[status]}`}>
      {STATUS_ICON[status]}
      {status.replace('_', ' ')}
    </span>
  )
}

// ── PriorityBadge ─────────────────────────────────────────────────────────────

function PriorityBadge({ priority }: { priority: TicketPriority }) {
  return (
    <span className={`inline-flex items-center gap-1 text-[11px] font-medium px-2 py-0.5 rounded-full border ${PRIORITY_STYLES[priority]}`}>
      {PRIORITY_ICON[priority]}
      {priority}
    </span>
  )
}

// ── CreateModal ───────────────────────────────────────────────────────────────

interface CreateModalProps {
  onClose: () => void
  onCreated: (ticket: TicketItem) => void
}

function CreateModal({ onClose, onCreated }: CreateModalProps) {
  const [form, setForm] = useState({
    title: '',
    description: '',
    priority: 'medium' as TicketPriority,
    assignee_agent: '',
  })
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [agents, setAgents] = useState<string[]>([])
  const [agentSearch, setAgentSearch] = useState('')
  const [agentOpen, setAgentOpen] = useState(false)

  useEffect(() => {
    api.get('/agents').then((data: any) => {
      const list = Array.isArray(data) ? data : (data?.agents || [])
      const slugs = list.map((a: any) => typeof a === 'string' ? a : (a.slug || a.name)).filter(Boolean)
      setAgents(slugs.sort())
    }).catch(() => setAgents([]))
  }, [])

  const filteredAgents = agentSearch
    ? agents.filter(a => a.toLowerCase().includes(agentSearch.toLowerCase()))
    : agents

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!form.title.trim()) return
    setSaving(true)
    setError(null)
    try {
      const res = await api.post('/tickets', {
        title: form.title.trim(),
        description: form.description.trim() || undefined,
        priority: form.priority,
        assignee_agent: form.assignee_agent.trim() || undefined,
      })
      onCreated(res as TicketItem)
    } catch (err: any) {
      setError(err?.message ||'Failed to create ticket')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4" onClick={onClose}>
      <div className="bg-[#161b22] border border-[#21262d] rounded-xl w-full max-w-md shadow-2xl" onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between px-6 py-4 border-b border-[#21262d]">
          <h2 className="text-sm font-semibold text-white flex items-center gap-2">
            <Ticket size={16} className="text-[#00FFA7]" /> New Ticket
          </h2>
          <button onClick={onClose} className="text-[#667085] hover:text-white transition-colors">
            <X size={16} />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="px-6 py-4 space-y-3">
          <div>
            <label className="block text-xs text-[#667085] mb-1">Title *</label>
            <input
              className="w-full bg-[#0C111D] border border-[#21262d] rounded-lg px-3 py-2 text-sm text-[#e6edf3] placeholder-[#667085] focus:outline-none focus:border-[#00FFA7]/50 transition-colors"
              placeholder="Describe the issue or topic..."
              value={form.title}
              onChange={e => setForm(f => ({ ...f, title: e.target.value }))}
              autoFocus
              required
            />
          </div>

          <div>
            <label className="block text-xs text-[#667085] mb-1">Description</label>
            <textarea
              className="w-full bg-[#0C111D] border border-[#21262d] rounded-lg px-3 py-2 text-sm text-[#e6edf3] placeholder-[#667085] focus:outline-none focus:border-[#00FFA7]/50 resize-none transition-colors"
              placeholder="Optional details..."
              rows={3}
              value={form.description}
              onChange={e => setForm(f => ({ ...f, description: e.target.value }))}
            />
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs text-[#667085] mb-1">Priority</label>
              <select
                className="w-full bg-[#0C111D] border border-[#21262d] rounded-lg px-3 py-2 text-sm text-[#e6edf3] focus:outline-none focus:border-[#00FFA7]/50 transition-colors"
                value={form.priority}
                onChange={e => setForm(f => ({ ...f, priority: e.target.value as TicketPriority }))}
              >
                {ALL_PRIORITIES.map(p => <option key={p} value={p}>{p}</option>)}
              </select>
            </div>

            <div className="relative">
              <label className="block text-xs text-[#667085] mb-1">Assign to agent</label>
              <input
                className="w-full bg-[#0C111D] border border-[#21262d] rounded-lg px-3 py-2 text-sm text-[#e6edf3] placeholder-[#667085] focus:outline-none focus:border-[#00FFA7]/50 transition-colors"
                placeholder="Search agent..."
                value={form.assignee_agent || agentSearch}
                onChange={e => {
                  setAgentSearch(e.target.value)
                  setForm(f => ({ ...f, assignee_agent: '' }))
                  setAgentOpen(true)
                }}
                onFocus={() => setAgentOpen(true)}
                onBlur={() => setTimeout(() => setAgentOpen(false), 150)}
              />
              {agentOpen && filteredAgents.length > 0 && (
                <div className="absolute z-10 mt-1 w-full max-h-48 overflow-y-auto bg-[#161b22] border border-[#21262d] rounded-lg shadow-xl">
                  {filteredAgents.slice(0, 20).map(slug => (
                    <button
                      type="button"
                      key={slug}
                      onMouseDown={e => e.preventDefault()}
                      onClick={() => {
                        setForm(f => ({ ...f, assignee_agent: slug }))
                        setAgentSearch('')
                        setAgentOpen(false)
                      }}
                      className="w-full text-left px-3 py-1.5 text-sm font-mono text-[#e6edf3] hover:bg-white/5 transition-colors"
                    >
                      @{slug}
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>

          {error && (
            <p className="text-xs text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-3 py-2">{error}</p>
          )}

          <div className="flex items-center justify-end gap-2 pt-1">
            <button type="button" onClick={onClose} className="px-4 py-2 text-sm text-[#667085] hover:text-white rounded-lg hover:bg-white/5 transition-colors">
              Cancel
            </button>
            <button
              type="submit"
              disabled={saving || !form.title.trim()}
              className="px-4 py-2 text-sm font-semibold bg-[#00FFA7] text-black rounded-lg hover:bg-[#00FFA7]/90 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              {saving ? 'Creating...' : 'Create'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function Issues() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [tickets, setTickets] = useState<TicketItem[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Filters
  const [q, setQ] = useState('')
  const [selectedStatuses, setSelectedStatuses] = useState<TicketStatus[]>([])
  const [selectedPriorities, setSelectedPriorities] = useState<TicketPriority[]>([])
  const [selectedAssignee, setSelectedAssignee] = useState('')
  const [showFilters, setShowFilters] = useState(false)

  // Bulk selection
  const [selected, setSelected] = useState<Set<string>>(new Set())

  // Modals
  const [showCreate, setShowCreate] = useState(false)

  // Pagination
  const [offset, setOffset] = useState(0)
  const PAGE_SIZE = 50

  const fetchTickets = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const p = new URLSearchParams()
      p.set('limit', String(PAGE_SIZE))
      p.set('offset', String(offset))
      if (q) p.set('q', q)
      selectedStatuses.forEach(s => p.append('status', s))
      selectedPriorities.forEach(pr => p.append('priority', pr))
      if (selectedAssignee) p.set('assignee_agent', selectedAssignee)

      const res = await api.get(`/tickets?${p.toString()}`)
      setTickets(res.tickets)
      setTotal(res.total)
    } catch (err: any) {
      setError(err?.message || 'Failed to load tickets')
    } finally {
      setLoading(false)
    }
  }, [q, selectedStatuses, selectedPriorities, selectedAssignee, offset])

  useEffect(() => {
    fetchTickets()
  }, [fetchTickets])

  const toggleStatus = (s: TicketStatus) => {
    setSelectedStatuses(prev => prev.includes(s) ? prev.filter(x => x !== s) : [...prev, s])
    setOffset(0)
  }

  const togglePriority = (p: TicketPriority) => {
    setSelectedPriorities(prev => prev.includes(p) ? prev.filter(x => x !== p) : [...prev, p])
    setOffset(0)
  }

  const toggleSelect = (id: string) => {
    setSelected(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const toggleSelectAll = () => {
    if (selected.size === tickets.length) {
      setSelected(new Set())
    } else {
      setSelected(new Set(tickets.map(t => t.id)))
    }
  }

  const handleBulkClose = async () => {
    if (!selected.size) return
    try {
      await api.post('/tickets/bulk', { ids: Array.from(selected), action: 'close' })
      setSelected(new Set())
      fetchTickets()
    } catch (err: any) {
      alert(err?.message ||'Bulk close failed')
    }
  }

  const handleBulkDelete = async () => {
    if (!selected.size) return
    if (!confirm(`Delete ${selected.size} ticket(s)? This cannot be undone.`)) return
    try {
      await api.post('/tickets/bulk', { ids: Array.from(selected), action: 'delete' })
      setSelected(new Set())
      fetchTickets()
    } catch (err: any) {
      alert(err?.message ||'Bulk delete failed')
    }
  }

  const handleExport = () => {
    const params = new URLSearchParams()
    if (q) params.set('q', q)
    selectedStatuses.forEach(s => params.append('status', s))
    selectedPriorities.forEach(p => params.append('priority', p))
    if (selectedAssignee) params.set('assignee_agent', selectedAssignee)
    const base = import.meta.env.DEV ? 'http://localhost:8080' : ''
    window.open(`${base}/api/tickets/export.csv?${params.toString()}`, '_blank')
  }

  const clearFilters = () => {
    setSelectedStatuses([])
    setSelectedPriorities([])
    setSelectedAssignee('')
    setQ('')
    setOffset(0)
  }

  const hasFilters = q || selectedStatuses.length || selectedPriorities.length || selectedAssignee

  return (
    <div className="min-h-screen bg-[#0C111D]">
      {/* Header */}
      <div className="flex items-center justify-between mb-8">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl bg-[#161b22] border border-[#21262d] flex items-center justify-center">
            <Ticket size={20} className="text-[#00FFA7]" />
          </div>
          <div>
            <h1 className="text-2xl font-bold text-[#e6edf3]">{t('issues.title')}</h1>
            <p className="text-sm text-[#667085]">{total} ticket{total !== 1 ? 's' : ''} · work queue</p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={handleExport}
            className="flex items-center gap-1.5 px-3 py-2 text-xs text-[#667085] hover:text-white border border-[#21262d] bg-[#161b22] rounded-lg hover:border-[#344054] transition-colors"
          >
            <Download size={13} /> Export CSV
          </button>
          <button
            onClick={() => setShowFilters(f => !f)}
            className={`flex items-center gap-1.5 px-3 py-2 text-xs border rounded-lg transition-colors ${
              hasFilters
                ? 'text-[#00FFA7] border-[#00FFA7]/30 bg-[#00FFA7]/5'
                : 'text-[#667085] hover:text-white border-[#21262d] bg-[#161b22] hover:border-[#344054]'
            }`}
          >
            <Filter size={13} />
            Filters
            {hasFilters && <span className="bg-[#00FFA7]/20 text-[#00FFA7] text-[10px] px-1.5 rounded-full">
              {[selectedStatuses.length, selectedPriorities.length, selectedAssignee ? 1 : 0, q ? 1 : 0].reduce((a, b) => a + b, 0)}
            </span>}
          </button>
          <button
            onClick={() => setShowCreate(true)}
            className="flex items-center gap-1.5 px-3 py-2 text-xs font-medium bg-[#00FFA7] text-black rounded-lg hover:bg-[#00FFA7]/90 transition-colors"
          >
            <Plus size={13} /> New Ticket
          </button>
          <button
            onClick={fetchTickets}
            className="flex items-center gap-2 px-3 py-2 text-xs border border-[#21262d] bg-[#161b22] text-[#667085] hover:text-[#00FFA7] hover:border-[#00FFA7]/30 rounded-lg transition-colors"
            title="Refresh"
          >
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
            Refresh
          </button>
        </div>
      </div>

      {/* Search bar */}
      <div className="mb-4">
        <div className="relative">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-[#667085]" />
          <input
            className="w-full bg-[#161b22] border border-[#21262d] rounded-lg pl-9 pr-4 py-2 text-sm text-[#e6edf3] placeholder-[#667085] focus:outline-none focus:border-[#00FFA7]/50 transition-colors"
            placeholder="Search tickets by title, description or comments..."
            value={q}
            onChange={e => { setQ(e.target.value); setOffset(0) }}
          />
          {q && (
            <button onClick={() => { setQ(''); setOffset(0) }} className="absolute right-3 top-1/2 -translate-y-1/2 text-[#667085] hover:text-white">
              <X size={14} />
            </button>
          )}
        </div>
      </div>

      {/* Filter panel */}
      {showFilters && (
        <div className="mb-4 p-4 bg-[#161b22] border border-[#21262d] rounded-xl space-y-3">
          <div>
            <p className="text-xs font-medium text-[#e6edf3] mb-2">Status</p>
            <div className="flex flex-wrap gap-2">
              {ALL_STATUSES.map(s => (
                <button
                  key={s}
                  onClick={() => toggleStatus(s)}
                  className={`inline-flex items-center gap-1 text-[11px] font-medium px-2 py-1 rounded-full border transition-colors ${
                    selectedStatuses.includes(s) ? STATUS_STYLES[s] : 'text-[#667085] border-[#21262d] hover:border-[#344054]'
                  }`}
                >
                  {STATUS_ICON[s]}
                  {s.replace('_', ' ')}
                </button>
              ))}
            </div>
          </div>

          <div>
            <p className="text-xs font-medium text-[#e6edf3] mb-2">Priority</p>
            <div className="flex flex-wrap gap-2">
              {ALL_PRIORITIES.map(p => (
                <button
                  key={p}
                  onClick={() => togglePriority(p)}
                  className={`inline-flex items-center gap-1 text-[11px] font-medium px-2 py-1 rounded-full border transition-colors ${
                    selectedPriorities.includes(p) ? PRIORITY_STYLES[p] : 'text-[#667085] border-[#21262d] hover:border-[#344054]'
                  }`}
                >
                  {PRIORITY_ICON[p]}
                  {p}
                </button>
              ))}
            </div>
          </div>

          <div>
            <p className="text-xs font-medium text-[#e6edf3] mb-2">Assignee</p>
            <input
              className="bg-[#0C111D] border border-[#21262d] rounded-lg px-3 py-1.5 text-sm text-[#e6edf3] placeholder-[#667085] focus:outline-none focus:border-[#00FFA7]/50 transition-colors"
              placeholder="Agent slug (e.g. zara-cs)"
              value={selectedAssignee}
              onChange={e => { setSelectedAssignee(e.target.value); setOffset(0) }}
            />
          </div>

          {hasFilters && (
            <button onClick={clearFilters} className="text-xs text-[#667085] hover:text-red-400 transition-colors">
              Clear all filters
            </button>
          )}
        </div>
      )}

      {/* Bulk actions bar */}
      {selected.size > 0 && (
        <div className="mb-4 flex items-center gap-3 px-4 py-2.5 bg-[#00FFA7]/5 border border-[#00FFA7]/20 rounded-xl">
          <span className="text-xs text-[#00FFA7] font-medium">{selected.size} selected</span>
          <button
            onClick={handleBulkClose}
            className="text-xs text-[#e6edf3] bg-[#161b22] border border-[#21262d] hover:border-[#344054] px-3 py-1.5 rounded-lg transition-colors"
          >
            Close selected
          </button>
          <button
            onClick={handleBulkDelete}
            className="text-xs text-red-400 bg-red-500/5 border border-red-500/20 hover:bg-red-500/10 px-3 py-1.5 rounded-lg transition-colors"
          >
            Delete
          </button>
          <button
            onClick={() => setSelected(new Set())}
            className="ml-auto text-xs text-[#667085] hover:text-white"
          >
            <X size={14} />
          </button>
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="mb-4 px-4 py-3 bg-red-500/10 border border-red-500/20 rounded-xl text-sm text-red-400">
          {error}
        </div>
      )}

      {/* Table */}
      <div className="bg-[#161b22] border border-[#21262d] rounded-xl overflow-hidden">
        {/* Table header */}
        <div className="grid grid-cols-[auto_1fr_auto_auto_auto_auto] gap-3 px-4 py-2.5 border-b border-[#21262d] text-[11px] font-medium uppercase tracking-wider text-[#667085]">
          <button onClick={toggleSelectAll} className="flex items-center">
            {selected.size === tickets.length && tickets.length > 0
              ? <CheckSquare size={14} className="text-[#00FFA7]" />
              : <Square size={14} />}
          </button>
          <span>Title</span>
          <span>Status</span>
          <span>Priority</span>
          <span>Assignee</span>
          <span>Updated</span>
        </div>

        {loading ? (
          <div className="flex items-center justify-center py-16 text-[#667085] text-sm">
            <RefreshCw size={16} className="animate-spin mr-2" /> Loading...
          </div>
        ) : tickets.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-16 text-[#667085]">
            <Ticket size={32} className="mb-3 opacity-30" />
            <p className="text-sm font-medium">No tickets found</p>
            {hasFilters
              ? <p className="text-xs mt-1 text-[#667085]">Try clearing the filters</p>
              : <button onClick={() => setShowCreate(true)} className="mt-3 text-xs text-[#00FFA7] hover:underline">Create your first ticket</button>
            }
          </div>
        ) : (
          tickets.map((ticket) => (
            <div
              key={ticket.id}
              className={`grid grid-cols-[auto_1fr_auto_auto_auto_auto] gap-3 px-4 py-3 items-center border-b border-[#21262d]/50 last:border-0 hover:bg-white/5 transition-colors cursor-pointer ${
                selected.has(ticket.id) ? 'bg-[#00FFA7]/[0.03]' : ''
              }`}
              onClick={() => navigate(`/tickets/${ticket.id}`)}
            >
              <button
                onClick={e => { e.stopPropagation(); toggleSelect(ticket.id) }}
                className="text-[#667085] hover:text-[#00FFA7]"
              >
                {selected.has(ticket.id)
                  ? <CheckSquare size={14} className="text-[#00FFA7]" />
                  : <Square size={14} />}
              </button>

              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium text-[#e6edf3] truncate">{ticket.title}</span>
                  {ticket.locked_at && (
                    <span title={`Locked by ${ticket.locked_by}`}>
                      <Lock size={12} className="text-orange-400 shrink-0" />
                    </span>
                  )}
                </div>
                {ticket.description && (
                  <p className="text-xs text-[#667085] truncate mt-0.5">{ticket.description}</p>
                )}
              </div>

              <StatusBadge status={ticket.status} />
              <PriorityBadge priority={ticket.priority} />

              <span className="text-xs text-[#667085] max-w-[100px] truncate font-mono">
                {ticket.assignee_agent ? `@${ticket.assignee_agent}` : '—'}
              </span>

              <span className="text-xs text-[#667085] whitespace-nowrap">
                {formatDate(ticket.updated_at)}
              </span>
            </div>
          ))
        )}
      </div>

      {/* Pagination */}
      {total > PAGE_SIZE && (
        <div className="flex items-center justify-between mt-4 text-xs text-[#667085]">
          <span>Showing {offset + 1}–{Math.min(offset + PAGE_SIZE, total)} of {total}</span>
          <div className="flex items-center gap-2">
            <button
              disabled={offset === 0}
              onClick={() => setOffset(o => Math.max(0, o - PAGE_SIZE))}
              className="px-3 py-1.5 border border-[#21262d] bg-[#161b22] rounded-lg hover:border-[#344054] disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              Previous
            </button>
            <button
              disabled={offset + PAGE_SIZE >= total}
              onClick={() => setOffset(o => o + PAGE_SIZE)}
              className="px-3 py-1.5 border border-[#21262d] bg-[#161b22] rounded-lg hover:border-[#344054] disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              Next
            </button>
          </div>
        </div>
      )}

      {/* Create modal */}
      {showCreate && (
        <CreateModal
          onClose={() => setShowCreate(false)}
          onCreated={() => {
            setShowCreate(false)
            fetchTickets()
          }}
        />
      )}
    </div>
  )
}
