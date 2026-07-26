import { createContext, useContext, useState, useCallback, useEffect, type ReactNode } from 'react'
import { TS_HTTP } from '../lib/terminal-url'

export interface FloatWindow {
  id: string           // agent slug (unique key)
  sessionId: string    // terminal-server session id
  minimized: boolean
  viewMode: 'terminal' | 'chat'
  pendingApprovals: number
  width: number        // resizable window width (px)
}

const DEFAULT_WIDTH = 380
const MIN_WIDTH = 320
const MAX_WIDTH = 900

interface FloatingChatState {
  windows: FloatWindow[]
  panelOpen: boolean
  openWindow: (agent: string, sessionId?: string) => Promise<void>
  closeWindow: (agent: string) => void
  toggleMinimize: (agent: string) => void
  toggleViewMode: (agent: string) => void
  updateApprovals: (agent: string, count: number) => void
  setWindowWidth: (agent: string, width: number) => void
  setPanelOpen: (v: boolean) => void
}

const FloatingChatContext = createContext<FloatingChatState | null>(null)

const STORAGE_KEY = 'evo:float-windows'

function persistWindows(windows: FloatWindow[]) {
  try {
    const slim = windows.map(w => ({ id: w.id, sessionId: w.sessionId, minimized: w.minimized }))
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(slim))
  } catch {}
}

function loadWindows(): Array<{ id: string; sessionId: string; minimized: boolean }> {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY)
    if (!raw) return []
    return JSON.parse(raw)
  } catch { return [] }
}

function loadWidth(agent: string): number {
  try {
    const raw = localStorage.getItem(`evo:float-width-${agent}`)
    const n = raw ? parseInt(raw, 10) : NaN
    if (!Number.isNaN(n)) return Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, n))
  } catch {}
  return DEFAULT_WIDTH
}

async function getOrCreateSession(agent: string, existingSessionId?: string): Promise<string> {
  const storageKey = `evo:float-session-${agent}`

  // 1. Use explicitly passed sessionId (e.g. continuing an external session)
  //    and persist it as the agent's current session.
  if (existingSessionId) {
    try { sessionStorage.setItem(storageKey, existingSessionId) } catch {}
    return existingSessionId
  }

  // 2. Check sessionStorage for a previously created session
  const stored = sessionStorage.getItem(storageKey)
  if (stored) return stored

  // 3. Create new session
  try {
    const res = await fetch(`${TS_HTTP}/api/sessions/create`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ agentName: agent }),
    })
    if (res.ok) {
      const data = await res.json()
      const sessionId: string = data.sessionId
      sessionStorage.setItem(storageKey, sessionId)
      return sessionId
    }
  } catch {}

  // Fallback: generate local id (terminal will create session on connect)
  const fallback = `float-${agent}-${Math.random().toString(36).slice(2, 8)}`
  sessionStorage.setItem(storageKey, fallback)
  return fallback
}

export function FloatingChatProvider({ children }: { children: ReactNode }) {
  const [windows, setWindowsState] = useState<FloatWindow[]>([])
  const [panelOpen, setPanelOpen] = useState(false)

  // Restore windows from sessionStorage on mount
  useEffect(() => {
    const saved = loadWindows()
    if (saved.length > 0) {
      const restored: FloatWindow[] = saved.map(w => ({
        id: w.id,
        sessionId: w.sessionId,
        minimized: w.minimized,
        viewMode: (() => {
          try { return (localStorage.getItem(`evo:float-view-${w.id}`) as 'terminal' | 'chat') || 'terminal' } catch { return 'terminal' }
        })(),
        pendingApprovals: 0,
        width: loadWidth(w.id),
      }))
      setWindowsState(restored)
    }
  }, [])

  const setWindows = useCallback((updater: FloatWindow[] | ((prev: FloatWindow[]) => FloatWindow[])) => {
    setWindowsState(prev => {
      const next = typeof updater === 'function' ? updater(prev) : updater
      persistWindows(next)
      return next
    })
  }, [])

  const openWindow = useCallback(async (agent: string, sessionId?: string) => {
    // If already open: un-minimize, and switch to an explicit external session if one was passed.
    const existing = windows.find(w => w.id === agent)
    if (existing) {
      const sid = sessionId && sessionId !== existing.sessionId
        ? await getOrCreateSession(agent, sessionId)
        : existing.sessionId
      setWindows(prev => prev.map(w => w.id === agent ? { ...w, minimized: false, sessionId: sid } : w))
      setPanelOpen(false)
      return
    }

    const sid = await getOrCreateSession(agent, sessionId)
    const viewMode: 'terminal' | 'chat' = (() => {
      try { return (localStorage.getItem(`evo:float-view-${agent}`) as 'terminal' | 'chat') || 'terminal' } catch { return 'terminal' }
    })()

    setWindows(prev => [
      ...prev,
      { id: agent, sessionId: sid, minimized: false, viewMode, pendingApprovals: 0, width: loadWidth(agent) },
    ])
    setPanelOpen(false)
  }, [windows, setWindows])

  const closeWindow = useCallback((agent: string) => {
    setWindows(prev => prev.filter(w => w.id !== agent))
  }, [setWindows])

  const toggleMinimize = useCallback((agent: string) => {
    setWindows(prev => prev.map(w => w.id === agent ? { ...w, minimized: !w.minimized } : w))
  }, [setWindows])

  const toggleViewMode = useCallback((agent: string) => {
    setWindows(prev => prev.map(w => {
      if (w.id !== agent) return w
      const next: 'terminal' | 'chat' = w.viewMode === 'terminal' ? 'chat' : 'terminal'
      try { localStorage.setItem(`evo:float-view-${agent}`, next) } catch {}
      return { ...w, viewMode: next }
    }))
  }, [setWindows])

  const updateApprovals = useCallback((agent: string, count: number) => {
    setWindowsState(prev => prev.map(w => w.id === agent ? { ...w, pendingApprovals: count } : w))
  }, [])

  const setWindowWidth = useCallback((agent: string, width: number) => {
    const clamped = Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, Math.round(width)))
    try { localStorage.setItem(`evo:float-width-${agent}`, String(clamped)) } catch {}
    setWindowsState(prev => prev.map(w => w.id === agent ? { ...w, width: clamped } : w))
  }, [])

  return (
    <FloatingChatContext.Provider value={{
      windows,
      panelOpen,
      openWindow,
      closeWindow,
      toggleMinimize,
      toggleViewMode,
      updateApprovals,
      setWindowWidth,
      setPanelOpen,
    }}>
      {children}
    </FloatingChatContext.Provider>
  )
}

export function useFloatingChat(): FloatingChatState {
  const ctx = useContext(FloatingChatContext)
  if (!ctx) throw new Error('useFloatingChat must be used inside FloatingChatProvider')
  return ctx
}
