import { useState, useEffect, useRef, useCallback } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { CheckCircle, Copy, Check, PanelLeftOpen } from 'lucide-react'
import { api } from '../lib/api'
import FileTree from '../components/workspace/FileTree'
import FileToolbar, { type EditorMode } from '../components/workspace/FileToolbar'
import FilePreview from '../components/workspace/FilePreview'
import FileEditor from '../components/workspace/FileEditor'
import ConfirmDialog, { type ConfirmVariant } from '../components/workspace/ConfirmDialog'
import UploadDropzone from '../components/workspace/UploadDropzone'
import FileTabs, { type TabEntry } from '../components/workspace/FileTabs'
import ShareDialog from '../components/workspace/ShareDialog'

const API_BASE = import.meta.env.DEV ? 'http://localhost:8080' : ''

// Toast
interface Toast {
  id: number
  message: string
  type: 'success' | 'error'
}

let toastCounter = 0

// Inline name input dialog
interface NameDialogProps {
  title: string
  placeholder: string
  defaultValue?: string
  onConfirm: (value: string) => void
  onCancel: () => void
}

function NameDialog({ title, placeholder, defaultValue = '', onConfirm, onCancel }: NameDialogProps) {
  const [value, setValue] = useState(defaultValue)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    inputRef.current?.focus()
    inputRef.current?.select()
    const handleKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onCancel() }
    window.addEventListener('keydown', handleKey)
    return () => window.removeEventListener('keydown', handleKey)
  }, [onCancel])

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center"
      style={{ background: 'rgba(0,0,0,0.6)' }}
      onClick={e => { if (e.target === e.currentTarget) onCancel() }}
    >
      <div
        className="w-full max-w-sm mx-4 p-5 rounded-xl"
        style={{
          background: 'var(--bg-card)',
          border: '1px solid var(--border)',
          boxShadow: '0 25px 50px rgba(0,0,0,0.5)',
        }}
      >
        <p className="text-sm font-semibold mb-3" style={{ color: 'var(--text-primary)' }}>{title}</p>
        <input
          ref={inputRef}
          type="text"
          value={value}
          onChange={e => setValue(e.target.value)}
          placeholder={placeholder}
          onKeyDown={e => { if (e.key === 'Enter' && value.trim()) onConfirm(value.trim()) }}
          className="w-full text-sm rounded-lg px-3 py-2 outline-none mb-4"
          style={{
            background: 'var(--bg-primary)',
            border: '1px solid var(--border)',
            color: 'var(--text-primary)',
          }}
          onFocus={e => (e.currentTarget.style.borderColor = 'var(--evo-green)')}
          onBlur={e => (e.currentTarget.style.borderColor = 'var(--border)')}
        />
        <div className="flex gap-2 justify-end">
          <button
            onClick={onCancel}
            className="px-3 py-1.5 text-sm rounded-lg transition-colors"
            style={{ color: 'var(--text-secondary)', border: '1px solid var(--border)' }}
          >
            Cancelar
          </button>
          <button
            onClick={() => value.trim() && onConfirm(value.trim())}
            className="px-3 py-1.5 text-sm font-medium rounded-lg transition-colors"
            style={{
              background: 'var(--evo-green)',
              color: '#0C111D',
              opacity: value.trim() ? 1 : 0.5,
            }}
          >
            Confirmar
          </button>
        </div>
      </div>
    </div>
  )
}

