import { NavLink, Outlet } from 'react-router-dom'
import { Database } from 'lucide-react'
import { useAuth } from '../../context/AuthContext'
import { KnowledgeProvider } from '../../context/KnowledgeContext'
import ConnectionSwitcher from './ConnectionSwitcher'

type Tab = { to: string; label: string; exact?: boolean }

const tabs: Tab[] = [
  { to: '/knowledge', label: 'Connections', exact: true },
  { to: '/knowledge/settings', label: 'Settings' },
  { to: '/knowledge/spaces', label: 'Spaces' },
  { to: '/knowledge/units', label: 'Units' },
  { to: '/knowledge/upload', label: 'Upload' },
  { to: '/knowledge/browse', label: 'Browse' },
  { to: '/knowledge/search', label: 'Search' },
  { to: '/knowledge/api-keys', label: 'API Keys' },
]

export default function KnowledgeLayout() {
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
      <div>
        {/* Header */}
        <div className="mb-6">
          <div className="flex items-center justify-between flex-wrap gap-4">
            <div>
              <h1 className="text-2xl font-bold text-[#F9FAFB] flex items-center gap-2">
                <Database size={22} className="text-[#00FFA7]" />
                Knowledge
              </h1>
              <p className="text-[#667085] mt-1 text-sm">Multi-connection pgvector knowledge base</p>
            </div>
            <ConnectionSwitcher />
          </div>
        </div>

        {/* Tabs */}
        <div className="flex gap-1 mb-6 border-b border-[#344054] overflow-x-auto">
          {tabs.map((t) => (
            <NavLink
              key={t.to}
              to={t.to}
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
    </KnowledgeProvider>
  )
}
