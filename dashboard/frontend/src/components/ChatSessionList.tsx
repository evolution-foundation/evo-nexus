import { useEffect, useRef, useState } from 'react'
import { useConfirm } from './ConfirmDialog'
import {
  MessageSquare,
  Plus,
  Clock,
  Ticket as TicketIcon,
  Edit2,
  Archive,
  ArchiveRestore,
  Trash2,
  ChevronDown,
  ChevronRight,
} from 'lucide-react'

export interface ChatSession {
  id: string
  name: string
  active: boolean
  preview?: string
  ts?: number
  ticketId?: string | null
  archived?: boolean
}

interface ContextMenuState {
  sessionId: string
  x: number
  y: number
}

interface ChatSessionListProps {
  sessions: ChatSession[]
  activeSessionId: string | null
  onSelectSession: (id: string) => void
  onNewSession: () => void
  accentColor: string
  approvalCounts?: Map<string, number>
  onRename?: (id: string, newName: string) => void
  onArchive?: (id: string, archived: boolean) => void
  onDelete?: (id: string) => void
}

function formatRelativeTime(ts: number): string {
  const diff = Date.now() - ts
  const minutes = Math.floor(diff / 60000)
  if (minutes < 1) return 'agora'
  if (minutes < 60) return `${minutes}m atrás`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h atrás`
  const days = Math.floor(hours / 24)
  return `${days}d atrás`
}

export default function ChatSessionList({
  sessions,
  activeSessionId,
  onSelectSession,
  onNewSession,
  accentColor,
  approvalCounts,
  onRename,
  onArchive,
  onDelete,
}: ChatSessionListProps) {
  const confirm = useConfirm()
  const [contextMenu, setContextMenu] = useState<ContextMenuState | null>(null)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editName, setEditName] = useState('')
  const [archivedOpen, setArchivedOpen] = useState(false)
  const menuRef = useRef<HTMLDivElement>(null)
  const editInputRef = useRef<HTMLInputElement>(null)

  const activeSessions = sessions.filter(s => !s.archived)
  const archivedSessions = sessions.filter(s => s.archived)

  // Close context menu on outside click or Escape
  useEffect(() => {
    if (!contextMenu) return
    function onDown(e: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setContextMenu(null)
      }
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') setContextMenu(null)
    }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [contextMenu])

  // Focus input when entering edit mode
  useEffect(() => {
    if (editingId && editInputRef.current) {
      editInputRef.current.focus()
      editInputRef.current.select()
    }
  }, [editingId])

  function handleContextMenu(e: React.MouseEvent, sessionId: string) {
    e.preventDefault()
    e.stopPropagation()
    setContextMenu({ sessionId, x: e.clientX, y: e.clientY })
  }

  function startRename(session: ChatSession) {
    setContextMenu(null)
    setEditingId(session.id)
    setEditName(session.name)
  }

  function commitRename() {
    if (!editingId) return
    const trimmed = editName.trim()
    if (trimmed && onRename) {
      onRename(editingId, trimmed)
    }
    setEditingId(null)
    setEditName('')
  }

  function cancelRename() {
    setEditingId(null)
    setEditName('')
  }

  function handleRenameKey(e: React.KeyboardEvent) {
    if (e.key === 'Enter') commitRename()
    if (e.key === 'Escape') cancelRename()
  }

  function handleArchive(session: ChatSession) {
    setContextMenu(null)
    if (onArchive) onArchive(session.id, !session.archived)
  }

  async function handleDelete(session: ChatSession) {
    setContextMenu(null)
    const ok = await confirm({
      title: 'Deletar conversa',
      description: `Deletar a conversa "${session.name}"? Esta ação não pode ser desfeita.`,
      confirmText: 'Deletar',
      variant: 'danger',
    })
    if (!ok) return
    if (onDelete) onDelete(session.id)
  }

  const contextSession = contextMenu
    ? sessions.find(s => s.id === contextMenu.sessionId) ?? null
    : null

  function renderSession(session: ChatSession, isArchived = false) {
    const isActive = session.id === activeSessionId
    const isEditing = editingId === session.id
    return (
      <li key={session.id}>
        <button
          onClick={() => !isEditing && onSelectSession(session.id)}
          onContextMenu={(e) => handleContextMenu(e, session.id)}
          className={`w-full text-left flex items-start gap-3 px-3 py-2.5 rounded-lg transition-colors group${isArchived ? ' opacity-50' : ''}`}
          style={{
            background: isActive ? `${accentColor}10` : 'transparent',
            border: `1px solid ${isActive ? `${accentColor}25` : 'transparent'}`,
          }}
          onMouseEnter={(e) => {
            if (!isActive) {
              e.currentTarget.style.background = '#161b22'
            }
          }}
          onMouseLeave={(e) => {
            if (!isActive) {
              e.currentTarget.style.background = 'transparent'
            }
          }}
        >
          {/* Status dot */}
          <div className="flex-shrink-0 mt-1">
            <span
              className="inline-block h-2 w-2 rounded-full"
              style={{
                backgroundColor: session.active
                  ? accentColor
                  : isActive
                  ? `${accentColor}60`
                  : '#344054',
                boxShadow: session.active
                  ? `0 0 6px ${accentColor}80`
                  : 'none',
              }}
            />
          </div>

          {/* Content */}
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-1.5 mb-0.5">
              {isEditing ? (
                <input
                  ref={editInputRef}
                  value={editName}
                  onChange={(e) => setEditName(e.target.value)}
                  onKeyDown={handleRenameKey}
                  onBlur={commitRename}
                  onClick={(e) => e.stopPropagation()}
                  className="flex-1 min-w-0 bg-[#0d1117] border border-[#30363d] rounded px-1.5 py-0.5 text-[12px] font-medium text-[#e6edf3] outline-none focus:border-[#58a6ff]"
                />
              ) : (
                <p
                  className="text-[12px] font-medium truncate leading-tight"
                  style={{ color: isActive ? '#e6edf3' : '#c9d1d9' }}
                >
                  {session.name}
                </p>
              )}
              {!isEditing && (approvalCounts?.get(session.id) ?? 0) > 0 && (
                <span
                  className="flex-shrink-0 inline-block w-2 h-2 rounded-full animate-pulse"
                  style={{ backgroundColor: accentColor, boxShadow: `0 0 5px ${accentColor}99` }}
                  title="Waiting for approval"
                />
              )}
              {!isEditing && session.ticketId && (
                <span
                  className="flex-shrink-0 inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[10px] font-mono leading-none"
                  style={{
                    background: `${accentColor}15`,
                    border: `1px solid ${accentColor}30`,
                    color: accentColor,
                  }}
                >
                  <TicketIcon size={9} />
                  #{session.ticketId.slice(0, 8)}
                </span>
              )}
            </div>
            {!isEditing && session.preview && (
              <p className="text-[11px] text-[#667085] truncate leading-tight">
                {session.preview}
              </p>
            )}
            {!isEditing && session.ts && (
              <div className="flex items-center gap-1 mt-1">
                <Clock size={9} className="text-[#3F3F46]" />
                <span className="text-[10px] text-[#3F3F46]">
                  {formatRelativeTime(session.ts)}
                </span>
              </div>
            )}
          </div>
        </button>
      </li>
    )
  }

  return (
    <div className="flex flex-col h-full">
      {/* New conversation button */}
      <div className="flex-shrink-0 px-4 pt-4 pb-3">
        <button
          onClick={onNewSession}
          className="w-full flex items-center justify-center gap-2 py-2 px-3 rounded-lg text-[11px] font-medium uppercase tracking-[0.1em] border transition-colors"
          style={{
            borderColor: `${accentColor}30`,
            color: accentColor,
            background: `${accentColor}08`,
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.background = `${accentColor}15`
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.background = `${accentColor}08`
          }}
        >
          <Plus size={12} />
          Nova conversa
        </button>
      </div>

      {/* Sessions list */}
      <div className="flex-1 min-h-0 overflow-y-auto px-3 pb-4">
        {sessions.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-10 gap-3">
            <div
              className="w-10 h-10 rounded-xl flex items-center justify-center"
              style={{ background: `${accentColor}10`, border: `1px solid ${accentColor}20` }}
            >
              <MessageSquare size={16} style={{ color: accentColor }} />
            </div>
            <p className="text-[11px] text-[#667085] text-center leading-relaxed">
              Nenhuma conversa ainda.<br />Inicie uma nova conversa abaixo.
            </p>
          </div>
        ) : (
          <>
            {/* Active sessions */}
            {activeSessions.length > 0 && (
              <ul className="space-y-0.5">
                {activeSessions.map((session) => renderSession(session, false))}
              </ul>
            )}

            {/* Archived section */}
            {archivedSessions.length > 0 && (
              <div className={activeSessions.length > 0 ? 'mt-3' : ''}>
                <button
                  onClick={() => setArchivedOpen(v => !v)}
                  className="w-full flex items-center gap-1.5 px-1 py-1 text-[11px] text-[#667085] hover:text-[#c9d1d9] transition-colors"
                >
                  {archivedOpen
                    ? <ChevronDown size={12} />
                    : <ChevronRight size={12} />}
                  <Archive size={11} />
                  <span>Arquivadas ({archivedSessions.length})</span>
                </button>
                {archivedOpen && (
                  <ul className="space-y-0.5 mt-1">
                    {archivedSessions.map((session) => renderSession(session, true))}
                  </ul>
                )}
              </div>
            )}
          </>
        )}
      </div>

      {/* Context menu */}
      {contextMenu && contextSession && (
        <div
          ref={menuRef}
          className="fixed z-[200] rounded-lg border border-[#21262d] bg-[#161b22] shadow-2xl py-1 min-w-[160px]"
          style={{ top: contextMenu.y, left: contextMenu.x }}
        >
          <button
            className="w-full flex items-center gap-2.5 px-3 py-2 text-sm text-[#c9d1d9] hover:bg-white/5 transition-colors"
            onClick={() => startRename(contextSession)}
          >
            <Edit2 size={13} className="text-[#667085]" />
            Renomear
          </button>
          <button
            className="w-full flex items-center gap-2.5 px-3 py-2 text-sm text-[#c9d1d9] hover:bg-white/5 transition-colors"
            onClick={() => handleArchive(contextSession)}
          >
            {contextSession.archived
              ? <ArchiveRestore size={13} className="text-[#667085]" />
              : <Archive size={13} className="text-[#667085]" />}
            {contextSession.archived ? 'Desarquivar' : 'Arquivar'}
          </button>
          <div className="border-t border-[#21262d] my-1" />
          <button
            className="w-full flex items-center gap-2.5 px-3 py-2 text-sm text-red-400 hover:bg-white/5 transition-colors"
            onClick={() => handleDelete(contextSession)}
          >
            <Trash2 size={13} />
            Deletar
          </button>
        </div>
      )}
    </div>
  )
}
