import { useEffect, useState, useCallback } from 'react'
import { RefreshCw, CheckCircle, Download, Info } from 'lucide-react'
import { api } from '../../lib/api'
import { useAuth } from '../../context/AuthContext'

type EmbedderProvider = 'local' | 'openai' | 'gemini'

interface KnowledgeSettings {
  embedder_provider: EmbedderProvider
  embedder_model: string
  vector_dim: number
  parser_default: 'marker'
  locked: boolean
  openai_api_key_set: boolean
  gemini_api_key_set: boolean
}

interface ParserStatus {
  marker_installed: boolean
  marker_version?: string
  install_path?: string
}

interface EmbedderModel {
  id: string
  dim: number
  recommended?: boolean
  legacy?: boolean
  preview?: boolean
  supports_task_type?: boolean
  note?: string
}

type ModelsByProvider = Record<EmbedderProvider, EmbedderModel[]>

const EMBEDDER_OPTIONS: Array<{
  value: EmbedderProvider
  label: string
  desc: string
  defaultModel?: string
}> = [
  {
    value: 'local',
    label: 'Local (offline)',
    desc: 'paraphrase-multilingual-mpnet-base-v2 · 768 dims · No API key required',
  },
  {
    value: 'openai',
    label: 'OpenAI',
    desc: '1536 dims · Requires an OpenAI API key',
    defaultModel: 'text-embedding-3-small',
  },
  {
    value: 'gemini',
    label: 'Google Gemini',
    desc: '768 / 1536 / 3072 dims (MRL) · Requires a Gemini API key · generous free tier',
    defaultModel: 'gemini-embedding-001',
  },
]

const GEMINI_DIM_CHOICES = [768, 1536, 3072] as const

const PARSER_OPTIONS: Array<{ value: 'marker'; label: string; desc: string }> = [
  {
    value: 'marker',
    label: 'Marker (default)',
    desc: 'PDF, DOCX, PPTX, XLSX, HTML, EPUB, images with OCR · Offline · ~500 MB download',
  },
]

// Gemini API key pattern (Google AI Studio): AIzaSy + 33 chars
const GEMINI_KEY_PATTERN = /^AIzaSy[A-Za-z0-9_-]{33}$/

