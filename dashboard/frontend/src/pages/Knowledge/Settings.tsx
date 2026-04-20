import { useEffect, useState, useCallback } from 'react'
import { RefreshCw, CheckCircle, Download, Info } from 'lucide-react'
import { api } from '../../lib/api'
import { useAuth } from '../../context/AuthContext'
import { useKnowledge } from '../../context/KnowledgeContext'

interface KnowledgeSettings {
  embedder_provider: 'local' | 'openai' | 'voyage'
  embedder_model: string
  vector_dim: number
  parser_default: 'marker' | 'llamaparse'
}

interface ParserStatus {
  marker_installed: boolean
  marker_version?: string
  install_path?: string
}

export default function KnowledgeSettings() {
  const { hasPermission } = useAuth()
  const { connections } = useKnowledge()
  const canManage = hasPermission('knowledge', 'manage')
  const hasConnections = connections.length > 0

  const [settings, setSettings] = useState<KnowledgeSettings | null>(null)
  const [parserStatus, setParserStatus] = useState<ParserStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [installing, setInstalling] = useState(false)
  const [installDone, setInstallDone] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)

  // Local form state
  const [embedder, setEmbedder] = useState<'local' | 'openai' | 'voyage'>('local')
  const [parser, setParser] = useState<'marker' | 'llamaparse'>('marker')

  const load = useCallback(async () => {
    try {
      // Settings live in config — for now read from first ready connection's config
      // or fall back to defaults if no connection yet
      const status = await api.get('/knowledge/parsers/status')
      setParserStatus(status)
    } catch {}

    // TODO(Step 3): GET /api/knowledge/settings when backend adds it
    // For now use defaults
    setSettings({
      embedder_provider: 'local',
      embedder_model: 'paraphrase-multilingual-mpnet-base-v2',
      vector_dim: 768,
      parser_default: 'marker',
    })
    setLoading(false)
  }, [])

  useEffect(() => { load() }, [load])

  useEffect(() => {
    if (settings) {
      setEmbedder(settings.embedder_provider)
      setParser(settings.parser_default)
    }
  }, [settings])

  const handleSave = async () => {
    setSaving(true)
    setError(null)
    try {
      // TODO(Step 3): PATCH /api/knowledge/settings
      // For now store locally
      setSaved(true)
      setTimeout(() => setSaved(false), 2000)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Save failed')
    }
    setSaving(false)
  }

  const handleInstallMarker = async () => {
    setInstalling(true)
    setError(null)
    try {
      const result = await api.post('/knowledge/parsers/install')
      if (result.already_installed) {
        setInstallDone(true)
      } else {
        setInstallDone(true)
        await load()
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Install failed')
    }
    setInstalling(false)
  }

  if (loading) {
    return (
      <div className="space-y-3">
        {[...Array(3)].map((_, i) => (
          <div key={i} className="h-24 bg-[#182230] border border-[#344054] rounded-xl animate-pulse" />
        ))}
      </div>
    )
  }

  return (
    <div className="space-y-6 max-w-2xl">
      {error && (
        <div className="bg-red-500/10 border border-red-500/20 rounded-lg px-4 py-3 text-sm text-red-400">
          {error}
        </div>
      )}

      {/* Embedder */}
      <div className="bg-[#182230] border border-[#344054] rounded-xl p-5">
        <div className="flex items-start justify-between mb-4">
          <div>
            <h3 className="text-sm font-semibold text-[#F9FAFB]">Embedder Provider</h3>
            <p className="text-xs text-[#667085] mt-0.5">Global for all connections. Cannot change after first connection is added.</p>
          </div>
          {hasConnections && (
            <div className="flex items-center gap-1.5 px-2 py-1 bg-yellow-500/10 rounded-lg">
              <Info size={12} className="text-yellow-400" />
              <span className="text-xs text-yellow-400">Locked</span>
            </div>
          )}
        </div>

        <div className="space-y-2">
          {[
            { value: 'local', label: 'Local (offline)', desc: 'paraphrase-multilingual-mpnet-base-v2 · 768 dims · No API key required' },
            { value: 'openai', label: 'OpenAI', desc: 'text-embedding-3-small · 1536 dims · Requires OPENAI_API_KEY' },
            { value: 'voyage', label: 'Voyage (v1.1)', desc: 'voyage-3-large · 1024 dims · Requires VOYAGE_API_KEY' },
          ].map((opt) => (
            <label
              key={opt.value}
              className={`flex items-start gap-3 p-3 rounded-lg border transition-colors cursor-pointer ${
                embedder === opt.value
                  ? 'border-[#00FFA7]/40 bg-[#00FFA7]/5'
                  : 'border-[#344054] hover:border-[#344054]/80'
              } ${hasConnections ? 'opacity-60 cursor-not-allowed' : ''}`}
            >
              <input
                type="radio"
                name="embedder"
                value={opt.value}
                checked={embedder === opt.value}
                onChange={() => !hasConnections && setEmbedder(opt.value as typeof embedder)}
                disabled={hasConnections}
                className="mt-0.5 accent-[#00FFA7]"
              />
              <div>
                <p className="text-sm font-medium text-[#D0D5DD]">{opt.label}</p>
                <p className="text-xs text-[#667085]">{opt.desc}</p>
              </div>
            </label>
          ))}
        </div>

        {hasConnections && (
          <p className="text-xs text-[#667085] mt-3">
            To change embedder provider, run <code className="text-[#00FFA7] bg-[#0C111D] px-1 py-0.5 rounded">knowledge-reindex</code> skill first.
          </p>
        )}
      </div>

      {/* Parser */}
      <div className="bg-[#182230] border border-[#344054] rounded-xl p-5">
        <h3 className="text-sm font-semibold text-[#F9FAFB] mb-1">Default Parser</h3>
        <p className="text-xs text-[#667085] mb-4">Used when uploading documents without explicit parser selection.</p>

        <div className="space-y-2">
          {[
            { value: 'marker', label: 'Marker (default)', desc: 'PDF, DOCX, PPTX, XLSX, HTML, EPUB, images with OCR · Offline · ~500 MB download' },
            { value: 'llamaparse', label: 'LlamaParse', desc: 'Premium accuracy for complex PDFs · Requires API key · Paid per page' },
          ].map((opt) => (
            <label
              key={opt.value}
              className={`flex items-start gap-3 p-3 rounded-lg border transition-colors cursor-pointer ${
                parser === opt.value
                  ? 'border-[#00FFA7]/40 bg-[#00FFA7]/5'
                  : 'border-[#344054] hover:border-[#344054]/80'
              } ${!canManage ? 'opacity-60 cursor-not-allowed' : ''}`}
            >
              <input
                type="radio"
                name="parser"
                value={opt.value}
                checked={parser === opt.value}
                onChange={() => canManage && setParser(opt.value as typeof parser)}
                disabled={!canManage}
                className="mt-0.5 accent-[#00FFA7]"
              />
              <div>
                <p className="text-sm font-medium text-[#D0D5DD]">{opt.label}</p>
                <p className="text-xs text-[#667085]">{opt.desc}</p>
              </div>
            </label>
          ))}
        </div>

        {/* Marker install */}
        <div className="mt-4 pt-4 border-t border-[#344054]">
          <div className="flex items-center justify-between flex-wrap gap-3">
            <div>
              <p className="text-xs font-medium text-[#D0D5DD]">Marker Models</p>
              <p className="text-xs text-[#667085]">
                {parserStatus?.marker_installed
                  ? `Installed${parserStatus.marker_version ? ` · v${parserStatus.marker_version}` : ''}`
                  : 'Not installed — ~500 MB download required'}
              </p>
            </div>
            {canManage && !parserStatus?.marker_installed && (
              <button
                onClick={handleInstallMarker}
                disabled={installing}
                className="flex items-center gap-1.5 px-3 py-1.5 bg-[#00FFA7]/10 text-[#00FFA7] rounded-lg text-xs font-medium hover:bg-[#00FFA7]/20 transition-colors disabled:opacity-50"
              >
                {installing ? (
                  <><RefreshCw size={10} className="animate-spin" /> Installing...</>
                ) : (
                  <><Download size={10} /> Install Marker models</>
                )}
              </button>
            )}
            {(parserStatus?.marker_installed || installDone) && (
              <span className="flex items-center gap-1 text-xs text-[#00FFA7]">
                <CheckCircle size={12} /> Ready
              </span>
            )}
          </div>
          {installing && (
            <div className="mt-3">
              <div className="h-1.5 bg-[#0C111D] rounded-full overflow-hidden">
                <div className="h-full bg-[#00FFA7] animate-pulse w-2/3" />
              </div>
              <p className="text-xs text-[#667085] mt-1">Downloading Surya models (~500 MB)...</p>
            </div>
          )}
        </div>
      </div>

      {/* Save */}
      {canManage && (
        <div className="flex items-center gap-3">
          <button
            onClick={handleSave}
            disabled={saving}
            className="flex items-center gap-2 px-4 py-2 bg-[#00FFA7] text-[#0C111D] rounded-lg text-sm font-medium hover:bg-[#00FFA7]/90 transition-colors disabled:opacity-50"
          >
            {saving ? <RefreshCw size={14} className="animate-spin" /> : null}
            {saved ? 'Saved!' : 'Save Settings'}
          </button>
          {saved && <CheckCircle size={14} className="text-[#00FFA7]" />}
        </div>
      )}
    </div>
  )
}
