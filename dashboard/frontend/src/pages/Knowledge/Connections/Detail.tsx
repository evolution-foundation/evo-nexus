import { useEffect, useState, useCallback } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { ArrowLeft, RefreshCw, Trash2, CheckCircle, XCircle, AlertTriangle, Wifi } from 'lucide-react'
import { api } from '../../../lib/api'
import { useAuth } from '../../../context/AuthContext'
import { useKnowledge } from '../../../context/KnowledgeContext'

interface ConnectionDetail {
  id: string
  name: string
  slug: string
  status: 'ready' | 'needs_migration' | 'error' | 'disconnected'
  host?: string
  port?: number
  database_name?: string
  username?: string
  ssl_mode?: string
  schema_version?: string
  pgvector_version?: string
  postgres_version?: string
  last_health_check?: string
  last_error?: string
  created_at: string
  events?: ConnectionEvent[]
}

interface ConnectionEvent {
  id: number
  event_type: string
  details: Record<string, unknown>
  created_at: string
}

export default function ConnectionDetail() {
  const { t } = useTranslation()
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const { hasPermission } = useAuth()
  const { refreshConnections } = useKnowledge()
  const [conn, setConn] = useState<ConnectionDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [actionLoading, setActionLoading] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const canManage = hasPermission('knowledge', 'manage')

  const load = useCallback(async () => {
    if (!id) return
    try {
      const data = await api.get(`/knowledge/connections/${id}`)
      setConn(data)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load connection')
    }
    setLoading(false)
  }, [id])

  useEffect(() => { load() }, [load])

  const handleHealthCheck = async () => {
    if (!id) return
    setActionLoading('health')
    setError(null)
    try {
      await api.get(`/knowledge/connections/${id}/health`)
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Health check failed')
    }
    setActionLoading(null)
  }

  const handleMigrate = async () => {
    if (!id) return
    setActionLoading('migrate')
    setError(null)
    try {
      await api.post(`/knowledge/connections/${id}/migrate`)
      await load()
      await refreshConnections()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Migration failed')
    }
    setActionLoading(null)
  }

  const handleReconnect = async () => {
    if (!id) return
    setActionLoading('reconnect')
    setError(null)
    try {
      await api.post(`/knowledge/connections/${id}/configure`)
      await load()
      await refreshConnections()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Reconnect failed')
    }
    setActionLoading(null)
  }

  const handleDelete = async () => {
    if (!id) return
    setActionLoading('delete')
    setError(null)
    try {
      await api.delete(`/knowledge/connections/${id}`)
      await refreshConnections()
      navigate('/knowledge')
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Delete failed')
      setActionLoading(null)
    }
  }

  if (loading) {
    return (
      <div className="space-y-3">
        {[...Array(4)].map((_, i) => (
          <div key={i} className="h-16 bg-[#182230] border border-[#344054] rounded-xl animate-pulse" />
        ))}
      </div>
    )
  }

  if (!conn) {
    return (
      <div className="text-center py-12 text-[#667085] text-sm">
        {error || 'Connection not found.'}
      </div>
    )
  }

  const StatusIcon = conn.status === 'ready' ? CheckCircle :
    conn.status === 'needs_migration' ? AlertTriangle :
    conn.status === 'error' ? XCircle : Wifi
  const statusColor = conn.status === 'ready' ? 'text-[#00FFA7]' :
    conn.status === 'needs_migration' ? 'text-yellow-400' :
    conn.status === 'error' ? 'text-red-400' : 'text-[#667085]'

  return (
    <div className="space-y-6">
      {/* Back + header */}
      <div className="flex items-center gap-3">
        <button
          onClick={() => navigate('/knowledge')}
          className="p-1.5 rounded-lg text-[#667085] hover:text-[#D0D5DD] hover:bg-white/5 transition-colors"
        >
          <ArrowLeft size={16} />
        </button>
        <div className="flex-1">
          <div className="flex items-center gap-2">
            <h2 className="text-lg font-semibold text-[#F9FAFB]">{conn.name}</h2>
            <StatusIcon size={14} className={statusColor} />
          </div>
          <p className="text-xs text-[#667085]">/{conn.slug}</p>
        </div>
        {canManage && (
          <div className="flex items-center gap-2">
            <button
              onClick={handleHealthCheck}
              disabled={actionLoading === 'health'}
              className="flex items-center gap-1.5 px-3 py-1.5 bg-white/5 text-[#D0D5DD] rounded-lg text-xs font-medium hover:bg-white/10 transition-colors disabled:opacity-50"
            >
              {actionLoading === 'health' ? <RefreshCw size={10} className="animate-spin" /> : <RefreshCw size={10} />}
              Health check
            </button>
            {conn.status === 'needs_migration' && (
              <button
                onClick={handleMigrate}
                disabled={actionLoading === 'migrate'}
                className="flex items-center gap-1.5 px-3 py-1.5 bg-yellow-500/10 text-yellow-400 rounded-lg text-xs font-medium hover:bg-yellow-500/20 transition-colors disabled:opacity-50"
              >
                {actionLoading === 'migrate' ? <RefreshCw size={10} className="animate-spin" /> : null}
                Run migrations
              </button>
            )}
            {(conn.status === 'error' || conn.status === 'disconnected') && (
              <button
                onClick={handleReconnect}
                disabled={actionLoading === 'reconnect'}
                className="flex items-center gap-1.5 px-3 py-1.5 bg-[#00FFA7]/10 text-[#00FFA7] rounded-lg text-xs font-medium hover:bg-[#00FFA7]/20 transition-colors disabled:opacity-50"
              >
                {actionLoading === 'reconnect' ? <RefreshCw size={10} className="animate-spin" /> : null}
                Reconnect
              </button>
            )}
          </div>
        )}
      </div>

      {error && (
        <div className="bg-red-500/10 border border-red-500/20 rounded-lg px-4 py-3 text-sm text-red-400">
          {error}
        </div>
      )}

      {/* Info grid */}
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-4">
        {[
          { label: 'Host', value: conn.host ? `${conn.host}:${conn.port ?? 5432}` : '—' },
          { label: 'Database', value: conn.database_name || '—' },
          { label: 'Username', value: conn.username || '—' },
          { label: 'SSL Mode', value: conn.ssl_mode || '—' },
          { label: 'Schema Version', value: conn.schema_version || '—' },
          { label: 'pgvector', value: conn.pgvector_version || '—' },
          { label: 'Postgres', value: conn.postgres_version || '—' },
          { label: 'Last Health Check', value: conn.last_health_check ? new Date(conn.last_health_check).toLocaleString() : '—' },
          { label: 'Created', value: new Date(conn.created_at).toLocaleString() },
        ].map(({ label, value }) => (
          <div key={label} className="bg-[#182230] border border-[#344054] rounded-xl p-4">
            <p className="text-xs text-[#667085] mb-1">{label}</p>
            <p className="text-sm text-[#D0D5DD] font-medium truncate">{value}</p>
          </div>
        ))}
      </div>

      {conn.last_error && (
        <div className="bg-red-500/5 border border-red-500/20 rounded-xl px-4 py-3">
          <p className="text-xs text-[#667085] mb-1">Last Error</p>
          <p className="text-sm text-red-400 font-mono">{conn.last_error}</p>
        </div>
      )}

      {/* Events */}
      {conn.events && conn.events.length > 0 && (
        <div>
          <h3 className="text-sm font-semibold text-[#D0D5DD] mb-3">{t('knowledge.recentEvents')}</h3>
          <div className="space-y-2">
            {conn.events.map((ev) => (
              <div key={ev.id} className="bg-[#182230] border border-[#344054] rounded-xl px-4 py-3 flex items-start gap-3">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-medium text-[#D0D5DD]">{ev.event_type}</span>
                    <span className="text-[10px] text-[#667085]">{new Date(ev.created_at).toLocaleString()}</span>
                  </div>
                  {ev.details && Object.keys(ev.details).length > 0 && (
                    <p className="text-xs text-[#667085] mt-0.5 font-mono truncate">
                      {JSON.stringify(ev.details).slice(0, 120)}
                    </p>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Danger zone */}
      {canManage && (
        <div className="border border-red-500/20 rounded-xl p-4">
          <h3 className="text-sm font-semibold text-red-400 mb-2">{t('knowledge.dangerZone')}</h3>
          <p className="text-xs text-[#667085] mb-4">
            Deleting this connection removes it from EvoNexus only.{' '}
            <strong className="text-[#D0D5DD]">Data on your Postgres remains untouched.</strong>
          </p>
          {!confirmDelete ? (
            <button
              onClick={() => setConfirmDelete(true)}
              className="flex items-center gap-1.5 px-3 py-1.5 bg-red-500/10 text-red-400 rounded-lg text-xs font-medium hover:bg-red-500/20 transition-colors"
            >
              <Trash2 size={12} /> Delete connection
            </button>
          ) : (
            <div className="flex items-center gap-3">
              <p className="text-xs text-red-400 flex-1">Are you sure? This cannot be undone.</p>
              <button
                onClick={() => setConfirmDelete(false)}
                className="px-3 py-1.5 bg-white/5 text-[#D0D5DD] rounded-lg text-xs font-medium hover:bg-white/10 transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleDelete}
                disabled={actionLoading === 'delete'}
                className="flex items-center gap-1 px-3 py-1.5 bg-red-500 text-white rounded-lg text-xs font-medium hover:bg-red-600 transition-colors disabled:opacity-50"
              >
                {actionLoading === 'delete' ? <RefreshCw size={10} className="animate-spin" /> : null}
                Delete
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
