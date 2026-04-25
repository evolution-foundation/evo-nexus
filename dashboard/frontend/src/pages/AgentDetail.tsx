import { useEffect, useState, useCallback, useRef } from 'react'
import { useParams, Link } from 'react-router-dom'
import { ArrowLeft, ChevronDown, ChevronRight, PanelLeft, X, Lock, Plus, Terminal as TerminalIcon, MessageSquare } from 'lucide-react'
import { api } from '../lib/api'
import Markdown from '../components/Markdown'
import AgentTerminal from '../components/AgentTerminal'
import AgentChat from '../components/AgentChat'
import ChatSessionList, { type ChatSession } from '../components/ChatSessionList'
import { getAgentMeta } from '../lib/agent-meta'
import { trackAgentVisit } from './Agents'
import { AgentAvatar } from '../components/AgentAvatar'
import { useAuth } from '../context/AuthContext'
import { useNotificationBadge } from '../hooks/useNotificationBadge'

interface MemoryFile {
  name: string
  path: string
  size: number
}

type Tab = 'sessions' | 'profile' | 'memory'

// Terminal-server URL — always go through the dashboard's /terminal proxy in
// production builds. Direct cross-port fetches (e.g. localhost:32352 from a
// page served at localhost:8080) are blocked by the dashboard's
// `connect-src 'self'` CSP directive even when the network path works.
// In Vite dev mode (no proxy mounted) we fall back to a direct connection.
const isViteDev = import.meta.env.DEV
const TS_HTTP = isViteDev
  ? `http://${window.location.hostname}:32352`
  : `${window.location.origin}/terminal`

interface TerminalTab {
  id: string       // sessionId
  name: string     // display name
  active: boolean  // is claude running
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes}b`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}kb`
  return `${(bytes / 1024 / 1024).toFixed(1)}mb`
}

function formatName(slug: string): string {
  return slug
    .split('-')
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ')
}