export default function KnowledgeSettings() {
  const { hasPermission } = useAuth()
  const canManage = hasPermission('knowledge', 'manage')

  const [settings, setSettings] = useState<KnowledgeSettings | null>(null)
  const [parserStatus, setParserStatus] = useState<ParserStatus | null>(null)
  const [models, setModels] = useState<ModelsByProvider | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [installing, setInstalling] = useState(false)
  const [installDone, setInstallDone] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)

  const [embedder, setEmbedder] = useState<EmbedderProvider>('local')
  const [embedderModel, setEmbedderModel] = useState<string>('')
  const [parser, setParser] = useState<'marker'>('marker')
  const [openaiKey, setOpenaiKey] = useState<string>('')
  const [openaiKeySet, setOpenaiKeySet] = useState<boolean>(false)
  const [geminiKey, setGeminiKey] = useState<string>('')
  const [geminiKeySet, setGeminiKeySet] = useState<boolean>(false)
  const [geminiDim, setGeminiDim] = useState<number>(768)

  const load = useCallback(async () => {
    try {
      const [cfg, status, mdls] = await Promise.all([
        api.get('/knowledge/settings'),
        api.get('/knowledge/parsers/status').catch(() => null),
        api.get('/knowledge/embedders/models').catch(() => null),
      ])
      setSettings(cfg)
      if (status) setParserStatus(status)
      if (mdls?.providers) setModels(mdls.providers as ModelsByProvider)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load settings')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  useEffect(() => {
    if (settings) {
      setEmbedder(settings.embedder_provider)
      setParser(settings.parser_default)
      setOpenaiKeySet(Boolean(settings.openai_api_key_set))
      setGeminiKeySet(Boolean(settings.gemini_api_key_set))
      // vector_dim reflects the active Gemini dim when provider is gemini
      if (settings.embedder_provider === 'gemini') {
        setGeminiDim(settings.vector_dim || 768)
      }
    }
  }, [settings])

  useEffect(() => {
    if (!settings || !models) return
    const list = models[embedder] || []
    const incoming = embedder === settings.embedder_provider ? settings.embedder_model : ''
    if (incoming && list.some((m) => m.id === incoming)) {
      setEmbedderModel(incoming)
      return
    }
    const recommended = list.find((m) => m.recommended)?.id
    setEmbedderModel(recommended || list[0]?.id || '')
  }, [settings, models, embedder])

  const providerLocked = Boolean(settings?.locked)
  const modelEditable = embedder === 'openai' || embedder === 'gemini'
  const providerNeedsKey = embedder === 'openai' || embedder === 'gemini'

  const dirty = Boolean(
    settings && (
      embedder !== settings.embedder_provider ||
      parser !== settings.parser_default ||
      (modelEditable && embedderModel !== settings.embedder_model) ||
      (embedder === 'openai' && openaiKey.trim().length > 0) ||
      (embedder === 'gemini' && geminiKey.trim().length > 0) ||
      (embedder === 'gemini' && geminiDim !== (settings.vector_dim || 768))
    )
  )

  // Key validation — same UX as OpenAI's sk- prefix check
  const geminiKeyInvalid = geminiKey.trim().length > 0 && !GEMINI_KEY_PATTERN.test(geminiKey.trim())
  const openaiKeyInvalid = openaiKey.trim().length > 0 && !openaiKey.trim().startsWith('sk-')

  const handleSave = async () => {
    setSaving(true)
    setError(null)
    setSaved(false)
    try {
      const payload: Record<string, string | number> = {
        embedder_provider: embedder,
        parser_default: parser,
      }
      if (modelEditable && embedderModel.trim()) {
        payload.embedder_model = embedderModel.trim()
      }
      if (embedder === 'openai' && openaiKey.trim()) {
        payload.openai_api_key = openaiKey.trim()
      }
      if (embedder === 'gemini') {
        if (geminiKey.trim()) {
          payload.gemini_api_key = geminiKey.trim()
        }
        payload.gemini_dim = geminiDim
      }
      const updated = await api.put('/knowledge/settings', payload)
      setSettings(updated)
      setOpenaiKey('')
      setGeminiKey('')
      setSaved(true)
      setTimeout(() => setSaved(false), 2500)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Save failed')
    } finally {
      setSaving(false)
    }
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
          {providerLocked && (
            <div className="flex items-center gap-1.5 px-2 py-1 bg-yellow-500/10 rounded-lg">
              <Info size={12} className="text-yellow-400" />
              <span className="text-xs text-yellow-400">Locked</span>
            </div>
          )}
        </div>

        <div className="space-y-2">
          {EMBEDDER_OPTIONS.map((opt) => (
            <label
              key={opt.value}
              className={`flex items-start gap-3 p-3 rounded-lg border transition-colors cursor-pointer ${
                embedder === opt.value
                  ? 'border-[#00FFA7]/40 bg-[#00FFA7]/5'
                  : 'border-[#344054] hover:border-[#344054]/80'
              } ${providerLocked || !canManage ? 'opacity-60 cursor-not-allowed' : ''}`}
            >
              <input
                type="radio"
                name="embedder"
                value={opt.value}
                checked={embedder === opt.value}
                onChange={() => {
                  if (providerLocked || !canManage) return
                  setEmbedder(opt.value)
                  const list = models?.[opt.value] || []
                  const recommended = list.find((m) => m.recommended)?.id
                  const fallback = list[0]?.id
                  setEmbedderModel(recommended || fallback || opt.defaultModel || '')
                }}
                disabled={providerLocked || !canManage}
                className="mt-0.5 accent-[#00FFA7]"
              />
              <div className="flex-1">
                <p className="text-sm font-medium text-[#D0D5DD]">{opt.label}</p>
                <p className="text-xs text-[#667085]">{opt.desc}</p>
              </div>
            </label>
          ))}
        </div>

        {/* Model selector — for openai and gemini */}
        {modelEditable && (
          <div className="mt-4 pt-4 border-t border-[#344054]">
            <label className="block text-xs font-medium text-[#D0D5DD] mb-1.5">
              Model
            </label>
            <select
              value={embedderModel}
              onChange={(e) => setEmbedderModel(e.target.value)}
              disabled={!canManage || providerLocked}
              className="w-full px-3 py-2 bg-[#0C111D] border border-[#344054] rounded-lg text-sm text-[#F9FAFB] focus:outline-none focus:border-[#00FFA7]/60 disabled:opacity-60 cursor-pointer"
            >
              {(models?.[embedder] || []).map((m) => (
                <option key={m.id} value={m.id}>
                  {m.id}
                  {m.recommended ? ' · recommended' : ''}
                  {m.legacy ? ' · legacy' : ''}
                  {m.preview ? ' · preview' : ''}
                </option>
              ))}
            </select>
            {(() => {
              const current = models?.[embedder]?.find((m) => m.id === embedderModel)
              if (!current) return null
              return (
                <div className="mt-1.5 space-y-0.5">
                  {embedder === 'openai' && (
                    <p className="text-xs text-[#667085]">
                      Vector dimensions: <code className="text-[#98A2B3] bg-[#0C111D] px-1 py-0.5 rounded">{current.dim}</code>
                    </p>
                  )}
                  {current.note && (
                    <p className="text-xs text-[#667085]">{current.note}</p>
                  )}
                </div>
              )
            })()}
          </div>
        )}

        {/* Gemini dim selector — MRL allows 768 / 1536 / 3072 */}
        {embedder === 'gemini' && (
          <div className="mt-4 pt-4 border-t border-[#344054]">
            <label className="block text-xs font-medium text-[#D0D5DD] mb-1.5">
              Vector Dimensions
            </label>
            <div className="flex gap-2">
              {GEMINI_DIM_CHOICES.map((d) => (
                <button
                  key={d}
                  type="button"
                  onClick={() => { if (!providerLocked && canManage) setGeminiDim(d) }}
                  disabled={providerLocked || !canManage}
                  className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${
                    geminiDim === d
                      ? 'bg-[#00FFA7]/15 text-[#00FFA7] border border-[#00FFA7]/40'
                      : 'bg-[#0C111D] text-[#98A2B3] border border-[#344054] hover:border-[#344054]/80'
                  } ${providerLocked || !canManage ? 'opacity-60 cursor-not-allowed' : ''}`}
                >
                  {d}
                  {d === 768 ? ' · recommended' : ''}
                </button>
              ))}
            </div>
            <p className="text-xs text-[#667085] mt-1.5">
              Matryoshka Representation Learning — same model, selectable output size.
              768 aligns storage cost with the local provider. 3072 maximizes quality.
            </p>
          </div>
        )}

        {/* OpenAI API key — inline input (never displayed back) */}
        {embedder === 'openai' && (
          <div className="mt-4 pt-4 border-t border-[#344054]">
            <div className="flex items-center justify-between mb-1.5">
              <label className="block text-xs font-medium text-[#D0D5DD]">
                OpenAI API Key
              </label>
              {openaiKeySet && (
                <span className="flex items-center gap-1 text-xs text-[#00FFA7]">
                  <CheckCircle size={12} /> Configured
                </span>
              )}
            </div>
            <input
              type="password"
              value={openaiKey}
              onChange={(e) => setOpenaiKey(e.target.value)}
              placeholder={openaiKeySet ? '•••••••• · paste a new key to rotate' : 'sk-...'}
              disabled={!canManage}
              autoComplete="off"
              spellCheck={false}
              className={`w-full px-3 py-2 bg-[#0C111D] border rounded-lg text-sm text-[#F9FAFB] placeholder-[#667085] focus:outline-none disabled:opacity-60 font-mono ${
                openaiKeyInvalid
                  ? 'border-red-500/60 focus:border-red-500'
                  : 'border-[#344054] focus:border-[#00FFA7]/60'
              }`}
            />
            {openaiKeyInvalid && (
              <p className="text-xs text-red-400 mt-1.5">Key must start with <code>sk-</code>.</p>
            )}
            <p className="text-xs text-[#667085] mt-1.5">
              Stored in <code className="text-[#98A2B3] bg-[#0C111D] px-1 py-0.5 rounded">.env</code> as <code className="text-[#98A2B3] bg-[#0C111D] px-1 py-0.5 rounded">OPENAI_API_KEY</code>. Only used by the Knowledge embedder.
            </p>
          </div>
        )}

        {/* Gemini API key — inline input (never displayed back) */}
        {embedder === 'gemini' && (
          <div className="mt-4 pt-4 border-t border-[#344054]">
            <div className="flex items-center justify-between mb-1.5">
              <label className="block text-xs font-medium text-[#D0D5DD]">
                Gemini API Key
              </label>
              {geminiKeySet && (
                <span className="flex items-center gap-1 text-xs text-[#00FFA7]">
                  <CheckCircle size={12} /> Configured
                </span>
              )}
            </div>
            <input
              type="password"
              value={geminiKey}
              onChange={(e) => setGeminiKey(e.target.value)}
              placeholder={geminiKeySet ? '•••••••• · paste a new key to rotate' : 'AIzaSy...'}
              disabled={!canManage}
              autoComplete="off"
              spellCheck={false}
              className={`w-full px-3 py-2 bg-[#0C111D] border rounded-lg text-sm text-[#F9FAFB] placeholder-[#667085] focus:outline-none disabled:opacity-60 font-mono ${
                geminiKeyInvalid
                  ? 'border-red-500/60 focus:border-red-500'
                  : 'border-[#344054] focus:border-[#00FFA7]/60'
              }`}
            />
            {geminiKeyInvalid && (
              <p className="text-xs text-red-400 mt-1.5">
                Key must match the Google AI Studio pattern <code>AIzaSy...</code> (39 chars total).
              </p>
            )}
            <p className="text-xs text-[#667085] mt-1.5">
              Get one at <a href="https://aistudio.google.com/apikey" target="_blank" rel="noreferrer" className="text-[#00FFA7]/80 hover:text-[#00FFA7] underline">aistudio.google.com/apikey</a>.
              Stored in <code className="text-[#98A2B3] bg-[#0C111D] px-1 py-0.5 rounded">.env</code> as <code className="text-[#98A2B3] bg-[#0C111D] px-1 py-0.5 rounded">GEMINI_API_KEY</code>. Only used by the Knowledge embedder.
            </p>
          </div>
        )}

        {providerLocked && (
          <p className="text-xs text-[#667085] mt-3">
            Embedder provider is locked because connections exist. To change the provider, remove all connections and recreate them (reindex feature planned for v0.25.1).
          </p>
        )}
      </div>

      {/* Parser */}
      <div className="bg-[#182230] border border-[#344054] rounded-xl p-5">
        <h3 className="text-sm font-semibold text-[#F9FAFB] mb-1">Default Parser</h3>
        <p className="text-xs text-[#667085] mb-4">Used when uploading documents without explicit parser selection.</p>

        <div className="space-y-2">
          {PARSER_OPTIONS.map((opt) => (
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
                onChange={() => canManage && setParser(opt.value)}
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
            disabled={saving || !dirty || geminiKeyInvalid || openaiKeyInvalid}
            className="flex items-center gap-2 px-4 py-2 bg-[#00FFA7] text-[#0C111D] rounded-lg text-sm font-medium hover:bg-[#00FFA7]/90 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {saving ? <RefreshCw size={14} className="animate-spin" /> : null}
            {saved ? 'Saved!' : 'Save Settings'}
          </button>
          {saved && <CheckCircle size={14} className="text-[#00FFA7]" />}
          {dirty && !saving && !saved && (
            <span className="text-xs text-[#667085]">Unsaved changes</span>
          )}
        </div>
      )}
    </div>
  )
}
