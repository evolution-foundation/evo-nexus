import { useState, useRef, useEffect } from 'react'
import { Upload as UploadIcon, X, CheckCircle, XCircle, RefreshCw } from 'lucide-react'
import { useAuth } from '../../context/AuthContext'
import { useKnowledge } from '../../context/KnowledgeContext'

const API = import.meta.env.DEV ? 'http://localhost:8080' : ''

type FilePhase = 'queued' | 'scanning' | 'parsing' | 'chunking' | 'embedding' | 'storing' | 'classifying' | 'done' | 'error'

const PHASES: FilePhase[] = ['scanning', 'parsing', 'chunking', 'embedding', 'storing', 'classifying', 'done']

interface UploadFile {
  id: string
  file: File
  phase: FilePhase
  error?: string
  documentId?: string
}

interface Space {
  id: string
  name: string
}

interface Unit {
  id: string
  title: string
}

function PhaseBar({ phase }: { phase: FilePhase }) {
  if (phase === 'done') return <div className="h-1.5 bg-[#00FFA7] rounded-full w-full" />
  if (phase === 'error') return <div className="h-1.5 bg-red-500 rounded-full w-full" />
  if (phase === 'queued') return <div className="h-1.5 bg-white/10 rounded-full w-full" />
  const idx = PHASES.indexOf(phase)
  const pct = Math.round(((idx + 1) / (PHASES.length - 1)) * 100)
  return (
    <div className="h-1.5 bg-[#0C111D] rounded-full overflow-hidden">
      <div className="h-full bg-[#00FFA7] transition-all duration-500" style={{ width: `${pct}%` }} />
    </div>
  )
}

