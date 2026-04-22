import { useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import {
  Folder, FileText, FileCode, FileJson, Table, ScrollText, ImageIcon,
  File, FileCog, Search, ChevronRight, ArrowLeft, FolderOpen,
} from 'lucide-react'
import { api } from '../lib/api'
import Markdown from '../components/Markdown'
import { useTranslation } from 'react-i18next'

interface TreeItem {
  name: string
  path: string
  is_dir: boolean
  children_count?: number
  size?: number
  modified?: number
  extension?: string
  icon?: string
  viewable?: boolean
}

interface Breadcrumb {
  name: string
  path: string
}

interface TreeResponse {
  items: TreeItem[]
  breadcrumbs: Breadcrumb[]
  current_path: string
}

const ICON_COMPONENTS: Record<string, typeof File> = {
  'file-code': FileCode,
  'file-text': FileText,
  'file-json': FileJson,
  'file-cog': FileCog,
  'table': Table,
  'scroll-text': ScrollText,
  'image': ImageIcon,
  'file': File,
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function relativeTime(ts: number): string {
  const now = Date.now() / 1000
  const diff = now - ts
  if (diff < 60) return 'just now'
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
  if (diff < 604800) return `${Math.floor(diff / 86400)}d ago`
  return new Date(ts * 1000).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}

export default function Reports() {
  const { t } = useTranslation()
  const { '*': routePath } = useParams()
  const navigate = useNavigate()
  const [tree, setTree] = useState<TreeResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState('')
  const [viewContent, setViewContent] = useState<string | null>(null)
  const [viewItem, setViewItem] = useState<TreeItem | null>(null)

  // Determine if we're viewing a file or browsing a directory
  const currentPath = routePath || ''

  useEffect(() => {
    // Reset view state when path changes
    setViewContent(null)
    setViewItem(null)
    setLoading(true)

    // Try loading as directory first
    api.get(`/workspace/tree?path=${encodeURIComponent(currentPath)}`)
      .then((data) => {
        setTree(data)
        setLoading(false)
      })
      .catch(() => {
        // Not a directory — try loading as a file
        api.getRaw(`/reports/workspace/${currentPath}`)
          .then((content) => {
            const name = currentPath.split('/').pop() || ''
            const ext = '.' + name.split('.').pop()
            setViewItem({ name, path: currentPath, is_dir: false, extension: ext, viewable: true })
            setViewContent(content)
            setLoading(false)
          })
          .catch(() => {
            setTree({ items: [], breadcrumbs: [{ name: 'workspace', path: '' }], current_path: '' })
            setLoading(false)
          })
      })
  }, [currentPath])

  const handleNavigate = (path: string) => {
    navigate(`/workspace/${path}`)
  }

  const handleBack = () => {
    const parts = currentPath.split('/').filter(Boolean)
    parts.pop()
    navigate(`/workspace/${parts.join('/')}`)
  }

  // File viewer
  if (viewContent !== null && viewItem) {
    const isHtml = viewItem.extension === '.html'
    const isMd = viewItem.extension === '.md'
    const isJson = viewItem.extension === '.json'

    return (
      <div className="max-w-[1400px] mx-auto">
        <div className="mb-6">
          <button onClick={handleBack} className="text-[#00FFA7] text-sm hover:underline mb-4 inline-flex items-center gap-1">
            <ArrowLeft size={14} /> Back
          </button>
          <h1 className="text-2xl font-bold text-[#e6edf3] tracking-tight">{viewItem.name}</h1>
          <p className="text-[#667085] text-sm mt-1 font-mono">{currentPath}</p>
        </div>
        <div className="bg-[#161b22] border border-[#21262d] rounded-2xl overflow-hidden">
          {isHtml ? (
            <iframe
              srcDoc={viewContent}
              className="w-full rounded-lg border-0"
              style={{ height: 'calc(100vh - 220px)', minHeight: '600px' }}
              title={viewItem.name}
            />
          ) : isMd ? (
            <div className="p-6"><Markdown>{viewContent}</Markdown></div>
          ) : isJson ? (
            <pre className="p-6 text-sm font-mono text-[#e6edf3] overflow-x-auto whitespace-pre-wrap">
              {(() => { try { return JSON.stringify(JSON.parse(viewContent), null, 2) } catch { return viewContent } })()}
            </pre>
          ) : (
            <pre className="p-6 text-sm font-mono text-[#8b949e] overflow-x-auto whitespace-pre-wrap">{viewContent}</pre>
          )}
        </div>
      </div>
    )
  }

  // Directory browser
  const items = tree?.items || []
  const breadcrumbs = tree?.breadcrumbs || [{ name: 'workspace', path: '' }]

  const filtered = search
    ? items.filter((item) => item.name.toLowerCase().includes(search.toLowerCase()))
    : items

  const dirs = filtered.filter((i) => i.is_dir)
  const files = filtered.filter((i) => !i.is_dir)

  return (
    <div className="max-w-[1400px] mx-auto">
      {/* Header */}
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-[#e6edf3] tracking-tight">{t('reports.title')}</h1>
        <p className="text-[#667085] text-sm mt-1">Browse your workspace files and reports</p>
      </div>

      {/* Breadcrumbs */}
      <div className="flex items-center gap-1 mb-4 text-sm flex-wrap">
        {breadcrumbs.map((bc, i) => (
          <span key={bc.path} className="flex items-center gap-1">
            {i > 0 && <ChevronRight size={14} className="text-[#3F3F46]" />}
            <button
              onClick={() => handleNavigate(bc.path)}
              className={`px-2 py-0.5 rounded transition-colors ${
                i === breadcrumbs.length - 1
                  ? 'text-[#e6edf3] font-medium'
                  : 'text-[#667085] hover:text-[#00FFA7]'
              }`}
            >
              {bc.name}
            </button>
          </span>
        ))}
      </div>

      {/* Search */}
      <div className="relative mb-6">
        <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-[#667085]" />
        <input
          type="text"
          placeholder="Filter files..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="w-full max-w-md bg-[#161b22] border border-[#21262d] rounded-xl pl-9 pr-4 py-2.5 text-sm text-[#e6edf3] placeholder-[#667085] focus:outline-none focus:border-[#00FFA7]/50 transition-all"
        />
      </div>

      {loading ? (
        <div className="space-y-2">
          {[...Array(8)].map((_, i) => (
            <div key={i} className="flex items-center gap-3 p-3 rounded-xl bg-[#161b22] border border-[#21262d]">
              <div className="h-8 w-8 rounded-lg bg-[#21262d] animate-pulse" />
              <div className="h-4 w-48 rounded bg-[#21262d] animate-pulse" />
            </div>
          ))}
        </div>
      ) : filtered.length === 0 ? (
        <div className="text-center py-16">
          <div className="mx-auto mb-4 flex h-16 w-16 items-center justify-center rounded-2xl bg-[#161b22] border border-[#21262d]">
            <FolderOpen size={32} className="text-[#3F3F46]" />
          </div>
          <p className="text-[#667085] text-lg">{search ? 'No matches found' : 'Empty directory'}</p>
        </div>
      ) : (
        <div className="bg-[#161b22] border border-[#21262d] rounded-2xl overflow-hidden">
          {/* Directories */}
          {dirs.map((item) => (
            <button
              key={item.path}
              onClick={() => handleNavigate(item.path)}
              className="w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-[#1c2128] transition-colors border-b border-[#21262d] last:border-b-0 group"
            >
              <div className="flex items-center justify-center w-8 h-8 rounded-lg bg-[#00FFA7]/8 group-hover:bg-[#00FFA7]/15 transition-colors">
                <Folder size={16} className="text-[#00FFA7]" />
              </div>
              <div className="flex-1 min-w-0">
                <span className="text-sm font-medium text-[#e6edf3] group-hover:text-[#00FFA7] transition-colors">{item.name}</span>
              </div>
              <span className="text-xs text-[#667085]">{item.children_count} items</span>
              <ChevronRight size={14} className="text-[#3F3F46] group-hover:text-[#667085] transition-colors" />
            </button>
          ))}

          {/* Files */}
          {files.map((item) => {
            const IconComp = ICON_COMPONENTS[item.icon || 'file'] || File
            return (
              <button
                key={item.path}
                onClick={() => item.viewable ? handleNavigate(item.path) : null}
                disabled={!item.viewable}
                className={`w-full flex items-center gap-3 px-4 py-3 text-left border-b border-[#21262d] last:border-b-0 transition-colors ${
                  item.viewable ? 'hover:bg-[#1c2128] cursor-pointer group' : 'opacity-50 cursor-default'
                }`}
              >
                <div className="flex items-center justify-center w-8 h-8 rounded-lg bg-[#21262d]">
                  <IconComp size={16} className="text-[#8b949e]" />
                </div>
                <div className="flex-1 min-w-0">
                  <span className={`text-sm text-[#e6edf3] ${item.viewable ? 'group-hover:text-[#00FFA7]' : ''} transition-colors truncate block`}>
                    {item.name}
                  </span>
                </div>
                <span className="text-xs text-[#667085] hidden sm:block">{item.size ? formatSize(item.size) : ''}</span>
                <span className="text-xs text-[#667085] hidden md:block w-20 text-right">{item.modified ? relativeTime(item.modified) : ''}</span>
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}