export default function AgentDetail() {
  const { name } = useParams()
  const { hasAgentAccess } = useAuth()
  const [content, setContent] = useState<string | null>(null)
  const [memories, setMemories] = useState<MemoryFile[]>([])
  const [memoryContents, setMemoryContents] = useState<Record<string, string>>({})
  const [loading, setLoading] = useState(true)
  const [expandedMemory, setExpandedMemory] = useState<string | null>(null)
  const [tab, setTab] = useState<Tab>('sessions')
  const [railOpen, setRailOpen] = useState(false) // mobile drawer

  // View mode: terminal or chat
  type ViewMode = 'terminal' | 'chat'
  const [viewMode, setViewMode] = useState<ViewMode>(() => {
    try { return (localStorage.getItem('evo:agent-view-mode') as ViewMode) || 'terminal' } catch { return 'terminal' }
  })

  // Multi-terminal tabs
  const [termTabs, setTermTabs] = useState<TerminalTab[]>([])
  const [activeTermTab, setActiveTermTab] = useState<string | null>(null)
  const [, setTermTabsLoading] = useState(true)

  // Chat sessions
  const [chatSessions, setChatSessions] = useState<ChatSession[]>([])
  const [activeChatSessionId, setActiveChatSessionId] = useState<string | null>(null)
  const [chatConnectError, setChatConnectError] = useState<string | null>(null)
  const [chatConnecting, setChatConnecting] = useState(false)

  // Notification badge state — pending approvals per session
  const approvalCountsRef = useRef<Map<string, number>>(new Map())
  const [totalPending, setTotalPending] = useState(0)
  const [needsAttention, setNeedsAttention] = useState(false)

  // Clear "needs attention" when user comes back to the tab
  useEffect(() => {
    const onVisibility = () => {
      if (!document.hidden) setNeedsAttention(false)
    }
    document.addEventListener('visibilitychange', onVisibility)
    return () => document.removeEventListener('visibilitychange', onVisibility)
  }, [])

  useNotificationBadge(totalPending, needsAttention)

  const handlePendingCountChange = useCallback((sessionId: string, count: number) => {
    approvalCountsRef.current.set(sessionId, count)
    const total = Array.from(approvalCountsRef.current.values()).reduce((a, b) => a + b, 0)
    setTotalPending(total)
  }, [])

  const handleNeedsAttention = useCallback((_sessionId: string) => {
    setNeedsAttention(true)
  }, [])

  // Track agent visit for "Recent" section
  useEffect(() => {
    if (name) trackAgentVisit(name)
  }, [name])

  // Load existing terminal sessions for this agent
  useEffect(() => {
    if (!name) return
    setTermTabsLoading(true)
    fetch(`${TS_HTTP}/api/sessions/by-agent/${name}`)
      .then(r => r.ok ? r.json() : { sessions: [] })
      .then(data => {
        const sessions: TerminalTab[] = (data.sessions || []).map((s: any) => ({
          id: s.id,
          name: s.name || name,
          active: s.active,
        }))
        if (sessions.length === 0) {
          // No existing sessions — will use default find-or-create (no tab needed yet)
          setTermTabs([])
          setActiveTermTab(null)
        } else {
          setTermTabs(sessions)
          setActiveTermTab(sessions[0].id)
        }
      })
      .catch(() => {
        setTermTabs([])
        setActiveTermTab(null)
      })
      .finally(() => setTermTabsLoading(false))
  }, [name])

  const createNewTerminal = useCallback(async () => {
    if (!name) return
    try {
      const res = await fetch(`${TS_HTTP}/api/sessions/create`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ agentName: name }),
      })
      if (!res.ok) return
      const data = await res.json()
      const newTab: TerminalTab = {
        id: data.sessionId,
        name: data.session?.name || `${name} #${termTabs.length + 1}`,
        active: false,
      }
      // If this is the first extra tab, we also need to load the existing default session
      if (termTabs.length === 0 && activeTermTab === null) {
        // Fetch existing sessions first to get the default one
        const existing = await fetch(`${TS_HTTP}/api/sessions/by-agent/${name}`)
        if (existing.ok) {
          const existingData = await existing.json()
          const allSessions: TerminalTab[] = (existingData.sessions || [])
            .filter((s: any) => s.id !== data.sessionId)
            .map((s: any) => ({ id: s.id, name: s.name || name, active: s.active }))
          setTermTabs([...allSessions, newTab])
        } else {
          setTermTabs([newTab])
        }
      } else {
        setTermTabs(prev => [...prev, newTab])
      }
      setActiveTermTab(data.sessionId)
    } catch {}
  }, [name, termTabs, activeTermTab])

  // Load chat sessions for this agent
  const loadChatSessions = useCallback(async () => {
    if (!name) return
    try {
      const res = await fetch(`${TS_HTTP}/api/sessions/by-agent/${name}`)
      if (!res.ok) return
      const data = await res.json()
      const sessions: ChatSession[] = (data.sessions || []).map((s: any) => ({
        id: s.id,
        name: s.name || name,
        active: s.active,
        preview: s.preview || undefined,
        ts: typeof s.lastActivity === 'number' ? s.lastActivity : (s.lastActivity ? new Date(s.lastActivity).getTime() : undefined),
        ticketId: s.ticketId || null,
        archived: s.archived || false,
      }))
      setChatSessions(sessions)
      // Auto-select first session only if none active
      setActiveChatSessionId(prev => {
        if (prev && sessions.some(s => s.id === prev)) return prev
        return sessions.length > 0 ? sessions[0].id : null
      })
    } catch {}
  }, [name])

  useEffect(() => {
    if (viewMode === 'chat') loadChatSessions()
  }, [viewMode, name])

  const createNewChatSession = useCallback(async () => {
    if (!name) return
    try {
      const res = await fetch(`${TS_HTTP}/api/sessions/create`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ agentName: name }),
      })
      if (!res.ok) return
      const data = await res.json()
      const newSession: ChatSession = {
        id: data.sessionId,
        name: data.session?.name || `${name} #${chatSessions.length + 1}`,
        active: false,
        ts: Date.now(),
      }
      setChatSessions(prev => [newSession, ...prev])
      setActiveChatSessionId(data.sessionId)
    } catch {}
  }, [name, chatSessions])

  const selectChatSession = useCallback((id: string) => {
    setActiveChatSessionId(id)
    // Reload sessions list to get fresh previews and order
    loadChatSessions()
  }, [loadChatSessions])

  const renameChatSession = useCallback(async (id: string, newName: string) => {
    try {
      await fetch(`${TS_HTTP}/api/sessions/${id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: newName }),
      })
      loadChatSessions()
    } catch {}
  }, [loadChatSessions])

  const archiveChatSession = useCallback(async (id: string, archived: boolean) => {
    try {
      await fetch(`${TS_HTTP}/api/sessions/${id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ archived }),
      })
      loadChatSessions()
    } catch {}
  }, [loadChatSessions])

  const deleteChatSession = useCallback(async (id: string) => {
    try {
      await fetch(`${TS_HTTP}/api/sessions/${id}`, { method: 'DELETE' })
      setChatSessions(prev => prev.filter(s => s.id !== id))
      setActiveChatSessionId(prev => {
        if (prev !== id) return prev
        const remaining = chatSessions.filter(s => s.id !== id && !s.archived)
        return remaining.length > 0 ? remaining[0].id : null
      })
    } catch {}
  }, [chatSessions])

  // Auto-create a chat session when switching to chat mode if none exist
  useEffect(() => {
    if (viewMode === 'chat' && chatSessions.length === 0 && name) {
      setChatConnectError(null)
      setChatConnecting(true)
      // Find-or-create via the for-agent endpoint
      fetch(`${TS_HTTP}/api/sessions/for-agent`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ agentName: name }),
      })
        .then(r => {
          if (!r.ok) throw new Error(`HTTP ${r.status}`)
          return r.json()
        })
        .then(data => {
          if (!data) return
          const session: ChatSession = {
            id: data.sessionId,
            name: data.session?.name || name,
            active: data.session?.active ?? false,
            ts: Date.now(),
          }
          setChatSessions([session])
          setActiveChatSessionId(data.sessionId)
        })
        .catch(() => {
          setChatConnectError(`Could not reach terminal-server at ${TS_HTTP}. Is it running?`)
        })
        .finally(() => {
          setChatConnecting(false)
        })
    }
  }, [viewMode, chatSessions.length, name])

  const closeTerminalTab = useCallback(async (sessionId: string) => {
    // Stop and delete session
    try {
      await fetch(`${TS_HTTP}/api/sessions/${sessionId}`, { method: 'DELETE' })
    } catch {}
    setTermTabs(prev => {
      const next = prev.filter(t => t.id !== sessionId)
      if (activeTermTab === sessionId) {
        setActiveTermTab(next.length > 0 ? next[0].id : null)
      }
      return next
    })
  }, [activeTermTab])

  useEffect(() => {
    if (!name) return
    setLoading(true)
    Promise.all([
      api.getRaw(`/agents/${name}`).catch(() => null),
      api.get(`/agents/${name}/memory`).catch(() => []),
    ])
      .then(([md, mems]) => {
        setContent(md)
        setMemories(Array.isArray(mems) ? mems : [])
      })
      .finally(() => setLoading(false))
  }, [name])

  const toggleMemory = async (memName: string) => {
    if (expandedMemory === memName) {
      setExpandedMemory(null)
      return
    }
    setExpandedMemory(memName)
    if (!memoryContents[memName]) {
      try {
        const text = await api.getRaw(`/agents/${name}/memory/${memName}`)
        setMemoryContents((prev) => ({ ...prev, [memName]: text }))
      } catch {
        setMemoryContents((prev) => ({ ...prev, [memName]: 'Failed to load' }))
      }
    }
  }

  if (!name) return null

  // Check agent access before rendering anything
  if (!hasAgentAccess(name)) {
    return (
      <div className="h-full w-full flex flex-col items-center justify-center bg-[#0C111D] gap-4">
        <div className="flex items-center justify-center w-16 h-16 rounded-2xl bg-[#161b22] border border-[#21262d]">
          <Lock size={28} className="text-[#667085]" />
        </div>
        <div className="text-center">
          <p className="text-[#e6edf3] font-semibold text-base mb-1">Acesso restrito</p>
          <p className="text-[#667085] text-sm">Você não tem permissão para acessar este agente.</p>
        </div>
        <Link
          to="/agents"
          className="mt-2 text-[11px] uppercase tracking-[0.12em] text-[#00FFA7] hover:underline"
        >
          ← Agentes
        </Link>
      </div>
    )
  }

  const meta = getAgentMeta(name)
  const agentColor = meta.color

  if (loading) {
    return (
      <div className="h-full w-full flex items-center justify-center bg-[#0C111D]">
        <div className="text-[#667085] text-xs uppercase tracking-[0.12em]">loading agent…</div>
      </div>
    )
  }

  if (!content) {
    return (
      <div className="h-full w-full flex flex-col items-center justify-center bg-[#0C111D] gap-3">
        <p className="text-[#667085] text-sm">Agent not found</p>
        <Link to="/agents" className="text-[11px] uppercase tracking-[0.12em] text-[#00FFA7] hover:underline">
          ← Agents
        </Link>
      </div>
    )
  }

  const profileBody = extractProfileBody(content)
  const profileLead = extractProfileLead(content)

  return (
    <div className="flex h-full w-full flex-col bg-[#0C111D]">
      {/* ── HERO STRIP ─────────────────────────────────────────────── */}
      <header className="flex-shrink-0 h-20 flex items-center px-4 lg:px-6 gap-4 border-b border-[#21262d] bg-[#0d1117]">
        <Link
          to="/agents"
          className="flex items-center gap-1.5 text-[10px] uppercase tracking-[0.14em] text-[#667085] hover:text-[#e6edf3] transition-colors"
        >
          <ArrowLeft size={12} />
          Agents
        </Link>

        <span className="text-[#21262d]">·</span>

        {/* Avatar */}
        <div
          className="rounded-full flex-shrink-0"
          style={{ padding: 2, background: `${agentColor}40` }}
        >
          <AgentAvatar name={name} size={60} />
        </div>

        <div className="flex flex-col gap-0.5 min-w-0">
          <h1 className="text-[16px] font-semibold text-[#e6edf3] tracking-tight truncate">
            {formatName(name)}
          </h1>
          <code
            className="font-mono text-[11px] tracking-tight"
            style={{ color: agentColor }}
          >
            {meta.command}
          </code>
        </div>

        {/* Memory count — right aligned */}
        <div className="ml-auto flex items-center gap-4">
          <span className="hidden sm:inline text-[10px] uppercase tracking-[0.12em] text-[#667085]">
            {memories.length} {memories.length === 1 ? 'memory' : 'memories'}
          </span>

          {/* Mobile drawer toggle */}
          <button
            onClick={() => setRailOpen(true)}
            className="lg:hidden flex h-8 w-8 items-center justify-center rounded-md border border-[#21262d] text-[#8b949e] hover:text-[#e6edf3] hover:border-[#30363d]"
            aria-label="Open agent info"
          >
            <PanelLeft size={14} />
          </button>
        </div>
      </header>

      {/* ── BODY ───────────────────────────────────────────────────── */}
      <div className="flex flex-1 min-h-0 relative">
        {/* Info rail — desktop */}
        <aside className="hidden lg:flex flex-col w-[320px] flex-shrink-0 border-r border-[#21262d] bg-[#0d1117]">
          <InfoRail
            tab={tab}
            setTab={setTab}
            agentColor={agentColor}
            profileLead={profileLead}
            profileBody={profileBody}
            memories={memories}
            expandedMemory={expandedMemory}
            memoryContents={memoryContents}
            toggleMemory={toggleMemory}
            agentSlug={name}
            chatSessions={chatSessions}
            activeChatSessionId={activeChatSessionId}
            onSelectChatSession={selectChatSession}
            onNewChatSession={createNewChatSession}
            approvalCounts={approvalCountsRef.current}
            onRename={renameChatSession}
            onArchive={archiveChatSession}
            onDelete={deleteChatSession}
          />
        </aside>

        {/* Info rail — mobile drawer */}
        {railOpen && (
          <div
            className="lg:hidden fixed inset-0 z-40 bg-black/60 backdrop-blur-sm"
            onClick={() => setRailOpen(false)}
          >
            <aside
              className="absolute top-14 left-0 bottom-0 w-[85vw] max-w-[340px] border-r border-[#21262d] bg-[#0d1117] flex flex-col"
              onClick={(e) => e.stopPropagation()}
            >
              <div className="flex items-center justify-between px-4 h-10 border-b border-[#21262d]">
                <span className="text-[10px] uppercase tracking-[0.12em] text-[#667085]">
                  {formatName(name)}
                </span>
                <button
                  onClick={() => setRailOpen(false)}
                  className="text-[#8b949e] hover:text-[#e6edf3]"
                  aria-label="Close"
                >
                  <X size={14} />
                </button>
              </div>
              <InfoRail
                tab={tab}
                setTab={setTab}
                agentColor={agentColor}
                profileLead={profileLead}
                profileBody={profileBody}
                memories={memories}
                expandedMemory={expandedMemory}
                memoryContents={memoryContents}
                toggleMemory={toggleMemory}
                agentSlug={name}
                chatSessions={chatSessions}
                activeChatSessionId={activeChatSessionId}
                onSelectChatSession={selectChatSession}
                onNewChatSession={createNewChatSession}
                approvalCounts={approvalCountsRef.current}
                onRename={renameChatSession}
                onArchive={archiveChatSession}
                onDelete={deleteChatSession}
              />
            </aside>
          </div>
        )}

        {/* Terminal / Chat stage */}
        <section className="flex-1 min-w-0 relative bg-[#0C111D] overflow-hidden flex flex-col">
          {/* Ambient glow */}
          <div
            className="pointer-events-none absolute top-0 right-0 h-[400px] w-[400px] blur-3xl"
            style={{
              background: `radial-gradient(circle, ${agentColor} 0%, transparent 60%)`,
              opacity: 0.06,
            }}
          />

          {/* View mode bar + terminal tabs */}
          <div className="relative z-10 flex items-center flex-shrink-0 h-9 border-b border-[#21262d] bg-[#0d1117]">
            {/* View mode toggle */}
            <div className="flex items-center border-r border-[#21262d] h-full">
              <button
                onClick={() => { setViewMode('chat'); localStorage.setItem('evo:agent-view-mode', 'chat') }}
                className={`flex items-center gap-1.5 px-3 h-full text-[11px] transition-colors ${
                  viewMode === 'chat'
                    ? 'text-[#e6edf3] bg-[#0C111D]'
                    : 'text-[#667085] hover:text-[#e6edf3] hover:bg-[#161b22]'
                }`}
              >
                <MessageSquare size={12} style={{ color: viewMode === 'chat' ? agentColor : undefined }} />
                Chat
              </button>
              <button
                onClick={() => { setViewMode('terminal'); localStorage.setItem('evo:agent-view-mode', 'terminal') }}
                className={`flex items-center gap-1.5 px-3 h-full text-[11px] transition-colors ${
                  viewMode === 'terminal'
                    ? 'text-[#e6edf3] bg-[#0C111D]'
                    : 'text-[#667085] hover:text-[#e6edf3] hover:bg-[#161b22]'
                }`}
              >
                <TerminalIcon size={12} style={{ color: viewMode === 'terminal' ? agentColor : undefined }} />
                Terminal
              </button>
            </div>

            {/* Terminal tabs — only in terminal mode with multiple tabs */}
            {viewMode === 'terminal' && (
              <div className="flex items-center flex-1 h-full overflow-x-auto">
                {termTabs.length > 1 && termTabs.map((tt) => (
                  <div
                    key={tt.id}
                    className={`group flex items-center gap-2 px-3 h-full text-[11px] cursor-pointer border-r border-[#21262d] transition-colors ${
                      activeTermTab === tt.id
                        ? 'bg-[#0C111D] text-[#e6edf3]'
                        : 'text-[#8b949e] hover:text-[#e6edf3] hover:bg-[#161b22]'
                    }`}
                    onClick={() => setActiveTermTab(tt.id)}
                  >
                    <TerminalIcon size={11} style={{ color: activeTermTab === tt.id ? agentColor : undefined }} />
                    <span className="truncate max-w-[120px]">{tt.name}</span>
                    {tt.active && (
                      <span
                        className="inline-block h-1.5 w-1.5 rounded-full flex-shrink-0"
                        style={{ backgroundColor: agentColor, boxShadow: `0 0 4px ${agentColor}88` }}
                      />
                    )}
                    <button
                      onClick={(e) => { e.stopPropagation(); closeTerminalTab(tt.id) }}
                      className="opacity-0 group-hover:opacity-100 text-[#667085] hover:text-[#ef4444] transition-opacity"
                    >
                      <X size={11} />
                    </button>
                  </div>
                ))}
                <button
                  onClick={createNewTerminal}
                  className="flex items-center justify-center h-full px-2.5 text-[#667085] hover:text-[#e6edf3] hover:bg-[#161b22] transition-colors"
                  title="New terminal"
                >
                  <Plus size={13} />
                </button>
              </div>
            )}
          </div>

          {/* Content */}
          <div className="relative z-10 flex-1 min-h-0">
            {viewMode === 'chat' ? (
              <AgentChat
                key={`chat-${name}-${activeChatSessionId || 'default'}`}
                agent={name}
                sessionId={activeChatSessionId || undefined}
                accentColor={agentColor}
                externalLoading={chatConnecting}
                externalError={chatConnectError}
                onPendingCountChange={handlePendingCountChange}
                onNeedsAttention={handleNeedsAttention}
              />
            ) : (
              termTabs.length === 0 || activeTermTab === null ? (
                <AgentTerminal key={`default-${name}`} agent={name} accentColor={agentColor} />
              ) : (
                <AgentTerminal key={activeTermTab} agent={name} sessionId={activeTermTab} accentColor={agentColor} />
              )
            )}
          </div>
        </section>
      </div>
    </div>
  )
}