export default function KnowledgeUpload() {
  const { hasPermission } = useAuth()
  const { activeConnectionId } = useKnowledge()
  const canManage = hasPermission('knowledge', 'manage')

  const [files, setFiles] = useState<UploadFile[]>([])
  const [dragging, setDragging] = useState(false)
  const [spaces, setSpaces] = useState<Space[]>([])
  const [units, setUnits] = useState<Unit[]>([])
  const [selectedSpaceId, setSelectedSpaceId] = useState('')
  const [selectedUnitId, setSelectedUnitId] = useState('')
  const [uploading, setUploading] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const pollers = useRef<Record<string, ReturnType<typeof setInterval>>>({})

  // Load spaces
  useEffect(() => {
    if (!activeConnectionId) return
    fetch(`${API}/api/knowledge/connections/${activeConnectionId}/spaces`, { credentials: 'include' })
      .then((r) => r.ok ? r.json() : null)
      .then((data) => {
        const list: Space[] = data?.spaces || data || []
        setSpaces(list)
        if (list.length > 0) setSelectedSpaceId(list[0].id)
      })
      .catch(() => {})
  }, [activeConnectionId])

  // Load units when space changes
  useEffect(() => {
    if (!activeConnectionId || !selectedSpaceId) return
    setSelectedUnitId('')
    fetch(`${API}/api/knowledge/connections/${activeConnectionId}/spaces/${selectedSpaceId}/units`, { credentials: 'include' })
      .then((r) => r.ok ? r.json() : null)
      .then((data) => {
        const list: Unit[] = data?.units || data || []
        setUnits(list)
      })
      .catch(() => {})
  }, [activeConnectionId, selectedSpaceId])

  // Cleanup pollers on unmount
  useEffect(() => {
    return () => {
      Object.values(pollers.current).forEach(clearInterval)
    }
  }, [])

  function addFiles(newFiles: File[]) {
    const toAdd: UploadFile[] = newFiles.map((f) => ({
      id: `${f.name}-${Date.now()}-${Math.random()}`,
      file: f,
      phase: 'queued',
    }))
    setFiles((prev) => [...prev, ...toAdd])
  }

  function handleDrop(e: React.DragEvent) {
    e.preventDefault()
    setDragging(false)
    const dropped = Array.from(e.dataTransfer.files)
    if (dropped.length > 0) addFiles(dropped)
  }

  function removeFile(id: string) {
    if (pollers.current[id]) { clearInterval(pollers.current[id]); delete pollers.current[id] }
    setFiles((prev) => prev.filter((f) => f.id !== id))
  }

  function setPhase(id: string, phase: FilePhase, extra?: Partial<UploadFile>) {
    setFiles((prev) => prev.map((f) => f.id === id ? { ...f, phase, ...extra } : f))
  }

  function pollStatus(uploadId: string, docId: string) {
    const interval = setInterval(async () => {
      try {
        const res = await fetch(`${API}/api/knowledge/documents/${docId}/status`, { credentials: 'include' })
        if (!res.ok) { clearInterval(interval); return }
        const data = await res.json()
        const status = data.status as string
        if (status === 'ready') {
          clearInterval(interval)
          delete pollers.current[uploadId]
          setPhase(uploadId, 'done')
        } else if (status === 'error') {
          clearInterval(interval)
          delete pollers.current[uploadId]
          setPhase(uploadId, 'error', { error: data.error_message || 'Processing failed' })
        } else {
          // Map backend status to phase
          const phaseMap: Record<string, FilePhase> = {
            processing: 'parsing',
            parsing: 'parsing',
            chunking: 'chunking',
            embedding: 'embedding',
            storing: 'storing',
            classifying: 'classifying',
          }
          setPhase(uploadId, phaseMap[status] || 'parsing')
        }
      } catch {}
    }, 2000)
    pollers.current[uploadId] = interval
  }

  async function handleUpload() {
    if (!activeConnectionId || !selectedSpaceId) return
    const pending = files.filter((f) => f.phase === 'queued')
    if (pending.length === 0) return

    setUploading(true)
    for (const item of pending) {
      setPhase(item.id, 'scanning')
      try {
        const formData = new FormData()
        formData.append('file', item.file)
        formData.append('space_id', selectedSpaceId)
        if (selectedUnitId) formData.append('unit_id', selectedUnitId)

        const res = await fetch(
          `${API}/api/knowledge/connections/${activeConnectionId}/documents`,
          { method: 'POST', body: formData, credentials: 'include' }
        )
        if (!res.ok) {
          const err = await res.json().catch(() => ({}))
          setPhase(item.id, 'error', { error: err.error || `Upload failed: ${res.statusText}` })
          continue
        }
        const data = await res.json()
        const docId = data.id || data.document_id
        setPhase(item.id, 'parsing', { documentId: docId })
        if (docId) pollStatus(item.id, docId)
      } catch (e) {
        setPhase(item.id, 'error', { error: e instanceof Error ? e.message : 'Upload failed' })
      }
    }
    setUploading(false)
  }

  if (!canManage) {
    return <div className="text-center py-12 text-[#667085] text-sm">You don&apos;t have permission to upload documents.</div>
  }

  if (!activeConnectionId) {
    return <div className="text-center py-12 text-[#667085] text-sm">Select a connection using the switcher above.</div>
  }

  const hasPending = files.some((f) => f.phase === 'queued')

  return (
    <div className="space-y-4 max-w-2xl">
      {/* Space + Unit selectors */}
      <div className="flex items-center gap-3 flex-wrap">
        <div>
          <label className="block text-xs text-[#667085] mb-1">Space *</label>
          <select
            value={selectedSpaceId}
            onChange={(e) => setSelectedSpaceId(e.target.value)}
            className="bg-[#182230] border border-[#344054] rounded-lg px-3 py-2 text-sm text-[#D0D5DD] focus:border-[#00FFA7] focus:outline-none"
          >
            {spaces.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
        </div>
        {units.length > 0 && (
          <div>
            <label className="block text-xs text-[#667085] mb-1">Unit (optional)</label>
            <select
              value={selectedUnitId}
              onChange={(e) => setSelectedUnitId(e.target.value)}
              className="bg-[#182230] border border-[#344054] rounded-lg px-3 py-2 text-sm text-[#D0D5DD] focus:border-[#00FFA7] focus:outline-none"
            >
              <option value="">No unit</option>
              {units.map((u) => <option key={u.id} value={u.id}>{u.title}</option>)}
            </select>
          </div>
        )}
      </div>

      {/* Drop zone */}
      <div
        onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
        onDragLeave={() => setDragging(false)}
        onDrop={handleDrop}
        onClick={() => fileInputRef.current?.click()}
        className={`border-2 border-dashed rounded-xl p-10 text-center cursor-pointer transition-colors ${
          dragging ? 'border-[#00FFA7] bg-[#00FFA7]/5' : 'border-[#344054] hover:border-[#00FFA7]/40 hover:bg-white/2'
        }`}
      >
        <UploadIcon size={28} className={`mx-auto mb-3 ${dragging ? 'text-[#00FFA7]' : 'text-[#667085]'}`} />
        <p className="text-sm font-medium text-[#D0D5DD]">Drag files here or click to browse</p>
        <p className="text-xs text-[#667085] mt-1">PDF, DOCX, PPTX, XLSX, HTML, EPUB, TXT, MD, CSV, JSON, images · Max 100 MB per file</p>
        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept=".pdf,.docx,.pptx,.xlsx,.html,.htm,.epub,.txt,.md,.markdown,.csv,.json,.png,.jpg,.jpeg,.gif,.webp,.tiff,.tif"
          className="hidden"
          onChange={(e) => {
            if (e.target.files) {
              addFiles(Array.from(e.target.files))
              e.target.value = ''
            }
          }}
        />
      </div>

      {/* File list */}
      {files.length > 0 && (
        <div className="space-y-2">
          {files.map((item) => (
            <div key={item.id} className="bg-[#182230] border border-[#344054] rounded-xl p-4">
              <div className="flex items-center gap-3 mb-2">
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-[#D0D5DD] truncate">{item.file.name}</p>
                  <p className="text-xs text-[#667085]">
                    {(item.file.size / 1024 / 1024).toFixed(1)} MB
                    {' · '}
                    {item.phase === 'done' ? (
                      <span className="text-[#00FFA7]">Done</span>
                    ) : item.phase === 'error' ? (
                      <span className="text-red-400">{item.error}</span>
                    ) : item.phase === 'queued' ? (
                      <span>Queued</span>
                    ) : (
                      <span className="capitalize text-[#00FFA7]">{item.phase}...</span>
                    )}
                  </p>
                </div>
                <div className="flex items-center gap-1.5 shrink-0">
                  {item.phase === 'done' && <CheckCircle size={16} className="text-[#00FFA7]" />}
                  {item.phase === 'error' && <XCircle size={16} className="text-red-400" />}
                  {(item.phase !== 'scanning' && item.phase !== 'parsing' && item.phase !== 'chunking' && item.phase !== 'embedding' && item.phase !== 'storing' && item.phase !== 'classifying') && (
                    <button
                      onClick={() => removeFile(item.id)}
                      className="p-1 rounded text-[#667085] hover:text-[#D0D5DD] hover:bg-white/5 transition-colors"
                    >
                      <X size={12} />
                    </button>
                  )}
                </div>
              </div>
              <PhaseBar phase={item.phase} />
            </div>
          ))}

          <div className="flex items-center gap-3">
            <button
              onClick={handleUpload}
              disabled={uploading || !hasPending || !selectedSpaceId}
              className="flex items-center gap-2 px-4 py-2 bg-[#00FFA7] text-[#0C111D] rounded-lg text-sm font-medium hover:bg-[#00FFA7]/90 transition-colors disabled:opacity-50"
            >
              {uploading ? <RefreshCw size={14} className="animate-spin" /> : <UploadIcon size={14} />}
              Upload {hasPending ? `${files.filter((f) => f.phase === 'queued').length} file(s)` : ''}
            </button>
            <button
              onClick={() => setFiles([])}
              className="px-4 py-2 bg-white/5 text-[#D0D5DD] rounded-lg text-sm hover:bg-white/10 transition-colors"
            >
              Clear all
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
