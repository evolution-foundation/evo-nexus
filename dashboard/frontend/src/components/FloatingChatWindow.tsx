import { useCallback, useRef } from 'react'
import { Terminal as TerminalIcon, MessageSquare, Minus, X } from 'lucide-react'
import { AgentAvatar } from './AgentAvatar'
import { getAgentMeta } from '../lib/agent-meta'
import { useFloatingChat, type FloatWindow } from '../context/FloatingChatContext'
import AgentTerminal from './AgentTerminal'
import AgentChat from './AgentChat'

interface FloatingChatWindowProps {
  window: FloatWindow
  /** Horizontal offset from the right edge (px), computed by the FAB stack. */
  rightOffset: number
}

const WINDOW_HEIGHT = 480
const MIN_WIDTH = 320
const MAX_WIDTH = 900
const FAB_BOTTOM = 80 // height of FAB area (so windows stack above it)

export default function FloatingChatWindow({ window: win, rightOffset }: FloatingChatWindowProps) {
  const { closeWindow, toggleMinimize, toggleViewMode, updateApprovals, setWindowWidth } = useFloatingChat()
  const meta = getAgentMeta(win.id)
  const accentColor = meta.color || '#00FFA7'

  // When minimized, only show header (48px)
  const height = win.minimized ? 48 : WINDOW_HEIGHT

  const handlePendingCount = useCallback((_sid: string, count: number) => {
    updateApprovals(win.id, count)
  }, [win.id, updateApprovals])

  // Drag the left border to resize (window is anchored to the right).
  const dragRef = useRef<{ startX: number; startWidth: number } | null>(null)
  const startResize = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    e.stopPropagation()
    dragRef.current = { startX: e.clientX, startWidth: win.width }
    document.body.style.userSelect = 'none'
    document.body.style.cursor = 'ew-resize'

    const onMove = (ev: MouseEvent) => {
      if (!dragRef.current) return
      const delta = dragRef.current.startX - ev.clientX // drag left → wider
      const next = Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, dragRef.current.startWidth + delta))
      setWindowWidth(win.id, next)
    }
    const onUp = () => {
      dragRef.current = null
      document.body.style.userSelect = ''
      document.body.style.cursor = ''
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
  }, [win.id, win.width, setWindowWidth])

  return (
    <div
      className="fixed z-[150] flex flex-col rounded-xl border shadow-2xl overflow-hidden transition-[height] duration-200"
      style={{
        bottom: FAB_BOTTOM,
        right: rightOffset,
        width: win.width,
        height,
        background: '#0C111D',
        borderColor: `${accentColor}30`,
        boxShadow: `0 8px 32px rgba(0,0,0,0.6), 0 0 0 1px ${accentColor}20`,
      }}
    >
      {/* Resize handle — left border */}
      {!win.minimized && (
        <div
          onMouseDown={startResize}
          className="absolute left-0 top-0 bottom-0 w-1.5 cursor-ew-resize z-10 hover:bg-white/10"
          title="Arraste para redimensionar"
        />
      )}
      {/* Header */}
      <div
        className="flex items-center gap-2 px-3 py-2 flex-shrink-0 select-none cursor-pointer"
        style={{ background: '#161b22', borderBottom: win.minimized ? 'none' : `1px solid ${accentColor}20` }}
        onClick={() => toggleMinimize(win.id)}
      >
        {/* Avatar */}
        <AgentAvatar name={win.id} size={22} />

        {/* Name */}
        <span className="text-xs font-semibold text-[#e6edf3] truncate flex-1">@{win.id}</span>

        {/* Approval badge */}
        {win.pendingApprovals > 0 && (
          <span
            className="flex-shrink-0 text-[10px] font-medium px-1.5 py-0.5 rounded-full animate-pulse"
            style={{ background: '#F59E0B20', color: '#F59E0B', border: '1px solid #F59E0B40' }}
          >
            {win.pendingApprovals}
          </span>
        )}

        {/* Toggle view mode — stop propagation so it doesn't also minimize */}
        <button
          onClick={(e) => { e.stopPropagation(); toggleViewMode(win.id) }}
          className="flex-shrink-0 flex items-center justify-center w-6 h-6 rounded-md text-[#667085] hover:text-[#e6edf3] hover:bg-white/10 transition-colors"
          title={win.viewMode === 'terminal' ? 'Mudar para chat' : 'Mudar para terminal'}
        >
          {win.viewMode === 'terminal'
            ? <MessageSquare size={13} />
            : <TerminalIcon size={13} />
          }
        </button>

        {/* Minimize */}
        <button
          onClick={(e) => { e.stopPropagation(); toggleMinimize(win.id) }}
          className="flex-shrink-0 flex items-center justify-center w-6 h-6 rounded-md text-[#667085] hover:text-[#e6edf3] hover:bg-white/10 transition-colors"
          title={win.minimized ? 'Expandir' : 'Minimizar'}
        >
          <Minus size={13} />
        </button>

        {/* Close */}
        <button
          onClick={(e) => { e.stopPropagation(); closeWindow(win.id) }}
          className="flex-shrink-0 flex items-center justify-center w-6 h-6 rounded-md text-[#667085] hover:text-red-400 hover:bg-red-400/10 transition-colors"
          title="Fechar"
        >
          <X size={13} />
        </button>
      </div>

      {/* Body — hidden when minimized */}
      {!win.minimized && (
        <div className="flex-1 min-h-0 overflow-hidden">
          {win.viewMode === 'terminal' ? (
            <AgentTerminal
              agent={win.id}
              sessionId={win.sessionId}
              accentColor={accentColor}
            />
          ) : (
            <AgentChat
              agent={win.id}
              sessionId={win.sessionId}
              accentColor={accentColor}
              onPendingCountChange={handlePendingCount}
            />
          )}
        </div>
      )}
    </div>
  )
}
