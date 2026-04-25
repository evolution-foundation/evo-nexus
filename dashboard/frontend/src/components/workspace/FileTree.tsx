import { useState, useEffect, useRef, useCallback } from 'react'
import {
  Folder, FolderOpen, File, FileText, FileCode, FileJson,
  ChevronRight, Search, Home,
} from 'lucide-react'
import { api } from '../../lib/api'

export interface TreeNode {
  name: string
  path: string
  is_dir: boolean
  children?: TreeNode[]
  size?: number
  modified?: number
  extension?: string
  icon?: string
  viewable?: boolean
  loaded?: boolean
}

interface Breadcrumb {
  name: string
  path: string
}

interface FileTreeProps {
  selectedPath: string | null
  onSelect: (path: string, isDir: boolean) => void
  onNavigate: (path: string) => void
  refreshTrigger?: number
}

function getFileIcon(node: TreeNode) {
  if (node.is_dir) return null
  const ext = node.extension?.toLowerCase() ?? ''
  switch (ext) {
    case '.md':
    case '.txt':
      return FileText
    case '.ts':
    case '.tsx':
    case '.js':
    case '.jsx':
    case '.py':
    case '.go':
    case '.sh':
    case '.yaml':
    case '.yml':
    case '.toml':
      return FileCode
    case '.json':
      return FileJson
    default:
      return File
  }
}

interface TreeItemProps {
  node: TreeNode
  level: number
  selectedPath: string | null
  onSelect: (path: string, isDir: boolean) => void
  onNavigate: (path: string) => void
  searchTerm: string
}

function TreeItem({ node, level, selectedPath, onSelect, onNavigate, searchTerm }: TreeItemProps) {
  const [expanded, setExpanded] = useState(false)
  const [children, setChildren] = useState<TreeNode[]>([])
  const [loadingChildren, setLoadingChildren] = useState(false)
  const hasAutoExpanded = useRef(false)
  const isSelected = selectedPath === node.path

  const loadChildren = useCallback(async () => {
    if (!node.is_dir || loadingChildren) return
    setLoadingChildren(true)
    try {
      const data = await api.get(`/workspace/tree?path=${encodeURIComponent(node.path)}&depth=1`)
      const items: TreeNode[] = (data.items || []).sort((a: TreeNode, b: TreeNode) => {
        if (a.is_dir !== b.is_dir) return a.is_dir ? -1 : 1
        return a.name.toLowerCase().localeCompare(b.name.toLowerCase())
      })
      setChildren(items)
    } catch {
      setChildren([])
    } finally {
      setLoadingChildren(false)
    }
  }, [node.path, node.is_dir, loadingChildren])

  // Auto-expand ancestor folders when selectedPath is inside this node
  useEffect(() => {
    if (
      !hasAutoExpanded.current &&
      node.is_dir &&
      selectedPath &&
      selectedPath.startsWith(node.path + '/') &&
      !expanded
    ) {
      hasAutoExpanded.current = true
      setExpanded(true)
      loadChildren()
    }
  }, [selectedPath, node.is_dir, node.path, expanded, loadChildren])

  const handleClick = () => {
    if (node.is_dir) {
      if (!expanded) {
        loadChildren()
        setExpanded(true)
      } else {
        setExpanded(false)
      }
      onSelect(node.path, true)
    } else {
      onSelect(node.path, false)
    }
  }

  const IconComp = node.is_dir
    ? (expanded ? FolderOpen : Folder)
    : (getFileIcon(node) ?? File)

  const iconColor = node.is_dir ? 'var(--evo-green)' : 'var(--text-muted)'

  return (
    <div>
      <button
        onClick={handleClick}
        className="w-full flex items-center gap-1.5 py-1 pr-3 text-left text-sm rounded transition-colors duration-150 select-none"
        style={{
          paddingLeft: `${8 + level * 16}px`,
          background: isSelected ? 'var(--surface-active)' : 'transparent',
          borderLeft: isSelected ? '2px solid var(--evo-green)' : '2px solid transparent',
          color: isSelected ? 'var(--text-primary)' : 'var(--text-secondary)',
        }}
        onMouseEnter={e => {
          if (!isSelected) e.currentTarget.style.background = 'var(--surface-hover)'
        }}
        onMouseLeave={e => {
          if (!isSelected) e.currentTarget.style.background = 'transparent'
        }}
        title={node.path}
      >
        {/* Chevron for dirs */}
        <span
          className="flex-shrink-0 transition-transform duration-200"
          style={{
            transform: expanded ? 'rotate(90deg)' : 'rotate(0deg)',
            opacity: node.is_dir ? 1 : 0,
            width: '14px',
          }}
        >
          {node.is_dir && <ChevronRight size={14} style={{ color: 'var(--text-muted)' }} />}
        </span>

        {/* File/folder icon */}
        <span className="flex-shrink-0">
          <IconComp size={14} style={{ color: iconColor }} />
        </span>

        {/* Name — highlight search term */}
        <span className="truncate flex-1 min-w-0">
          {searchTerm ? highlightMatch(node.name, searchTerm) : node.name}
        </span>
      </button>

      {/* Children */}
      {node.is_dir && expanded && (
        <div>
          {loadingChildren ? (
            <div className="space-y-1 py-1" style={{ paddingLeft: `${8 + (level + 1) * 16}px` }}>
              {[1, 2, 3].map(i => (
                <div key={i} className="h-4 rounded skeleton" style={{ width: `${60 + i * 15}%` }} />
              ))}
            </div>
          ) : children.length === 0 ? (
            <div
              className="py-1 text-xs"
              style={{ paddingLeft: `${8 + (level + 1) * 16}px`, color: 'var(--text-muted)' }}
            >
              Vazio
            </div>
          ) : (
            children.map(child => (
              <TreeItem
                key={child.path}
                node={child}
                level={level + 1}
                selectedPath={selectedPath}
                onSelect={onSelect}
                onNavigate={onNavigate}
                searchTerm={searchTerm}
              />
            ))
          )}
        </div>
      )}
    </div>
  )
}

