import { lazy, Suspense, useEffect } from 'react'
import { Routes, Route, Navigate, useLocation } from 'react-router-dom'
import { AuthProvider, useAuth } from './context/AuthContext'
import { hydrateAgentMeta } from './lib/agent-meta'
import { hydratePluginUiRegistry } from './lib/plugin-ui-registry'
import { initEvoNexusSdk } from './lib/evonexus-sdk'
import PluginPageHost from './pages/PluginPageHost'
import { NotificationProvider } from './context/NotificationContext'
import Sidebar from './components/Sidebar'
import Overview from './pages/Overview'
import Agents from './pages/Agents'
import AgentDetail from './pages/AgentDetail'
import Routines from './pages/Routines'
import Skills from './pages/Skills'
import SkillDetail from './pages/SkillDetail'
import Costs from './pages/Costs'
import Integrations from './pages/Integrations'
import Templates from './pages/Templates'
import Scheduler from './pages/Scheduler'
import Tasks from './pages/Tasks'
import Memory from './pages/Memory'
import Systems from './pages/Systems'
import Setup from './pages/Setup'
import Login from './pages/Login'
import Users from './pages/Users'
import Audit from './pages/Audit'
import Roles from './pages/Roles'
import Docs from './pages/Docs'
import MemPalace from './pages/MemPalace'
import Triggers from './pages/Triggers'
import Backups from './pages/Backups'
import Providers from './pages/Providers'
import Workspace from './pages/Workspace'
import Settings from './pages/Settings'
import ShareView from './pages/ShareView'
import ShareLinks from './pages/ShareLinks'
import HeartbeatsList, { HeartbeatDetail } from './pages/Heartbeats'
import Activity from './pages/Activity'
import Goals from './pages/Goals'
import Plugins from './pages/Plugins'
import PluginDetail from './pages/PluginDetail'
import McpServers from './pages/McpServers'
import Topics from './pages/Topics'
import TicketDetail from './pages/TicketDetail'
import KnowledgeLayout from './pages/Knowledge/KnowledgeLayout'
import ConnectionLayout from './pages/Knowledge/ConnectionLayout'
import KnowledgeConnections from './pages/Knowledge/Connections/List'
import ConnectionDetail from './pages/Knowledge/Connections/Detail'
import KnowledgeSettings from './pages/Knowledge/Settings'
import KnowledgeSpaces from './pages/Knowledge/Spaces'
import KnowledgeUnits from './pages/Knowledge/Units'
import KnowledgeUpload from './pages/Knowledge/Upload'
import KnowledgeBrowse from './pages/Knowledge/Browse'
import KnowledgeSearch from './pages/Knowledge/Search'
import KnowledgeApiKeys from './pages/Knowledge/ApiKeys'

// Lazy-loaded onboarding + settings pages
const OnboardingRouter = lazy(() => import('./pages/onboarding/OnboardingRouter'))
const BrainRepo = lazy(() => import('./pages/settings/BrainRepo'))

// Extended user type with onboarding fields (backend may include these)
interface OnboardingUser {
  onboarding_state?: string | null
  onboarding_completed_agents_visit?: boolean
}

