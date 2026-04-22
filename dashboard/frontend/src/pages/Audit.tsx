import { useEffect, useState, useCallback } from 'react'
import { api } from '../lib/api'
import { ScrollText, ChevronLeft, ChevronRight } from 'lucide-react'
import { useTranslation } from 'react-i18next'

interface AuditEntry {
  id: string
  created_at: string
  username: string
  action: string
  resource: string
  detail: string
  ip_address: string
}

const ACTION_STYLE: Record<string, { bg: string; text: string; border: string }> = {
  login: { bg: 'rgba(0,255,167,0.10)', text: '#00FFA7', border: 'rgba(0,255,167,0.25)' },
  login_failed: { bg: 'rgba(248,113,113,0.10)', text: '#F87171', border: 'rgba(248,113,113,0.25)' },
  logout: { bg: 'rgba(251,191,36,0.10)', text: '#FBBF24', border: 'rgba(251,191,36,0.25)' },
  create: { bg: 'rgba(96,165,250,0.10)', text: '#60A5FA', border: 'rgba(96,165,250,0.25)' },
  update: { bg: 'rgba(168,85,247,0.10)', text: '#A855F7', border: 'rgba(168,85,247,0.25)' },
  delete: { bg: 'rgba(248,113,113,0.10)', text: '#F87171', border: 'rgba(248,113,113,0.25)' },
}

function getActionStyle(action: string) {
  const key = Object.keys(ACTION_STYLE).find(k => action.toLowerCase().includes(k))
  return key ? ACTION_STYLE[key] : { bg: 'rgba(102,112,133,0.15)', text: '#667085', border: 'rgba(102,112,133,0.30)' }
}

function timeAgo(dateStr: string): string {
  const now = Date.now()
  const then = new Date(dateStr).getTime()
  const diff = now - then
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return 'Just now'
  if (mins < 60) return `${mins}m ago`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.floor(hours / 24)
  if (days < 7) return `${days}d ago`
  if (days < 30) return `${Math.floor(days / 7)}w ago`
  return new Date(dateStr).toLocaleDateString()
}

const PAGE_SIZE = 50

export default function Audit() {
  const { t } = useTranslation()
  const [entries, setEntries] = useState<AuditEntry[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(true)

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  const fetchAudit = useCallback(async (p: number) => {
    setLoading(true)
    try {
      const data = await api.get(`/audit?page=${p}&per_page=${PAGE_SIZE}`)
      setEntries(data.entries || [])
      setTotal(data.total || 0)
      setPage(data.page || p)
    } catch {
      /* ignore */
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { fetchAudit(page) }, [fetchAudit, page])

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl bg-[#161b22] border border-[#21262d] flex items-center justify-center">
            <ScrollText size={20} className="text-[#00FFA7]" />
          </div>
          <div>
            <h1 className="text-xl font-bold text-[#e6edf3]">{t('audit.title')}</h1>
            <p className="text-sm text-[#667085]">{total} entries</p>
          </div>
        </div>
      </div>

      <div className="bg-[#161b22] rounded-xl border border-[#21262d] overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-[#21262d] text-[#667085]">
              <th className="text-left px-4 py-3 font-medium text-xs uppercase tracking-wider">When</th>
              <th className="text-left px-4 py-3 font-medium text-xs uppercase tracking-wider">User</th>
              <th className="text-left px-4 py-3 font-medium text-xs uppercase tracking-wider">Action</th>
              <th className="text-left px-4 py-3 font-medium text-xs uppercase tracking-wider">Resource</th>
              <th className="text-left px-4 py-3 font-medium text-xs uppercase tracking-wider">Detail</th>
              <th className="text-left px-4 py-3 font-medium text-xs uppercase tracking-wider">IP</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={6} className="px-4 py-12 text-center text-[#667085]">Loading...</td>
              </tr>
            ) : entries.length === 0 ? (
              <tr>
                <td colSpan={6} className="px-4 py-12 text-center text-[#667085]">No audit entries</td>
              </tr>
            ) : (
              entries.map((e) => {
                const style = getActionStyle(e.action)
                return (
                  <tr key={e.id} className="border-b border-[#21262d]/50 hover:bg-white/[0.02] transition-colors">
                    <td className="px-4 py-3 text-[#667085] text-xs whitespace-nowrap" title={new Date(e.created_at).toLocaleString()}>
                      {timeAgo(e.created_at)}
                    </td>
                    <td className="px-4 py-3 text-[#e6edf3] font-medium">{e.username || '--'}</td>
                    <td className="px-4 py-3">
                      <span
                        className="inline-block text-[11px] font-medium px-2.5 py-0.5 rounded-full border"
                        style={{ background: style.bg, color: style.text, borderColor: style.border }}
                      >
                        {e.action}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-[#667085]">{e.resource || '--'}</td>
                    <td className="px-4 py-3 text-[#667085] max-w-xs truncate text-xs">{e.detail || '--'}</td>
                    <td className="px-4 py-3 text-[#667085] text-xs font-mono">{e.ip_address || '--'}</td>
                  </tr>
                )
              })
            )}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-between mt-4 text-sm">
          <span className="text-[#667085]">
            Page {page} of {totalPages}
          </span>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={page <= 1}
              className="flex items-center gap-1 px-3 py-1.5 rounded-lg border border-[#21262d] text-[#e6edf3] hover:bg-white/5 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
            >
              <ChevronLeft size={16} /> Prev
            </button>
            <button
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              disabled={page >= totalPages}
              className="flex items-center gap-1 px-3 py-1.5 rounded-lg border border-[#21262d] text-[#e6edf3] hover:bg-white/5 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
            >
              Next <ChevronRight size={16} />
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
