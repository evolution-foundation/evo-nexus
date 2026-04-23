import { useEffect, useState, useCallback } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import {
  Ticket, ArrowLeft, Lock, Unlock, MessageSquare, Activity,
  RefreshCw, Send, Trash2, RotateCcw, Pencil, Archive,
} from 'lucide-react'
import { api } from '../lib/api'
import AgentChat from '../components/AgentChat'
import { TS_HTTP } from '../lib/terminal-url'

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
  project_id: number | null
  goal_id: number | null
  locked_at: string | null
  locked_by: string | null
  created_by: string
  source_agent: string | null
  source_session_id: string | null
  // thread-areas fields
  workspace_path: string | null
  memory_md_path: string | null
  thread_session_id: string | null
  message_count: number
  last_summary_at_message: number
  is_thread: boolean
  created_at: string
  updated_at: string
  resolved_at: string | null
  comments: CommentItem[]
  activity: ActivityItem[]
}

interface CommentItem {
  id: string
  ticket_id: string
  author: string
  body: string
  mentions: string[]
  created_at: string
}

interface ActivityItem {
  id: string
  ticket_id: string
  actor: string
  action: string
  payload: Record<string, any>
  created_at: string
}

type TimelineItem = (CommentItem | ActivityItem) & { _type: 'comment' | 'activity' }

