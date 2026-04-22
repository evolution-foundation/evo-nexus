import { useState, useEffect, useCallback } from 'react'
import { NavLink } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import NotificationBell from './NotificationBell'
import {
  LayoutDashboard, Bot, Clock, Zap, Layout, Calendar, CalendarClock,
  Brain, Plug, DollarSign, FolderOpen, Cpu,
  Monitor, Users, ScrollText, LogOut, Menu, X, Shield, BookOpen, Library, Database,
  ArrowUpCircle, ChevronDown, Webhook, HardDriveDownload, Settings, Share2, Heart, Target, Ticket,
} from 'lucide-react'

interface VersionInfo {
  current: string
  latest: string | null
  update_available: boolean
  release_url: string | null
  release_notes: string | null
}

interface NavItem {
  to: string
  label: string
  icon: React.ComponentType<{ size?: number }>
  resource: string | null
  desktopOnly?: boolean
}

interface NavGroup {
  key: string
  label: string
  collapsible: boolean
  adminOnly?: boolean
  items: NavItem[]
}

const navGroups: NavGroup[] = [
  {
    key: 'main',
    label: 'Main',
    collapsible: false,
    items: [
      { to: '/', label: 'Overview', icon: LayoutDashboard, resource: null },
    ],
  },
  {
    key: 'operations',
    label: 'Operations',
    collapsible: true,
    items: [
      { to: '/agents', label: 'Agents', icon: Bot, resource: 'agents' },
      { to: '/skills', label: 'Skills', icon: Zap, resource: 'skills' },
      { to: '/routines', label: 'Routines', icon: Clock, resource: 'routines' },
      { to: '/tasks', label: 'Tasks', icon: CalendarClock, resource: 'tasks' },
      { to: '/triggers', label: 'Triggers', icon: Webhook, resource: 'triggers' },
      { to: '/heartbeats', label: 'Heartbeats', icon: Heart, resource: 'heartbeats' },
      { to: '/goals', label: 'Goals', icon: Target, resource: 'goals' },
      { to: '/issues', label: 'Issues', icon: Ticket, resource: 'tickets' },
      { to: '/templates', label: 'Templates', icon: Layout, resource: 'templates' },
    ],
  },
  {
    key: 'data',
    label: 'Data',
    collapsible: true,
    items: [
      { to: '/workspace', label: 'Workspace', icon: FolderOpen, resource: 'workspace' },
      { to: '/shares', label: 'Share Links', icon: Share2, resource: 'workspace' },
      { to: '/memory', label: 'Memory', icon: Brain, resource: 'memory' },
      { to: '/mempalace', label: 'MemPalace', icon: Library, resource: 'mempalace' },
      { to: '/knowledge', label: 'Knowledge', icon: Database, resource: 'knowledge' },
      { to: '/costs', label: 'Costs', icon: DollarSign, resource: 'costs' },
    ],
  },
  {
    key: 'system',
    label: 'System',
    collapsible: true,
    items: [
      { to: '/settings', label: 'Settings', icon: Settings, resource: 'config' },
      { to: '/systems', label: 'Systems', icon: Monitor, resource: 'systems' },
      { to: '/providers', label: 'Providers', icon: Cpu, resource: 'config' },
      { to: '/integrations', label: 'Integrations', icon: Plug, resource: 'integrations' },
      { to: '/scheduler', label: 'Services', icon: Calendar, resource: 'scheduler' },
      { to: '/backups', label: 'Backups', icon: HardDriveDownload, resource: 'config' },
    ],
  },
  {
    key: 'admin',
    label: 'Admin',
    collapsible: true,
    adminOnly: true,
    items: [
      { to: '/users', label: 'Users', icon: Users, resource: 'users' },
      { to: '/roles', label: 'Roles', icon: Shield, resource: 'users' },
      { to: '/audit', label: 'Audit Log', icon: ScrollText, resource: 'audit' },
    ],
  },
]

