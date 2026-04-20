import { useState, useCallback } from 'react'
import { Search as SearchIcon, ChevronDown, ChevronUp } from 'lucide-react'
import { api } from '../../lib/api'
import { useKnowledge } from '../../context/KnowledgeContext'

interface SearchChunk {
  id: string
  content: string
  document_id: string
  document_title?: string
  source_uri?: string
  content_type?: string
  difficulty_level?: string
  topics?: string[]
  similarity_score?: number
  rrf_score?: number
  section?: string
}

interface SearchResponse {
  results: SearchChunk[]
  total: number
  query_time_ms?: number
  query: string
}

const SCORE_COLOR = (score: number) =>
  score > 0.8 ? 'bg-[#00FFA7]/10 text-[#00FFA7]' :
  score > 0.6 ? 'bg-yellow-500/10 text-yellow-400' :
  'bg-white/5 text-[#667085]'

export default function KnowledgeSearch() {
  const { activeConnectionId } = useKnowledge()

  const [query, setQuery] = useState('')
  const [results, setResults] = useState<SearchResponse | null>(null)
  const [searching, setSearching] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [showFilters, setShowFilters] = useState(false)

  // Filters
  const [spaceId, setSpaceId] = useState('')
  const [contentType, setContentType] = useState('')
  const [topK, setTopK] = useState('10')

  const handleSearch = useCallback(async () => {
    if (!query.trim() || !activeConnectionId) return
    setSearching(true)
    setError(null)
    try {
      const params = new URLSearchParams({ q: query.trim(), top_k: topK })
      if (spaceId) params.set('space_id', spaceId)
      if (contentType) params.set('content_type', contentType)
      const data = await api.get(`/knowledge/connections/${activeConnectionId}/search?${params}`)
      setResults(data)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Search failed')
      setResults(null)
    }
    setSearching(false)
  }, [query, activeConnectionId, spaceId, contentType, topK])

  if (!activeConnectionId) {
    return <div className="text-center py-12 text-[#667085] text-sm">Select a connection using the switcher above.</div>
  }

  return (
    <div className="space-y-6">
      {/* Search bar */}
      <div className="bg-[#182230] border border-[#344054] rounded-xl p-5">
        <div className="flex gap-3">
          <div className="flex-1 relative">
            <SearchIcon size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-[#667085]" />
            <input
              type="text"
              placeholder="Search your knowledge base..."
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
              className="w-full bg-[#0C111D] border border-[#344054] rounded-lg pl-10 pr-3 py-2.5 text-sm text-[#F9FAFB] placeholder-[#667085] focus:border-[#00FFA7] focus:outline-none"
            />
          </div>
          <button
            onClick={handleSearch}
            disabled={searching || !query.trim()}
            className="px-5 py-2.5 bg-[#00FFA7] text-[#0C111D] rounded-lg font-medium text-sm hover:bg-[#00FFA7]/90 transition-colors disabled:opacity-50"
          >
            {searching ? 'Searching...' : 'Search'}
          </button>
        </div>

        {/* Filters toggle */}
        <button
          onClick={() => setShowFilters((v) => !v)}
          className="flex items-center gap-1 mt-3 text-xs text-[#667085] hover:text-[#D0D5DD] transition-colors"
        >
          {showFilters ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
          Advanced filters
        </button>

        {showFilters && (
          <div className="flex items-center gap-3 mt-3 flex-wrap">
            <select value={spaceId} onChange={(e) => setSpaceId(e.target.value)} className="bg-[#0C111D] border border-[#344054] rounded-lg px-3 py-1.5 text-xs text-[#D0D5DD] focus:border-[#00FFA7] focus:outline-none">
              <option value="">All spaces</option>
            </select>
            <select value={contentType} onChange={(e) => setContentType(e.target.value)} className="bg-[#0C111D] border border-[#344054] rounded-lg px-3 py-1.5 text-xs text-[#D0D5DD] focus:border-[#00FFA7] focus:outline-none">
              <option value="">All content types</option>
              {['lesson', 'tutorial', 'faq', 'reference', 'transcript', 'article', 'decision', 'note'].map((t) => (
                <option key={t} value={t}>{t}</option>
              ))}
            </select>
            <select value={topK} onChange={(e) => setTopK(e.target.value)} className="bg-[#0C111D] border border-[#344054] rounded-lg px-3 py-1.5 text-xs text-[#D0D5DD] focus:border-[#00FFA7] focus:outline-none">
              {['5', '10', '20', '50'].map((k) => <option key={k} value={k}>Top {k}</option>)}
            </select>
          </div>
        )}
      </div>

      {error && (
        <div className="bg-red-500/10 border border-red-500/20 rounded-lg px-4 py-3 text-sm text-red-400">{error}</div>
      )}

      {/* Results */}
      {results && (
        <div>
          <div className="flex items-center gap-3 mb-3 text-xs text-[#667085]">
            <span>{results.total ?? results.results.length} result(s) for &quot;{results.query}&quot;</span>
            {results.query_time_ms != null && (
              <span>{results.query_time_ms.toFixed(0)} ms</span>
            )}
          </div>

          {results.results.length === 0 ? (
            <div className="text-center py-12 text-[#667085] text-sm">No results found. Try different terms or filters.</div>
          ) : (
            <div className="space-y-3">
              {results.results.map((r, i) => {
                const score = r.rrf_score ?? r.similarity_score ?? 0
                return (
                  <div key={r.id ?? i} className="bg-[#182230] border border-[#344054] rounded-xl p-4 hover:border-[#00FFA7]/30 transition-colors">
                    <div className="flex items-start justify-between gap-3 mb-2">
                      <div className="flex-1 min-w-0">
                        {r.document_title && (
                          <p className="text-sm font-medium text-[#D0D5DD] truncate">{r.document_title}</p>
                        )}
                        {r.source_uri && (
                          <p className="text-xs text-[#667085] truncate">{r.source_uri}</p>
                        )}
                        {r.section && (
                          <p className="text-xs text-[#667085] truncate">§ {r.section}</p>
                        )}
                      </div>
                      <div className="flex items-center gap-2 shrink-0">
                        {r.content_type && (
                          <span className="text-[10px] px-1.5 py-0.5 rounded bg-[#00FFA7]/10 text-[#00FFA7]">{r.content_type}</span>
                        )}
                        {score > 0 && (
                          <span className={`text-xs px-1.5 py-0.5 rounded ${SCORE_COLOR(score)}`}>
                            {score > 1 ? score.toFixed(4) : `${(score * 100).toFixed(0)}%`}
                          </span>
                        )}
                      </div>
                    </div>
                    <p className="text-sm text-[#D0D5DD] whitespace-pre-wrap line-clamp-4 leading-relaxed">{r.content}</p>
                    {(r.topics?.length ?? 0) > 0 && (
                      <div className="flex gap-1.5 mt-2 flex-wrap">
                        {r.topics!.slice(0, 4).map((t) => (
                          <span key={t} className="text-[10px] px-1.5 py-0.5 rounded bg-white/5 text-[#667085]">{t}</span>
                        ))}
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          )}
        </div>
      )}

      {!results && !error && (
        <div className="text-center py-12 text-[#667085] text-sm">
          Enter a query to search. Hybrid search combines vector similarity + BM25 keyword matching.
        </div>
      )}
    </div>
  )
}