function highlightMatch(name: string, term: string) {
  const idx = name.toLowerCase().indexOf(term.toLowerCase())
  if (idx === -1) return <>{name}</>
  return (
    <>
      {name.slice(0, idx)}
      <mark style={{ background: 'rgba(0,255,167,0.25)', color: 'var(--evo-green)', borderRadius: '2px' }}>
        {name.slice(idx, idx + term.length)}
      </mark>
      {name.slice(idx + term.length)}
    </>
  )
}

function flattenTree(nodes: TreeNode[], acc: TreeNode[] = []): TreeNode[] {
  for (const node of nodes) {
    acc.push(node)
    if (node.children && node.children.length > 0) {
      flattenTree(node.children, acc)
    }
  }
  return acc
}

interface SearchResultItemProps {
  node: TreeNode
  selectedPath: string | null
  onSelect: (path: string, isDir: boolean) => void
  searchTerm: string
}

function SearchResultItem({ node, selectedPath, onSelect, searchTerm }: SearchResultItemProps) {
  const isSelected = selectedPath === node.path
  const IconComp = node.is_dir ? Folder : (getFileIcon(node) ?? File)
  const iconColor = node.is_dir ? 'var(--evo-green)' : 'var(--text-muted)'
  // Show parent path without filename, trimming "workspace/" prefix for brevity
  const parts = node.path.split('/')
  const parentPath = parts.slice(0, -1).join('/').replace(/^workspace\/?/, '') || 'workspace'

  return (
    <button
      onClick={() => onSelect(node.path, node.is_dir)}
      className="w-full flex flex-col items-start gap-0.5 px-3 py-1.5 text-left rounded transition-colors duration-150 select-none"
      style={{
        background: isSelected ? 'var(--surface-active)' : 'transparent',
        borderLeft: isSelected ? '2px solid var(--evo-green)' : '2px solid transparent',
      }}
      onMouseEnter={e => {
        if (!isSelected) e.currentTarget.style.background = 'var(--surface-hover)'
      }}
      onMouseLeave={e => {
        if (!isSelected) e.currentTarget.style.background = 'transparent'
      }}
      title={node.path}
    >
      <div className="flex items-center gap-1.5 w-full min-w-0">
        <IconComp size={14} style={{ color: iconColor, flexShrink: 0 }} />
        <span className="truncate flex-1 text-sm" style={{ color: 'var(--text-primary)' }}>
          {highlightMatch(node.name, searchTerm)}
        </span>
      </div>
      <span
        className="truncate text-[10px] pl-5 w-full"
        style={{ color: 'var(--text-muted)' }}
      >
        {parentPath}
      </span>
    </button>
  )
}

