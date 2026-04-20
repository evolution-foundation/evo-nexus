import { useEffect, useState, useRef } from 'react'
import { ChevronDown, Circle, AlertTriangle, XCircle, Wifi } from 'lucide-react'
import { useKnowledge, type KnowledgeConnection } from '../../context/KnowledgeContext'

function StatusIcon({ status }: { status: KnowledgeConnection['status'] }) {
  if (status === 'ready')
    return <Circle size={8} className="fill-[#00FFA7] text-[#00FFA7]" />
  if (status === 'needs_migration')
    return <AlertTriangle size={10} className="text-yellow-400" />
  if (status === 'error')
    return <XCircle size={10} className="text-red-400" />
  return <Wifi size={10} className="text-[#667085]" />
}

export default function ConnectionSwitcher() {
  const { activeConnectionId, setActiveConnectionId, connections, refreshConnections } = useKnowledge()
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    refreshConnections()
  }, [refreshConnections])

  useEffect(() => {
    function handler(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  const active = connections.find((c) => c.id === activeConnectionId)

  if (connections.length === 0) {
    return (
      <div className="flex items-center gap-2 px-3 py-1.5 bg-[#182230] border border-[#344054] rounded-lg text-xs text-[#667085]">
        <Wifi size={12} />
        No connections — add one below
      </div>
    )
  }

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-2 px-3 py-1.5 bg-[#182230] border border-[#344054] rounded-lg text-sm font-medium text-[#D0D5DD] hover:border-[#00FFA7]/50 transition-colors min-w-[180px]"
      >
        {active ? (
          <>
            <StatusIcon status={active.status} />
            <span className="flex-1 text-left truncate">{active.name}</span>
          </>
        ) : (
          <span className="flex-1 text-left text-[#667085]">Select connection</span>
        )}
        <ChevronDown size={12} className={`transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>

      {open && (
        <div className="absolute top-full mt-1 left-0 right-0 bg-[#0C111D] border border-[#344054] rounded-lg shadow-lg z-50 py-1 min-w-[220px]">
          {connections.map((c) => (
            <button
              key={c.id}
              onClick={() => { setActiveConnectionId(c.id); setOpen(false) }}
              className={`w-full flex items-center gap-2 px-3 py-2 text-sm transition-colors hover:bg-white/5 ${
                c.id === activeConnectionId ? 'text-[#00FFA7]' : 'text-[#D0D5DD]'
              }`}
            >
              <StatusIcon status={c.status} />
              <span className="flex-1 text-left truncate">{c.name}</span>
              {c.id === activeConnectionId && (
                <span className="text-[10px] text-[#00FFA7]">active</span>
              )}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
