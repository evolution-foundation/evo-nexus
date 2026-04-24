import { useEffect, useState, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { Plus, ChevronRight, ChevronDown, PanelLeftClose, PanelLeftOpen } from 'lucide-react'
import { api } from '../lib/api'
import { getAgentMeta } from '../lib/agent-meta'
import { AgentIcon } from './AgentIcon'

// ── Types ────────────────────────────────────────────────────────────────────

interface ThreadTicket {
  id: string
  title: string
  assignee_agent: string | null
  status: string
  updated_at: string
}

interface AgentGroup {
  agent: string
  threads: ThreadTicket[]
}

// ── Relative time util ───────────────────────────────────────────────────────

function relativeTime(iso: string): string {
  const now = Date.now()
  const then = new Date(iso).getTime()
  const diffMs = now - then
  if (diffMs < 0) return 'agora'
  const diffMin = Math.floor(diffMs / 60_000)
  if (diffMin < 1) return 'agora'
  if (diffMin < 60) return `há ${diffMin}min`
  const diffH = Math.floor(diffMin / 60)
  if (diffH < 24) return `há ${diffH}h`
  const diffD = Math.floor(diffH / 24)
  if (diffD === 1) return 'ontem'
  if (diffD < 7) return `${diffD}d`
  const diffW = Math.floor(diffD / 7)
  return `${diffW}sem`
}

// ── Thread item ──────────────────────────────────────────────────────────────

function ThreadItem({
  thread,
  isActive,
  onSelect,
}: {
  thread: ThreadTicket
  isActive: boolean
  onSelect: (id: string) => void
}) {
  return (
    <button
      type="button"
      onClick={() => onSelect(thread.id)}
      className={`
        w-full text-left px-3 py-2 flex flex-col gap-0.5 transition-colors
        ${isActive
          ? 'border-l-2 border-[#00FFA7] bg-[#00FFA7]/5'
          : 'border-l-2 border-transparent hover:bg-white/[0.03]'}
      `}
    >
      <span
        className="text-[12px] leading-[1.35] text-[#e6edf3] overflow-hidden whitespace-nowrap text-ellipsis w-full"
        style={{ display: 'block' }}
        title={thread.title}
      >
        {thread.title}
      </span>
      <span className="text-[11px] text-white/40 tabular-nums">
        {relativeTime(thread.updated_at)}
      </span>
    </button>
  )
}

// ── Agent group accordion ─────────────────────────────────────────────────────

function AgentGroup({
  group,
  activeTicketId,
  defaultExpanded,
  onSelect,
}: {
  group: AgentGroup
  activeTicketId: string
  defaultExpanded: boolean
  onSelect: (id: string) => void
}) {
  const [expanded, setExpanded] = useState(defaultExpanded)
  const meta = getAgentMeta(group.agent)
  const label = meta.label !== 'Agent' ? meta.label : group.agent.split('-')[0]

  return (
    <div>
      <button
        type="button"
        onClick={() => setExpanded(e => !e)}
        className="w-full flex items-center gap-2 px-3 py-1.5 hover:bg-white/[0.03] transition-colors group"
      >
        <AgentIcon agent={group.agent} size={20} />
        <span className="flex-1 text-left text-[11px] font-medium text-[#8b949e] group-hover:text-[#e6edf3] transition-colors truncate capitalize">
          {label}
        </span>
        <span className="text-[10px] text-white/30 tabular-nums mr-1">
          {group.threads.length}
        </span>
        {expanded
          ? <ChevronDown size={12} className="text-white/30 flex-shrink-0" />
          : <ChevronRight size={12} className="text-white/30 flex-shrink-0" />}
      </button>

      {expanded && (
        <div className="ml-1">
          {group.threads.map(t => (
            <ThreadItem
              key={t.id}
              thread={t}
              isActive={t.id === activeTicketId}
              onSelect={onSelect}
            />
          ))}
        </div>
      )}
    </div>
  )
}

// ── Skeleton loader ───────────────────────────────────────────────────────────

function SkeletonList() {
  return (
    <div className="px-3 pt-2 space-y-3 animate-pulse">
      {[72, 56, 80, 64].map((w, i) => (
        <div key={i} className="space-y-1.5">
          <div className="h-2.5 bg-white/5 rounded" style={{ width: `${w}%` }} />
          <div className="h-2 bg-white/[0.03] rounded w-1/3" />
        </div>
      ))}
    </div>
  )
}

// ── Collapsed icon list ───────────────────────────────────────────────────────

function CollapsedAgentList({
  groups,
  activeTicketId,
}: {
  groups: AgentGroup[]
  activeTicketId: string
}) {
  return (
    <div className="flex flex-col items-center gap-1 pt-2 px-2">
      {groups.map(group => {
        const hasActive = group.threads.some(t => t.id === activeTicketId)
        return (
          <div
            key={group.agent}
            title={getAgentMeta(group.agent).label}
            className={`
              relative rounded p-0.5
              ${hasActive ? 'ring-1 ring-[#00FFA7]/50' : ''}
            `}
          >
            <AgentIcon agent={group.agent} size={28} />
            <span
              className="absolute -top-1 -right-1 text-[9px] tabular-nums bg-[#161b22] border border-[#30363d] text-[#8b949e] rounded-full w-4 h-4 flex items-center justify-center"
            >
              {group.threads.length}
            </span>
          </div>
        )
      })}
    </div>
  )
}

// ── Props ─────────────────────────────────────────────────────────────────────

interface ThreadsSidebarProps {
  activeTicketId: string
  collapsed: boolean
  onToggleCollapse: () => void
  /** When true, strips the `hidden md:flex` and fixed-width so the parent
   *  drawer controls visibility and sizing. Desktop behaviour unchanged. */
  asDrawer?: boolean
}

// ── Main component ────────────────────────────────────────────────────────────

export default function ThreadsSidebar({
  activeTicketId,
  collapsed,
  onToggleCollapse,
  asDrawer = false,
}: ThreadsSidebarProps) {
  const navigate = useNavigate()
  const [threads, setThreads] = useState<ThreadTicket[]>([])
  const [loading, setLoading] = useState(true)
  const hasFetched = useRef(false)

  useEffect(() => {
    if (hasFetched.current) return
    hasFetched.current = true

    api.get('/tickets?display_mode=threads&limit=200')
      .then((res: { tickets: ThreadTicket[] }) => {
        setThreads(res.tickets || [])
      })
      .catch(() => {
        setThreads([])
      })
      .finally(() => setLoading(false))
  }, [])

  const handleSelect = (id: string) => {
    navigate(`/tickets/${id}`)
  }

  // Split active vs archived
  const activeThreads = threads.filter(t => t.status !== 'archived')
  const archivedThreads = threads.filter(t => t.status === 'archived')

  // Group active threads by agent
  const groupsMap = new Map<string, ThreadTicket[]>()
  for (const t of activeThreads) {
    const key = t.assignee_agent || '__unassigned__'
    if (!groupsMap.has(key)) groupsMap.set(key, [])
    groupsMap.get(key)!.push(t)
  }
  const activeGroups: AgentGroup[] = Array.from(groupsMap.entries()).map(
    ([agent, thr]) => ({ agent, threads: thr })
  )

  // Group archived threads by agent
  const archivedMap = new Map<string, ThreadTicket[]>()
  for (const t of archivedThreads) {
    const key = t.assignee_agent || '__unassigned__'
    if (!archivedMap.has(key)) archivedMap.set(key, [])
    archivedMap.get(key)!.push(t)
  }
  const archivedGroups: AgentGroup[] = Array.from(archivedMap.entries()).map(
    ([agent, thr]) => ({ agent, threads: thr })
  )

  const onlyCurrentThread =
    activeThreads.length <= 1 && archivedThreads.length === 0

  // ── Collapsed mode ──
  if (collapsed) {
    return (
      <div
        className={`${asDrawer ? 'flex' : 'hidden md:flex'} flex-col shrink-0 border-r border-[#21262d] bg-[#0a0e14]`}
        style={asDrawer ? undefined : { width: 48, transition: 'width 200ms ease-out' }}
      >
        {/* Toggle */}
        <div className="flex items-center justify-center h-10 border-b border-[#21262d]">
          <button
            type="button"
            onClick={onToggleCollapse}
            className="p-1.5 text-[#667085] hover:text-[#e6edf3] transition-colors"
            aria-label="Expand sidebar"
          >
            <PanelLeftOpen size={14} />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto overflow-x-hidden scrollbar-thin">
          <CollapsedAgentList
            groups={[...activeGroups, ...archivedGroups]}
            activeTicketId={activeTicketId}
          />
        </div>
      </div>
    )
  }

  // ── Expanded mode ──
  return (
    <div
      className={`${asDrawer ? 'flex' : 'hidden md:flex'} flex-col shrink-0 border-r border-[#21262d] bg-[#0a0e14]`}
      style={asDrawer ? undefined : { width: 280, transition: 'width 200ms ease-out' }}
    >
      {/* Header */}
      <div className="flex items-center justify-between px-3 h-10 border-b border-[#21262d] shrink-0">
        <span className="text-[11px] font-semibold text-[#8b949e] uppercase tracking-wider">
          Threads
        </span>
        <div className="flex items-center gap-1">
          <button
            type="button"
            disabled
            aria-label="New thread"
            className="p-1 text-[#667085] opacity-40 cursor-not-allowed"
            title="Nova thread (em breve)"
          >
            <Plus size={13} />
          </button>
          <button
            type="button"
            onClick={onToggleCollapse}
            className="p-1 text-[#667085] hover:text-[#e6edf3] transition-colors"
            aria-label="Collapse sidebar"
          >
            <PanelLeftClose size={13} />
          </button>
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto overflow-x-hidden scrollbar-thin">
        {loading ? (
          <SkeletonList />
        ) : onlyCurrentThread ? (
          <p className="px-4 py-6 text-[11px] text-white/30 leading-relaxed">
            Só esta thread por enquanto.
          </p>
        ) : (
          <>
            {/* Active groups */}
            <div className="pt-1">
              {activeGroups.map(group => (
                <AgentGroup
                  key={group.agent}
                  group={group}
                  activeTicketId={activeTicketId}
                  defaultExpanded
                  onSelect={handleSelect}
                />
              ))}
            </div>

            {/* Archived section */}
            {archivedGroups.length > 0 && (
              <ArchivedSection
                groups={archivedGroups}
                activeTicketId={activeTicketId}
                onSelect={handleSelect}
              />
            )}
          </>
        )}
      </div>
    </div>
  )
}

// ── Archived section (collapsed by default) ───────────────────────────────────

function ArchivedSection({
  groups,
  activeTicketId,
  onSelect,
}: {
  groups: AgentGroup[]
  activeTicketId: string
  onSelect: (id: string) => void
}) {
  const [expanded, setExpanded] = useState(false)
  const total = groups.reduce((acc, g) => acc + g.threads.length, 0)

  return (
    <div className="mt-2 border-t border-[#21262d]">
      <button
        type="button"
        onClick={() => setExpanded(e => !e)}
        className="w-full flex items-center gap-2 px-3 py-2 hover:bg-white/[0.03] transition-colors"
      >
        <span className="flex-1 text-left text-[11px] font-medium text-[#f59e0b]/70">
          Arquivadas
        </span>
        <span className="text-[10px] text-white/30 tabular-nums mr-1">{total}</span>
        {expanded
          ? <ChevronDown size={12} className="text-white/30" />
          : <ChevronRight size={12} className="text-white/30" />}
      </button>

      {expanded && (
        <div>
          {groups.map(group => (
            <AgentGroup
              key={group.agent}
              group={group}
              activeTicketId={activeTicketId}
              defaultExpanded={false}
              onSelect={onSelect}
            />
          ))}
        </div>
      )}
    </div>
  )
}