const STORAGE_KEY = 'sidebar-collapsed-groups'

function loadCollapsedState(): Record<string, boolean> {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (stored) return JSON.parse(stored)
  } catch {}
  return {}
}

function saveCollapsedState(state: Record<string, boolean>) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state))
  } catch {}
}

const roleBadgeClass: Record<string, string> = {
  admin: 'bg-purple-500/20 text-purple-400',
  operator: 'bg-blue-500/20 text-blue-400',
  viewer: 'bg-gray-500/20 text-gray-400',
}

export default function Sidebar() {
  const { user, logout, hasPermission } = useAuth()
  const [mobileOpen, setMobileOpen] = useState(false)
  const [versionInfo, setVersionInfo] = useState<VersionInfo | null>(null)
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>(loadCollapsedState)

  useEffect(() => {
    fetch('/api/version/check')
      .then((r) => r.json())
      .then((data) => setVersionInfo(data))
      .catch(() => {})
  }, [])

  const toggleGroup = useCallback((key: string) => {
    setCollapsed((prev) => {
      const next = { ...prev, [key]: !prev[key] }
      saveCollapsedState(next)
      return next
    })
  }, [])

  const renderLink = (item: NavItem) => (
    <NavLink
      key={item.to}
      to={item.to}
      end={item.to === '/'}
      onClick={() => setMobileOpen(false)}
      className={({ isActive }) =>
        `items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium transition-colors ${
          item.desktopOnly ? 'hidden lg:flex' : 'flex'
        } ${
          isActive
            ? 'text-[#00FFA7] bg-[#00FFA7]/10 border-l-2 border-[#00FFA7]'
            : 'text-[#667085] hover:text-[#D0D5DD] hover:bg-white/5 border-l-2 border-transparent'
        }`
      }
    >
      <item.icon size={16} />
      {item.label}
    </NavLink>
  )

  const renderGroup = (group: NavGroup) => {
    // Filter items by permission
    const visibleItems = group.items.filter(
      (item) => item.resource === null || hasPermission(item.resource, 'view')
    )

    if (visibleItems.length === 0) return null

    // Admin group: only show if user has permission to view at least one admin item
    if (group.adminOnly) {
      const hasAnyAdmin = group.items.some((item) =>
        item.resource && hasPermission(item.resource, 'view')
      )
      if (!hasAnyAdmin) return null
    }

    const isCollapsed = collapsed[group.key] ?? false

    return (
      <div key={group.key} className="mb-1">
        {group.collapsible ? (
          <button
            onClick={() => toggleGroup(group.key)}
            className="w-full flex items-center justify-between px-3 py-1.5 mt-2 group cursor-pointer"
          >
            <span className="text-[10px] uppercase tracking-wider text-[#667085] font-semibold select-none">
              {group.label}
            </span>
            <ChevronDown
              size={12}
              className={`text-[#667085] transition-transform duration-200 group-hover:text-[#D0D5DD] ${
                isCollapsed ? '-rotate-90' : ''
              }`}
            />
          </button>
        ) : (
          <div className="px-3 py-1.5">
            <span className="text-[10px] uppercase tracking-wider text-[#667085] font-semibold">
              {group.label}
            </span>
          </div>
        )}

        <div
          className={`overflow-hidden transition-all duration-200 ease-in-out ${
            group.collapsible && isCollapsed ? 'max-h-0 opacity-0' : 'max-h-96 opacity-100'
          }`}
        >
          <div className="flex flex-col gap-0.5">
            {visibleItems.map(renderLink)}
          </div>
        </div>
      </div>
    )
  }

  const sidebarContent = (
    <>
      <div className="px-5 py-6 flex items-center justify-between">
        <img src="/EVO_NEXUS.png" alt="EvoNexus" className="h-8 w-auto" />
        <div className="flex items-center gap-1">
          <NotificationBell />
          <button onClick={() => setMobileOpen(false)} className="lg:hidden p-1 rounded hover:bg-white/10 text-[#667085]">
            <X size={20} />
          </button>
        </div>
      </div>

      <nav className="flex-1 overflow-y-auto px-3 pb-4">
        {navGroups.map(renderGroup)}

        {/* Docs link — standalone at the bottom of nav */}
        <div className="mt-2">
          <NavLink
            to="/docs"
            onClick={() => setMobileOpen(false)}
            className={({ isActive }) =>
              `flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium transition-colors ${
                isActive
                  ? 'text-[#00FFA7] bg-[#00FFA7]/10 border-l-2 border-[#00FFA7]'
                  : 'text-[#667085] hover:text-[#D0D5DD] hover:bg-white/5 border-l-2 border-transparent'
              }`
            }
          >
            <BookOpen size={16} />
            Docs
          </NavLink>
        </div>
      </nav>

      {user && (
        <div className="px-4 py-4 border-t border-[#344054]">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-full bg-[#00FFA7]/20 text-[#00FFA7] flex items-center justify-center text-sm font-bold shrink-0">
              {(user.display_name || user.username).charAt(0).toUpperCase()}
            </div>
            <div className="flex-1 min-w-0">
              <p className="text-sm text-white font-medium truncate">{user.display_name || user.username}</p>
              <span className={`inline-block px-1.5 py-0.5 rounded text-[10px] font-medium ${roleBadgeClass[user.role] || roleBadgeClass.viewer}`}>
                {user.role}
              </span>
            </div>
            <button
              onClick={logout}
              className="p-1.5 rounded-lg text-[#667085] hover:text-red-400 hover:bg-red-500/10 transition-colors shrink-0"
              title="Logout"
            >
              <LogOut size={16} />
            </button>
          </div>
        </div>
      )}

      {/* Version indicator */}
      {versionInfo && (
        <div className="px-4 py-2 border-t border-[#344054]/50">
          <div className="flex items-center justify-between text-[11px]">
            <span className="text-[#667085]">v{versionInfo.current}</span>
            {versionInfo.update_available && versionInfo.release_url && (
              <a
                href={versionInfo.release_url}
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center gap-1 text-[#00FFA7] hover:text-[#00FFA7]/80 transition-colors"
                title={`Update available: v${versionInfo.latest}`}
              >
                <ArrowUpCircle size={12} />
                <span>v{versionInfo.latest}</span>
              </a>
            )}
          </div>
        </div>
      )}

      {/* Credits */}
      <div className="px-4 py-3 border-t border-[#344054]/50">
        <a
          href="https://evolutionfoundation.com.br"
          target="_blank"
          rel="noopener noreferrer"
          className="flex items-center justify-center gap-1.5 text-[10px] text-[#667085] hover:text-[#00FFA7] transition-colors"
        >
          by <span className="font-semibold text-[#00FFA7]/60">Evolution Foundation</span>
        </a>
      </div>
    </>
  )

  return (
    <>
      {/* Mobile hamburger */}
      <button
        onClick={() => setMobileOpen(true)}
        className="fixed top-4 left-4 z-50 lg:hidden p-2 rounded-lg bg-[#182230] border border-[#344054] text-[#D0D5DD] hover:text-[#00FFA7] transition-colors"
      >
        <Menu size={20} />
      </button>

      {/* Mobile overlay */}
      {mobileOpen && (
        <div className="fixed inset-0 bg-black/60 z-40 lg:hidden" onClick={() => setMobileOpen(false)} />
      )}

      {/* Sidebar */}
      <aside className={`
        fixed left-0 top-0 bottom-0 w-60 bg-[#0a0f1a] border-r border-[#344054] flex flex-col z-50
        transition-transform duration-200 ease-in-out
        lg:translate-x-0
        ${mobileOpen ? 'translate-x-0' : '-translate-x-full lg:translate-x-0'}
      `}>
        {sidebarContent}
      </aside>
    </>
  )
}
