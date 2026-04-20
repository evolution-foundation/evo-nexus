import { useEffect, useState, useCallback } from 'react'
import { Plus, Pencil, Trash2, RefreshCw, X, Globe, Lock, Users } from 'lucide-react'
import { api } from '../../lib/api'
import { useAuth } from '../../context/AuthContext'
import { useKnowledge } from '../../context/KnowledgeContext'

interface Space {
  id: string
  slug: string
  name: string
  description?: string
  visibility: 'private' | 'shared' | 'public'
  owner_id?: string
  content_type_boosts?: Record<string, number>
  chunks_count?: number
  documents_count?: number
  created_at: string
}

interface SpaceForm {
  name: string
  slug: string
  description: string
  visibility: 'private' | 'shared' | 'public'
  content_type_boosts_raw: string
}

const defaultForm: SpaceForm = {
  name: '',
  slug: '',
  description: '',
  visibility: 'private',
  content_type_boosts_raw: '{}',
}

function VisibilityIcon({ v }: { v: Space['visibility'] }) {
  if (v === 'public') return <Globe size={12} className="text-[#00FFA7]" />
  if (v === 'shared') return <Users size={12} className="text-blue-400" />
  return <Lock size={12} className="text-[#667085]" />
}

export default function KnowledgeSpaces() {
  const { hasPermission } = useAuth()
  const { activeConnectionId } = useKnowledge()
  const canManage = hasPermission('knowledge', 'manage')

  const [spaces, setSpaces] = useState<Space[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [showModal, setShowModal] = useState(false)
  const [editingSpace, setEditingSpace] = useState<Space | null>(null)
  const [form, setForm] = useState<SpaceForm>(defaultForm)
  const [saving, setSaving] = useState(false)
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [boostsError, setBoostsError] = useState<string | null>(null)

  const load = useCallback(async () => {
    if (!activeConnectionId) { setLoading(false); return }
    setLoading(true)
    try {
      const data = await api.get(`/knowledge/connections/${activeConnectionId}/spaces`)
      setSpaces(data.spaces || data || [])
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load spaces')
    }
    setLoading(false)
  }, [activeConnectionId])

  useEffect(() => { load() }, [load])

  function openCreate() {
    setEditingSpace(null)
    setForm(defaultForm)
    setBoostsError(null)
    setShowModal(true)
  }

  function openEdit(space: Space) {
    setEditingSpace(space)
    setForm({
      name: space.name,
      slug: space.slug,
      description: space.description || '',
      visibility: space.visibility,
      content_type_boosts_raw: JSON.stringify(space.content_type_boosts || {}, null, 2),
    })
    setBoostsError(null)
    setShowModal(true)
  }

  function setField(key: keyof SpaceForm, value: string) {
    setForm((prev) => {
      const next = { ...prev, [key]: value }
      if (key === 'name') {
        next.slug = value.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '')
      }
      return next
    })
  }

  function validateBoosts(): Record<string, number> | null {
    try {
      const parsed = JSON.parse(form.content_type_boosts_raw)
      if (typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('Must be a JSON object')
      for (const [k, v] of Object.entries(parsed)) {
        if (typeof v !== 'number') throw new Error(`Value for "${k}" must be a number`)
      }
      setBoostsError(null)
      return parsed
    } catch (e) {
      setBoostsError(e instanceof Error ? e.message : 'Invalid JSON')
      return null
    }
  }

  async function handleSave() {
    const boosts = validateBoosts()
    if (boosts === null) return
    setSaving(true)
    setError(null)
    try {
      const body = {
        name: form.name,
        slug: form.slug,
        description: form.description || undefined,
        visibility: form.visibility,
        content_type_boosts: boosts,
      }
      if (editingSpace) {
        await api.patch(`/knowledge/connections/${activeConnectionId}/spaces/${editingSpace.id}`, body)
      } else {
        await api.post(`/knowledge/connections/${activeConnectionId}/spaces`, body)
      }
      setShowModal(false)
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Save failed')
    }
    setSaving(false)
  }

  async function handleDelete(id: string) {
    setDeleting(true)
    try {
      await api.delete(`/knowledge/connections/${activeConnectionId}/spaces/${id}`)
      setConfirmDeleteId(null)
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Delete failed')
    }
    setDeleting(false)
  }

  if (!activeConnectionId) {
    return (
      <div className="text-center py-12 text-[#667085] text-sm">
        Select a connection using the switcher above.
      </div>
    )
  }

  if (loading) {
    return (
      <div className="space-y-2">
        {[...Array(4)].map((_, i) => (
          <div key={i} className="h-16 bg-[#182230] border border-[#344054] rounded-xl animate-pulse" />
        ))}
      </div>
    )
  }

  return (
    <div className="space-y-4">
      {error && (
        <div className="bg-red-500/10 border border-red-500/20 rounded-lg px-4 py-3 text-sm text-red-400">
          {error}
        </div>
      )}

      {canManage && (
        <div className="flex justify-end">
          <button
            onClick={openCreate}
            className="flex items-center gap-2 px-4 py-2 bg-[#00FFA7] text-[#0C111D] rounded-lg text-sm font-medium hover:bg-[#00FFA7]/90 transition-colors"
          >
            <Plus size={14} /> New Space
          </button>
        </div>
      )}

      {spaces.length === 0 ? (
        <div className="text-center py-12 bg-[#182230] border border-[#344054] rounded-xl text-[#667085] text-sm">
          No spaces yet. Create one to organize your documents.
        </div>
      ) : (
        <div className="bg-[#182230] border border-[#344054] rounded-xl overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-[#344054]">
                {['Name', 'Visibility', 'Docs', 'Chunks', 'Created', ''].map((h) => (
                  <th key={h} className="px-4 py-3 text-left text-xs text-[#667085] font-medium">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {spaces.map((s) => (
                <tr key={s.id} className="border-b border-[#344054]/50 last:border-0 hover:bg-white/2 transition-colors">
                  <td className="px-4 py-3">
                    <p className="font-medium text-[#D0D5DD]">{s.name}</p>
                    <p className="text-xs text-[#667085] font-mono">{s.slug}</p>
                  </td>
                  <td className="px-4 py-3">
                    <span className="flex items-center gap-1 text-xs text-[#667085]">
                      <VisibilityIcon v={s.visibility} />
                      {s.visibility}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-xs text-[#667085]">{s.documents_count ?? '—'}</td>
                  <td className="px-4 py-3 text-xs text-[#667085]">{s.chunks_count?.toLocaleString() ?? '—'}</td>
                  <td className="px-4 py-3 text-xs text-[#667085]">{new Date(s.created_at).toLocaleDateString()}</td>
                  <td className="px-4 py-3">
                    {canManage && (
                      <div className="flex items-center gap-1">
                        <button onClick={() => openEdit(s)} className="p-1.5 rounded-lg text-[#667085] hover:text-[#D0D5DD] hover:bg-white/5 transition-colors">
                          <Pencil size={12} />
                        </button>
                        <button onClick={() => setConfirmDeleteId(s.id)} className="p-1.5 rounded-lg text-[#667085] hover:text-red-400 hover:bg-red-500/10 transition-colors">
                          <Trash2 size={12} />
                        </button>
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Modal */}
      {showModal && (
        <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4">
          <div className="bg-[#0C111D] border border-[#344054] rounded-xl w-full max-w-md shadow-2xl">
            <div className="flex items-center justify-between px-5 py-4 border-b border-[#344054]">
              <h3 className="text-sm font-semibold text-[#F9FAFB]">{editingSpace ? 'Edit Space' : 'New Space'}</h3>
              <button onClick={() => setShowModal(false)} className="p-1.5 rounded-lg text-[#667085] hover:text-[#D0D5DD] hover:bg-white/5 transition-colors"><X size={14} /></button>
            </div>
            <div className="px-5 py-4 space-y-3">
              <div>
                <label className="block text-xs text-[#667085] mb-1">Name *</label>
                <input type="text" value={form.name} onChange={(e) => setField('name', e.target.value)} className="w-full bg-[#182230] border border-[#344054] rounded-lg px-3 py-2 text-sm text-[#F9FAFB] focus:border-[#00FFA7] focus:outline-none" placeholder="Academy 2026" />
              </div>
              <div>
                <label className="block text-xs text-[#667085] mb-1">Slug *</label>
                <input type="text" value={form.slug} onChange={(e) => setField('slug', e.target.value)} className="w-full bg-[#182230] border border-[#344054] rounded-lg px-3 py-2 text-sm text-[#F9FAFB] font-mono focus:border-[#00FFA7] focus:outline-none" placeholder="academy-2026" />
              </div>
              <div>
                <label className="block text-xs text-[#667085] mb-1">Description</label>
                <textarea value={form.description} onChange={(e) => setField('description', e.target.value)} rows={2} className="w-full bg-[#182230] border border-[#344054] rounded-lg px-3 py-2 text-sm text-[#F9FAFB] focus:border-[#00FFA7] focus:outline-none resize-none" />
              </div>
              <div>
                <label className="block text-xs text-[#667085] mb-1">Visibility</label>
                <select value={form.visibility} onChange={(e) => setField('visibility', e.target.value)} className="w-full bg-[#182230] border border-[#344054] rounded-lg px-3 py-2 text-sm text-[#D0D5DD] focus:border-[#00FFA7] focus:outline-none">
                  <option value="private">private</option>
                  <option value="shared">shared</option>
                  <option value="public">public</option>
                </select>
              </div>
              <div>
                <label className="block text-xs text-[#667085] mb-1">Content Type Boosts (JSON)</label>
                <textarea
                  value={form.content_type_boosts_raw}
                  onChange={(e) => setField('content_type_boosts_raw', e.target.value)}
                  rows={3}
                  className={`w-full bg-[#182230] border rounded-lg px-3 py-2 text-sm text-[#F9FAFB] font-mono focus:outline-none resize-none ${boostsError ? 'border-red-500/50' : 'border-[#344054] focus:border-[#00FFA7]'}`}
                />
                {boostsError && <p className="text-xs text-red-400 mt-1">{boostsError}</p>}
              </div>
              <div className="flex gap-3 pt-2">
                <button onClick={() => setShowModal(false)} className="flex-1 px-4 py-2 bg-white/5 text-[#D0D5DD] rounded-lg text-sm hover:bg-white/10 transition-colors">Cancel</button>
                <button onClick={handleSave} disabled={saving || !form.name} className="flex-1 px-4 py-2 bg-[#00FFA7] text-[#0C111D] rounded-lg text-sm font-semibold hover:bg-[#00FFA7]/90 transition-colors disabled:opacity-50">
                  {saving ? <RefreshCw size={14} className="animate-spin mx-auto" /> : (editingSpace ? 'Save' : 'Create')}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Delete confirm */}
      {confirmDeleteId && (
        <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4">
          <div className="bg-[#0C111D] border border-[#344054] rounded-xl w-full max-w-sm shadow-2xl p-6 text-center">
            <Trash2 size={28} className="text-red-400 mx-auto mb-3" />
            <p className="text-sm font-semibold text-[#F9FAFB] mb-1">Delete Space?</p>
            <p className="text-xs text-[#667085] mb-6">All documents and chunks in this space will be permanently deleted from Postgres.</p>
            <div className="flex gap-3">
              <button onClick={() => setConfirmDeleteId(null)} className="flex-1 px-4 py-2 bg-white/5 text-[#D0D5DD] rounded-lg text-sm hover:bg-white/10 transition-colors">Cancel</button>
              <button onClick={() => handleDelete(confirmDeleteId)} disabled={deleting} className="flex-1 px-4 py-2 bg-red-500 text-white rounded-lg text-sm font-semibold hover:bg-red-600 transition-colors disabled:opacity-50">
                {deleting ? <RefreshCw size={14} className="animate-spin mx-auto" /> : 'Delete'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
