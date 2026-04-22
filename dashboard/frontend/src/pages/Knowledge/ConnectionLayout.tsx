import { NavLink, Outlet, useParams, useNavigate, Link } from 'react-router-dom'
import { ArrowLeft, Database } from 'lucide-react'
import { useEffect } from 'react'
import { KnowledgeProvider, useKnowledge } from '../../context/KnowledgeContext'
import { useAuth } from '../../context/AuthContext'

type Tab = { to: (cid: string) => string; label: string; exact?: boolean }

const tabs: Tab[] = [
  { to: (cid) => `/knowledge/connections/${cid}`, label: 'Overview', exact: true },
  { to: (cid) => `/knowledge/connections/${cid}/spaces`, label: 'Spaces' },
  { to: (cid) => `/knowledge/connections/${cid}/units`, label: 'Units' },
  { to: (cid) => `/knowledge/connections/${cid}/upload`, label: 'Upload' },
  { to: (cid) => `/knowledge/connections/${cid}/browse`, label: 'Browse' },
  { to: (cid) => `/knowledge/connections/${cid}/search`, label: 'Search' },
  { to: (cid) => `/knowledge/connections/${cid}/api-keys`, label: 'API Keys' },
]

function ConnectionLayoutInner() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const { connections, activeConnectionId, setActiveConnectionId, loading } = useKnowledge()

  // Sync URL param → active connection context.
  useEffect(() => {
    if (id && id !== activeConnectionId) {
      setActiveConnectionId(id)
    }
  }, [id, activeConnectionId, setActiveConnectionId])

  const connection = connections.find((c) => c.id === id)

  // Initial load: defer "not found" verdict until the context has actually
  // fetched the connections list. Otherwise we flash the error on every
  // page refresh that lands directly on /knowledge/connections/:id.
  if (loading) {
    return (
      <div className="flex items-center justify-center py-16 text-[#667085] text-sm">
        Loading connection…
      </div>
    )
  }

  if (!connection) {
    return (
      <div className="text-center py-16">
        <p className="text-[#D0D5DD] font-medium mb-2">Connection not found</p>
        <p className="text-[#667085] text-sm mb-4">
          The connection you&apos;re trying to open doesn&apos;t exist or was deleted.
        </p>
        <button
          onClick={() => navigate('/knowledge')}
          className="text-sm text-[#00FFA7] hover:underline"
        >
          ← Back to Connections
        </button>
      </div>
    )
  }

  const statusColor =
    connection.status === 'ready'
      ? 'text-[#00FFA7] bg-[#00FFA7]/10'
      : connection.status === 'error'
      ? 'text-red-400 bg-red-400/10'
      : 'text-yellow-400 bg-yellow-400/10'

  return (
    <div>
      {/* Breadcrumb + header */}
      <div className="mb-6">
        <Link
          to="/knowledge"
          className="inline-flex items-center gap-1.5 text-xs text-[#667085] hover:text-[#D0D5DD] mb-3 transition-colors"
        >
          <ArrowLeft size={14} />
          All connections
        </Link>
        <div className="flex items-center gap-3 flex-wrap">
          <Database size={20} className="text-[#00FFA7]" />
          <h1 className="text-xl font-bold text-[#F9FAFB]">{connection.name}</h1>
          <span className={`text-[10px] px-2 py-0.5 rounded uppercase font-semibold ${statusColor}`}>
            {connection.status}
          </span>
          <span className="text-xs text-[#667085] font-mono">
            {connection.host}:{connection.port ?? 5432} / {connection.database_name}
          </span>
        </div>
      </div>

      {/* Per-connection tabs */}
      <div className="flex gap-1 mb-6 border-b border-[#344054] overflow-x-auto">
        {tabs.map((t) => (
          <NavLink
            key={t.label}
            to={t.to(connection.id)}
            end={t.exact}
            className={({ isActive }) =>
              `px-4 py-2.5 text-sm font-medium transition-colors border-b-2 -mb-px whitespace-nowrap ${
                isActive
                  ? 'text-[#00FFA7] border-[#00FFA7]'
                  : 'text-[#667085] border-transparent hover:text-[#D0D5DD]'
              }`
            }
          >
            {t.label}
          </NavLink>
        ))}
      </div>

      {/* Page content */}
      <Outlet />
    </div>
  )
}

export default function ConnectionLayout() {
  const { hasPermission } = useAuth()
  if (!hasPermission('knowledge', 'view')) {
    return (
      <div className="flex items-center justify-center h-64 text-[#667085] text-sm">
        You don&apos;t have permission to view Knowledge.
      </div>
    )
  }
  return (
    <KnowledgeProvider>
      <ConnectionLayoutInner />
    </KnowledgeProvider>
  )
}
