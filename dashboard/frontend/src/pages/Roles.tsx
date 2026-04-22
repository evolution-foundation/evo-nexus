import { useEffect, useState, useCallback } from 'react'
import { api } from '../lib/api'
import { Shield, Plus, Pencil, Trash2, X, Check, Lock, Users, Code2, FolderOpen } from 'lucide-react'
import { useTranslation } from 'react-i18next'

interface AgentAccess {
  mode: 'all' | 'none' | 'selected' | 'layer'
  agents?: string[]
  layers?: string[]
}

interface WorkspaceFolders {
  mode: 'all' | 'none' | 'selected'
  folders?: string[]
}

interface RoleData {
  id: number
  name: string
  description: string
  permissions: Record<string, string[]>
  agent_access: AgentAccess
  workspace_folders: WorkspaceFolders
  is_builtin: boolean
}

type Resources = Record<string, string[]>
type AgentLayers = Record<string, string> // agent-name → "business" | "engineering"

function countPermissions(perms: Record<string, string[]>): number {
  return Object.values(perms).reduce((sum, actions) => sum + actions.length, 0)
}

const DEFAULT_AGENT_ACCESS: AgentAccess = { mode: 'all' }
const DEFAULT_WORKSPACE_FOLDERS: WorkspaceFolders = { mode: 'all' }