const STATUS_STYLES: Record<TicketStatus, string> = {
  open: 'bg-blue-500/10 text-blue-400 border-blue-500/20',
  in_progress: 'bg-[#00FFA7]/10 text-[#00FFA7] border-[#00FFA7]/20',
  blocked: 'bg-red-500/10 text-red-400 border-red-500/20',
  review: 'bg-purple-500/10 text-purple-400 border-purple-500/20',
  resolved: 'bg-gray-500/10 text-gray-400 border-gray-500/20',
  closed: 'bg-[#21262d] text-[#667085] border-[#21262d]',
  archived: 'bg-orange-500/10 text-orange-400 border-orange-500/20',
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

function actionLabel(action: string, payload: Record<string, any>): string {
  switch (action) {
    case 'created': {
      const parts: string[] = ['created this ticket']
      if (payload?.source_agent || payload?.source_session_id) {
        const via: string[] = []
        if (payload.source_agent) via.push(`@${payload.source_agent}`)
        if (payload.source_session_id) via.push(`session #${String(payload.source_session_id).slice(0, 8)}`)
        parts.push(`via ${via.join(' ')}`)
      }
      return parts.join(' ')
    }
    case 'status_changed': return `changed status from ${payload?.from} to ${payload?.to}`
    case 'checkout': return `checked out (timeout: ${payload?.lock_timeout_seconds}s)`
    case 'release': return 'released lock'
    case 'auto_release': return `lock auto-released (was held by ${payload?.previously_locked_by})`
    case 'assigned': return `assigned to ${payload?.assignee_agent || 'unassigned'}`
    case 'commented': return 'added a comment'
    case 'linked_session': return `linked session ${payload?.session_id}`
    default: return action.replace('_', ' ')
  }
}

export default function TicketDetail() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [ticket, setTicket] = useState<TicketItem | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [commentBody, setCommentBody] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [editStatus, setEditStatus] = useState(false)
  const [editPriority, setEditPriority] = useState(false)
  // Thread-mode state
  const [threadSessionId, setThreadSessionId] = useState<string | null>(null)
  const [sessionLoading, setSessionLoading] = useState(false)
  const [showConvertModal, setShowConvertModal] = useState(false)

  const fetchTicket = async () => {
    if (!id) return
    setLoading(true)
    setError(null)
    try {
      const res = await api.get(`/tickets/${id}`)
      setTicket(res)
    } catch (err: any) {
      setError(err?.message ||'Failed to load ticket')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { fetchTicket() }, [id])

  const buildTimeline = (): TimelineItem[] => {
    if (!ticket) return []
    const items: TimelineItem[] = [
      ...ticket.comments.map(c => ({ ...c, _type: 'comment' as const })),
      ...ticket.activity.map(a => ({ ...a, _type: 'activity' as const })),
    ]
    return items.sort((a, b) => a.created_at.localeCompare(b.created_at))
  }

  const handleAddComment = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!commentBody.trim() || !id) return
    setSubmitting(true)
    try {
      await api.post(`/tickets/${id}/comments`, { body: commentBody.trim() })
      setCommentBody('')
      fetchTicket()
    } catch (err: any) {
      alert(err?.message ||'Failed to add comment')
    } finally {
      setSubmitting(false)
    }
  }

  const handleStatusChange = async (newStatus: TicketStatus) => {
    if (!id) return
    try {
      await api.patch(`/tickets/${id}`, { status: newStatus })
      setEditStatus(false)
      fetchTicket()
    } catch (err: any) {
      alert(err?.message ||'Failed to update status')
    }
  }

  const handlePriorityChange = async (newPriority: TicketPriority) => {
    if (!id) return
    try {
      await api.patch(`/tickets/${id}`, { priority: newPriority })
      setEditPriority(false)
      fetchTicket()
    } catch (err: any) {
      alert(err?.message ||'Failed to update priority')
    }
  }

  const handleDelete = async () => {
    if (!id || !ticket) return
    if (!confirm(`Delete "${ticket.title}"? This cannot be undone.`)) return
    try {
      await api.delete(`/tickets/${id}`)
      navigate('/issues')
    } catch (err: any) {
      alert(err?.message || 'Failed to delete ticket')
    }
  }

  const handleReopen = () => handleStatusChange('open')

  const handleEditTitle = async () => {
    if (!id || !ticket) return
    const newTitle = prompt('Edit title:', ticket.title)
    if (!newTitle || newTitle === ticket.title) return
    try {
      await api.patch(`/tickets/${id}`, { title: newTitle })
      fetchTicket()
    } catch (err: any) {
      alert(err?.message || 'Failed to update title')
    }
  }

  // Init thread session (lazy — called when thread ticket is opened)
  const initThreadSession = useCallback(async (t: TicketItem) => {
    if (!t.is_thread || !t.assignee_agent) return
    setSessionLoading(true)
    try {
      // Fetch memory.md content to inject as systemPromptExtras
      let memoryContent = ''
      try {
        const memRes = await api.get(`/tickets/${t.id}/memory`)
        memoryContent = memRes.content || ''
      } catch {
        // memory fetch failure is non-fatal
      }

      const systemPromptExtras = memoryContent
        ? `## Thread Memory\n\nThis is a persistent thread. Below is the accumulated memory from previous sessions:\n\n${memoryContent}`
        : ''

      // Get or create session scoped to this ticket
      const sessionRes = await fetch(`${TS_HTTP}/api/sessions/for-agent`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          agentName: t.assignee_agent,
          workingDir: t.workspace_path || undefined,
          ticketId: t.id,
          systemPromptExtras: systemPromptExtras || undefined,
        }),
      })
      if (!sessionRes.ok) throw new Error(`Session init failed: ${sessionRes.status}`)
      const sessionData = await sessionRes.json()
      const nodeSessionId = sessionData.sessionId

      // If DB thread_session_id differs, update it
      if (t.thread_session_id !== nodeSessionId) {
        try {
          await api.patch(`/tickets/${t.id}`, { thread_session_id: nodeSessionId } as any)
        } catch {
          // non-fatal — session still works
        }
      }

      setThreadSessionId(nodeSessionId)
    } catch (err: any) {
      console.error('[TicketDetail] thread session init failed:', err)
    } finally {
      setSessionLoading(false)
    }
  }, [])

  // Auto-init thread session when ticket loads as thread
  useEffect(() => {
    if (ticket?.is_thread && !threadSessionId && !sessionLoading) {
      initThreadSession(ticket)
    }
  }, [ticket?.id, ticket?.is_thread])

  // Option D: fire turn-completed on each chat_complete in thread mode
  const handleTurnCompleted = useCallback(async () => {
    if (!id) return
    try {
      await api.post(`/tickets/${id}/turn-completed`, {})
    } catch {
      // non-fatal — summary safety net (heartbeat) handles misses
    }
  }, [id])

  const handleArchiveThread = async () => {
    if (!id || !ticket) return
    if (!confirm(`Archive thread "${ticket.title}"? It will be read-only. You can unarchive later.`)) return
    try {
      await api.post(`/tickets/${id}/archive-thread`, {})
      navigate('/issues')
    } catch (err: any) {
      alert(err?.message || 'Failed to archive thread')
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center py-24 text-[#667085]">
        <RefreshCw size={18} className="animate-spin mr-2" /> Loading...
      </div>
    )
  }

  if (error || !ticket) {
    return (
      <div className="flex flex-col items-center justify-center py-24 text-[#667085]">
        <p className="text-sm text-red-400">{error || 'Ticket not found'}</p>
        <button onClick={() => navigate('/issues')} className="mt-3 text-xs text-[#00FFA7] hover:underline">
          Back to Issues
        </button>
      </div>
    )
  }

  const timeline = buildTimeline()

  // --- Thread mode: render AgentChat instead of issue detail ---
  if (ticket.is_thread) {
    return (
      <div className="flex flex-col h-full">
        {/* Thread header bar */}
        <div className="flex items-center gap-3 px-4 py-2 border-b border-[#21262d] bg-[#0C111D] shrink-0">
          <button
            onClick={() => navigate('/issues')}
            className="flex items-center gap-1.5 text-xs text-[#667085] hover:text-white transition-colors"
          >
            <ArrowLeft size={13} /> Issues
          </button>
          <span className="text-[#667085]">/</span>
          <span className="text-xs font-medium text-[#e6edf3] truncate max-w-xs">{ticket.title}</span>
          <span className="ml-auto flex items-center gap-2">
            {ticket.status === 'archived' && (
              <span className="text-[10px] text-orange-400 border border-orange-400/30 bg-orange-400/10 px-2 py-0.5 rounded-full">archived</span>
            )}
            {ticket.status !== 'archived' && (
              <button
                onClick={handleArchiveThread}
                className="flex items-center gap-1 px-2 py-1 text-[11px] text-[#667085] hover:text-orange-400 border border-[#21262d] hover:border-orange-400/30 rounded-lg transition-colors"
                title="Archive thread"
              >
                <Archive size={11} /> Archive
              </button>
            )}
          </span>
        </div>
        {/* AgentChat fills remaining space */}
        {sessionLoading ? (
          <div className="flex items-center justify-center flex-1 text-[#667085]">
            <RefreshCw size={16} className="animate-spin mr-2" /> Initialising session...
          </div>
        ) : threadSessionId ? (
          <div className="flex-1 overflow-hidden flex flex-col">
            {ticket.status === 'archived' && (
              <div className="flex items-center gap-2 px-4 py-2 bg-[#161b22] border-b border-[#21262d] shrink-0">
                <span className="text-sm">📦</span>
                <span className="text-xs text-[#667085] flex-1">Thread arquivada — read-only.</span>
                <button
                  onClick={async () => {
                    try {
                      await api.post(`/tickets/${ticket.id}/unarchive-thread`, {})
                      setTicket(t => t ? { ...t, status: 'open' } : t)
                    } catch (err: any) {
                      alert(err?.message || 'Falha ao reativar thread')
                    }
                  }}
                  className="text-xs text-[#00FFA7] hover:underline shrink-0"
                >
                  Unarchive
                </button>
              </div>
            )}
            <div className={`flex-1 overflow-hidden${ticket.status === 'archived' ? ' pointer-events-none opacity-60' : ''}`}>
              <AgentChat
                agent={ticket.assignee_agent || ''}
                sessionId={threadSessionId}
                workingDir={ticket.workspace_path || undefined}
                threadTicketId={ticket.id}
                onTurnCompleted={ticket.status !== 'archived' ? handleTurnCompleted : undefined}
                externalLoading={false}
              />
            </div>
          </div>
        ) : (
          <div className="flex items-center justify-center flex-1 text-[#667085] flex-col gap-2">
            <p className="text-sm">Session could not be initialised.</p>
            <button onClick={() => ticket && initThreadSession(ticket)} className="text-xs text-[#00FFA7] hover:underline">
              Retry
            </button>
          </div>
        )}
      </div>
    )
  }
  // --- End thread mode ---

  return (
    <div className="max-w-3xl mx-auto">
      {/* Back */}
      <button
        onClick={() => navigate('/issues')}
        className="flex items-center gap-1.5 text-xs text-[#667085] hover:text-white mb-6 transition-colors"
      >
        <ArrowLeft size={13} /> Back to Issues
      </button>

      {/* Ticket header */}
      <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-5 mb-4">
        <div className="flex items-start gap-3 mb-4">
          <div className="w-10 h-10 rounded-xl bg-[#161b22] border border-[#21262d] flex items-center justify-center shrink-0 mt-0.5">
            <Ticket size={18} className="text-[#00FFA7]" />
          </div>
          <div className="flex-1 min-w-0">
            <h1 className="text-xl font-bold text-[#e6edf3] mb-1">{ticket.title}</h1>
            {ticket.description && (
              <p className="text-sm text-[#667085]">{ticket.description}</p>
            )}
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <button
              onClick={handleEditTitle}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs text-[#667085] hover:text-[#e6edf3] bg-[#161b22] border border-[#21262d] hover:border-[#344054] rounded-lg transition-colors"
              title="Edit title"
            >
              <Pencil size={12} /> Edit
            </button>
            {(ticket.status === 'closed' || ticket.status === 'resolved') && (
              <button
                onClick={handleReopen}
                className="flex items-center gap-1.5 px-3 py-1.5 text-xs text-[#00FFA7] bg-[#00FFA7]/5 border border-[#00FFA7]/20 hover:bg-[#00FFA7]/10 rounded-lg transition-colors"
                title="Reopen ticket"
              >
                <RotateCcw size={12} /> Reopen
              </button>
            )}
            {!ticket.is_thread && ticket.assignee_agent && (
              <button
                onClick={() => setShowConvertModal(true)}
                className="flex items-center gap-1.5 px-3 py-1.5 text-xs text-[#00FFA7] bg-[#00FFA7]/5 border border-[#00FFA7]/20 hover:bg-[#00FFA7]/10 rounded-lg transition-colors"
                title="Convert to persistent chat thread"
              >
                <MessageSquare size={12} /> Convert to Thread
              </button>
            )}
            <button
              onClick={handleDelete}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs text-red-400 bg-red-500/5 border border-red-500/20 hover:bg-red-500/10 rounded-lg transition-colors"
              title="Delete ticket"
            >
              <Trash2 size={12} /> Delete
            </button>
          </div>
        </div>

        {/* Metadata grid */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs border-t border-[#21262d] pt-4">
          <div>
            <p className="text-[#667085] mb-1.5">Status</p>
            {editStatus ? (
              <select
                autoFocus
                className="bg-[#0C111D] border border-[#21262d] rounded px-2 py-1 text-[#e6edf3] text-xs focus:outline-none focus:border-[#00FFA7]/50 transition-colors"
                value={ticket.status}
                onChange={e => handleStatusChange(e.target.value as TicketStatus)}
                onBlur={() => setEditStatus(false)}
              >
                {ALL_STATUSES.map(s => <option key={s} value={s}>{s.replace('_', ' ')}</option>)}
              </select>
            ) : (
              <button
                onClick={() => setEditStatus(true)}
                className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full border text-[11px] font-medium hover:opacity-80 transition-opacity ${STATUS_STYLES[ticket.status]}`}
              >
                {ticket.status.replace('_', ' ')}
              </button>
            )}
          </div>

          <div>
            <p className="text-[#667085] mb-1.5">Priority</p>
            {editPriority ? (
              <select
                autoFocus
                className="bg-[#0C111D] border border-[#21262d] rounded px-2 py-1 text-[#e6edf3] text-xs focus:outline-none focus:border-[#00FFA7]/50 transition-colors"
                value={ticket.priority}
                onChange={e => handlePriorityChange(e.target.value as TicketPriority)}
                onBlur={() => setEditPriority(false)}
              >
                {ALL_PRIORITIES.map(p => <option key={p} value={p}>{p}</option>)}
              </select>
            ) : (
              <button
                onClick={() => setEditPriority(true)}
                className="text-[#e6edf3] hover:text-[#00FFA7] transition-colors capitalize text-xs"
              >
                {ticket.priority}
              </button>
            )}
          </div>

          <div>
            <p className="text-[#667085] mb-1.5">Assignee</p>
            <span className="text-[#e6edf3] font-mono">{ticket.assignee_agent ? `@${ticket.assignee_agent}` : '—'}</span>
          </div>

          <div>
            <p className="text-[#667085] mb-1.5">Lock</p>
            {ticket.locked_at ? (
              <span className="flex items-center gap-1 text-orange-400">
                <Lock size={11} /> {ticket.locked_by}
              </span>
            ) : (
              <span className="flex items-center gap-1 text-[#667085]">
                <Unlock size={11} /> unlocked
              </span>
            )}
          </div>

          {(ticket.source_agent || ticket.source_session_id) && (
            <div>
              <p className="text-[#667085] mb-1.5">Source</p>
              <span className="text-[#8b949e] font-mono text-[11px]">
                {ticket.source_agent ? `@${ticket.source_agent}` : ''}
                {ticket.source_agent && ticket.source_session_id ? ' ' : ''}
                {ticket.source_session_id ? `via session #${ticket.source_session_id.slice(0, 8)}` : ''}
              </span>
            </div>
          )}

          <div>
            <p className="text-[#667085] mb-1.5">Created</p>
            <span className="text-[#8b949e]">{formatDate(ticket.created_at)}</span>
          </div>

          <div>
            <p className="text-[#667085] mb-1.5">Updated</p>
            <span className="text-[#8b949e]">{formatDate(ticket.updated_at)}</span>
          </div>

          {ticket.resolved_at && (
            <div>
              <p className="text-[#667085] mb-1.5">Resolved</p>
              <span className="text-[#00FFA7]">{formatDate(ticket.resolved_at)}</span>
            </div>
          )}
        </div>
      </div>

      {/* Timeline */}
      <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-5 mb-4">
        <h2 className="text-sm font-semibold text-[#e6edf3] mb-4 flex items-center gap-2">
          <Activity size={14} className="text-[#00FFA7]" /> Timeline
          <span className="text-[10px] text-[#667085] font-normal">{timeline.length} items</span>
        </h2>

        {timeline.length === 0 ? (
          <div className="flex flex-col items-center py-8 text-[#667085]">
            <Activity size={24} className="opacity-30 mb-2" />
            <p className="text-xs">No activity yet</p>
          </div>
        ) : (
          <div className="space-y-3">
            {timeline.map(item => (
              <div key={item.id} className="flex gap-3">
                <div className="shrink-0 flex items-center justify-center mt-0.5">
                  {item._type === 'comment'
                    ? <div className="w-6 h-6 rounded-full bg-[#00FFA7]/20 flex items-center justify-center"><MessageSquare size={11} className="text-[#00FFA7]" /></div>
                    : <div className="w-6 h-6 rounded-full bg-[#21262d] flex items-center justify-center"><Activity size={11} className="text-[#667085]" /></div>
                  }
                </div>

                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="text-xs font-medium text-[#e6edf3]">{(item as ActivityItem).actor || (item as CommentItem).author}</span>
                    <span className="text-[10px] text-[#667085]">{formatDate(item.created_at)}</span>
                  </div>

                  {item._type === 'comment' ? (
                    <div className="bg-[#0C111D] border border-[#21262d] rounded-lg px-3 py-2">
                      <p className="text-sm text-[#e6edf3] whitespace-pre-wrap">{(item as CommentItem).body}</p>
                      {(item as CommentItem).mentions.length > 0 && (
                        <p className="text-[10px] text-[#667085] mt-1.5 font-mono">
                          mentions: {(item as CommentItem).mentions.map(m => `@${m}`).join(', ')}
                        </p>
                      )}
                    </div>
                  ) : (
                    <p className="text-xs text-[#8b949e] italic">
                      {actionLabel((item as ActivityItem).action, (item as ActivityItem).payload)}
                    </p>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Add comment */}
      {ticket.status !== 'closed' && (
        <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-5">
          <h2 className="text-sm font-semibold text-[#e6edf3] mb-3 flex items-center gap-2">
            <MessageSquare size={14} className="text-[#00FFA7]" /> Add Comment
          </h2>
          <form onSubmit={handleAddComment}>
            <textarea
              className="w-full bg-[#0C111D] border border-[#21262d] rounded-lg px-3 py-2 text-sm text-[#e6edf3] placeholder-[#667085] focus:outline-none focus:border-[#00FFA7]/50 resize-none mb-3 transition-colors"
              placeholder="Add a comment... Use @agent-slug to mention an agent"
              rows={3}
              value={commentBody}
              onChange={e => setCommentBody(e.target.value)}
            />
            <div className="flex items-center justify-between">
              <p className="text-[10px] text-[#667085]">Tip: @mention an agent to wake their heartbeat</p>
              <button
                type="submit"
                disabled={submitting || !commentBody.trim()}
                className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold bg-[#00FFA7] text-black rounded-lg hover:bg-[#00FFA7]/90 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              >
                <Send size={12} />
                {submitting ? 'Sending...' : 'Comment'}
              </button>
            </div>
          </form>
        </div>
      )}

      {/* Convert to Thread modal */}
      {showConvertModal && (
        <ConvertToThreadModal
          ticketId={ticket.id}
          ticketTitle={ticket.title}
          onClose={() => setShowConvertModal(false)}
          onConverted={() => { setShowConvertModal(false); fetchTicket() }}
        />
      )}
    </div>
  )
}

// ─── ConvertToThreadModal ────────────────────────────────────────────────────

interface ConvertModalProps {
  ticketId: string
  ticketTitle: string
  onClose: () => void
  onConverted: () => void
}

function ConvertToThreadModal({ ticketId, ticketTitle, onClose, onConverted }: ConvertModalProps) {
  const [folders, setFolders] = useState<{ name: string; path: string }[]>([])
  const [selectedFolder, setSelectedFolder] = useState<string>('')
  const [customPath, setCustomPath] = useState('')
  const [useCustom, setUseCustom] = useState(false)
  const [loading, setLoading] = useState(true)
  const [converting, setConverting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.get('/workspace/subfolders')
      .then(res => { setFolders(res.folders || []); setLoading(false) })
      .catch(() => { setUseCustom(true); setLoading(false) })
  }, [])

  const handleConvert = async () => {
    const workspacePath = useCustom ? customPath.trim() : selectedFolder
    if (!workspacePath) { setError('Select or enter a workspace path'); return }
    setConverting(true)
    setError(null)
    try {
      await api.patch(`/tickets/${ticketId}/convert-to-thread`, { workspace_path: workspacePath })
      onConverted()
    } catch (err: any) {
      setError(err?.message || 'Conversion failed')
      setConverting(false)
    }
  }

  return (
    <div className="fixed inset-0 bg-black/60 backdrop-blur-sm z-50 flex items-center justify-center p-4">
      <div className="bg-[#161b22] border border-[#21262d] rounded-xl w-full max-w-md p-5 shadow-2xl">
        <h2 className="text-base font-semibold text-[#e6edf3] mb-1">Convert to Thread</h2>
        <p className="text-xs text-[#667085] mb-4">
          "{ticketTitle}" will become a persistent chat thread with isolated memory.
        </p>

        {loading ? (
          <div className="flex items-center gap-2 text-xs text-[#667085] py-4">
            <RefreshCw size={14} className="animate-spin" /> Loading workspace folders...
          </div>
        ) : (
          <>
            {!useCustom && folders.length > 0 && (
              <div className="mb-3">
                <label className="text-xs text-[#667085] mb-1.5 block">Working directory</label>
                <select
                  value={selectedFolder}
                  onChange={e => setSelectedFolder(e.target.value)}
                  className="w-full bg-[#0C111D] border border-[#21262d] rounded-lg px-3 py-2 text-sm text-[#e6edf3] focus:outline-none focus:border-[#00FFA7]/50"
                >
                  <option value="">Select a folder...</option>
                  {folders.map(f => (
                    <option key={f.path} value={f.path}>{f.name}</option>
                  ))}
                </select>
                <button
                  onClick={() => setUseCustom(true)}
                  className="text-[10px] text-[#667085] hover:text-[#00FFA7] mt-1 transition-colors"
                >
                  Enter custom path
                </button>
              </div>
            )}

            {(useCustom || folders.length === 0) && (
              <div className="mb-3">
                <label className="text-xs text-[#667085] mb-1.5 block">Working directory path</label>
                <input
                  type="text"
                  value={customPath}
                  onChange={e => setCustomPath(e.target.value)}
                  placeholder="workspace/my-project"
                  className="w-full bg-[#0C111D] border border-[#21262d] rounded-lg px-3 py-2 text-sm text-[#e6edf3] placeholder-[#667085] focus:outline-none focus:border-[#00FFA7]/50"
                />
                {folders.length > 0 && (
                  <button
                    onClick={() => setUseCustom(false)}
                    className="text-[10px] text-[#667085] hover:text-[#00FFA7] mt-1 transition-colors"
                  >
                    Pick from list
                  </button>
                )}
              </div>
            )}
          </>
        )}

        <div className="flex items-start gap-2 p-3 mb-3 rounded-lg bg-orange-400/10 border border-orange-400/30">
          <span className="text-orange-400 text-xs mt-0.5 shrink-0">⚠️</span>
          <p className="text-xs text-orange-300 leading-relaxed">
            Após converter, o agente desta thread não poderá ser alterado. Crie uma thread nova para trocar de agente.
          </p>
        </div>

        {error && <p className="text-xs text-red-400 mb-3">{error}</p>}

        <div className="flex items-center gap-2 justify-end">
          <button
            onClick={onClose}
            className="px-3 py-1.5 text-xs text-[#667085] border border-[#21262d] rounded-lg hover:border-[#344054] transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={handleConvert}
            disabled={converting || loading}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold bg-[#00FFA7] text-black rounded-lg hover:bg-[#00FFA7]/90 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            <MessageSquare size={12} />
            {converting ? 'Converting...' : 'Convert to Thread'}
          </button>
        </div>
      </div>
    </div>
  )
}
