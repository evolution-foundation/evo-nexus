import { useEffect, useState, useCallback } from 'react'
import { Library, Search, FolderPlus, Play, Trash2, RefreshCw, Download, Database, Layers, Grid3X3 } from 'lucide-react'
import { api } from '../lib/api'
import { useAuth } from '../context/AuthContext'
import { useTranslation } from 'react-i18next'

interface MiningStatus {
  pid: number | null
  started_at: string
  phase?: 'scanning' | 'mining' | 'done'
  sources?: string[]
  current_source?: string | null
  current_file?: string | null
  files_done?: number
  files_total?: number
  files_skipped?: number
  drawers_added?: number
  elapsed_seconds?: number
  eta_seconds?: number | null
  rate_files_per_sec?: number
}

interface PalaceStats {
  total_drawers?: number
  wings?: string[]
  rooms?: string[]
}

interface StatusData {
  installed: boolean
  version: string | null
  palace_path: string
  stats: PalaceStats | null
  sources_count: number
  mining: MiningStatus | null
}

interface Source {
  path: string
  label: string
  wing: string | null
  added_at: string
  last_indexed: string | null
}

interface SearchResult {
  text: string
  wing: string
  room: string
  source_file: string
  similarity: number
}

interface SearchResponse {
  query: string
  filters?: Record<string, string | null>
  results?: SearchResult[]
}

type Tab = 'status' | 'sources' | 'search'