export default function FileTree({ selectedPath, onSelect, onNavigate, refreshTrigger }: FileTreeProps) {
  const [rootItems, setRootItems] = useState<TreeNode[]>([])
  const [breadcrumbs, setBreadcrumbs] = useState<Breadcrumb[]>([])
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState('')
  const [debouncedSearch, setDebouncedSearch] = useState('')
  const [searchResults, setSearchResults] = useState<TreeNode[]>([])
  const [searching, setSearching] = useState(false)
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const loadRoot = useCallback(async () => {
    setLoading(true)
    try {
      const data = await api.get('/workspace/tree?path=workspace&depth=1')
      const items: TreeNode[] = (data.items || []).sort((a: TreeNode, b: TreeNode) => {
        if (a.is_dir !== b.is_dir) return a.is_dir ? -1 : 1
        return a.name.toLowerCase().localeCompare(b.name.toLowerCase())
      })
      setRootItems(items)
      setBreadcrumbs(data.breadcrumbs || [{ name: 'workspace', path: 'workspace' }])
    } catch {
      setRootItems([])
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    loadRoot()
  }, [loadRoot, refreshTrigger])

  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => setDebouncedSearch(search), 200)
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current)
    }
  }, [search])

  // Recursive search: when a term is entered, fetch the tree with depth=8
  // and flatten all nodes whose name matches. Limited to workspace/ for speed.
  useEffect(() => {
    if (!debouncedSearch) {
      setSearchResults([])
      setSearching(false)
      return
    }
    let cancelled = false
    setSearching(true)
    ;(async () => {
      try {
        const data = await api.get('/workspace/tree?path=workspace&depth=8')
        const all = flattenTree(data.items || [])
        const term = debouncedSearch.toLowerCase()
        const matches = all
          .filter(n => n.name.toLowerCase().includes(term))
          .sort((a, b) => {
            if (a.is_dir !== b.is_dir) return a.is_dir ? -1 : 1
            return a.name.toLowerCase().localeCompare(b.name.toLowerCase())
          })
          .slice(0, 200) // cap to avoid rendering thousands
        if (!cancelled) setSearchResults(matches)
      } catch {
        if (!cancelled) setSearchResults([])
      } finally {
        if (!cancelled) setSearching(false)
      }
    })()
    return () => { cancelled = true }
  }, [debouncedSearch])

  const isSearching = debouncedSearch.length > 0
  const sortedItems = isSearching
    ? searchResults
    : [...rootItems].sort((a, b) => {
        if (a.is_dir !== b.is_dir) return a.is_dir ? -1 : 1
        return a.name.toLowerCase().localeCompare(b.name.toLowerCase())
      })

  return (
    <div
      className="flex flex-col h-full overflow-hidden"
      style={{
        width: '280px',
        flexShrink: 0,
        background: 'var(--bg-sidebar)',
        borderRight: '1px solid var(--border)',
      }}
    >
      {/* Breadcrumbs */}
      <div
        className="flex items-center gap-1 px-3 py-2 text-xs flex-wrap border-b"
        style={{ borderColor: 'var(--border)', minHeight: '36px' }}
      >
        <button
          onClick={() => onNavigate('workspace')}
          className="flex items-center gap-1 transition-colors"
          style={{ color: 'var(--text-muted)' }}
          onMouseEnter={e => (e.currentTarget.style.color = 'var(--evo-green)')}
          onMouseLeave={e => (e.currentTarget.style.color = 'var(--text-muted)')}
        >
          <Home size={11} />
          <span>workspace</span>
        </button>
        {breadcrumbs.slice(1).map((bc) => (
          <span key={bc.path} className="flex items-center gap-1">
            <ChevronRight size={10} style={{ color: 'var(--text-muted)' }} />
            <button
              onClick={() => onNavigate(bc.path)}
              className="transition-colors"
              style={{ color: 'var(--text-muted)' }}
              onMouseEnter={e => (e.currentTarget.style.color = 'var(--evo-green)')}
              onMouseLeave={e => (e.currentTarget.style.color = 'var(--text-muted)')}
            >
              {bc.name}
            </button>
          </span>
        ))}
      </div>

      {/* Search */}
      <div className="px-2 py-2 border-b" style={{ borderColor: 'var(--border)' }}>
        <div className="relative">
          <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2" style={{ color: 'var(--text-muted)' }} />
          <input
            type="text"
            placeholder="Filtrar..."
            value={search}
            onChange={e => setSearch(e.target.value)}
            className="w-full text-xs rounded-md pl-8 pr-3 py-1.5 outline-none transition-colors"
            style={{
              background: 'var(--bg-card)',
              border: '1px solid var(--border)',
              color: 'var(--text-primary)',
            }}
            onFocus={e => (e.currentTarget.style.borderColor = 'var(--evo-green)')}
            onBlur={e => (e.currentTarget.style.borderColor = 'var(--border)')}
          />
        </div>
      </div>

      {/* Tree */}
      <div className="flex-1 overflow-y-auto py-1">
        {loading || (isSearching && searching) ? (
          <div className="space-y-1 px-3 py-2">
            {[80, 60, 90, 50, 75, 65].map((w, i) => (
              <div key={i} className="h-5 rounded skeleton" style={{ width: `${w}%` }} />
            ))}
          </div>
        ) : sortedItems.length === 0 ? (
          <div className="px-3 py-4 text-xs" style={{ color: 'var(--text-muted)' }}>
            {isSearching ? 'Nenhum resultado' : 'Vazio'}
          </div>
        ) : isSearching ? (
          sortedItems.map(node => (
            <SearchResultItem
              key={node.path}
              node={node}
              selectedPath={selectedPath}
              onSelect={onSelect}
              searchTerm={debouncedSearch}
            />
          ))
        ) : (
          sortedItems.map(node => (
            <TreeItem
              key={node.path}
              node={node}
              level={0}
              selectedPath={selectedPath}
              onSelect={onSelect}
              onNavigate={onNavigate}
              searchTerm={debouncedSearch}
            />
          ))
        )}
      </div>
    </div>
  )
}
