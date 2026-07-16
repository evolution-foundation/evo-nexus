import { useEffect, useRef, useState } from 'react'
import { ArrowDown, ArrowUp, Minus, Ticket as TicketIcon, X } from 'lucide-react'
import { api } from '../lib/api'
import { getAllAgentSlugs } from '../lib/agent-meta'

export type TicketPriority = 'urgent' | 'high' | 'medium' | 'low'

// Loose shape matching the backend's Ticket.to_dict() — callers (Kanban,
// AgentChat) cast to their own local, more specific ticket type as needed.
export interface CreatedTicket {
  id: string
  title: string
  description: string | null
  status: string
  priority: TicketPriority
  assignee_agent: string | null
  goal_id: number | null
  [key: string]: unknown
}

interface GoalOption {
  id: number
  title: string
}

const PRIORITY_OPTIONS: Array<{ value: TicketPriority; label: string; icon: React.ReactNode }> = [
  { value: 'urgent', label: 'Urgente', icon: <ArrowUp size={12} /> },
  { value: 'high', label: 'Alta', icon: <ArrowUp size={12} className="opacity-70" /> },
  { value: 'medium', label: 'Média', icon: <Minus size={12} /> },
  { value: 'low', label: 'Baixa', icon: <ArrowDown size={12} /> },
]

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="mb-3.5">
      <label className="block text-[11px] font-ticket-mono uppercase tracking-wider text-[#667085] mb-1.5">
        {label}
      </label>
      {children}
    </div>
  )
}

const inputClass =
  'w-full bg-[#0C111D] border border-[#21262d] rounded-lg px-3 py-2 text-sm text-[#e6edf3] placeholder-[#667085] focus:outline-none focus:border-[#00FFA7]/50 transition-colors'

interface CreateTicketModalProps {
  onClose: () => void
  onCreated: (ticket: CreatedTicket) => void
  defaultAssigneeAgent?: string
}

export default function CreateTicketModal({ onClose, onCreated, defaultAssigneeAgent }: CreateTicketModalProps) {
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [priority, setPriority] = useState<TicketPriority>('medium')
  const [assigneeAgent, setAssigneeAgent] = useState(defaultAssigneeAgent || '')
  const [goalId, setGoalId] = useState('')
  const [goals, setGoals] = useState<GoalOption[]>([])
  const [titleMissing, setTitleMissing] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')

  const titleRef = useRef<HTMLInputElement>(null)
  const agents = getAllAgentSlugs()

  useEffect(() => {
    titleRef.current?.focus()
  }, [])

  useEffect(() => {
    // OQ1 (resolved): goal picker lists active goals only, to keep it short.
    api.get('/goals?status=active').then(setGoals).catch(() => setGoals([]))
  }, [])

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [onClose])

  const handleSubmit = async () => {
    const trimmed = title.trim()
    if (!trimmed) {
      setTitleMissing(true)
      titleRef.current?.focus()
      return
    }
    setSubmitting(true)
    setError('')
    try {
      const ticket = await api.post('/tickets', {
        title: trimmed,
        description: description.trim() || null,
        priority,
        assignee_agent: assigneeAgent || null,
        goal_id: goalId ? Number(goalId) : null,
      })
      onCreated(ticket)
      onClose()
    } catch (err: any) {
      setError(err?.message || 'Falha ao criar ticket')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div
      className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4"
      onClick={onClose}
      role="presentation"
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="create-ticket-title"
        onClick={(e) => e.stopPropagation()}
        className="dialog-enter bg-[#161b22] border border-[#21262d] rounded-xl w-full max-w-lg shadow-2xl"
      >
        <div className="flex items-center justify-between px-6 py-4 border-b border-[#21262d]">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-lg bg-[#00FFA7]/10 border border-[#00FFA7]/25 flex items-center justify-center shrink-0">
              <TicketIcon size={15} className="text-[#00FFA7]" />
            </div>
            <h3 id="create-ticket-title" className="font-display text-[15px] font-semibold text-white tracking-tight">
              Novo ticket
            </h3>
          </div>
          <button onClick={onClose} className="text-[#667085] hover:text-white transition-colors">
            <X size={16} />
          </button>
        </div>

        <div className="px-6 py-5">
          {error && (
            <div className="mb-3.5 px-3 py-2 bg-red-500/10 border border-red-500/20 rounded-lg text-xs text-red-400">
              {error}
            </div>
          )}

          <Field label="Título">
            <input
              ref={titleRef}
              value={title}
              onChange={(e) => { setTitle(e.target.value); if (titleMissing) setTitleMissing(false) }}
              placeholder="O que precisa ser feito?"
              className={`${inputClass} ${titleMissing ? 'border-red-500/60 focus:border-red-500/60' : ''}`}
              aria-invalid={titleMissing}
            />
            {titleMissing && <p className="mt-1 text-[11px] text-red-400">Título é obrigatório.</p>}
          </Field>

          <Field label="Descrição (opcional)">
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Contexto adicional, critérios de aceite, links..."
              rows={3}
              className={`${inputClass} resize-none`}
            />
          </Field>

          <div className="grid grid-cols-2 gap-3">
            <Field label="Prioridade">
              <select
                value={priority}
                onChange={(e) => setPriority(e.target.value as TicketPriority)}
                className={inputClass}
              >
                {PRIORITY_OPTIONS.map((p) => (
                  <option key={p.value} value={p.value}>{p.label}</option>
                ))}
              </select>
            </Field>
            <Field label="Responsável">
              <select
                value={assigneeAgent}
                onChange={(e) => setAssigneeAgent(e.target.value)}
                className={inputClass}
              >
                <option value="">Sem responsável</option>
                {agents.map((a) => (
                  <option key={a.slug} value={a.slug}>{a.label} ({a.slug})</option>
                ))}
              </select>
            </Field>
          </div>

          <Field label="Vincular a uma meta (opcional)">
            <select
              value={goalId}
              onChange={(e) => setGoalId(e.target.value)}
              className={inputClass}
            >
              <option value="">Nenhuma</option>
              {goals.map((g) => (
                <option key={g.id} value={g.id}>{g.title}</option>
              ))}
            </select>
          </Field>

          <div className="flex justify-end gap-2 mt-2">
            <button onClick={onClose} className="px-4 py-2 text-sm text-[#667085] hover:text-white transition-colors">
              Cancelar
            </button>
            <button
              onClick={handleSubmit}
              disabled={submitting}
              className="px-4 py-2 text-sm bg-[#00FFA7] text-black font-semibold rounded-lg hover:bg-[#00FFA7]/90 transition-colors disabled:opacity-50"
            >
              {submitting ? 'Criando...' : 'Criar ticket'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
