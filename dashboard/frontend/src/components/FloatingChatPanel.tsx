import { useEffect, useState, useRef } from 'react'
import { Plus, X, Maximize2, Minimize2, MessageSquare, History } from 'lucide-react'
import { useFloatingChat } from '../context/FloatingChatContext'
import { AgentAvatar } from './AgentAvatar'
import { TS_HTTP } from '../lib/terminal-url'

interface AgentItem {
  name: string
  description?: string
}

interface SessionItem {
  id: string
  agentName: string | null
  active: boolean
  preview?: string
}

export default function FloatingChatPanel() {
  const { windows, openWindow, closeWindow, toggleMinimize, setPanelOpen } = useFloatingChat()
  const [agents, setAgents] = useState<AgentItem[]>([])
  const [sessions, setSessions] = useState<SessionItem[]>([])
  const [search, setSearch] = useState('')
  const [showPicker, setShowPicker] = useState(false)
  const [loadingAgent, setLoadingAgent] = useState<string | null>(null)
  const panelRef = useRef<HTMLDivElement>(null)

  // Load agent list
  useEffect(() => {
    fetch('/api/agents', { credentials: 'include' })
      .then(r => r.ok ? r.json() : null)
      .then(data => {
        if (data?.agents) {
          setAgents(data.agents.map((a: any) => ({ name: a.name || a.slug, description: a.description })))
        } else if (Array.isArray(data)) {
          setAgents(data.map((a: any) => ({ name: a.name || a.slug, description: a.description })))
        }
      })
      .catch(() => {})
  }, [])

  // Load all active sessions (including ones started outside the floating chat)
  useEffect(() => {
    fetch(`${TS_HTTP}/api/sessions`)
      .then(r => r.ok ? r.json() : null)
      .then(data => {
        if (data?.sessions) setSessions(data.sessions as SessionItem[])
      })
      .catch(() => {})
  }, [])

  // Close panel on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (panelRef.current && !panelRef.current.contains(e.target as Node)) {
        // Only close if not clicking the FAB itself (FAB handles its own toggle)
        setPanelOpen(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [setPanelOpen])

  const filteredAgents = agents.filter(a =>
    a.name.toLowerCase().includes(search.toLowerCase().replace(/^@/, ''))
  )

  const handleOpenAgent = async (agentName: string) => {
    setLoadingAgent(agentName)
    await openWindow(agentName)
    setLoadingAgent(null)
    setShowPicker(false)
    setSearch('')
  }

  const handleContinueSession = async (s: SessionItem) => {
    if (!s.agentName) return
    setLoadingAgent(s.id)
    await openWindow(s.agentName, s.id)
    setLoadingAgent(null)
  }

  // External sessions not already open in the floating chat
  const externalSessions = sessions.filter(
    s => s.agentName && !windows.some(w => w.id === s.agentName && w.sessionId === s.id)
  )

  return (
    <div
      ref={panelRef}
      className="fixed z-[190] bottom-20 right-6 w-72 rounded-xl border shadow-2xl overflow-hidden"
      style={{
        background: '#0d1117',
        borderColor: '#21262d',
        boxShadow: '0 8px 32px rgba(0,0,0,0.6)',
      }}
    >
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2.5 border-b border-[#21262d]">
        <div className="flex items-center gap-2">
          <MessageSquare size={13} className="text-[#00FFA7]" />
          <span className="text-xs font-semibold text-[#e6edf3]">Chats</span>
        </div>
        <button
          onClick={() => setPanelOpen(false)}
          className="w-5 h-5 flex items-center justify-center rounded text-[#667085] hover:text-[#e6edf3] hover:bg-white/10 transition-colors"
        >
          <X size={12} />
        </button>
      </div>

      {/* Active windows list */}
      {windows.length > 0 && (
        <div className="py-1 border-b border-[#21262d]">
          {windows.map(win => (
            <div
              key={win.id}
              className="flex items-center gap-2 px-3 py-1.5 hover:bg-white/5 transition-colors"
            >
              <AgentAvatar name={win.id} size={20} />
              <span className="text-xs text-[#e6edf3] flex-1 truncate">@{win.id}</span>

              {win.pendingApprovals > 0 && (
                <span
                  className="text-[9px] font-bold px-1.5 py-0.5 rounded-full"
                  style={{ background: '#F59E0B20', color: '#F59E0B' }}
                >
                  {win.pendingApprovals}
                </span>
              )}

              {/* Minimize/restore */}
              <button
                onClick={() => { toggleMinimize(win.id); setPanelOpen(false) }}
                className="w-5 h-5 flex items-center justify-center rounded text-[#667085] hover:text-[#e6edf3] hover:bg-white/10 transition-colors"
                title={win.minimized ? 'Restaurar' : 'Minimizar'}
              >
                {win.minimized ? <Maximize2 size={11} /> : <Minimize2 size={11} />}
              </button>

              {/* Close */}
              <button
                onClick={() => closeWindow(win.id)}
                className="w-5 h-5 flex items-center justify-center rounded text-[#667085] hover:text-red-400 hover:bg-red-400/10 transition-colors"
                title="Fechar"
              >
                <X size={11} />
              </button>
            </div>
          ))}
        </div>
      )}

      {/* Active sessions started outside the floating chat */}
      {externalSessions.length > 0 && (
        <div className="py-1 border-b border-[#21262d]">
          <div className="flex items-center gap-1.5 px-3 py-1 text-[10px] uppercase tracking-wide text-[#667085]">
            <History size={10} />
            Sessões ativas
          </div>
          <div className="max-h-44 overflow-y-auto">
            {externalSessions.map(s => (
              <button
                key={s.id}
                onClick={() => handleContinueSession(s)}
                disabled={loadingAgent === s.id}
                className="w-full flex items-center gap-2 px-3 py-1.5 hover:bg-white/5 transition-colors text-left disabled:opacity-50"
              >
                <AgentAvatar name={s.agentName!} size={20} />
                <span className="flex flex-col min-w-0 flex-1">
                  <span className="text-xs text-[#e6edf3] truncate">@{s.agentName}</span>
                  {s.preview && (
                    <span className="text-[10px] text-[#667085] truncate">{s.preview}</span>
                  )}
                </span>
                {s.active && (
                  <span className="flex-shrink-0 w-1.5 h-1.5 rounded-full bg-[#00FFA7]" title="Ativa" />
                )}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* New chat button / agent picker */}
      {!showPicker ? (
        <button
          onClick={() => setShowPicker(true)}
          className="w-full flex items-center gap-2 px-3 py-2.5 text-xs text-[#667085] hover:text-[#00FFA7] hover:bg-[#00FFA7]/5 transition-colors"
        >
          <Plus size={13} />
          Nova conversa
        </button>
      ) : (
        <div className="p-2 space-y-1.5">
          <input
            autoFocus
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="Buscar agente..."
            className="w-full bg-[#161b22] border border-[#21262d] rounded-lg px-2.5 py-1.5 text-xs text-[#e6edf3] placeholder:text-[#667085] focus:outline-none focus:border-[#00FFA7]/40"
          />
          <div className="max-h-52 overflow-y-auto space-y-0.5">
            {filteredAgents.length === 0 && (
              <p className="text-[11px] text-[#667085] px-2 py-2 text-center">
                {agents.length === 0 ? 'Carregando...' : 'Nenhum agente encontrado'}
              </p>
            )}
            {filteredAgents.map(agent => {
              const alreadyOpen = windows.some(w => w.id === agent.name)
              const isLoading = loadingAgent === agent.name
              return (
                <button
                  key={agent.name}
                  onClick={() => handleOpenAgent(agent.name)}
                  disabled={isLoading}
                  className="w-full flex items-center gap-2 px-2 py-1.5 rounded-lg text-left hover:bg-white/5 transition-colors disabled:opacity-60"
                >
                  <AgentAvatar name={agent.name} size={20} />
                  <div className="flex-1 min-w-0">
                    <div className="text-xs text-[#e6edf3] truncate">@{agent.name}</div>
                    {agent.description && (
                      <div className="text-[10px] text-[#667085] truncate">{agent.description.slice(0, 50)}</div>
                    )}
                  </div>
                  {alreadyOpen && (
                    <span className="text-[9px] text-[#00FFA7] flex-shrink-0">aberto</span>
                  )}
                  {isLoading && (
                    <span className="text-[9px] text-[#667085] flex-shrink-0 animate-pulse">...</span>
                  )}
                </button>
              )
            })}
          </div>
          <button
            onClick={() => { setShowPicker(false); setSearch('') }}
            className="w-full text-[11px] text-[#667085] hover:text-[#e6edf3] py-1 transition-colors"
          >
            Cancelar
          </button>
        </div>
      )}
    </div>
  )
}