export default function Roles() {
  const { t } = useTranslation()
  const [roles, setRoles] = useState<RoleData[]>([])
  const [resources, setResources] = useState<Resources>({})
  const [agentLayers, setAgentLayers] = useState<AgentLayers>({})
  const [workspaceFolderList, setWorkspaceFolderList] = useState<string[]>([])
  const [loading, setLoading] = useState(true)
  const [editingRole, setEditingRole] = useState<RoleData | null>(null)
  const [creating, setCreating] = useState(false)
  const [newName, setNewName] = useState('')
  const [newDesc, setNewDesc] = useState('')
  const [editPerms, setEditPerms] = useState<Record<string, string[]>>({})
  const [editAgentAccess, setEditAgentAccess] = useState<AgentAccess>(DEFAULT_AGENT_ACCESS)
  const [editWorkspaceFolders, setEditWorkspaceFolders] = useState<WorkspaceFolders>(DEFAULT_WORKSPACE_FOLDERS)
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)

  const fetchData = useCallback(async () => {
    try {
      const [rolesData, resourcesData, agentLayersData, wsFoldersData] = await Promise.all([
        api.get('/roles'),
        api.get('/roles/resources'),
        api.get('/roles/agent-layers'),
        api.get('/roles/workspace-folders'),
      ])
      setRoles(Array.isArray(rolesData) ? rolesData : [])
      setResources(resourcesData || {})
      setAgentLayers(agentLayersData || {})
      setWorkspaceFolderList(wsFoldersData?.folders || [])
    } catch {
      /* ignore */
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { fetchData() }, [fetchData])

  const openEdit = (role: RoleData) => {
    setEditingRole(role)
    setEditPerms(JSON.parse(JSON.stringify(role.permissions)))
    setEditAgentAccess(JSON.parse(JSON.stringify(role.agent_access || DEFAULT_AGENT_ACCESS)))
    setEditWorkspaceFolders(JSON.parse(JSON.stringify(role.workspace_folders || DEFAULT_WORKSPACE_FOLDERS)))
    setNewName(role.name)
    setNewDesc(role.description || '')
    setCreating(false)
    setError('')
  }

  const openCreate = () => {
    setEditingRole(null)
    setEditPerms({})
    setEditAgentAccess(DEFAULT_AGENT_ACCESS)
    setEditWorkspaceFolders(DEFAULT_WORKSPACE_FOLDERS)
    setNewName('')
    setNewDesc('')
    setCreating(true)
    setError('')
  }

  const closeEditor = () => {
    setEditingRole(null)
    setCreating(false)
    setError('')
  }

  const togglePerm = (resource: string, action: string) => {
    setEditPerms(prev => {
      const current = prev[resource] || []
      if (current.includes(action)) {
        const next = current.filter(a => a !== action)
        if (next.length === 0) {
          const { [resource]: _, ...rest } = prev
          return rest
        }
        return { ...prev, [resource]: next }
      }
      return { ...prev, [resource]: [...current, action] }
    })
  }

  const toggleAllResource = (resource: string) => {
    const allActions = resources[resource] || []
    const current = editPerms[resource] || []
    if (current.length === allActions.length) {
      const { [resource]: _, ...rest } = editPerms
      setEditPerms(rest)
    } else {
      setEditPerms({ ...editPerms, [resource]: [...allActions] })
    }
  }

  const handleSave = async () => {
    setError('')
    setSaving(true)
    try {
      if (creating) {
        if (!newName.trim()) { setError('Name is required'); setSaving(false); return }
        await api.post('/roles', {
          name: newName.trim(),
          description: newDesc.trim(),
          permissions: editPerms,
          agent_access: editAgentAccess,
          workspace_folders: editWorkspaceFolders,
        })
      } else if (editingRole) {
        await api.put(`/roles/${editingRole.id}`, {
          name: editingRole.is_builtin ? undefined : newName.trim(),
          description: newDesc.trim(),
          permissions: editPerms,
          agent_access: editAgentAccess,
          workspace_folders: editWorkspaceFolders,
        })
      }
      closeEditor()
      fetchData()
    } catch (ex: unknown) {
      setError(ex instanceof Error ? ex.message : 'Failed')
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async (role: RoleData) => {
    if (!confirm(`Delete role "${role.name}"? Users with this role will lose access.`)) return
    try {
      await api.delete(`/roles/${role.id}`)
      fetchData()
    } catch (ex: unknown) {
      alert(ex instanceof Error ? ex.message : 'Failed to delete')
    }
  }

  const isEditing = editingRole !== null || creating

  if (loading) return (
    <div className="space-y-4">
      <div className="skeleton h-12 rounded-xl" />
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
        {[...Array(3)].map((_, i) => <div key={i} className="skeleton h-36 rounded-xl" />)}
      </div>
    </div>
  )

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl bg-[#161b22] border border-[#21262d] flex items-center justify-center">
            <Shield size={20} className="text-[#00FFA7]" />
          </div>
          <div>
            <h1 className="text-xl font-bold text-[#e6edf3]">{t('roles.title')}</h1>
            <p className="text-sm text-[#667085]">{roles.length} role{roles.length !== 1 ? 's' : ''} configured</p>
          </div>
        </div>
        {!isEditing && (
          <button
            onClick={openCreate}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-[#00FFA7] text-[#0d1117] font-semibold text-sm hover:bg-[#00FFA7]/90 transition-colors"
          >
            <Plus size={16} /> New Role
          </button>
        )}
      </div>

      {/* Role cards */}
      {!isEditing && (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3 mb-6">
          {roles.map(role => {
            const permCount = countPermissions(role.permissions)
            return (
              <div key={role.id} className="bg-[#161b22] rounded-xl border border-[#21262d] p-5 hover:border-[#00FFA7]/30 transition-all group">
                <div className="flex items-center justify-between mb-2">
                  <div className="flex items-center gap-2">
                    <h3 className="font-semibold text-[#e6edf3]">{role.name}</h3>
                    {role.is_builtin ? (
                      <span className="inline-flex items-center gap-1 text-[10px] font-medium px-2 py-0.5 rounded-full border bg-[#21262d]/60 border-[#21262d] text-[#667085]">
                        <Lock size={8} /> built-in
                      </span>
                    ) : (
                      <span className="text-[10px] font-medium px-2 py-0.5 rounded-full border bg-[#00FFA7]/8 border-[#00FFA7]/20 text-[#00FFA7]">custom</span>
                    )}
                  </div>
                  <div className="flex items-center gap-1">
                    <button
                      onClick={() => openEdit(role)}
                      className="p-1.5 rounded-lg text-[#667085] hover:text-[#e6edf3] hover:bg-white/5 transition-colors"
                      title="Edit permissions"
                    >
                      <Pencil size={14} />
                    </button>
                    {!role.is_builtin && (
                      <button
                        onClick={() => handleDelete(role)}
                        className="p-1.5 rounded-lg text-[#667085] hover:text-red-400 hover:bg-red-500/10 transition-colors"
                        title="Delete"
                      >
                        <Trash2 size={14} />
                      </button>
                    )}
                  </div>
                </div>
                <p className="text-xs text-[#667085] mb-3">{role.description || 'No description'}</p>

                {/* Permission count badge */}
                <div className="flex items-center justify-between">
                  <div className="flex flex-wrap gap-1">
                    {Object.entries(role.permissions).slice(0, 4).map(([resource, actions]) => (
                      <span key={resource} className="text-[10px] px-2 py-0.5 rounded-full bg-[#00FFA7]/8 text-[#00FFA7] border border-[#00FFA7]/15">
                        {resource} ({actions.length})
                      </span>
                    ))}
                    {Object.keys(role.permissions).length > 4 && (
                      <span className="text-[10px] px-2 py-0.5 rounded-full bg-[#21262d] text-[#667085]">
                        +{Object.keys(role.permissions).length - 4}
                      </span>
                    )}
                  </div>
                  <span className="text-[11px] font-medium text-[#667085] bg-[#21262d] px-2 py-0.5 rounded-full shrink-0 ml-2">
                    {permCount} perm{permCount !== 1 ? 's' : ''}
                  </span>
                </div>
              </div>
            )
          })}
        </div>
      )}

      {/* Permission Editor */}
      {isEditing && (
        <div className="bg-[#161b22] rounded-xl border border-[#21262d] p-6">
          <div className="flex items-center justify-between mb-6">
            <h2 className="text-lg font-bold text-[#e6edf3]">
              {creating ? 'Create New Role' : `Edit: ${editingRole?.name}`}
            </h2>
            <button onClick={closeEditor} className="text-[#667085] hover:text-[#e6edf3] transition-colors"><X size={18} /></button>
          </div>

          {error && (
            <div className="mb-4 px-4 py-3 rounded-lg bg-red-500/10 border border-red-500/30 text-red-400 text-sm">
              {error}
            </div>
          )}

          {/* Role info */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6">
            <div>
              <label className="block text-sm font-medium text-[#e6edf3] mb-1">Name</label>
              <input
                type="text"
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                disabled={editingRole?.is_builtin}
                className="w-full px-3 py-2 rounded-lg bg-[#0d1117] border border-[#21262d] text-[#e6edf3] text-sm focus:outline-none focus:border-[#00FFA7] disabled:opacity-50 transition-colors"
                placeholder="e.g. moderator"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-[#e6edf3] mb-1">Description</label>
              <input
                type="text"
                value={newDesc}
                onChange={(e) => setNewDesc(e.target.value)}
                className="w-full px-3 py-2 rounded-lg bg-[#0d1117] border border-[#21262d] text-[#e6edf3] text-sm focus:outline-none focus:border-[#00FFA7] transition-colors"
                placeholder="Brief description"
              />
            </div>
          </div>

          {/* Permissions matrix */}
          <div className="mb-6">
            <h3 className="text-sm font-semibold text-[#e6edf3] mb-3">Permissions</h3>
            <div className="overflow-x-auto rounded-lg border border-[#21262d]">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-[#667085] text-xs uppercase tracking-wider bg-[#0d1117]">
                    <th className="text-left py-2.5 pl-4 pr-4 font-medium">Resource</th>
                    {['view', 'execute', 'manage'].map(action => (
                      <th key={action} className="text-center py-2.5 px-3 font-medium">{action}</th>
                    ))}
                    <th className="text-center py-2.5 px-3 font-medium">All</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(resources).map(([resource, availableActions]) => {
                    const currentPerms = editPerms[resource] || []
                    const allChecked = availableActions.length === currentPerms.length
                    return (
                      <tr key={resource} className="border-t border-[#21262d]/50 hover:bg-white/[0.02] transition-colors">
                        <td className="py-2.5 pl-4 pr-4 text-[#e6edf3] font-medium capitalize">{resource}</td>
                        {['view', 'execute', 'manage'].map(action => {
                          const available = availableActions.includes(action)
                          const checked = currentPerms.includes(action)
                          return (
                            <td key={action} className="text-center py-2.5 px-3">
                              {available ? (
                                <button
                                  onClick={() => togglePerm(resource, action)}
                                  className={`w-6 h-6 rounded border-2 flex items-center justify-center mx-auto transition-colors ${
                                    checked
                                      ? 'bg-[#00FFA7] border-[#00FFA7]'
                                      : 'border-[#21262d] hover:border-[#667085]'
                                  }`}
                                >
                                  {checked && <Check size={14} className="text-[#0d1117]" />}
                                </button>
                              ) : (
                                <span className="text-[#21262d]">-</span>
                              )}
                            </td>
                          )
                        })}
                        <td className="text-center py-2.5 px-3">
                          <button
                            onClick={() => toggleAllResource(resource)}
                            className={`w-6 h-6 rounded border-2 flex items-center justify-center mx-auto transition-colors ${
                              allChecked
                                ? 'bg-[#00FFA7] border-[#00FFA7]'
                                : 'border-[#21262d] hover:border-[#667085]'
                            }`}
                          >
                            {allChecked && <Check size={14} className="text-[#0d1117]" />}
                          </button>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </div>

          {/* Agent Access section */}
          <div className="mb-6">
            <h3 className="text-sm font-semibold text-[#e6edf3] mb-3">Acesso a Agentes</h3>

            {/* Mode selector */}
            <div className="flex flex-wrap gap-2 mb-4">
              {([
                { value: 'all', label: 'Todos' },
                { value: 'layer', label: 'Por camada' },
                { value: 'selected', label: 'Selecionar agentes' },
                { value: 'none', label: 'Nenhum' },
              ] as { value: AgentAccess['mode']; label: string }[]).map(({ value, label }) => (
                <button
                  key={value}
                  onClick={() => setEditAgentAccess({ mode: value })}
                  className={`px-3 py-1.5 rounded-lg border text-sm transition-colors ${
                    editAgentAccess.mode === value
                      ? 'bg-[#00FFA7]/10 border-[#00FFA7]/40 text-[#00FFA7]'
                      : 'border-[#21262d] text-[#667085] hover:text-[#e6edf3] hover:border-[#30363d]'
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>

            {/* Layer selector */}
            {editAgentAccess.mode === 'layer' && (
              <div className="flex gap-3">
                {(['business', 'engineering'] as const).map((layer) => {
                  const count = Object.values(agentLayers).filter((l) => l === layer).length
                  const active = (editAgentAccess.layers || []).includes(layer)
                  return (
                    <button
                      key={layer}
                      onClick={() => {
                        const current = editAgentAccess.layers || []
                        const next = active
                          ? current.filter((l) => l !== layer)
                          : [...current, layer]
                        setEditAgentAccess({ mode: 'layer', layers: next })
                      }}
                      className={`flex items-center gap-2 px-4 py-3 rounded-xl border transition-all ${
                        active
                          ? 'bg-[#00FFA7]/10 border-[#00FFA7]/40 text-[#00FFA7]'
                          : 'border-[#21262d] text-[#667085] hover:border-[#30363d] hover:text-[#e6edf3]'
                      }`}
                    >
                      {layer === 'business' ? <Users size={16} /> : <Code2 size={16} />}
                      <div className="text-left">
                        <div className="text-[13px] font-medium capitalize">{layer} Layer</div>
                        <div className="text-[11px] opacity-70">{count} agentes</div>
                      </div>
                      {active && <Check size={14} className="ml-1" />}
                    </button>
                  )
                })}
              </div>
            )}

            {/* Individual agent selector */}
            {editAgentAccess.mode === 'selected' && (
              <div className="rounded-lg border border-[#21262d] overflow-hidden">
                {(['business', 'engineering'] as const).map((layer) => {
                  const layerAgents = Object.entries(agentLayers)
                    .filter(([, l]) => l === layer)
                    .map(([name]) => name)
                    .sort()
                  if (layerAgents.length === 0) return null
                  const selected = editAgentAccess.agents || []
                  const allSelected = layerAgents.every((a) => selected.includes(a))
                  return (
                    <div key={layer} className="border-b border-[#21262d] last:border-b-0">
                      <div className="flex items-center justify-between px-4 py-2 bg-[#0d1117]">
                        <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-wider text-[#667085]">
                          {layer === 'business' ? <Users size={12} /> : <Code2 size={12} />}
                          {layer}
                        </div>
                        <button
                          onClick={() => {
                            const current = editAgentAccess.agents || []
                            const next = allSelected
                              ? current.filter((a) => !layerAgents.includes(a))
                              : [...new Set([...current, ...layerAgents])]
                            setEditAgentAccess({ mode: 'selected', agents: next })
                          }}
                          className="text-[10px] text-[#667085] hover:text-[#00FFA7] transition-colors"
                        >
                          {allSelected ? 'Desmarcar todos' : 'Selecionar todos'}
                        </button>
                      </div>
                      <div className="grid grid-cols-2 md:grid-cols-3 gap-1 p-2">
                        {layerAgents.map((agentName) => {
                          const isSelected = selected.includes(agentName)
                          const label = agentName
                            .split('-')
                            .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
                            .join(' ')
                          return (
                            <button
                              key={agentName}
                              onClick={() => {
                                const current = editAgentAccess.agents || []
                                const next = isSelected
                                  ? current.filter((a) => a !== agentName)
                                  : [...current, agentName]
                                setEditAgentAccess({ mode: 'selected', agents: next })
                              }}
                              className={`flex items-center gap-2 px-3 py-2 rounded-lg border text-left transition-colors ${
                                isSelected
                                  ? 'bg-[#00FFA7]/8 border-[#00FFA7]/20 text-[#e6edf3]'
                                  : 'border-[#21262d] text-[#667085] hover:border-[#30363d] hover:text-[#e6edf3]'
                              }`}
                            >
                              <div
                                className={`w-4 h-4 rounded border-2 flex items-center justify-center flex-shrink-0 transition-colors ${
                                  isSelected ? 'bg-[#00FFA7] border-[#00FFA7]' : 'border-[#21262d]'
                                }`}
                              >
                                {isSelected && <Check size={10} className="text-[#0d1117]" />}
                              </div>
                              <span className="text-[11px] truncate">{label}</span>
                            </button>
                          )
                        })}
                      </div>
                    </div>
                  )
                })}
              </div>
            )}
          </div>

          {/* Workspace Folders section */}
          <div className="mb-6">
            <h3 className="text-sm font-semibold text-[#e6edf3] mb-1 flex items-center gap-2">
              <FolderOpen size={14} className="text-[#00FFA7]" />
              Pastas do Workspace
            </h3>
            <p className="text-xs text-[#667085] mb-3">Define quais pastas de primeiro nível do workspace este role pode acessar.</p>

            {/* Mode selector */}
            <div className="flex flex-wrap gap-2 mb-4">
              {([
                { value: 'all', label: 'Todas' },
                { value: 'selected', label: 'Selecionar pastas' },
                { value: 'none', label: 'Nenhuma' },
              ] as { value: WorkspaceFolders['mode']; label: string }[]).map(({ value, label }) => (
                <button
                  key={value}
                  onClick={() => setEditWorkspaceFolders({ mode: value })}
                  className={`px-3 py-1.5 rounded-lg border text-sm transition-colors ${
                    editWorkspaceFolders.mode === value
                      ? 'bg-[#00FFA7]/10 border-[#00FFA7]/40 text-[#00FFA7]'
                      : 'border-[#21262d] text-[#667085] hover:text-[#e6edf3] hover:border-[#30363d]'
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>

            {/* Individual folder selector */}
            {editWorkspaceFolders.mode === 'selected' && (
              <div className="rounded-lg border border-[#21262d] overflow-hidden">
                <div className="flex items-center justify-between px-4 py-2 bg-[#0d1117]">
                  <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-wider text-[#667085]">
                    <FolderOpen size={12} />
                    Pastas disponíveis
                  </div>
                  <button
                    onClick={() => {
                      const allSelected = workspaceFolderList.every(f => (editWorkspaceFolders.folders || []).includes(f))
                      setEditWorkspaceFolders({
                        mode: 'selected',
                        folders: allSelected ? [] : [...workspaceFolderList],
                      })
                    }}
                    className="text-[10px] text-[#667085] hover:text-[#00FFA7] transition-colors"
                  >
                    {workspaceFolderList.every(f => (editWorkspaceFolders.folders || []).includes(f))
                      ? 'Desmarcar todas'
                      : 'Selecionar todas'}
                  </button>
                </div>
                <div className="grid grid-cols-2 md:grid-cols-3 gap-1 p-2">
                  {workspaceFolderList.map((folderName) => {
                    const isSelected = (editWorkspaceFolders.folders || []).includes(folderName)
                    const label = folderName
                      .split('-')
                      .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
                      .join(' ')
                    return (
                      <button
                        key={folderName}
                        onClick={() => {
                          const current = editWorkspaceFolders.folders || []
                          const next = isSelected
                            ? current.filter((f) => f !== folderName)
                            : [...current, folderName]
                          setEditWorkspaceFolders({ mode: 'selected', folders: next })
                        }}
                        className={`flex items-center gap-2 px-3 py-2 rounded-lg border text-left transition-colors ${
                          isSelected
                            ? 'bg-[#00FFA7]/8 border-[#00FFA7]/20 text-[#e6edf3]'
                            : 'border-[#21262d] text-[#667085] hover:border-[#30363d] hover:text-[#e6edf3]'
                        }`}
                      >
                        <div
                          className={`w-4 h-4 rounded border-2 flex items-center justify-center flex-shrink-0 transition-colors ${
                            isSelected ? 'bg-[#00FFA7] border-[#00FFA7]' : 'border-[#21262d]'
                          }`}
                        >
                          {isSelected && <Check size={10} className="text-[#0d1117]" />}
                        </div>
                        <span className="text-[11px] truncate">{label}</span>
                      </button>
                    )
                  })}
                </div>
              </div>
            )}
          </div>

          {/* Actions */}
          <div className="flex justify-end gap-3">
            <button
              onClick={closeEditor}
              className="px-4 py-2 rounded-lg text-[#667085] text-sm hover:text-[#e6edf3] hover:bg-white/5 transition-colors"
            >
              Cancel
            </button>
            <button
              onClick={handleSave}
              disabled={saving}
              className="px-4 py-2 rounded-lg bg-[#00FFA7] text-[#0d1117] font-semibold text-sm hover:bg-[#00FFA7]/90 transition-colors disabled:opacity-50"
            >
              {saving ? 'Saving...' : creating ? 'Create Role' : 'Save Changes'}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