export default function MemPalace() {
  const { t } = useTranslation()
  const { hasPermission } = useAuth()
  const canManage = hasPermission('mempalace', 'manage')

  const [tab, setTab] = useState<Tab>('status')
  const [status, setStatus] = useState<StatusData | null>(null)
  const [sources, setSources] = useState<Source[]>([])
  const [loading, setLoading] = useState(true)
  const [installing, setInstalling] = useState(false)
  const [mining, setMining] = useState(false)

  // Sources form
  const [newPath, setNewPath] = useState('')
  const [newLabel, setNewLabel] = useState('')
  const [newWing, setNewWing] = useState('')
  const [addingSource, setAddingSource] = useState(false)

  // Search
  const [query, setQuery] = useState('')
  const [searchWing, setSearchWing] = useState('')
  const [searchRoom, setSearchRoom] = useState('')
  const [searchResults, setSearchResults] = useState<SearchResponse | null>(null)
  const [searching, setSearching] = useState(false)

  const loadStatus = useCallback(async () => {
    try {
      const s = await api.get('/mempalace/status')
      setStatus(s)
    } catch {
      setStatus(null)
    }
  }, [])

  const loadSources = useCallback(async () => {
    try {
      const data = await api.get('/mempalace/sources')
      setSources(data.sources || [])
    } catch {
      setSources([])
    }
  }, [])

  useEffect(() => {
    Promise.all([loadStatus(), loadSources()]).finally(() => setLoading(false))
  }, [loadStatus, loadSources])

  // Poll while mining is active — fast (1s) so the progress bar feels live
  useEffect(() => {
    if (!status?.mining) return
    const id = setInterval(loadStatus, 1000)
    return () => clearInterval(id)
  }, [status?.mining, loadStatus])

  const handleInstall = async () => {
    setInstalling(true)
    try {
      await api.post('/mempalace/install')
      await loadStatus()
    } catch { /* ignore */ }
    setInstalling(false)
  }

  const handleAddSource = async () => {
    if (!newPath.trim()) return
    setAddingSource(true)
    try {
      const data = await api.post('/mempalace/sources', {
        path: newPath.trim(),
        label: newLabel.trim() || undefined,
        wing: newWing.trim() || undefined,
      })
      setSources(data.sources || [])
      setNewPath('')
      setNewLabel('')
      setNewWing('')
    } catch { /* ignore */ }
    setAddingSource(false)
  }

  const handleDeleteSource = async (idx: number) => {
    try {
      const data = await api.delete(`/mempalace/sources/${idx}`)
      setSources(data.sources || [])
    } catch { /* ignore */ }
  }

  const handleMine = async (sourceIndex?: number) => {
    setMining(true)
    try {
      await api.post('/mempalace/mine', {
        source_index: sourceIndex ?? null,
      })
      await loadStatus()
      await loadSources()
    } catch { /* ignore */ }
    setMining(false)
  }

  const handleSearch = async () => {
    if (!query.trim()) return
    setSearching(true)
    try {
      const params = new URLSearchParams({ q: query.trim(), n: '10' })
      if (searchWing) params.set('wing', searchWing)
      if (searchRoom) params.set('room', searchRoom)
      const data = await api.get(`/mempalace/search?${params}`)
      setSearchResults(data)
    } catch {
      setSearchResults(null)
    }
    setSearching(false)
  }

  const tabs: { key: Tab; label: string }[] = [
    { key: 'status', label: 'Status' },
    { key: 'sources', label: 'Sources' },
    { key: 'search', label: 'Search' },
  ]

  if (loading) {
    return (
      <div>
        <div className="mb-8">
          <h1 className="text-2xl font-bold text-[#F9FAFB]">{t('mempalace.title')}</h1>
          <p className="text-[#667085] mt-1">Semantic search powered by MemPalace</p>
        </div>
        <div className="space-y-3">
          {[...Array(3)].map((_, i) => <div key={i} className="skeleton h-24 rounded-xl" />)}
        </div>
      </div>
    )
  }

  return (
    <div>
      <div className="mb-8">
        <h1 className="text-2xl font-bold text-[#F9FAFB]">{t('mempalace.title')}</h1>
        <p className="text-[#667085] mt-1">Semantic search powered by MemPalace</p>
      </div>

      {/* Not installed */}
      {!status?.installed && (
        <div className="bg-[#182230] border border-[#344054] rounded-xl p-8 text-center max-w-lg mx-auto">
          <div className="w-16 h-16 rounded-2xl bg-[#00FFA7]/10 flex items-center justify-center mx-auto mb-4">
            <Library size={32} className="text-[#00FFA7]" />
          </div>
          <h2 className="text-xl font-semibold text-[#F9FAFB] mb-2">Enable MemPalace</h2>
          <p className="text-[#667085] text-sm mb-6">
            Index your code, docs, and knowledge for semantic search.
            Everything runs locally — no external APIs required.
          </p>
          {canManage ? (
            <button
              onClick={handleInstall}
              disabled={installing}
              className="px-6 py-2.5 bg-[#00FFA7] text-[#0C111D] rounded-lg font-medium text-sm hover:bg-[#00FFA7]/90 transition-colors disabled:opacity-50"
            >
              {installing ? (
                <span className="flex items-center gap-2">
                  <RefreshCw size={14} className="animate-spin" /> Installing...
                </span>
              ) : (
                <span className="flex items-center gap-2">
                  <Download size={14} /> Install MemPalace
                </span>
              )}
            </button>
          ) : (
            <p className="text-xs text-[#667085]">Ask an admin to enable MemPalace.</p>
          )}
        </div>
      )}

      {/* Installed */}
      {status?.installed && (
        <>
          {/* Tabs */}
          <div className="flex gap-1 mb-6 border-b border-[#344054]">
            {tabs.map((t) => (
              <button
                key={t.key}
                onClick={() => setTab(t.key)}
                className={`px-4 py-2.5 text-sm font-medium transition-colors border-b-2 -mb-px ${
                  tab === t.key
                    ? 'text-[#00FFA7] border-[#00FFA7]'
                    : 'text-[#667085] border-transparent hover:text-[#D0D5DD]'
                }`}
              >
                {t.label}
              </button>
            ))}
          </div>

          {/* Status Tab */}
          {tab === 'status' && (
            <div className="space-y-6">
              {/* Metric cards */}
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                <div className="bg-[#182230] border border-[#344054] rounded-xl p-5">
                  <div className="flex items-center gap-2 mb-1">
                    <Layers size={14} className="text-[#00FFA7]" />
                    <span className="text-xs text-[#667085] uppercase tracking-wider">Wings</span>
                  </div>
                  <p className="text-2xl font-bold text-[#F9FAFB]">{status.stats?.wings?.length || 0}</p>
                  {status.stats?.wings && status.stats.wings.length > 0 && (
                    <p className="text-xs text-[#667085] mt-1 truncate">{status.stats.wings.join(', ')}</p>
                  )}
                </div>
                <div className="bg-[#182230] border border-[#344054] rounded-xl p-5">
                  <div className="flex items-center gap-2 mb-1">
                    <Grid3X3 size={14} className="text-[#00FFA7]" />
                    <span className="text-xs text-[#667085] uppercase tracking-wider">Rooms</span>
                  </div>
                  <p className="text-2xl font-bold text-[#F9FAFB]">{status.stats?.rooms?.length || 0}</p>
                  {status.stats?.rooms && status.stats.rooms.length > 0 && (
                    <p className="text-xs text-[#667085] mt-1 truncate">{status.stats.rooms.join(', ')}</p>
                  )}
                </div>
                <div className="bg-[#182230] border border-[#344054] rounded-xl p-5">
                  <div className="flex items-center gap-2 mb-1">
                    <Database size={14} className="text-[#00FFA7]" />
                    <span className="text-xs text-[#667085] uppercase tracking-wider">Drawers</span>
                  </div>
                  <p className="text-2xl font-bold text-[#F9FAFB]">{status.stats?.total_drawers || 0}</p>
                </div>
              </div>

              {/* Mining status with progress bar + ETA */}
              {status.mining && (() => {
                const m = status.mining
                const done = m.files_done ?? 0
                const total = m.files_total ?? 0
                const percent = total > 0 ? Math.min(100, Math.round((done / total) * 100)) : 0
                const currentFileName = m.current_file
                  ? m.current_file.split('/').slice(-1)[0]
                  : null
                const currentFileDir = m.current_file
                  ? m.current_file.split('/').slice(-3, -1).join('/')
                  : null
                const fmtDuration = (secs?: number | null) => {
                  if (secs == null || !isFinite(secs) || secs < 0) return '—'
                  const s = Math.round(secs)
                  if (s < 60) return `${s}s`
                  const m = Math.floor(s / 60)
                  const r = s % 60
                  if (m < 60) return `${m}m ${r}s`
                  const h = Math.floor(m / 60)
                  return `${h}h ${m % 60}m`
                }
                const isScanning = m.phase === 'scanning' || (total === 0 && done === 0)
                return (
                  <div className="bg-[#00FFA7]/5 border border-[#00FFA7]/20 rounded-xl p-5 space-y-3">
                    <div className="flex items-center gap-3">
                      <RefreshCw size={16} className="text-[#00FFA7] animate-spin shrink-0" />
                      <div className="flex-1 min-w-0">
                        <p className="text-sm text-[#F9FAFB] font-medium">
                          {isScanning ? 'Scanning files...' : 'Mining in progress'}
                        </p>
                        <p className="text-xs text-[#667085] truncate">
                          Started {new Date(m.started_at).toLocaleTimeString()}
                          {' · '}{m.sources?.length ?? 0} source(s)
                          {m.drawers_added != null && m.drawers_added > 0 && (
                            <> · {m.drawers_added.toLocaleString()} drawers added</>
                          )}
                        </p>
                      </div>
                      {!isScanning && (
                        <div className="text-right shrink-0">
                          <p className="text-2xl font-bold text-[#00FFA7] tabular-nums">{percent}%</p>
                          <p className="text-[10px] text-[#667085] tabular-nums">
                            {done.toLocaleString()} / {total.toLocaleString()}
                          </p>
                        </div>
                      )}
                    </div>

                    {/* Progress bar */}
                    {!isScanning && (
                      <div className="h-2 bg-[#0C111D] rounded-full overflow-hidden">
                        <div
                          className="h-full bg-[#00FFA7] transition-all duration-500 ease-out"
                          style={{ width: `${percent}%` }}
                        />
                      </div>
                    )}

                    {/* Current file */}
                    {currentFileName && (
                      <div className="flex items-baseline gap-2 text-xs">
                        <span className="text-[#667085] shrink-0">Processing:</span>
                        <span className="text-[#D0D5DD] font-mono truncate" title={m.current_file || undefined}>
                          {currentFileDir && <span className="text-[#667085]">{currentFileDir}/</span>}
                          {currentFileName}
                        </span>
                      </div>
                    )}

                    {/* Metrics row */}
                    <div className="flex items-center gap-4 text-xs text-[#667085] tabular-nums">
                      <span>⏱ {fmtDuration(m.elapsed_seconds)} elapsed</span>
                      {!isScanning && m.eta_seconds != null && (
                        <span>ETA {fmtDuration(m.eta_seconds)}</span>
                      )}
                      {!isScanning && (m.rate_files_per_sec ?? 0) > 0 && (
                        <span>{(m.rate_files_per_sec ?? 0).toFixed(1)} files/s</span>
                      )}
                      {(m.files_skipped ?? 0) > 0 && (
                        <span>{m.files_skipped} skipped</span>
                      )}
                    </div>
                  </div>
                )
              })()}

              {/* Version info */}
              <div className="flex items-center gap-4 text-xs text-[#667085]">
                <span>MemPalace v{status.version}</span>
                <span>{status.palace_path}</span>
                <span>{status.sources_count} source(s)</span>
              </div>
            </div>
          )}

          {/* Sources Tab */}
          {tab === 'sources' && (
            <div className="space-y-6">
              {/* Add source form */}
              {canManage && (
                <div className="bg-[#182230] border border-[#344054] rounded-xl p-5">
                  <h3 className="text-sm font-semibold text-[#D0D5DD] mb-4 flex items-center gap-2">
                    <FolderPlus size={14} className="text-[#00FFA7]" />
                    Add Source
                  </h3>
                  <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                    <input
                      type="text"
                      placeholder="Directory path (e.g. /path/to/project)"
                      value={newPath}
                      onChange={(e) => setNewPath(e.target.value)}
                      className="bg-[#0C111D] border border-[#344054] rounded-lg px-3 py-2 text-sm text-[#F9FAFB] placeholder-[#667085] focus:border-[#00FFA7] focus:outline-none sm:col-span-3"
                    />
                    <input
                      type="text"
                      placeholder="Label (optional)"
                      value={newLabel}
                      onChange={(e) => setNewLabel(e.target.value)}
                      className="bg-[#0C111D] border border-[#344054] rounded-lg px-3 py-2 text-sm text-[#F9FAFB] placeholder-[#667085] focus:border-[#00FFA7] focus:outline-none"
                    />
                    <input
                      type="text"
                      placeholder="Wing override (optional)"
                      value={newWing}
                      onChange={(e) => setNewWing(e.target.value)}
                      className="bg-[#0C111D] border border-[#344054] rounded-lg px-3 py-2 text-sm text-[#F9FAFB] placeholder-[#667085] focus:border-[#00FFA7] focus:outline-none"
                    />
                    <button
                      onClick={handleAddSource}
                      disabled={addingSource || !newPath.trim()}
                      className="px-4 py-2 bg-[#00FFA7] text-[#0C111D] rounded-lg font-medium text-sm hover:bg-[#00FFA7]/90 transition-colors disabled:opacity-50"
                    >
                      {addingSource ? 'Adding...' : 'Add'}
                    </button>
                  </div>
                </div>
              )}

              {/* Sources list */}
              {sources.length === 0 ? (
                <div className="text-center py-12 text-[#667085] text-sm">
                  No sources configured yet. Add a directory to get started.
                </div>
              ) : (
                <div className="space-y-2">
                  {canManage && (
                    <div className="flex justify-end mb-2">
                      <button
                        onClick={() => handleMine()}
                        disabled={mining || !!status?.mining}
                        className="px-4 py-2 bg-[#00FFA7]/10 text-[#00FFA7] rounded-lg text-sm font-medium hover:bg-[#00FFA7]/20 transition-colors disabled:opacity-50 flex items-center gap-2"
                      >
                        <Play size={14} /> Index All
                      </button>
                    </div>
                  )}
                  {sources.map((source, idx) => (
                    <div key={idx} className="bg-[#182230] border border-[#344054] rounded-xl p-4 flex items-center justify-between gap-4">
                      <div className="min-w-0 flex-1">
                        <p className="text-sm font-medium text-[#F9FAFB] truncate">{source.label}</p>
                        <p className="text-xs text-[#667085] truncate">{source.path}</p>
                        <div className="flex gap-3 mt-1">
                          {source.wing && (
                            <span className="text-xs px-1.5 py-0.5 rounded bg-[#00FFA7]/10 text-[#00FFA7]">
                              {source.wing}
                            </span>
                          )}
                          <span className="text-xs text-[#667085]">
                            {source.last_indexed
                              ? `Indexed ${new Date(source.last_indexed).toLocaleDateString()}`
                              : 'Not indexed'}
                          </span>
                        </div>
                      </div>
                      {canManage && (
                        <div className="flex items-center gap-2 shrink-0">
                          <button
                            onClick={() => handleMine(idx)}
                            disabled={mining || !!status?.mining}
                            className="p-2 rounded-lg text-[#667085] hover:text-[#00FFA7] hover:bg-[#00FFA7]/10 transition-colors disabled:opacity-50"
                            title="Index this source"
                          >
                            <Play size={14} />
                          </button>
                          <button
                            onClick={() => handleDeleteSource(idx)}
                            className="p-2 rounded-lg text-[#667085] hover:text-red-400 hover:bg-red-500/10 transition-colors"
                            title="Remove source"
                          >
                            <Trash2 size={14} />
                          </button>
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* Search Tab */}
          {tab === 'search' && (
            <div className="space-y-6">
              {/* Search bar */}
              <div className="bg-[#182230] border border-[#344054] rounded-xl p-5">
                <div className="flex gap-3">
                  <div className="flex-1 relative">
                    <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-[#667085]" />
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
                {/* Filters */}
                {status?.stats && ((status.stats.wings?.length ?? 0) > 0 || (status.stats.rooms?.length ?? 0) > 0) && (
                  <div className="flex gap-3 mt-3">
                    {(status.stats.wings?.length ?? 0) > 0 && (
                      <select
                        value={searchWing}
                        onChange={(e) => setSearchWing(e.target.value)}
                        className="bg-[#0C111D] border border-[#344054] rounded-lg px-3 py-1.5 text-xs text-[#D0D5DD] focus:border-[#00FFA7] focus:outline-none"
                      >
                        <option value="">All wings</option>
                        {status.stats.wings?.map((w) => (
                          <option key={w} value={w}>{w}</option>
                        ))}
                      </select>
                    )}
                    {(status.stats.rooms?.length ?? 0) > 0 && (
                      <select
                        value={searchRoom}
                        onChange={(e) => setSearchRoom(e.target.value)}
                        className="bg-[#0C111D] border border-[#344054] rounded-lg px-3 py-1.5 text-xs text-[#D0D5DD] focus:border-[#00FFA7] focus:outline-none"
                      >
                        <option value="">All rooms</option>
                        {status.stats.rooms?.map((r) => (
                          <option key={r} value={r}>{r}</option>
                        ))}
                      </select>
                    )}
                  </div>
                )}
              </div>

              {/* Results */}
              {searchResults && (
                <div>
                  <p className="text-xs text-[#667085] mb-3">
                    {searchResults.results?.length ?? 0} result(s) for "{searchResults.query}"
                  </p>
                  {(searchResults.results?.length ?? 0) === 0 ? (
                    <div className="text-center py-12 text-[#667085] text-sm">
                      No results found. Try a different query.
                    </div>
                  ) : (
                    <div className="space-y-2">
                      {searchResults.results?.map((r, i) => (
                        <div key={i} className="bg-[#182230] border border-[#344054] rounded-xl p-4 hover:border-[#00FFA7]/30 transition-colors">
                          <div className="flex items-start justify-between gap-3 mb-2">
                            <p className="text-xs text-[#667085] truncate flex-1">{r.source_file}</p>
                            <span className={`text-xs px-1.5 py-0.5 rounded shrink-0 ${
                              r.similarity > 0.8
                                ? 'bg-[#00FFA7]/10 text-[#00FFA7]'
                                : r.similarity > 0.6
                                  ? 'bg-yellow-500/10 text-yellow-400'
                                  : 'bg-white/5 text-[#667085]'
                            }`}>
                              {(r.similarity * 100).toFixed(0)}%
                            </span>
                          </div>
                          <p className="text-sm text-[#D0D5DD] whitespace-pre-wrap line-clamp-4">{r.text}</p>
                          <div className="flex gap-2 mt-2">
                            {r.wing && (
                              <span className="text-[10px] px-1.5 py-0.5 rounded bg-[#00FFA7]/10 text-[#00FFA7]">{r.wing}</span>
                            )}
                            {r.room && (
                              <span className="text-[10px] px-1.5 py-0.5 rounded bg-white/5 text-[#667085]">{r.room}</span>
                            )}
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}

              {!searchResults && (
                <div className="text-center py-12 text-[#667085] text-sm">
                  Enter a query to search your indexed knowledge.
                </div>
              )}
            </div>
          )}
        </>
      )}
    </div>
  )
}
