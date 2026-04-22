import { useEffect, useState, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { Plus, Copy, Trash2, RefreshCw, X, Key, CheckCircle, AlertTriangle } from 'lucide-react'
import { api } from '../../lib/api'
import { useAuth } from '../../context/AuthContext'
import { useKnowledge } from '../../context/KnowledgeContext'

interface ApiKey {
  id: string
  name: string
  prefix: string
  connection_id?: string
  space_ids?: string[]
  scopes: string[]
  rate_limit_per_minute?: number
  rate_limit_per_day?: number
  expires_at?: string
  last_used_at?: string
  created_at: string
  revoked?: boolean
}

const ALL_SCOPES = ['search', 'documents:read', 'documents:write', 'spaces:read', 'spaces:write']

interface NewKeyForm {
  name: string
  scopes: string[]
  rate_limit_per_minute: string
  rate_limit_per_day: string
  expires_at: string
}

const defaultForm: NewKeyForm = {
  name: '',
  scopes: ['search', 'documents:read'],
  rate_limit_per_minute: '60',
  rate_limit_per_day: '10000',
  expires_at: '',
}

export default function KnowledgeApiKeys() {
  const { t } = useTranslation()
  const { hasPermission } = useAuth()
  const { activeConnectionId } = useKnowledge()
  const canManage = hasPermission('knowledge', 'manage')

  const [keys, setKeys] = useState<ApiKey[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [showModal, setShowModal] = useState(false)
  const [form, setForm] = useState<NewKeyForm>(defaultForm)
  const [saving, setSaving] = useState(false)
  const [createdToken, setCreatedToken] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  const [confirmRevokeId, setConfirmRevokeId] = useState<string | null>(null)

  const load = useCallback(async () => {
    if (!activeConnectionId) { setLoading(false); return }
    setLoading(true)
    try {
      const data = await api.get(`/knowledge/connections/${activeConnectionId}/api-keys`)
      // Backend returns {api_keys: [...]}; tolerate legacy {keys} and bare array.
      const list = data.api_keys || data.keys || (Array.isArray(data) ? data : [])
      setKeys(Array.isArray(list) ? list : [])
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load API keys')
    }
    setLoading(false)
  }, [activeConnectionId])

  useEffect(() => { load() }, [load])

  function toggleScope(scope: string) {
    setForm((prev) => ({
      ...prev,
      scopes: prev.scopes.includes(scope)
        ? prev.scopes.filter((s) => s !== scope)
        : [...prev.scopes, scope],
    }))
  }

  async function handleCreate() {
    if (!form.name.trim()) return
    setSaving(true)
    setError(null)
    try {
      const body = {
        name: form.name,
        connection_id: activeConnectionId,
        scopes: form.scopes,
        rate_limit_per_minute: parseInt(form.rate_limit_per_minute) || undefined,
        rate_limit_per_day: parseInt(form.rate_limit_per_day) || undefined,
        expires_at: form.expires_at || undefined,
      }
      const result = await api.post(`/knowledge/connections/${activeConnectionId}/api-keys`, body)
      // Backend returns plain token exactly once
      const token = result.token || result.key || result.api_key
      if (token) {
        setCreatedToken(token)
      }
      setShowModal(false)
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to create API key')
    }
    setSaving(false)
  }

  async function handleRevoke(id: string) {
    try {
      await api.delete(`/knowledge/connections/${activeConnectionId}/api-keys/${id}`)
      setConfirmRevokeId(null)
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Revoke failed')
    }
  }

  function copyToken() {
    if (!createdToken) return
    navigator.clipboard.writeText(createdToken).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }

  if (!activeConnectionId) {
    return <div className="text-center py-12 text-[#667085] text-sm">Select a connection using the switcher above.</div>
  }

  if (loading) {
    return (
      <div className="space-y-2">
        {[...Array(3)].map((_, i) => <div key={i} className="h-16 bg-[#182230] border border-[#344054] rounded-xl animate-pulse" />)}
      </div>
    )
  }

  return (
    <div className="space-y-4">
      {error && (
        <div className="bg-red-500/10 border border-red-500/20 rounded-lg px-4 py-3 text-sm text-red-400">{error}</div>
      )}

      {/* Plain token reveal (shown once after creation) */}
      {createdToken && (
        <div className="bg-[#00FFA7]/5 border border-[#00FFA7]/30 rounded-xl p-5">
          <div className="flex items-start gap-3">
            <CheckCircle size={20} className="text-[#00FFA7] shrink-0 mt-0.5" />
            <div className="flex-1 min-w-0">
              <p className="text-sm font-semibold text-[#F9FAFB] mb-1">API Key created</p>
              <p className="text-xs text-yellow-400 mb-3 flex items-center gap-1.5">
                <AlertTriangle size={12} />
                Save this token now — it won&apos;t be shown again.
              </p>
              <div className="flex items-center gap-2">
                <code className="flex-1 bg-[#0C111D] border border-[#344054] rounded-lg px-3 py-2 text-sm text-[#00FFA7] font-mono break-all">
                  {createdToken}
                </code>
                <button
                  onClick={copyToken}
                  className="flex items-center gap-1.5 px-3 py-2 bg-[#00FFA7]/10 text-[#00FFA7] rounded-lg text-xs font-medium hover:bg-[#00FFA7]/20 transition-colors shrink-0"
                >
                  {copied ? <CheckCircle size={12} /> : <Copy size={12} />}
                  {copied ? 'Copied!' : 'Copy'}
                </button>
              </div>
            </div>
            <button onClick={() => setCreatedToken(null)} className="p-1.5 rounded-lg text-[#667085] hover:text-[#D0D5DD] hover:bg-white/5 shrink-0"><X size={14} /></button>
          </div>
        </div>
      )}

      {canManage && (
        <div className="flex justify-end">
          <button
            onClick={() => { setForm(defaultForm); setShowModal(true) }}
            className="flex items-center gap-2 px-4 py-2 bg-[#00FFA7] text-[#0C111D] rounded-lg text-sm font-medium hover:bg-[#00FFA7]/90 transition-colors"
          >
            <Plus size={14} /> New API Key
          </button>
        </div>
      )}

      {keys.length === 0 ? (
        <div className="text-center py-12 bg-[#182230] border border-[#344054] rounded-xl text-[#667085] text-sm">
          No API keys yet. Create one to access knowledge from external apps or skills.
        </div>
      ) : (
        <div className="bg-[#182230] border border-[#344054] rounded-xl overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-[#344054]">
                {['Name', 'Prefix', 'Scopes', 'Rate Limit', 'Expires', 'Last Used', ''].map((h) => (
                  <th key={h} className="px-4 py-3 text-left text-xs text-[#667085] font-medium">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {keys.map((k) => (
                <tr key={k.id} className={`border-b border-[#344054]/50 last:border-0 hover:bg-white/2 transition-colors ${k.revoked ? 'opacity-50' : ''}`}>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      <Key size={12} className="text-[#667085]" />
                      <p className="text-[#D0D5DD] font-medium">{k.name}</p>
                    </div>
                    {k.revoked && <span className="text-[10px] text-red-400 ml-4">revoked</span>}
                  </td>
                  <td className="px-4 py-3">
                    <code className="text-xs text-[#00FFA7] bg-[#00FFA7]/5 px-1.5 py-0.5 rounded font-mono">{k.prefix}...</code>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex flex-wrap gap-1">
                      {k.scopes.map((s) => (
                        <span key={s} className="text-[10px] px-1.5 py-0.5 rounded bg-white/5 text-[#667085]">{s}</span>
                      ))}
                    </div>
                  </td>
                  <td className="px-4 py-3 text-xs text-[#667085]">
                    {k.rate_limit_per_minute && `${k.rate_limit_per_minute}/min`}
                    {k.rate_limit_per_day && ` · ${k.rate_limit_per_day}/day`}
                  </td>
                  <td className="px-4 py-3 text-xs text-[#667085]">
                    {k.expires_at ? new Date(k.expires_at).toLocaleDateString() : 'Never'}
                  </td>
                  <td className="px-4 py-3 text-xs text-[#667085]">
                    {k.last_used_at ? new Date(k.last_used_at).toLocaleString() : '—'}
                  </td>
                  <td className="px-4 py-3">
                    {canManage && !k.revoked && (
                      <button
                        onClick={() => setConfirmRevokeId(k.id)}
                        className="p-1.5 rounded-lg text-[#667085] hover:text-red-400 hover:bg-red-500/10 transition-colors"
                        title="Revoke key"
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

      {/* Create modal */}
      {showModal && (
        <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4">
          <div className="bg-[#0C111D] border border-[#344054] rounded-xl w-full max-w-md shadow-2xl">
            <div className="flex items-center justify-between px-5 py-4 border-b border-[#344054]">
              <div className="flex items-center gap-2">
                <Key size={14} className="text-[#00FFA7]" />
                <h3 className="text-sm font-semibold text-[#F9FAFB]">{t('knowledge.newApiKey')}</h3>
              </div>
              <button onClick={() => setShowModal(false)} className="p-1.5 rounded-lg text-[#667085] hover:text-[#D0D5DD] hover:bg-white/5"><X size={14} /></button>
            </div>
            <div className="px-5 py-4 space-y-4">
              <div>
                <label className="block text-xs text-[#667085] mb-1">Name *</label>
                <input
                  type="text"
                  placeholder="My app integration"
                  value={form.name}
                  onChange={(e) => setForm((p) => ({ ...p, name: e.target.value }))}
                  className="w-full bg-[#182230] border border-[#344054] rounded-lg px-3 py-2 text-sm text-[#F9FAFB] focus:border-[#00FFA7] focus:outline-none"
                />
              </div>

              <div>
                <label className="block text-xs text-[#667085] mb-2">Scopes</label>
                <div className="flex flex-wrap gap-2">
                  {ALL_SCOPES.map((s) => (
                    <button
                      key={s}
                      onClick={() => toggleScope(s)}
                      className={`px-2.5 py-1 rounded-lg text-xs font-medium transition-colors ${
                        form.scopes.includes(s)
                          ? 'bg-[#00FFA7]/10 text-[#00FFA7] border border-[#00FFA7]/30'
                          : 'bg-white/5 text-[#667085] border border-[#344054] hover:border-[#667085]'
                      }`}
                    >
                      {s}
                    </button>
                  ))}
                </div>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs text-[#667085] mb-1">Rate limit / min</label>
                  <input
                    type="number"
                    value={form.rate_limit_per_minute}
                    onChange={(e) => setForm((p) => ({ ...p, rate_limit_per_minute: e.target.value }))}
                    className="w-full bg-[#182230] border border-[#344054] rounded-lg px-3 py-2 text-sm text-[#F9FAFB] focus:border-[#00FFA7] focus:outline-none"
                  />
                </div>
                <div>
                  <label className="block text-xs text-[#667085] mb-1">Rate limit / day</label>
                  <input
                    type="number"
                    value={form.rate_limit_per_day}
                    onChange={(e) => setForm((p) => ({ ...p, rate_limit_per_day: e.target.value }))}
                    className="w-full bg-[#182230] border border-[#344054] rounded-lg px-3 py-2 text-sm text-[#F9FAFB] focus:border-[#00FFA7] focus:outline-none"
                  />
                </div>
                <div className="col-span-2">
                  <label className="block text-xs text-[#667085] mb-1">Expiration date (optional)</label>
                  <input
                    type="date"
                    value={form.expires_at}
                    onChange={(e) => setForm((p) => ({ ...p, expires_at: e.target.value }))}
                    className="w-full bg-[#182230] border border-[#344054] rounded-lg px-3 py-2 text-sm text-[#D0D5DD] focus:border-[#00FFA7] focus:outline-none"
                  />
                </div>
              </div>

              <div className="flex gap-3 pt-1">
                <button onClick={() => setShowModal(false)} className="flex-1 px-4 py-2 bg-white/5 text-[#D0D5DD] rounded-lg text-sm hover:bg-white/10 transition-colors">Cancel</button>
                <button onClick={handleCreate} disabled={saving || !form.name.trim()} className="flex-1 px-4 py-2 bg-[#00FFA7] text-[#0C111D] rounded-lg text-sm font-semibold hover:bg-[#00FFA7]/90 transition-colors disabled:opacity-50">
                  {saving ? <RefreshCw size={14} className="animate-spin mx-auto" /> : 'Create Key'}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Revoke confirm */}
      {confirmRevokeId && (
        <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4">
          <div className="bg-[#0C111D] border border-[#344054] rounded-xl w-full max-w-sm shadow-2xl p-6 text-center">
            <Trash2 size={28} className="text-red-400 mx-auto mb-3" />
            <p className="text-sm font-semibold text-[#F9FAFB] mb-1">Revoke API Key?</p>
            <p className="text-xs text-[#667085] mb-6">Any application using this key will immediately lose access. This cannot be undone.</p>
            <div className="flex gap-3">
              <button onClick={() => setConfirmRevokeId(null)} className="flex-1 px-4 py-2 bg-white/5 text-[#D0D5DD] rounded-lg text-sm hover:bg-white/10 transition-colors">Cancel</button>
              <button onClick={() => handleRevoke(confirmRevokeId)} className="flex-1 px-4 py-2 bg-red-500 text-white rounded-lg text-sm font-semibold hover:bg-red-600 transition-colors">Revoke</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