// ─── InfoRail ─────────────────────────────────────────────────────────

interface InfoRailProps {
  tab: Tab
  setTab: (t: Tab) => void
  agentColor: string
  profileLead: string | null
  profileBody: string
  memories: MemoryFile[]
  expandedMemory: string | null
  memoryContents: Record<string, string>
  toggleMemory: (name: string) => void
  agentSlug: string
  chatSessions: ChatSession[]
  activeChatSessionId: string | null
  onSelectChatSession: (id: string) => void
  onNewChatSession: () => void
  approvalCounts: Map<string, number>
  onRename: (id: string, newName: string) => void
  onArchive: (id: string, archived: boolean) => void
  onDelete: (id: string) => void
}

function InfoRail({
  tab,
  setTab,
  agentColor,
  profileLead,
  profileBody,
  memories,
  expandedMemory,
  memoryContents,
  toggleMemory,
  agentSlug,
  chatSessions,
  activeChatSessionId,
  onSelectChatSession,
  onNewChatSession,
  approvalCounts,
  onRename,
  onArchive,
  onDelete,
}: InfoRailProps) {
  return (
    <>
      {/* Tab bar */}
      <div className="flex-shrink-0 flex items-center h-10 px-5 gap-6 border-b border-[#21262d]">
        <TabButton
          label="Sessions"
          active={tab === 'sessions'}
          onClick={() => setTab('sessions')}
          color={agentColor}
          count={chatSessions.length}
        />
        <TabButton label="Profile" active={tab === 'profile'} onClick={() => setTab('profile')} color={agentColor} />
        <TabButton
          label="Memory"
          active={tab === 'memory'}
          onClick={() => setTab('memory')}
          color={agentColor}
          count={memories.length}
        />
      </div>

      {/* Content */}
      <div className="flex-1 min-h-0 overflow-y-auto">
        {tab === 'sessions' && (
          <ChatSessionList
            sessions={chatSessions}
            activeSessionId={activeChatSessionId}
            onSelectSession={onSelectChatSession}
            onNewSession={onNewChatSession}
            accentColor={agentColor}
            approvalCounts={approvalCounts}
            onRename={onRename}
            onArchive={onArchive}
            onDelete={onDelete}
          />
        )}

        {tab === 'profile' && (
          <div className="px-5 py-4">
            {profileLead && (
              <p className="text-[13px] leading-[1.6] text-[#e6edf3] mb-4 pb-4 border-b border-[#21262d]">
                {profileLead}
              </p>
            )}
            <div
              className="prose-agent text-[12.5px] leading-[1.65] text-[#8b949e]"
              style={
                {
                  ['--agent-color' as string]: agentColor,
                } as React.CSSProperties
              }
            >
              <Markdown>{profileBody}</Markdown>
            </div>
          </div>
        )}

        {tab === 'memory' && (
          <div className="px-5 py-4">
            {memories.length === 0 ? (
              <div className="text-[12px] text-[#667085]">
                <p className="mb-1">Sem memórias ainda.</p>
                <p className="text-[11px] text-[#3F3F46]">
                  Adicione arquivos em{' '}
                  <code className="font-mono text-[#667085]">.claude/agent-memory/{agentSlug}/</code>
                </p>
              </div>
            ) : (
              <ul className="space-y-0.5">
                {memories.map((mem) => {
                  const open = expandedMemory === mem.name
                  return (
                    <li key={mem.name}>
                      <button
                        onClick={() => toggleMemory(mem.name)}
                        className="w-full flex items-center gap-2 py-1.5 px-2 rounded-md hover:bg-[#161b22] text-left transition-colors"
                      >
                        {open ? (
                          <ChevronDown size={11} className="text-[#667085] flex-shrink-0" />
                        ) : (
                          <ChevronRight size={11} className="text-[#3F3F46] flex-shrink-0" />
                        )}
                        <span className="font-mono text-[11.5px] text-[#e6edf3] truncate">
                          {mem.name}
                        </span>
                        <span className="ml-auto font-mono text-[10px] text-[#667085] flex-shrink-0">
                          {formatSize(mem.size)}
                        </span>
                      </button>
                      {open && (
                        <div
                          className="ml-4 mt-1 mb-2 pl-4 py-1 border-l text-[11.5px] leading-[1.6] text-[#8b949e] overflow-hidden"
                          style={{ borderColor: `${agentColor}40` }}
                        >
                          <Markdown>{memoryContents[mem.name] || 'Loading...'}</Markdown>
                        </div>
                      )}
                    </li>
                  )
                })}
              </ul>
            )}
          </div>
        )}
      </div>
    </>
  )
}

