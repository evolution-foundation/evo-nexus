import { createContext, useContext, useState, useCallback, type ReactNode } from 'react'

export interface KnowledgeConnection {
  id: string
  name: string
  slug: string
  status: 'ready' | 'needs_migration' | 'error' | 'disconnected'
  host?: string
  port?: number
  database_name?: string
  username?: string
  ssl_mode?: string
  schema_version?: string
  pgvector_version?: string
  postgres_version?: string
  last_health_check?: string
  last_error?: string
  created_at: string
  spaces_count?: number
  chunks_count?: number
}

interface KnowledgeContextType {
  activeConnectionId: string | null
  setActiveConnectionId: (id: string | null) => void
  connections: KnowledgeConnection[]
  setConnections: (connections: KnowledgeConnection[]) => void
  refreshConnections: () => Promise<void>
}

const KnowledgeContext = createContext<KnowledgeContextType | null>(null)

const STORAGE_KEY = 'knowledge-active-connection'

export function useKnowledge() {
  const ctx = useContext(KnowledgeContext)
  if (!ctx) throw new Error('useKnowledge must be used within KnowledgeProvider')
  return ctx
}

export function KnowledgeProvider({ children }: { children: ReactNode }) {
  const [activeConnectionId, setActiveConnectionIdState] = useState<string | null>(() => {
    try {
      return localStorage.getItem(STORAGE_KEY)
    } catch {
      return null
    }
  })
  const [connections, setConnections] = useState<KnowledgeConnection[]>([])

  const setActiveConnectionId = useCallback((id: string | null) => {
    setActiveConnectionIdState(id)
    try {
      if (id) {
        localStorage.setItem(STORAGE_KEY, id)
      } else {
        localStorage.removeItem(STORAGE_KEY)
      }
    } catch {}
  }, [])

  const refreshConnections = useCallback(async () => {
    try {
      const API = import.meta.env.DEV ? 'http://localhost:8080' : ''
      const res = await fetch(`${API}/api/knowledge/connections`, { credentials: 'include' })
      if (!res.ok) return
      const data = await res.json()
      const list: KnowledgeConnection[] = data.connections || data || []
      setConnections(list)
      // If active connection was deleted, clear it
      if (activeConnectionId && !list.find((c) => c.id === activeConnectionId)) {
        const firstReady = list.find((c) => c.status === 'ready')
        setActiveConnectionId(firstReady?.id ?? null)
      }
      // Auto-select first ready connection if none selected
      if (!activeConnectionId && list.length > 0) {
        const firstReady = list.find((c) => c.status === 'ready')
        if (firstReady) setActiveConnectionId(firstReady.id)
      }
    } catch {}
  }, [activeConnectionId, setActiveConnectionId])

  return (
    <KnowledgeContext.Provider
      value={{ activeConnectionId, setActiveConnectionId, connections, setConnections, refreshConnections }}
    >
      {children}
    </KnowledgeContext.Provider>
  )
}