export default function Workspace() {
  const location = useLocation()
  const navigate = useNavigate()
  const [selectedPath, setSelectedPath] = useState<string | null>(null)
  const [isDir, setIsDir] = useState(false)
  const [mode, setMode] = useState<EditorMode>('preview')
  // Per-tab state — restore from localStorage on mount
  const [openTabs, setOpenTabs] = useState<TabEntry[]>(() => {
    try {
      const saved = localStorage.getItem('evo:workspace:tabs')
      return saved ? JSON.parse(saved) : []
    } catch { return [] }
  })
  const [dirtyTabs, setDirtyTabs] = useState<Set<string>>(new Set())
  const [tabEditorContents, setTabEditorContents] = useState<Record<string, string>>({})
  const [tabModes, setTabModes] = useState<Record<string, EditorMode>>({})
  // Persist tabs to localStorage
  useEffect(() => {
    try { localStorage.setItem('evo:workspace:tabs', JSON.stringify(openTabs)) } catch {}
  }, [openTabs])

  // Derived: is the active tab dirty?
  const isDirty = dirtyTabs.has(selectedPath ?? '')
  const [editorContent, setEditorContent] = useState<string | null>(null)
  const [refreshTrigger, setRefreshTrigger] = useState(0)
  const [toasts, setToasts] = useState<Toast[]>([])
  const [showUpload, setShowUpload] = useState(false)
  const [showDrag, setShowDrag] = useState(false)

  // Confirm dialog state
  const [confirmDialog, setConfirmDialog] = useState<{
    variant: ConfirmVariant
    filename: string
    onConfirm: () => void
  } | null>(null)

  // Name dialog
  const [nameDialog, setNameDialog] = useState<NameDialogProps | null>(null)

  // Share dialog
  const [showShareDialog, setShowShareDialog] = useState(false)

  const handleShare = useCallback(() => {
    if (!selectedPath || isDir) return
    setShowShareDialog(true)
  }, [selectedPath, isDir])

  // Editor content getter ref
  const editorRef = useRef<{ getContent: () => string } | null>(null)

  // Overwrite promise resolvers
  const overwriteResolverRef = useRef<((v: boolean) => void) | null>(null)

  // Track which paths we already know are directories so the URL→state effect
  // can skip the redundant /api/workspace/file probe (the file probe returns
  // 400 for directories — harmless but noisy and re-triggers setSelectedPath)
  const knownDirsRef = useRef<Set<string>>(new Set())

  const showToast = useCallback((message: string, type: 'success' | 'error' = 'success') => {
    const id = ++toastCounter
    setToasts(prev => [...prev, { id, message, type }])
    setTimeout(() => setToasts(prev => prev.filter(t => t.id !== id)), 3000)
  }, [])

  // beforeunload guard — fires if any tab has unsaved changes
  useEffect(() => {
    const handler = (e: BeforeUnloadEvent) => {
      if (dirtyTabs.size > 0) {
        e.preventDefault()
      }
    }
    window.addEventListener('beforeunload', handler)
    return () => window.removeEventListener('beforeunload', handler)
  }, [dirtyTabs])

  // Deep-link: sync URL → selectedPath
  // URL shape: /workspace/<path>
  //   /workspace                                    → no selection
  //   /workspace/finance/x.md                       → treated as workspace/finance/x.md
  //   /workspace/.claude/agents/apex.md             → admin scope, kept as-is
  useEffect(() => {
    const raw = location.pathname.replace(/^\/workspace\/?/, '')
    if (!raw) {
      if (selectedPath !== null) setSelectedPath(null)
      return
    }
    let decoded = decodeURIComponent(raw.replace(/\/$/, ''))
    // Normalize: if path doesn't start with a known top-level root,
    // assume it's relative to workspace/
    const ADMIN_PREFIXES = ['workspace/', 'workspace', '.claude/', '.claude', 'config/', 'config', 'docs/', 'docs']
    const hasKnownPrefix = ADMIN_PREFIXES.some(p => decoded === p || decoded.startsWith(p + '/'))
    if (!hasKnownPrefix) {
      decoded = `workspace/${decoded}`
    }
    if (decoded === selectedPath) return
    // If we already know this path is a directory (e.g. user just clicked on
    // it in the tree), skip the file probe — it would 400 and noisily re-fire
    // setSelectedPath. Just confirm via tree and set state.
    if (knownDirsRef.current.has(decoded)) {
      setSelectedPath(decoded)
      setIsDir(true)
      setMode('preview')
      return
    }
    ;(async () => {
      try {
        // Probe as file first; fall back to dir on 400
        const res = await fetch(`${API_BASE}/api/workspace/file?path=${encodeURIComponent(decoded)}`, {
          credentials: 'include',
        })
        if (res.ok) {
          setSelectedPath(decoded)
          setIsDir(false)
          setMode('preview')
          return
        }
        // Try as tree (directory)
        const treeRes = await fetch(`${API_BASE}/api/workspace/tree?path=${encodeURIComponent(decoded)}&depth=1`, {
          credentials: 'include',
        })
        if (treeRes.ok) {
          knownDirsRef.current.add(decoded)
          setSelectedPath(decoded)
          setIsDir(true)
          setMode('preview')
        }
      } catch {
        // ignore — leave state as-is
      }
    })()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location.pathname])

  // Sync selectedPath → URL (strip leading "workspace/" to keep URLs short)
  useEffect(() => {
    const currentUrlPath = location.pathname.replace(/^\/workspace\/?/, '')
    let decodedCurrent = decodeURIComponent(currentUrlPath.replace(/\/$/, ''))
    // Normalize same way the reverse sync does, for comparison
    const ADMIN_PREFIXES = ['workspace/', 'workspace', '.claude/', '.claude', 'config/', 'config', 'docs/', 'docs']
    const hasKnownPrefix = ADMIN_PREFIXES.some(p => decodedCurrent === p || decodedCurrent.startsWith(p + '/'))
    if (decodedCurrent && !hasKnownPrefix) {
      decodedCurrent = `workspace/${decodedCurrent}`
    }
    const target = selectedPath ?? ''
    if (decodedCurrent === target) return
    // Strip leading "workspace/" from URL for brevity
    const urlPath = target.startsWith('workspace/') ? target.slice('workspace/'.length) : target
    const next = urlPath ? `/workspace/${urlPath.split('/').map(encodeURIComponent).join('/')}` : '/workspace'
    navigate(next, { replace: true })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedPath])

  // Global drag detection for dropzone overlay
  useEffect(() => {
    let dragCounter = 0
    const onDragEnter = (e: DragEvent) => {
      if (e.dataTransfer?.types.includes('Files')) {
        dragCounter++
        setShowDrag(true)
      }
    }
    const onDragLeave = () => {
      dragCounter--
      if (dragCounter <= 0) { dragCounter = 0; setShowDrag(false) }
    }
    const onDrop = () => { dragCounter = 0; setShowDrag(false) }
    window.addEventListener('dragenter', onDragEnter)
    window.addEventListener('dragleave', onDragLeave)
    window.addEventListener('drop', onDrop)
    return () => {
      window.removeEventListener('dragenter', onDragEnter)
      window.removeEventListener('dragleave', onDragLeave)
      window.removeEventListener('drop', onDrop)
    }
  }, [])

  // Save current tab editor state before switching away
  const saveCurrentTabState = useCallback(() => {
    if (!selectedPath) return
    setTabModes(prev => ({ ...prev, [selectedPath]: mode }))
    if (mode === 'edit') {
      const content = editorRef.current?.getContent() ?? editorContent ?? ''
      setTabEditorContents(prev => ({ ...prev, [selectedPath]: content }))
    }
  }, [selectedPath, mode, editorContent])

  const handleSelect = useCallback((path: string, dir: boolean) => {
    // Directories: just navigate, no tab
    if (dir) {
      knownDirsRef.current.add(path)
      saveCurrentTabState()
      setSelectedPath(path)
      setIsDir(true)
      setMode('preview')
      setEditorContent(null)
      return
    }

    // Check if tab already open
    const alreadyOpen = openTabs.some(t => t.path === path)

    if (alreadyOpen) {
      // Just switch to it
      saveCurrentTabState()
      const savedMode = tabModes[path] ?? 'preview'
      const savedContent = tabEditorContents[path] ?? null
      setSelectedPath(path)
      setIsDir(false)
      setMode(savedMode)
      setEditorContent(savedMode === 'edit' ? savedContent : null)
      return
    }

    // New file: save current state, open new tab
    saveCurrentTabState()
    setOpenTabs(prev => [...prev, { path }])
    setSelectedPath(path)
    setIsDir(false)
    setMode('preview')
    setEditorContent(null)
  }, [openTabs, tabModes, tabEditorContents, saveCurrentTabState])

  const markTabDirty = useCallback((path: string, dirty: boolean) => {
    setDirtyTabs(prev => {
      const next = new Set(prev)
      if (dirty) next.add(path)
      else next.delete(path)
      return next
    })
  }, [])

  const handleTabSwitch = useCallback((path: string) => {
    if (path === selectedPath) return
    saveCurrentTabState()
    const savedMode = tabModes[path] ?? 'preview'
    const savedContent = tabEditorContents[path] ?? null
    setSelectedPath(path)
    setIsDir(false)
    setMode(savedMode)
    setEditorContent(savedMode === 'edit' ? savedContent : null)
  }, [selectedPath, tabModes, tabEditorContents, saveCurrentTabState])

  const handleTabClose = useCallback((path: string) => {
    const isCurrentlyActive = path === selectedPath

    const performClose = () => {
      setOpenTabs(prev => {
        const idx = prev.findIndex(t => t.path === path)
        const next = prev.filter(t => t.path !== path)

        if (isCurrentlyActive && next.length > 0) {
          // Switch to adjacent tab
          const newActive = next[idx] ?? next[idx - 1]
          const savedMode = tabModes[newActive.path] ?? 'preview'
          const savedContent = tabEditorContents[newActive.path] ?? null
          setSelectedPath(newActive.path)
          setIsDir(false)
          setMode(savedMode)
          setEditorContent(savedMode === 'edit' ? savedContent : null)
        } else if (isCurrentlyActive && next.length === 0) {
          setSelectedPath(null)
          setIsDir(false)
          setMode('preview')
          setEditorContent(null)
        }

        return next
      })
      // Clean up per-tab state
      setDirtyTabs(prev => { const n = new Set(prev); n.delete(path); return n })
      setTabEditorContents(prev => { const n = { ...prev }; delete n[path]; return n })
      setTabModes(prev => { const n = { ...prev }; delete n[path]; return n })
    }

    if (dirtyTabs.has(path)) {
      setConfirmDialog({
        variant: 'discard',
        filename: path.split('/').pop() ?? '',
        onConfirm: () => {
          setConfirmDialog(null)
          performClose()
        },
      })
    } else {
      performClose()
    }
  }, [selectedPath, dirtyTabs, tabModes, tabEditorContents])

  const handleCloseAll = useCallback(() => {
    setOpenTabs([])
    setSelectedPath(null)
    setIsDir(false)
    setMode('preview')
    setEditorContent(null)
    setDirtyTabs(new Set())
    setTabEditorContents({})
    setTabModes({})
  }, [])

  const handleCloseOthers = useCallback((keepPath: string) => {
    setOpenTabs(prev => prev.filter(t => t.path === keepPath))
    setSelectedPath(keepPath)
    setIsDir(false)
    // Clean up state for closed tabs
    setDirtyTabs(prev => {
      const n = new Set<string>()
      if (prev.has(keepPath)) n.add(keepPath)
      return n
    })
  }, [])

  const handleCloseToLeft = useCallback((path: string) => {
    setOpenTabs(prev => {
      const idx = prev.findIndex(t => t.path === path)
      return idx > 0 ? prev.slice(idx) : prev
    })
  }, [])

  const handleCloseToRight = useCallback((path: string) => {
    setOpenTabs(prev => {
      const idx = prev.findIndex(t => t.path === path)
      return idx >= 0 ? prev.slice(0, idx + 1) : prev
    })
  }, [])

  const handleEdit = useCallback(async () => {
    if (!selectedPath || isDir) return
    try {
      const data = await api.get(`/workspace/file?path=${encodeURIComponent(selectedPath)}`)
      if (typeof data.content !== 'string') {
        showToast('Arquivo binário ou muito grande para editar', 'error')
        return
      }
      setEditorContent(data.content)
      setTabEditorContents(prev => ({ ...prev, [selectedPath]: data.content }))
      setMode('edit')
      setTabModes(prev => ({ ...prev, [selectedPath]: 'edit' }))
    } catch {
      showToast('Erro ao carregar arquivo para edição', 'error')
    }
  }, [selectedPath, isDir, showToast])

  const handleSave = useCallback(async (content?: string) => {
    if (!selectedPath) return
    const finalContent = content ?? editorRef.current?.getContent() ?? editorContent ?? ''
    try {
      await api.put('/workspace/file', { path: selectedPath, content: finalContent })
      markTabDirty(selectedPath, false)
      showToast('Arquivo salvo')
    } catch {
      showToast('Erro ao salvar arquivo', 'error')
    }
  }, [selectedPath, editorContent, showToast, markTabDirty])

  const handleCancel = useCallback(() => {
    if (isDirty) {
      setConfirmDialog({
        variant: 'discard',
        filename: selectedPath?.split('/').pop() ?? '',
        onConfirm: () => {
          setConfirmDialog(null)
          setMode('preview')
          setTabModes(prev => selectedPath ? { ...prev, [selectedPath]: 'preview' } : prev)
          if (selectedPath) markTabDirty(selectedPath, false)
          setEditorContent(null)
        },
      })
    } else {
      setMode('preview')
      setTabModes(prev => selectedPath ? { ...prev, [selectedPath]: 'preview' } : prev)
      setEditorContent(null)
    }
  }, [isDirty, selectedPath, markTabDirty])

  const handleDelete = useCallback(() => {
    if (!selectedPath) return
    const filename = selectedPath.split('/').pop() ?? ''
    setConfirmDialog({
      variant: 'delete',
      filename,
      onConfirm: async () => {
        setConfirmDialog(null)
        try {
          const pathToDelete = selectedPath
          await api.delete(`/workspace/file?path=${encodeURIComponent(pathToDelete)}`)
          // Close the tab for the deleted file
          setOpenTabs(prev => {
            const idx = prev.findIndex(t => t.path === pathToDelete)
            const next = prev.filter(t => t.path !== pathToDelete)
            if (next.length > 0) {
              const newActive = next[idx] ?? next[idx - 1]
              const savedMode = tabModes[newActive.path] ?? 'preview'
              const savedContent = tabEditorContents[newActive.path] ?? null
              setSelectedPath(newActive.path)
              setIsDir(false)
              setMode(savedMode)
              setEditorContent(savedMode === 'edit' ? savedContent : null)
            } else {
              setSelectedPath(null)
              setMode('preview')
              setEditorContent(null)
            }
            return next
          })
          setDirtyTabs(prev => { const n = new Set(prev); n.delete(pathToDelete); return n })
          setTabEditorContents(prev => { const n = { ...prev }; delete n[pathToDelete]; return n })
          setTabModes(prev => { const n = { ...prev }; delete n[pathToDelete]; return n })
          setRefreshTrigger(t => t + 1)
          showToast('Arquivo movido para .trash/')
        } catch {
          showToast('Erro ao excluir arquivo', 'error')
        }
      },
    })
  }, [selectedPath, showToast, tabModes, tabEditorContents])

  const handleRename = useCallback(() => {
    if (!selectedPath) return
    const currentName = selectedPath.split('/').pop() ?? ''
    setNameDialog({
      title: 'Renomear',
      placeholder: 'Novo nome',
      defaultValue: currentName,
      onConfirm: async (newName) => {
        setNameDialog(null)
        const dir = selectedPath.split('/').slice(0, -1).join('/')
        const newPath = dir ? `${dir}/${newName}` : newName
        try {
          const oldPath = selectedPath
          await api.post('/workspace/rename', { from: oldPath, to: newPath })
          // Update tab entry for renamed file
          setOpenTabs(prev => prev.map(t => t.path === oldPath ? { path: newPath } : t))
          // Migrate per-tab state to new path
          setDirtyTabs(prev => {
            const next = new Set(prev)
            if (next.has(oldPath)) { next.delete(oldPath); next.add(newPath) }
            return next
          })
          setTabEditorContents(prev => {
            const n = { ...prev }
            if (oldPath in n) { n[newPath] = n[oldPath]; delete n[oldPath] }
            return n
          })
          setTabModes(prev => {
            const n = { ...prev }
            if (oldPath in n) { n[newPath] = n[oldPath]; delete n[oldPath] }
            return n
          })
          setSelectedPath(newPath)
          setRefreshTrigger(t => t + 1)
          showToast('Renomeado com sucesso')
        } catch {
          showToast('Erro ao renomear', 'error')
        }
      },
      onCancel: () => setNameDialog(null),
    })
  }, [selectedPath, showToast])

  const handleNewFile = useCallback(() => {
    const base = selectedPath
      ? (isDir ? selectedPath : selectedPath.split('/').slice(0, -1).join('/'))
      : 'workspace'
    setNameDialog({
      title: 'Novo arquivo',
      placeholder: 'nome-do-arquivo.md',
      onConfirm: async (name) => {
        setNameDialog(null)
        const path = `${base}/${name}`
        try {
          await api.post('/workspace/file', { path })
          setRefreshTrigger(t => t + 1)
          setOpenTabs(prev => prev.some(t => t.path === path) ? prev : [...prev, { path }])
          setSelectedPath(path)
          setIsDir(false)
          setMode('preview')
          setEditorContent(null)
          showToast('Arquivo criado')
        } catch {
          showToast('Erro ao criar arquivo', 'error')
        }
      },
      onCancel: () => setNameDialog(null),
    })
  }, [selectedPath, isDir, showToast])

  const handleNewFolder = useCallback(() => {
    const base = selectedPath
      ? (isDir ? selectedPath : selectedPath.split('/').slice(0, -1).join('/'))
      : 'workspace'
    setNameDialog({
      title: 'Nova pasta',
      placeholder: 'nome-da-pasta',
      onConfirm: async (name) => {
        setNameDialog(null)
        const path = `${base}/${name}`
        try {
          await api.post('/workspace/folder', { path })
          setRefreshTrigger(t => t + 1)
          showToast('Pasta criada')
        } catch {
          showToast('Erro ao criar pasta', 'error')
        }
      },
      onCancel: () => setNameDialog(null),
    })
  }, [selectedPath, isDir, showToast])

  const handleDownload = useCallback(async () => {
    if (!selectedPath) return
    try {
      const res = await fetch(`${API_BASE}/api/workspace/download?path=${encodeURIComponent(selectedPath)}`, {
        credentials: 'include',
      })
      if (!res.ok) throw new Error()
      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = selectedPath.split('/').pop() ?? 'download'
      a.click()
      URL.revokeObjectURL(url)
    } catch {
      showToast('Erro ao baixar arquivo', 'error')
    }
  }, [selectedPath, showToast])

  const handleConfirmOverwrite = useCallback((filename: string): Promise<boolean> => {
    return new Promise(resolve => {
      overwriteResolverRef.current = resolve
      setConfirmDialog({
        variant: 'overwrite',
        filename,
        onConfirm: () => {
          setConfirmDialog(null)
          overwriteResolverRef.current?.(true)
        },
      })
    })
  }, [])

  // Mobile sidebar toggle
  const [sidebarOpen, setSidebarOpen] = useState(false)

  const [copiedPath, setCopiedPath] = useState(false)

  const handleCopyPath = useCallback(() => {
    if (!selectedPath) return
    navigator.clipboard.writeText(selectedPath).then(() => {
      setCopiedPath(true)
      setTimeout(() => setCopiedPath(false), 1500)
    })
  }, [selectedPath])

  const pageTitle = selectedPath
    ? `${selectedPath}${isDirty ? ' \u25CF' : ''}`
    : 'Workspace'

  return (
    <div className="flex h-full overflow-hidden" style={{ background: 'var(--bg-primary)' }}>
      {/* Mobile overlay */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 bg-black/60 z-40 lg:hidden"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      {/* FileTree — desktop: static sidebar, mobile: slide-over */}
      <div
        className={`
          fixed inset-y-0 left-0 z-50 transition-transform duration-200
          lg:static lg:translate-x-0 lg:z-auto
          ${sidebarOpen ? 'translate-x-0' : '-translate-x-full'}
        `}
      >
        <FileTree
          selectedPath={selectedPath}
          onSelect={(path, dir) => {
            handleSelect(path, dir)
            setSidebarOpen(false)
          }}
          onNavigate={(path) => {
            handleSelect(path, true)
            setSidebarOpen(false)
          }}
          refreshTrigger={refreshTrigger}
        />
      </div>

      {/* Main area */}
      <div className="flex flex-col flex-1 min-w-0 overflow-hidden">
        {/* FileToolbar with mobile toggle */}
        <div className="flex items-center flex-shrink-0" style={{ background: 'var(--bg-card)', borderBottom: '1px solid var(--border)' }}>
          {/* Mobile sidebar toggle */}
          <button
            onClick={() => setSidebarOpen(true)}
            className="lg:hidden flex items-center justify-center flex-shrink-0"
            style={{
              width: '44px',
              height: '44px',
              color: 'var(--text-secondary)',
              borderRight: '1px solid var(--border)',
            }}
          >
            <PanelLeftOpen size={18} />
          </button>
          <div className="flex-1 min-w-0">
            <FileToolbar
              selectedPath={selectedPath}
              isDir={isDir}
              mode={mode}
              isDirty={isDirty}
              onNewFile={handleNewFile}
              onNewFolder={handleNewFolder}
              onUpload={() => setShowUpload(true)}
              onRefresh={() => setRefreshTrigger(t => t + 1)}
              onEdit={handleEdit}
              onSave={() => handleSave()}
              onCancel={handleCancel}
              onRename={handleRename}
              onDelete={handleDelete}
              onDownload={handleDownload}
              onShare={handleShare}
            />
          </div>
        </div>

        {/* File tabs */}
        <FileTabs
          tabs={openTabs}
          activePath={selectedPath}
          dirtyPaths={dirtyTabs}
          onSwitch={handleTabSwitch}
          onClose={handleTabClose}
          onCloseAll={handleCloseAll}
          onCloseOthers={handleCloseOthers}
          onCloseToLeft={handleCloseToLeft}
          onCloseToRight={handleCloseToRight}
        />

        {/* Path title */}
        {selectedPath && (
          <div
            className="px-4 py-2 text-xs font-mono flex items-center gap-2 border-b flex-shrink-0"
            style={{
              borderColor: 'var(--border)',
              background: 'var(--bg-card)',
              color: isDirty ? 'var(--warning)' : 'var(--text-muted)',
            }}
          >
            <span className="truncate flex-1">{pageTitle}</span>
            <button
              onClick={handleCopyPath}
              className="flex items-center gap-1 px-1.5 py-0.5 rounded transition-colors flex-shrink-0"
              style={{
                color: copiedPath ? 'var(--evo-green)' : 'var(--text-muted)',
                background: 'transparent',
              }}
              onMouseEnter={e => { e.currentTarget.style.background = 'var(--surface-hover)' }}
              onMouseLeave={e => { e.currentTarget.style.background = 'transparent' }}
              title="Copiar path"
            >
              {copiedPath ? <Check size={12} /> : <Copy size={12} />}
              <span>{copiedPath ? 'Copiado' : 'Copiar'}</span>
            </button>
          </div>
        )}

        {/* Content area */}
        <div className="flex-1 overflow-hidden">
          {!selectedPath && (
            <div className="flex items-center justify-center h-full">
              <div className="text-center">
                <div
                  className="mx-auto mb-4 flex items-center justify-center w-16 h-16 rounded-2xl"
                  style={{ background: 'var(--bg-card)', border: '1px solid var(--border)' }}
                >
                  <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="var(--text-muted)" strokeWidth="1.5">
                    <path d="M3 7V5a2 2 0 012-2h6l2 2h8a2 2 0 012 2v12a2 2 0 01-2 2H5a2 2 0 01-2-2V7z" />
                  </svg>
                </div>
                <p className="text-sm" style={{ color: 'var(--text-muted)' }}>
                  Selecione um arquivo para visualizar
                </p>
              </div>
            </div>
          )}

          {selectedPath && !isDir && mode === 'preview' && (
            <FilePreview path={selectedPath} onDownload={handleDownload} />
          )}

          {selectedPath && !isDir && mode === 'edit' && editorContent !== null && (
            <FileEditor
              initialContent={editorContent}
              path={selectedPath}
              onDirtyChange={(dirty) => markTabDirty(selectedPath, dirty)}
              onSave={handleSave}
              editorRef={editorRef}
            />
          )}

          {selectedPath && isDir && (
            <div className="flex items-center justify-center h-full">
              <p className="text-sm" style={{ color: 'var(--text-muted)' }}>
                Selecione um arquivo para visualizar
              </p>
            </div>
          )}
        </div>
      </div>

      {/* Modals */}
      {confirmDialog && (
        <ConfirmDialog
          variant={confirmDialog.variant}
          filename={confirmDialog.filename}
          onConfirm={confirmDialog.onConfirm}
          onCancel={() => {
            if (confirmDialog.variant === 'overwrite') {
              overwriteResolverRef.current?.(false)
            }
            setConfirmDialog(null)
          }}
        />
      )}

      {nameDialog && <NameDialog {...nameDialog} />}

      {/* Share dialog */}
      {showShareDialog && selectedPath && (
        <ShareDialog
          path={selectedPath}
          onClose={() => setShowShareDialog(false)}
        />
      )}

      {/* Upload dropzone */}
      {(showUpload || showDrag) && (
        <UploadDropzone
          currentPath={selectedPath && isDir ? selectedPath : selectedPath?.split('/').slice(0, -1).join('/') ?? 'workspace'}
          onUploadComplete={() => {
            setRefreshTrigger(t => t + 1)
            showToast('Upload concluído')
          }}
          onClose={() => { setShowUpload(false); setShowDrag(false) }}
          onConfirmOverwrite={handleConfirmOverwrite}
        />
      )}

      {/* Toast container */}
      <div
        className="fixed bottom-4 right-4 z-50 flex flex-col gap-2"
        style={{ pointerEvents: 'none' }}
        aria-live="polite"
      >
        {toasts.map(toast => (
          <div
            key={toast.id}
            className="flex items-center gap-2.5 px-4 py-3 rounded-xl text-sm font-medium"
            style={{
              background: 'var(--bg-card)',
              border: `1px solid ${toast.type === 'success' ? 'rgba(0,255,167,0.3)' : 'rgba(239,68,68,0.3)'}`,
              color: toast.type === 'success' ? 'var(--evo-green)' : 'var(--danger)',
              boxShadow: '0 8px 24px rgba(0,0,0,0.4)',
              animation: 'slideInRight 200ms ease-out',
              pointerEvents: 'auto',
            }}
          >
            <CheckCircle size={15} />
            {toast.message}
          </div>
        ))}
      </div>

      {/* Toast animation */}
      <style>{`
        @keyframes slideInRight {
          from { transform: translateX(100%); opacity: 0; }
          to { transform: translateX(0); opacity: 1; }
        }
        @media (prefers-reduced-motion: reduce) {
          * { animation: none !important; transition: none !important; }
        }
      `}</style>
    </div>
  )
}