function TabButton({
  label,
  active,
  onClick,
  color,
  count,
}: {
  label: string
  active: boolean
  onClick: () => void
  color: string
  count?: number
}) {
  return (
    <button
      onClick={onClick}
      className="relative h-10 flex items-center gap-2 text-[10.5px] uppercase tracking-[0.14em] font-medium transition-colors"
      style={{ color: active ? '#e6edf3' : '#667085' }}
    >
      {label}
      {count !== undefined && (
        <span className="font-mono text-[10px] text-[#3F3F46]">{count}</span>
      )}
      {active && (
        <span
          className="absolute bottom-0 left-0 right-0 h-[2px]"
          style={{ backgroundColor: color }}
        />
      )}
    </button>
  )
}

// ─── Helpers ─────────────────────────────────────────────────────────

// Pull the first substantive paragraph (after any YAML frontmatter & H1)
// to act as the "lead" intro above the main markdown body.
function extractProfileLead(md: string): string | null {
  const stripped = md.replace(/^---[\s\S]*?---\s*/m, '')
  const lines = stripped.split('\n')
  for (const line of lines) {
    const t = line.trim()
    if (!t) continue
    if (t.startsWith('#')) continue
    if (t.startsWith('```')) return null
    // Skip markdown list/quote markers
    if (/^[-*>]\s/.test(t)) continue
    return t.length > 300 ? t.slice(0, 297) + '…' : t
  }
  return null
}

function extractProfileBody(md: string): string {
  return md.replace(/^---[\s\S]*?---\s*/m, '')
}
