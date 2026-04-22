import { useEffect, useState, useCallback, useRef } from 'react'
import { Plus, Pencil, Trash2, RefreshCw, X, GripVertical } from 'lucide-react'
import { api } from '../../lib/api'
import { useAuth } from '../../context/AuthContext'
import { useKnowledge } from '../../context/KnowledgeContext'

interface Unit {
  id: string
  space_id: string
  slug: string
  title: string
  description?: string
  sequence_idx: number
  prerequisites?: string[]
  created_at: string
}

interface Space {
  id: string
  name: string
  slug: string
}

interface UnitForm {
  title: string
  slug: string
  description: string
  space_id: string
}

export default function KnowledgeUnits() {
  const { hasPermission } = useAuth()
  const { activeConnectionId } = useKnowledge()
  const canManage = hasPermission('knowledge', 'manage')

  const [spaces, setSpaces] = useState<Space[]>([])
  const [selectedSpaceId, setSelectedSpaceId] = useState<string>('')
  const [units, setUnits] = useState<Unit[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [showModal, setShowModal] = useState(false)
  const [editingUnit, setEditingUnit] = useState<Unit | null>(null)
  const [form, setForm] = useState<UnitForm>({ title: '', slug: '', description: '', space_id: '' })
  const [saving, setSaving] = useState(false)
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null)
  const [dragIdx, setDragIdx] = useState<number | null>(null)
  const [reordering, setReordering] = useState(false)
  const dragOver = useRef<number | null>(null)

  const loadSpaces = useCallback(async () => {
    if (!activeConnectionId) return
    try {
      const data = await api.get(`/knowledge/connections/${activeConnectionId}/spaces`)
      const list: Space[] = data.spaces || data || []
      setSpaces(list)
      if (list.length > 0 && !selectedSpaceId) setSelectedSpaceId(list[0].id)
    } catch {}
  }, [activeConnectionId, selectedSpaceId])

  const loadUnits = useCallback(async () => {
    if (!activeConnectionId || !selectedSpaceId) { setLoading(false); return }
    setLoading(true)
    try {
      const data = await api.get(`/knowledge/connections/${activeConnectionId}/spaces/${selectedSpaceId}/units`)
      const list: Unit[] = data.units || data || []
      setUnits(list.sort((a, b) => a.sequence_idx - b.sequence_idx))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load units')
    }
    setLoading(false)
  }, [activeConnectionId, selectedSpaceId])

  useEffect(() => { loadSpaces() }, [loadSpaces])
  useEffect(() => { loadUnits() }, [loadUnits])

  function openCreate() {
    setEditingUnit(null)
    setForm({ title: '', slug: '', description: '', space_id: selectedSpaceId })
    setShowModal(true)
  }

  function openEdit(unit: Unit) {
    setEditingUnit(unit)
    setForm({ title: unit.title, slug: unit.slug, description: unit.description || '', space_id: unit.space_id })
    setShowModal(true)
  }

  async function handleSave() {
    setSaving(true)
    setError(null)
    try {
      const body = {
        title: form.title,
        slug: form.slug,
        description: form.description || undefined,
        space_id: selectedSpaceId,
      }
      if (editingUnit) {
        await api.patch(`/knowledge/connections/${activeConnectionId}/spaces/${selectedSpaceId}/units/${editingUnit.id}`, body)
      } else {
        await api.post(`/knowledge/connections/${activeConnectionId}/spaces/${selectedSpaceId}/units`, body)
      }
      setShowModal(false)
      await loadUnits()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Save failed')
    }
    setSaving(false)
  }

  async function handleDelete(id: string) {
    try {
      await api.delete(`/knowledge/connections/${activeConnectionId}/spaces/${selectedSpaceId}/units/${id}`)
      setConfirmDeleteId(null)
      await loadUnits()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Delete failed')
    }
  }

  // HTML5 drag-drop reorder
  function handleDragStart(idx: number) { setDragIdx(idx) }
  function handleDragOver(e: React.DragEvent, idx: number) { e.preventDefault(); dragOver.current = idx }
  async function handleDrop() {
    if (dragIdx === null || dragOver.current === null || dragIdx === dragOver.current) {
      setDragIdx(null); return
    }
    const reordered = [...units]
    const [moved] = reordered.splice(dragIdx, 1)
    reordered.splice(dragOver.current, 0, moved)
    const withSeq = reordered.map((u, i) => ({ ...u, sequence_idx: i }))
    setUnits(withSeq)
    setDragIdx(null)
    dragOver.current = null

    setReordering(true)
    try {
      await api.post(`/knowledge/connections/${activeConnectionId}/spaces/${selectedSpaceId}/units/reorder`, {
        order: withSeq.map((u) => u.id),
      })
    } catch {}
    setReordering(false)
  }

  if (!activeConnectionId) {
    return <div className="text-center py-12 text-[#667085] text-sm">Select a connection using the switcher above.</div>
  }

  return (
    <div className="space-y-4">
      {error && (
        <div className="bg-red-500/10 border border-red-500/20 rounded-lg px-4 py-3 text-sm text-red-400">{error}</div>
      )}

      <div className="flex items-center gap-3 flex-wrap">
        <select
          value={selectedSpaceId}
          onChange={(e) => setSelectedSpaceId(e.target.value)}
          className="bg-[#182230] border border-[#344054] rounded-lg px-3 py-2 text-sm text-[#D0D5DD] focus:border-[#00FFA7] focus:outline-none"
        >
          {spaces.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select>
        {reordering && <RefreshCw size={14} className="text-[#00FFA7] animate-spin" />}
        {canManage && (
          <button
            onClick={openCreate}
            className="flex items-center gap-2 px-4 py-2 bg-[#00FFA7] text-[#0C111D] rounded-lg text-sm font-medium hover:bg-[#00FFA7]/90 transition-colors ml-auto"
          >
            <Plus size={14} /> New Unit
          </button>
        )}
      </div>

      {loading ? (
        <div className="space-y-2">
          {[...Array(4)].map((_, i) => <div key={i} className="h-14 bg-[#182230] border border-[#344054] rounded-xl animate-pulse" />)}
        </div>
      ) : units.length === 0 ? (
        <div className="text-center py-12 bg-[#182230] border border-[#344054] rounded-xl text-[#667085] text-sm">
          No units yet. Units group documents into lessons or modules.
        </div>
      ) : (
        <div className="space-y-1.5">
          {units.map((unit, idx) => (
            <div
              key={unit.id}
              draggable={canManage}
              onDragStart={() => handleDragStart(idx)}
              onDragOver={(e) => handleDragOver(e, idx)}
              onDrop={handleDrop}
              onDragEnd={() => { setDragIdx(null); dragOver.current = null }}
              className={`bg-[#182230] border border-[#344054] rounded-xl px-4 py-3 flex items-center gap-3 transition-colors ${canManage ? 'cursor-grab active:cursor-grabbing' : ''} ${dragIdx === idx ? 'opacity-50' : ''}`}
            >
              {canManage && <GripVertical size={14} className="text-[#344054] shrink-0" />}
              <div className="w-6 h-6 rounded-full bg-[#00FFA7]/10 flex items-center justify-center text-xs font-mono text-[#00FFA7] shrink-0">
                {idx + 1}
              </div>
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium text-[#D0D5DD] truncate">{unit.title}</p>
                <p className="text-xs text-[#667085] font-mono">{unit.slug}</p>
              </div>
              {unit.description && (
                <p className="text-xs text-[#667085] hidden sm:block truncate max-w-[200px]">{unit.description}</p>
              )}
              {canManage && (
                <div className="flex items-center gap-1 shrink-0">
                  <button onClick={() => openEdit(unit)} className="p-1.5 rounded-lg text-[#667085] hover:text-[#D0D5DD] hover:bg-white/5 transition-colors"><Pencil size={12} /></button>
                  <button onClick={() => setConfirmDeleteId(unit.id)} className="p-1.5 rounded-lg text-[#667085] hover:text-red-400 hover:bg-red-500/10 transition-colors"><Trash2 size={12} /></button>
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Modal */}
      {showModal && (
        <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4">
          <div className="bg-[#0C111D] border border-[#344054] rounded-xl w-full max-w-md shadow-2xl">
            <div className="flex items-center justify-between px-5 py-4 border-b border-[#344054]">
              <h3 className="text-sm font-semibold text-[#F9FAFB]">{editingUnit ? 'Edit Unit' : 'New Unit'}</h3>
              <button onClick={() => setShowModal(false)} className="p-1.5 rounded-lg text-[#667085] hover:text-[#D0D5DD] hover:bg-white/5"><X size={14} /></button>
            </div>
            <div className="px-5 py-4 space-y-3">
              <div>
                <label className="block text-xs text-[#667085] mb-1">Title *</label>
                <input type="text" value={form.title} onChange={(e) => {
                  const v = e.target.value
                  setForm((p) => ({ ...p, title: v, slug: v.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') }))
                }} className="w-full bg-[#182230] border border-[#344054] rounded-lg px-3 py-2 text-sm text-[#F9FAFB] focus:border-[#00FFA7] focus:outline-none" placeholder="Module 1 — Introduction" />
              </div>
              <div>
                <label className="block text-xs text-[#667085] mb-1">Slug *</label>
                <input type="text" value={form.slug} onChange={(e) => setForm((p) => ({ ...p, slug: e.target.value }))} className="w-full bg-[#182230] border border-[#344054] rounded-lg px-3 py-2 text-sm text-[#F9FAFB] font-mono focus:border-[#00FFA7] focus:outline-none" />
              </div>
              <div>
                <label className="block text-xs text-[#667085] mb-1">Description</label>
                <textarea value={form.description} onChange={(e) => setForm((p) => ({ ...p, description: e.target.value }))} rows={2} className="w-full bg-[#182230] border border-[#344054] rounded-lg px-3 py-2 text-sm text-[#F9FAFB] focus:border-[#00FFA7] focus:outline-none resize-none" />
              </div>
              <div className="flex gap-3 pt-2">
                <button onClick={() => setShowModal(false)} className="flex-1 px-4 py-2 bg-white/5 text-[#D0D5DD] rounded-lg text-sm hover:bg-white/10 transition-colors">Cancel</button>
                <button onClick={handleSave} disabled={saving || !form.title} className="flex-1 px-4 py-2 bg-[#00FFA7] text-[#0C111D] rounded-lg text-sm font-semibold hover:bg-[#00FFA7]/90 transition-colors disabled:opacity-50">
                  {saving ? <RefreshCw size={14} className="animate-spin mx-auto" /> : (editingUnit ? 'Save' : 'Create')}
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
            <p className="text-sm font-semibold text-[#F9FAFB] mb-1">Delete Unit?</p>
            <p className="text-xs text-[#667085] mb-6">Documents assigned to this unit will remain but lose their unit assignment.</p>
            <div className="flex gap-3">
              <button onClick={() => setConfirmDeleteId(null)} className="flex-1 px-4 py-2 bg-white/5 text-[#D0D5DD] rounded-lg text-sm hover:bg-white/10 transition-colors">Cancel</button>
              <button onClick={() => handleDelete(confirmDeleteId)} className="flex-1 px-4 py-2 bg-red-500 text-white rounded-lg text-sm font-semibold hover:bg-red-600 transition-colors">Delete</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
