import { useEffect, useRef, useState } from 'react'
import { FolderKanban, X } from 'lucide-react'
import { api } from '../lib/api'

export interface CreatedProject {
  id: number
  slug: string
  title: string
  mission_id: number | null
  [key: string]: unknown
}

interface MissionOption {
  id: number
  title: string
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="mb-3.5">
      <label className="block text-[11px] font-ticket-mono uppercase tracking-wider text-[#667085] mb-1.5">
        {label}
      </label>
      {children}
    </div>
  )
}

const inputClass =
  'w-full bg-[#0C111D] border border-[#21262d] rounded-lg px-3 py-2 text-sm text-[#e6edf3] placeholder-[#667085] focus:outline-none focus:border-[#00FFA7]/50 transition-colors'

function slugify(value: string): string {
  return value
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
}

interface CreateProjectModalProps {
  onClose: () => void
  onCreated: (project: CreatedProject) => void
  // Pre-select and lock a mission — used when opened from inside a specific
  // Mission's card in Goals.tsx. Left undefined when opened from a
  // mission-agnostic entry point (e.g. the Projects overview page).
  defaultMissionId?: number
}

export default function CreateProjectModal({ onClose, onCreated, defaultMissionId }: CreateProjectModalProps) {
  const [missions, setMissions] = useState<MissionOption[]>([])
  const [missionsLoading, setMissionsLoading] = useState(defaultMissionId === undefined)
  const [missionId, setMissionId] = useState<string>(defaultMissionId ? String(defaultMissionId) : '')
  const [creatingMission, setCreatingMission] = useState(false)
  const [newMissionTitle, setNewMissionTitle] = useState('')

  const [slug, setSlug] = useState('')
  const [slugTouched, setSlugTouched] = useState(false)
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [workspaceFolderPath, setWorkspaceFolderPath] = useState('')
  const [status, setStatus] = useState('active')

  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const titleRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    titleRef.current?.focus()
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [onClose])

  useEffect(() => {
    if (defaultMissionId !== undefined) return
    let cancelled = false
    ;(async () => {
      try {
        const data = await api.get('/missions')
        if (!cancelled) setMissions((data || []).map((m: any) => ({ id: m.id, title: m.title })))
      } catch {
        // Non-fatal — the picker just stays empty; user can still create a
        // mission inline below, or leave the project unassigned.
      } finally {
        if (!cancelled) setMissionsLoading(false)
      }
    })()
    return () => { cancelled = true }
  }, [defaultMissionId])

  const handleTitleChange = (value: string) => {
    setTitle(value)
    if (!slugTouched) setSlug(slugify(value))
  }

  const handleSubmit = async () => {
    setError('')
    if (!title.trim()) { setError('Título é obrigatório'); return }
    if (!slug.trim()) { setError('Slug é obrigatório'); return }
    if (creatingMission && !newMissionTitle.trim()) { setError('Dê um título pra Missão nova'); return }

    setSubmitting(true)
    try {
      let resolvedMissionId: number | null = missionId ? Number(missionId) : null

      if (creatingMission) {
        const missionSlug = slugify(newMissionTitle)
        const mission = await api.post('/missions', { slug: missionSlug, title: newMissionTitle.trim() })
        resolvedMissionId = mission.id
      }

      const project = await api.post('/projects', {
        slug: slug.trim(),
        title: title.trim(),
        description: description.trim() || undefined,
        workspace_folder_path: workspaceFolderPath.trim() || undefined,
        mission_id: resolvedMissionId,
        status,
      })
      onCreated(project as CreatedProject)
      onClose()
    } catch (e: any) {
      setError(e?.message || 'Falha ao criar projeto')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div
      className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4"
      onClick={onClose}
      role="presentation"
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="create-project-title"
        onClick={(e) => e.stopPropagation()}
        className="dialog-enter bg-[#161b22] border border-[#21262d] rounded-xl w-full max-w-lg shadow-2xl"
      >
        <div className="flex items-center justify-between px-6 py-4 border-b border-[#21262d]">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-lg bg-[#00FFA7]/10 border border-[#00FFA7]/25 flex items-center justify-center shrink-0">
              <FolderKanban size={15} className="text-[#00FFA7]" />
            </div>
            <h3 id="create-project-title" className="font-display text-[15px] font-semibold text-white tracking-tight">
              Novo projeto
            </h3>
          </div>
          <button onClick={onClose} className="text-[#667085] hover:text-white transition-colors">
            <X size={16} />
          </button>
        </div>

        <div className="px-6 py-5">
          {error && (
            <div className="mb-3.5 px-3 py-2 bg-red-500/10 border border-red-500/20 rounded-lg text-xs text-red-400">
              {error}
            </div>
          )}

          <Field label="Título">
            <input
              ref={titleRef}
              value={title}
              onChange={(e) => handleTitleChange(e.target.value)}
              placeholder="Evo AI"
              className={inputClass}
            />
          </Field>

          <Field label="Slug (identificador único)">
            <input
              value={slug}
              onChange={(e) => { setSlug(e.target.value); setSlugTouched(true) }}
              placeholder="evo-ai"
              className={inputClass}
            />
          </Field>

          <Field label="Descrição (opcional)">
            <input
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Do que se trata este projeto"
              className={inputClass}
            />
          </Field>

          <Field label="Pasta do workspace (opcional)">
            <input
              value={workspaceFolderPath}
              onChange={(e) => setWorkspaceFolderPath(e.target.value)}
              placeholder="workspace/development/features/evo-ai"
              className={inputClass}
            />
            <p className="text-[10px] text-[#667085] mt-1.5">
              Se preenchido, essa pasta é indexada no MemPalace como uma Room
              (a Missão vira a Wing) pra busca semântica.
            </p>
          </Field>

          {defaultMissionId === undefined && (
            <Field label="Missão">
              {creatingMission ? (
                <div className="flex gap-2">
                  <input
                    value={newMissionTitle}
                    onChange={(e) => setNewMissionTitle(e.target.value)}
                    placeholder="Título da nova Missão"
                    className={inputClass}
                  />
                  <button
                    onClick={() => { setCreatingMission(false); setNewMissionTitle('') }}
                    className="px-3 text-xs text-[#667085] hover:text-white transition-colors shrink-0"
                  >
                    Cancelar
                  </button>
                </div>
              ) : (
                <div className="flex gap-2">
                  <select
                    value={missionId}
                    onChange={(e) => setMissionId(e.target.value)}
                    disabled={missionsLoading}
                    className={inputClass}
                  >
                    <option value="">— sem missão —</option>
                    {missions.map((m) => (
                      <option key={m.id} value={m.id}>{m.title}</option>
                    ))}
                  </select>
                  <button
                    onClick={() => setCreatingMission(true)}
                    className="px-3 text-xs text-[#00FFA7] hover:underline shrink-0 whitespace-nowrap"
                  >
                    + Nova
                  </button>
                </div>
              )}
              {missions.length === 0 && !missionsLoading && !creatingMission && (
                <p className="text-[10px] text-[#667085] mt-1.5">
                  Nenhuma Missão ainda — pode criar o projeto sem uma, ou clicar em "+ Nova".
                </p>
              )}
            </Field>
          )}

          <Field label="Status">
            <select value={status} onChange={(e) => setStatus(e.target.value)} className={inputClass}>
              <option value="active">Ativo</option>
              <option value="on-hold">Em espera</option>
            </select>
          </Field>
        </div>

        <div className="flex justify-end gap-2 px-6 py-4 border-t border-[#21262d]">
          <button onClick={onClose} className="px-4 py-2 text-sm text-[#667085] hover:text-white transition-colors">
            Cancelar
          </button>
          <button
            onClick={handleSubmit}
            disabled={submitting}
            className="px-4 py-2 text-sm bg-[#00FFA7] text-black font-semibold rounded-lg hover:bg-[#00FFA7]/90 transition-colors disabled:opacity-50"
          >
            {submitting ? 'Criando...' : 'Criar'}
          </button>
        </div>
      </div>
    </div>
  )
}
