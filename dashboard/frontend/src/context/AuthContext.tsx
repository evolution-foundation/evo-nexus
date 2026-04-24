import { createContext, useContext, useEffect, useState, useCallback, type ReactNode } from 'react'
import { api } from '../lib/api'
import { setWorkspaceLanguage } from '../i18n'

interface User {
  id: string
  username: string
  email: string
  display_name: string
  role: string
}

// Backend returns permissions as Record<string, string[]>  e.g. {"chat": ["view", "execute"]}
type Permissions = Record<string, string[]>

interface AgentAccess {
  mode: 'all' | 'none' | 'selected' | 'layer'
  agents?: string[]
  layers?: string[]
}

interface WorkspaceFolders {
  mode: 'all' | 'none' | 'selected'
  folders?: string[]
}

// Map agent name → layer (mirrors backend AGENT_LAYERS)
const AGENT_LAYERS: Record<string, string> = {
  'clawdia-assistant': 'business',
  'flux-finance': 'business',
  'atlas-project': 'business',
  'kai-personal-assistant': 'business',
  'pulse-community': 'business',
  'sage-strategy': 'business',
  'pixel-social-media': 'business',
  'nex-sales': 'business',
  'mentor-courses': 'business',
  'lumen-learning': 'business',
  'oracle': 'business',
  'mako-marketing': 'business',
  'aria-hr': 'business',
  'zara-cs': 'business',
  'lex-legal': 'business',
  'nova-product': 'business',
  'dex-data': 'business',
  'apex-architect': 'engineering',
  'echo-analyst': 'engineering',
  'compass-planner': 'engineering',
  'raven-critic': 'engineering',
  'lens-reviewer': 'engineering',
  'zen-simplifier': 'engineering',
  'vault-security': 'engineering',
  'bolt-executor': 'engineering',
  'hawk-debugger': 'engineering',
  'grid-tester': 'engineering',
  'probe-qa': 'engineering',
  'oath-verifier': 'engineering',
  'trail-tracer': 'engineering',
  'flow-git': 'engineering',
  'scroll-docs': 'engineering',
  'canvas-designer': 'engineering',
  'prism-scientist': 'engineering',
  'helm-conductor': 'engineering',
  'mirror-retro': 'engineering',
  'scout-explorer': 'engineering',
  'quill-writer': 'engineering',
}

interface AuthContextType {
  user: User | null
  loading: boolean
  permissions: Permissions
  agentAccess: AgentAccess
  workspaceFolders: WorkspaceFolders
  needsSetup: boolean
  login: (username: string, password: string) => Promise<void>
  logout: () => Promise<void>
  hasPermission: (resource: string, action: string) => boolean
  hasAgentAccess: (agentName: string) => boolean
  hasWorkspaceFolderAccess: (folder: string) => boolean
  refreshUser: () => Promise<void>
}

const AuthContext = createContext<AuthContextType | null>(null)

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [permissions, setPermissions] = useState<Permissions>({})
  const [agentAccess, setAgentAccess] = useState<AgentAccess>({ mode: 'all' })
  const [workspaceFolders, setWorkspaceFolders] = useState<WorkspaceFolders>({ mode: 'all' })
  const [loading, setLoading] = useState(true)
  const [needsSetup, setNeedsSetup] = useState(false)

  const refreshUser = useCallback(async () => {
    try {
      const setupRes = await api.get('/auth/needs-setup')
      if (setupRes.needs_setup) {
        setNeedsSetup(true)
        setUser(null)
        setPermissions({})
        setAgentAccess({ mode: 'all' })
        setWorkspaceFolders({ mode: 'all' })
        return
      }
      setNeedsSetup(false)

      const meRes = await api.get('/auth/me')
      setUser(meRes.user)
      setPermissions(meRes.permissions || {})
      setAgentAccess(meRes.agent_access || { mode: 'all' })
      setWorkspaceFolders(meRes.workspace_folders || { mode: 'all' })

      // Sync i18n with workspace.language — takes priority over detector.
      // Best-effort: never let a transient 4xx break auth flow.
      try {
        const ws = await api.get('/settings/workspace')
        const lang = ws?.workspace?.language
        if (lang) setWorkspaceLanguage(lang)
      } catch {
        // Endpoint unreachable / 403 / etc. — i18n detector keeps its guess.
      }
    } catch {
      setUser(null)
      setPermissions({})
      setAgentAccess({ mode: 'all' })
      setWorkspaceFolders({ mode: 'all' })
    }
  }, [])

  useEffect(() => {
    refreshUser().finally(() => setLoading(false))
  }, [refreshUser])

  const login = useCallback(async (username: string, password: string) => {
    const res = await api.post('/auth/login', { username, password })
    setUser(res.user)
    await refreshUser()
  }, [refreshUser])

  const logout = useCallback(async () => {
    await api.post('/auth/logout')
    setUser(null)
    setPermissions({})
    setAgentAccess({ mode: 'all' })
    setWorkspaceFolders({ mode: 'all' })
  }, [])

  const hasPermission = useCallback((resource: string, action: string) => {
    if (user?.role === 'admin') return true
    const actions = permissions[resource]
    return Array.isArray(actions) && actions.includes(action)
  }, [user, permissions])

  const hasAgentAccess = useCallback((agentName: string): boolean => {
    if (user?.role === 'admin') return true
    const { mode, agents, layers } = agentAccess
    if (mode === 'all') return true
    if (mode === 'none') return false
    if (mode === 'selected') return (agents || []).includes(agentName)
    if (mode === 'layer') {
      const agentLayer = AGENT_LAYERS[agentName]
      return agentLayer !== undefined && (layers || []).includes(agentLayer)
    }
    return true
  }, [user, agentAccess])

  const hasWorkspaceFolderAccess = useCallback((folder: string): boolean => {
    if (user?.role === 'admin') return true
    const { mode, folders } = workspaceFolders
    if (mode === 'all') return true
    if (mode === 'none') return false
    if (mode === 'selected') {
      if (!folders || folders.length === 0) return false
      return folders.includes(folder)
    }
    return true
  }, [user, workspaceFolders])

  return (
    <AuthContext.Provider
      value={{ user, loading, permissions, agentAccess, workspaceFolders, needsSetup, login, logout, hasPermission, hasAgentAccess, hasWorkspaceFolderAccess, refreshUser }}
    >
      {children}
    </AuthContext.Provider>
  )
}
