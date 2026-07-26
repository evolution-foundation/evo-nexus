import { MessageSquare } from 'lucide-react'
import { useFloatingChat } from '../context/FloatingChatContext'
import FloatingChatPanel from './FloatingChatPanel'
import FloatingChatWindow from './FloatingChatWindow'

export default function FloatingChatFab() {
  const { windows, panelOpen, setPanelOpen } = useFloatingChat()

  const totalApprovals = windows.reduce((sum, w) => sum + w.pendingApprovals, 0)
  const accentColor = totalApprovals > 0 ? '#F59E0B' : '#00FFA7'

  // Show up to 3 non-minimized windows; rest accessible from panel
  const visibleWindows = windows.slice(0, 3)
  const GAP = 12

  // Each window is anchored to the right; offset accumulates real widths to its right.
  let acc = GAP
  const offsets = visibleWindows.map(win => {
    const right = acc
    acc += win.width + GAP
    return right
  })

  return (
    <>
      {/* Floating windows */}
      {visibleWindows.map((win, idx) => (
        <FloatingChatWindow key={win.id} window={win} rightOffset={offsets[idx]} />
      ))}

      {/* Panel */}
      {panelOpen && <FloatingChatPanel />}

      {/* FAB button */}
      <button
        onClick={() => setPanelOpen(!panelOpen)}
        className="fixed z-[200] bottom-6 right-6 w-12 h-12 rounded-full flex items-center justify-center shadow-lg transition-all duration-200 hover:scale-105 active:scale-95"
        style={{
          background: `${accentColor}20`,
          border: `1.5px solid ${accentColor}60`,
          boxShadow: `0 4px 20px ${accentColor}30`,
        }}
        title="Chats"
        aria-label="Abrir chats"
      >
        <MessageSquare size={20} style={{ color: accentColor }} />

        {/* Badge */}
        {totalApprovals > 0 && (
          <span
            className="absolute -top-1 -right-1 min-w-[18px] h-[18px] rounded-full text-[10px] font-bold flex items-center justify-center px-1 animate-pulse"
            style={{ background: '#F59E0B', color: '#0C111D' }}
          >
            {totalApprovals > 9 ? '9+' : totalApprovals}
          </span>
        )}

        {/* Open-sessions dot (when windows exist but no approvals) */}
        {windows.length > 0 && totalApprovals === 0 && (
          <span
            className="absolute -top-0.5 -right-0.5 w-2.5 h-2.5 rounded-full"
            style={{ background: '#00FFA7', border: '2px solid #0C111D' }}
          />
        )}
      </button>
    </>
  )
}
