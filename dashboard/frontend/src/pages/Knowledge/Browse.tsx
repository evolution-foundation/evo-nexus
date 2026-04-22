import { useEffect, useState, useCallback } from 'react'
import { Search, RefreshCw, Trash2, X, FileText } from 'lucide-react'
import { api } from '../../lib/api'
import { useAuth } from '../../context/AuthContext'
import { useKnowledge } from '../../context/KnowledgeContext'

interface Document {
  id: string
  title?: string
  source_uri?: string
  mime_type?: string
  content_type?: string
  difficulty_level?: string
  topics?: string[]
  status: string
  size_bytes?: number
  pages_count?: number
  chunks_count?: number
  created_at: string
}

const STATUS_COLORS: Record<string, string> = {
  ready: 'bg-[#00FFA7]/10 text-[#00FFA7]',
  processing: 'bg-blue-500/10 text-blue-400',
  pending: 'bg-white/5 text-[#667085]',
  error: 'bg-red-500/10 text-red-400',
}

export default function KnowledgeBrowse() {
  const { hasPermission } = useAuth()
  const { activeConnectionId } = useKnowledge()
  const canManage = hasPermission('knowledge', 'manage')

  const [documents, setDocuments] = useState<Document[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null)

  // Filters
  const [spaceId, setSpaceId] = useState('')
  const [contentType, setContentType] = useState('')
  const [status, setStatus] = useState('')
  const [query, setQuery] = useState('')
  const [spaces, setSpaces] = useState<{ id: string; name: string }[]>([])

  const loadSpaces = useCallback(async () => {
    if (!activeConnectionId) return
    try {
      const data = await api.get(`/knowledge/connections/${activeConnectionId}/spaces`)
      setSpaces(data.spaces || data || [])
    } catch {}
  }, [activeConnectionId])

  const load = useCallback(async () => {
    if (!activeConnectionId) { setLoading(false); return }
    setLoading(true)
    try {
      const params = new URLSearchParams()
      if (spaceId) params.set('space_id', spaceId)
      if (contentType) params.set('content_type', contentType)
      if (status) params.set('status', status)
      if (query) params.set('q', query)
      const data = await api.get(`/knowledge/connections/${activeConnectionId}/documents?${params}`)
      setDocuments(data.documents || data || [])
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load documents')
    }
    setLoading(false)
  }, [activeConnectionId, spaceId, contentType, status, query])

  useEffect(() => { loadSpaces() }, [loadSpaces])
  useEffect(() => { load() }, [load])

  async function handleDelete(id: string) {
    try {
      await api.delete(`/knowledge/connections/${activeConnectionId}/documents/${id}`)
      setConfirmDeleteId(null)
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Delete failed')
    }
  }

  function fileName(doc: Document) {
    if (doc.title) return doc.title
    if (doc.source_uri) return doc.source_uri.split('/').pop() || doc.source_uri
    return doc.id.slice(0, 8) + '...'
  }

  if (!activeConnectionId) {
    return <div className="text-center py-12 text-[#667085] text-sm">Select a connection using the switcher above.</div>
  }

  return (
    <div className="space-y-4">
      {error && (
        <div className="bg-red-500/10 border border-red-500/20 rounded-lg px-4 py-3 text-sm text-red-400">{error}</div>
      )}

      {/* Filters */}
      <div className="flex items-center gap-3 flex-wrap">
        <div className="flex-1 min-w-[200px] relative">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-[#667085]" />
          <input
            type="text"
            placeholder="Search by title..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && load()}
            className="w-full bg-[#182230] border border-[#344054] rounded-lg pl-9 pr-3 py-2 text-sm text-[#F9FAFB] placeholder-[#667085] focus:border-[#00FFA7] focus:outline-none"
          />
        </div>
        <select value={spaceId} onChange={(e) => setSpaceId(e.target.value)} className="bg-[#182230] border border-[#344054] rounded-lg px-3 py-2 text-sm text-[#D0D5DD] focus:border-[#00FFA7] focus:outline-none">
          <option value="">All spaces</option>
          {spaces.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select>
        <select value={contentType} onChange={(e) => setContentType(e.target.value)} className="bg-[#182230] border border-[#344054] rounded-lg px-3 py-2 text-sm text-[#D0D5DD] focus:border-[#00FFA7] focus:outline-none">
          <option value="">All types</option>
          {['lesson', 'tutorial', 'faq', 'reference', 'transcript', 'article', 'decision', 'note'].map((t) => (
            <option key={t} value={t}>{t}</option>
          ))}
        </select>
        <select value={status} onChange={(e) => setStatus(e.target.value)} className="bg-[#182230] border border-[#344054] rounded-lg px-3 py-2 text-sm text-[#D0D5DD] focus:border-[#00FFA7] focus:outline-none">
          <option value="">All statuses</option>
          {['ready', 'processing', 'pending', 'error'].map((s) => (
            <option key={s} value={s}>{s}</option>
          ))}
        </select>
        <button onClick={load} className="p-2 text-[#667085] hover:text-[#D0D5DD] hover:bg-white/5 rounded-lg transition-colors" title="Refresh">
          <RefreshCw size={14} />
        </button>
      </div>

      {loading ? (
        <div className="space-y-2">
          {[...Array(5)].map((_, i) => <div key={i} className="h-14 bg-[#182230] border border-[#344054] rounded-xl animate-pulse" />)}
        </div>
      ) : documents.length === 0 ? (
        <div className="text-center py-12 bg-[#182230] border border-[#344054] rounded-xl text-[#667085] text-sm">
          No documents found. Upload some files to get started.
        </div>
      ) : (
        <div className="bg-[#182230] border border-[#344054] rounded-xl overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-[#344054]">
                {['Title', 'Type', 'Difficulty', 'Topics', 'Pages', 'Chunks', 'Status', 'Added', ''].map((h) => (
                  <th key={h} className="px-4 py-3 text-left text-xs text-[#667085] font-medium">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {documents.map((doc) => (
                <tr key={doc.id} className="border-b border-[#344054]/50 last:border-0 hover:bg-white/2 transition-colors">
                  <td className="px-4 py-3 max-w-[200px]">
                    <div className="flex items-center gap-2">
                      <FileText size={12} className="text-[#667085] shrink-0" />
                      <p className="text-[#D0D5DD] truncate">{fileName(doc)}</p>
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    {doc.content_type && (
                      <span className="text-xs px-1.5 py-0.5 rounded bg-[#00FFA7]/10 text-[#00FFA7]">{doc.content_type}</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-xs text-[#667085]">{doc.difficulty_level || '—'}</td>
                  <td className="px-4 py-3">
                    <div className="flex gap-1 flex-wrap max-w-[120px]">
                      {(doc.topics || []).slice(0, 2).map((t) => (
                        <span key={t} className="text-[10px] px-1.5 py-0.5 rounded bg-white/5 text-[#667085]">{t}</span>
                      ))}
                      {(doc.topics?.length || 0) > 2 && <span className="text-[10px] text-[#667085]">+{(doc.topics?.length || 0) - 2}</span>}
                    </div>
                  </td>
                  <td className="px-4 py-3 text-xs text-[#667085]">{doc.pages_count ?? '—'}</td>
                  <td className="px-4 py-3 text-xs text-[#667085]">{doc.chunks_count?.toLocaleString() ?? '—'}</td>
                  <td className="px-4 py-3">
                    <span className={`text-xs px-1.5 py-0.5 rounded ${STATUS_COLORS[doc.status] || 'bg-white/5 text-[#667085]'}`}>
                      {doc.status}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-xs text-[#667085]">{new Date(doc.created_at).toLocaleDateString()}</td>
                  <td className="px-4 py-3">
                    {canManage && (
                      <button
                        onClick={() => setConfirmDeleteId(doc.id)}
                        className="p-1.5 rounded-lg text-[#667085] hover:text-red-400 hover:bg-red-500/10 transition-colors"
                      >
                        <Trash2 size={12} />
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {confirmDeleteId && (
        <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4">
          <div className="bg-[#0C111D] border border-[#344054] rounded-xl w-full max-w-sm shadow-2xl p-6 text-center">
            <Trash2 size={28} className="text-red-400 mx-auto mb-3" />
            <p className="text-sm font-semibold text-[#F9FAFB] mb-1">Delete Document?</p>
            <p className="text-xs text-[#667085] mb-6">All chunks and embeddings for this document will be permanently deleted.</p>
            <div className="flex gap-3">
              <button onClick={() => setConfirmDeleteId(null)} className="flex-1 px-4 py-2 bg-white/5 text-[#D0D5DD] rounded-lg text-sm hover:bg-white/10 transition-colors"><X size={12} className="inline mr-1" />Cancel</button>
              <button onClick={() => handleDelete(confirmDeleteId)} className="flex-1 px-4 py-2 bg-red-500 text-white rounded-lg text-sm font-semibold hover:bg-red-600 transition-colors">Delete</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
