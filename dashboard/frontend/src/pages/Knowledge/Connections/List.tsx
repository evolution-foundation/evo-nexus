import { useEffect, useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { Plus, RefreshCw, AlertTriangle, XCircle, CheckCircle, Wifi, ArrowRight } from 'lucide-react'
import { api } from '../../../lib/api'
import { useAuth } from '../../../context/AuthContext'
import { useKnowledge, type KnowledgeConnection } from '../../../context/KnowledgeContext'
import Wizard from './Wizard'

function StatusBadge({ status }: { status: KnowledgeConnection['status'] }) {
  if (status === 'ready')
    return <span className="flex items-center gap-1 text-xs px-2 py-0.5 rounded-full bg-[#00FFA7]/10 text-[#00FFA7]"><CheckCircle size={10} /> ready</span>
  if (status === 'needs_migration')
    return <span className="flex items-center gap-1 text-xs px-2 py-0.5 rounded-full bg-yellow-500/10 text-yellow-400"><AlertTriangle size={10} /> needs migration</span>
  if (status === 'error')
    return <span className="flex items-center gap-1 text-xs px-2 py-0.5 rounded-full bg-red-500/10 text-red-400"><XCircle size={10} /> error</span>
  return <span className="flex items-center gap-1 text-xs px-2 py-0.5 rounded-full bg-white/5 text-[#667085]"><Wifi size={10} /> disconnected</span>
}

export default function KnowledgeConnections() {
  const { hasPermission } = useAuth()
  const { connections, refreshConnections } = useKnowledge()
  const [loading, setLoading] = useState(true)
  const [showWizard, setShowWizard] = useState(false)
  const [migratingId, setMigratingId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const navigate = useNavigate()
  const canManage = hasPermission('knowledge', 'manage')

  const load = useCallback(async () => {
    setLoading(true)
    await refreshConnections()
    setLoading(false)
  }, [refreshConnections])

  useEffect(() => { load() }, [load])

  const handleMigrate = async (id: string) => {
    setMigratingId(id)
    setError(null)
    try {
      await api.post(`/knowledge/connections/${id}/migrate`)
      await refreshConnections()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Migration failed')
    }
    setMigratingId(null)
  }

  if (loading) {
    return (
      <div className="space-y-3">
        {[...Array(3)].map((_, i) => (
          <div key={i} className="h-20 bg-[#182230] border border-[#344054] rounded-xl animate-pulse" />
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
            onClick={() => setShowWizard(true)}
            className="flex items-center gap-2 px-4 py-2 bg-[#00FFA7] text-[#0C111D] rounded-lg text-sm font-medium hover:bg-[#00FFA7]/90 transition-colors"
          >
            <Plus size={14} /> New Connection
          </button>
        </div>
      )}

      {connections.length === 0 ? (
        <div className="text-center py-16 bg-[#182230] border border-[#344054] rounded-xl">
          <div className="w-16 h-16 rounded-2xl bg-[#00FFA7]/10 flex items-center justify-center mx-auto mb-4">
            <Wifi size={28} className="text-[#00FFA7]" />
          </div>
          <p className="text-[#F9FAFB] font-medium mb-1">No connections yet</p>
          <p className="text-[#667085] text-sm mb-6">Bring your own Postgres with pgvector to get started.</p>
          {canManage && (
            <button
              onClick={() => setShowWizard(true)}
              className="px-5 py-2 bg-[#00FFA7] text-[#0C111D] rounded-lg text-sm font-medium hover:bg-[#00FFA7]/90 transition-colors"
            >
              Add First Connection
            </button>
          )}
        </div>
      ) : (
        <div className="space-y-2">
          {connections.map((c) => (
            <div
              key={c.id}
              className="bg-[#182230] border border-[#344054] rounded-xl p-4 hover:border-[#00FFA7]/30 transition-colors"
            >
              <div className="flex items-center gap-4">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-3 flex-wrap">
                    <p className="text-sm font-semibold text-[#F9FAFB]">{c.name}</p>
                    <StatusBadge status={c.status} />
                  </div>
                  <div className="flex items-center gap-4 mt-1 flex-wrap">
                    {c.host && (
                      <span className="text-xs text-[#667085]">{c.host}{c.port ? `:${c.port}` : ''}</span>
                    )}
                    {c.database_name && (
                      <span className="text-xs text-[#667085]">/{c.database_name}</span>
                    )}
                    {c.spaces_count != null && (
                      <span className="text-xs text-[#667085]">{c.spaces_count} space{c.spaces_count !== 1 ? 's' : ''}</span>
                    )}
                    {c.chunks_count != null && (
                      <span className="text-xs text-[#667085]">{c.chunks_count.toLocaleString()} chunks</span>
                    )}
                    {c.schema_version && (
                      <span className="text-xs text-[#667085]">schema {c.schema_version}</span>
                    )}
                  </div>
                </div>

                <div className="flex items-center gap-2 shrink-0">
                  {c.status === 'needs_migration' && canManage && (
                    <button
                      onClick={() => handleMigrate(c.id)}
                      disabled={migratingId === c.id}
                      className="flex items-center gap-1 px-3 py-1.5 bg-yellow-500/10 text-yellow-400 rounded-lg text-xs font-medium hover:bg-yellow-500/20 transition-colors disabled:opacity-50"
                    >
                      {migratingId === c.id ? (
                        <><RefreshCw size={10} className="animate-spin" /> Migrating...</>
                      ) : (
                        <>Run migrations</>
                      )}
                    </button>
                  )}
                  {c.status === 'error' && canManage && (
                    <button
                      onClick={() => navigate(`/knowledge/connections/${c.id}`)}
                      className="flex items-center gap-1 px-3 py-1.5 bg-red-500/10 text-red-400 rounded-lg text-xs font-medium hover:bg-red-500/20 transition-colors"
                    >
                      Reconnect
                    </button>
                  )}
                  <button
                    onClick={() => navigate(`/knowledge/connections/${c.id}`)}
                    className="p-2 text-[#667085] hover:text-[#D0D5DD] hover:bg-white/5 rounded-lg transition-colors"
                    title="View details"
                  >
                    <ArrowRight size={14} />
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {showWizard && (
        <Wizard
          onClose={() => setShowWizard(false)}
          onCreated={async () => {
            setShowWizard(false)
            await refreshConnections()
          }}
        />
      )}
    </div>
  )
}