function AppContent() {
  const location = useLocation()
  const isDocs = location.pathname === '/docs' || location.pathname.startsWith('/docs/')
  const isShare = location.pathname.startsWith('/share/')
  const isOnboarding = location.pathname.startsWith('/onboarding')
  const isAgentDetail = /^\/agents\/[^/]+$/.test(location.pathname)
  const isTicketDetail = /^\/tickets\/[^/]+$/.test(location.pathname)
  const isWorkspace = location.pathname === '/workspace' || location.pathname.startsWith('/workspace/')
  const { user, loading, needsSetup, hasPermission } = useAuth()
  const extUser = user as (typeof user & OnboardingUser) | null

  // Wave 2.0/2.1: hydrate registries once per authenticated session.
  // Must be declared before any early return (Rules of Hooks).
  useEffect(() => {
    if (user) {
      hydrateAgentMeta()
      hydratePluginUiRegistry()
      initEvoNexusSdk()
    }
  }, [user])

  // Share links are public — render without auth or sidebar
  if (isShare) {
    return (
      <Routes>
        <Route path="/share/:token" element={<ShareView />} />
      </Routes>
    )
  }

  // Docs are public — render without auth
  if (isDocs) {
    // Redirect .txt files to API directly
    if (location.pathname.endsWith('.txt')) {
      const apiBase = import.meta.env.DEV ? 'http://localhost:8080' : ''
      window.location.replace(`${apiBase}/api/docs/llms-full.txt`)
      return null
    }
    return (
      <Routes>
        <Route path="/docs" element={<Docs />} />
        <Route path="/docs/*" element={<Docs />} />
      </Routes>
    )
  }

  if (loading) {
    return (
      <div className="min-h-screen bg-[#0C111D] flex items-center justify-center">
        <div className="text-[#667085] text-sm">Loading...</div>
      </div>
    )
  }

  if (needsSetup) return <Setup />
  if (!user) return <Login />

  // Onboarding guard — redirect to /onboarding if user hasn't completed/skipped it.
  // For brand-new users onboarding_state is null (column nullable), which counts as "needs onboarding".
  if (
    !isOnboarding &&
    extUser &&
    extUser.onboarding_state !== 'completed' &&
    extUser.onboarding_state !== 'skipped'
  ) {
    return <Navigate to="/onboarding" replace />
  }

  // Allow direct access to /onboarding regardless
  if (isOnboarding) {
    return (
      <Suspense fallback={<div className="min-h-screen bg-[#080c14] flex items-center justify-center"><div className="text-[#5a6b7f] text-sm">Loading...</div></div>}>
        <Routes>
          <Route path="/onboarding/*" element={<OnboardingRouter />} />
        </Routes>
      </Suspense>
    )
  }

  return (
    <NotificationProvider>
    <div className="flex min-h-screen bg-[#0C111D]">
      <Sidebar />

      {/* Pages — responsive margin */}
      <main
        className={
          isAgentDetail || isWorkspace || isTicketDetail
            ? 'flex-1 ml-0 lg:ml-60 pt-14 lg:pt-0 h-screen overflow-hidden'
            : 'flex-1 ml-0 lg:ml-60 p-4 lg:p-8 pt-16 lg:pt-8 overflow-auto'
        }
      >
        <Routes>
          {/* Onboarding & Settings routes (lazy) */}
          <Route path="/onboarding/*" element={
            <Suspense fallback={<div className="flex items-center justify-center py-16"><div className="text-[#5a6b7f] text-sm">Loading...</div></div>}>
              <OnboardingRouter />
            </Suspense>
          } />
          <Route path="/settings/brain-repo" element={
            <Suspense fallback={<div className="flex items-center justify-center py-16"><div className="text-[#5a6b7f] text-sm">Loading...</div></div>}>
              <BrainRepo />
            </Suspense>
          } />

          <Route path="/" element={<Overview />} />
          <Route path="/workspace/*" element={<Workspace />} />
          <Route path="/agents" element={<Agents />} />
          <Route path="/agents/:name" element={<AgentDetail />} />
          <Route path="/routines" element={<Routines />} />
          {hasPermission('scheduler', 'view') && <Route path="/activity" element={<Activity />} />}
          <Route path="/tasks" element={<Tasks />} />
          {hasPermission('triggers', 'view') && <Route path="/triggers" element={<Triggers />} />}
          <Route path="/skills" element={<Skills />} />
          <Route path="/skills/:name" element={<SkillDetail />} />
          <Route path="/costs" element={<Costs />} />
          <Route path="/integrations" element={<Integrations />} />
          <Route path="/templates" element={<Templates />} />
          <Route path="/scheduler" element={<Scheduler />} />
          {hasPermission('heartbeats', 'view') && <Route path="/heartbeats" element={<HeartbeatsList />} />}
          {hasPermission('heartbeats', 'view') && <Route path="/heartbeats/:id" element={<HeartbeatDetail />} />}
          <Route path="/memory" element={<Memory />} />
          <Route path="/mempalace" element={<MemPalace />} />
          <Route path="/systems" element={<Systems />} />
          {hasPermission('config', 'view') && <Route path="/settings" element={<Settings />} />}
          {hasPermission('config', 'view') && <Route path="/backups" element={<Backups />} />}
          <Route path="/config" element={<Navigate to="/settings" replace />} />
          <Route path="/providers" element={<Providers />} />
          {hasPermission('users', 'view') && <Route path="/users" element={<Users />} />}
          {hasPermission('audit', 'view') && <Route path="/audit" element={<Audit />} />}
          {hasPermission('users', 'manage') && <Route path="/roles" element={<Roles />} />}
          {hasPermission('workspace', 'manage') && <Route path="/shares" element={<ShareLinks />} />}
          <Route path="/goals" element={<Goals />} />
          <Route path="/plugins" element={<Plugins />} />
          <Route path="/plugins/:slug" element={<PluginDetail />} />
          <Route path="/mcp-servers" element={<McpServers />} />
          {/* Wave 2.1: full-screen plugin UI pages (catch-all, must come after /plugins/:slug) */}
          <Route path="/plugins-ui/:slug/*" element={<PluginPageHost />} />
          {hasPermission('tickets', 'view') && <Route path="/topics" element={<Topics />} />}
          {hasPermission('tickets', 'view') && <Route path="/issues" element={<Navigate to="/topics" replace />} />}
          {hasPermission('tickets', 'view') && <Route path="/tickets/:id" element={<TicketDetail />} />}
          {hasPermission('knowledge', 'view') && (
            <>
              {/* Top-level Knowledge shell: only Connections + Settings */}
              <Route path="/knowledge" element={<KnowledgeLayout />}>
                <Route index element={<KnowledgeConnections />} />
                <Route path="settings" element={<KnowledgeSettings />} />
              </Route>
              {/* Per-connection scope: tabs appear only inside a connection */}
              <Route path="/knowledge/connections/:id" element={<ConnectionLayout />}>
                <Route index element={<ConnectionDetail />} />
                <Route path="spaces" element={<KnowledgeSpaces />} />
                <Route path="units" element={<KnowledgeUnits />} />
                <Route path="upload" element={<KnowledgeUpload />} />
                <Route path="browse" element={<KnowledgeBrowse />} />
                <Route path="search" element={<KnowledgeSearch />} />
                <Route path="api-keys" element={<KnowledgeApiKeys />} />
              </Route>
            </>
          )}
        </Routes>
      </main>
    </div>
    </NotificationProvider>
  )
}

export default function App() {
  return (
    <AuthProvider>
      <AppContent />
    </AuthProvider>
  )
}
