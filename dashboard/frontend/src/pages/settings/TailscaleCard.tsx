import { useState, useEffect } from 'react'
import { Network, Loader2, CheckCircle, AlertTriangle, ExternalLink, Unlink, Plug } from 'lucide-react'
import { api } from '../../lib/api'

interface TailscaleStatus {
  connected: boolean
  ip?: string | null
  hostname?: string | null
  tailnet?: string | null
  online?: boolean
  error?: string | null
}

const inp = "w-full px-4 py-3 rounded-lg bg-[#0f1520] border border-[#1e2a3a] text-[#e2e8f0] placeholder-[#3d4f65] text-sm transition-colors duration-200 focus:outline-none focus:border-[#00FFA7]/60 focus:ring-1 focus:ring-[#00FFA7]/20"

export default function TailscaleCard() {
  const [status, setStatus] = useState<TailscaleStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [connecting, setConnecting] = useState(false)
  const [disconnecting, setDisconnecting] = useState(false)
  const [authKey, setAuthKey] = useState('')
  const [msg, setMsg] = useState<{ type: 'ok' | 'err'; text: string } | null>(null)

  const loadStatus = () => {
    api.get('/tailscale/status')
      .then((d: TailscaleStatus) => setStatus(d))
      .catch(() => setStatus({ connected: false, error: 'Failed to reach server' }))
      .finally(() => setLoading(false))
  }

  useEffect(() => { loadStatus() }, [])

  const handleConnect = async () => {
    if (!authKey.trim()) return
    setConnecting(true)
    setMsg(null)
    try {
      await api.post('/tailscale/connect', { auth_key: authKey.trim() })
      setMsg({ type: 'ok', text: 'Connected to Tailscale network' })
      setAuthKey('')
      loadStatus()
    } catch (ex: unknown) {
      const err = ex instanceof Error ? ex.message : 'Connection failed'
      setMsg({ type: 'err', text: err })
    } finally {
      setConnecting(false)
    }
  }

  const handleDisconnect = async () => {
    setDisconnecting(true)
    setMsg(null)
    try {
      await api.post('/tailscale/disconnect')
      setMsg({ type: 'ok', text: 'Disconnected from Tailscale' })
      loadStatus()
    } catch (ex: unknown) {
      const err = ex instanceof Error ? ex.message : 'Disconnect failed'
      setMsg({ type: 'err', text: err })
    } finally {
      setDisconnecting(false)
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center py-12">
        <Loader2 size={24} className="text-[#5a6b7f] animate-spin" />
      </div>
    )
  }

  return (
    <div className="max-w-2xl">
      {/* Header */}
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-[#e6edf3]">Tailscale VPN</h1>
        <p className="text-[#667085] mt-1">
          Connect this container to your Tailscale network using an Auth Key.
        </p>
      </div>

      {/* Status card */}
      <div className="rounded-xl border border-[#152030] bg-[#0b1018] shadow-[0_4px_40px_rgba(0,0,0,0.4)] mb-4">
        <div className="px-6 py-5 border-b border-[#152030] flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className={`flex items-center justify-center w-9 h-9 rounded-xl border ${
              status?.connected
                ? 'bg-[#00FFA7]/10 border-[#00FFA7]/20'
                : 'bg-[#5a6b7f]/10 border-[#5a6b7f]/20'
            }`}>
              <Network size={16} className={status?.connected ? 'text-[#00FFA7]' : 'text-[#5a6b7f]'} />
            </div>
            <div>
              <p className="text-[14px] font-semibold text-[#e2e8f0]">
                {status?.connected ? 'Connected' : 'Disconnected'}
              </p>
              {status?.connected && status?.ip && (
                <p className="text-[11px] text-[#00FFA7]/70 font-mono">{status.ip}</p>
              )}
              {status?.error && !status?.connected && (
                <p className="text-[11px] text-[#f87171]/80">{status.error}</p>
              )}
            </div>
          </div>
          {status?.connected ? (
            <span className="flex items-center gap-1.5 px-2 py-1 rounded-full bg-[#00FFA7]/10 border border-[#00FFA7]/20 text-[10px] font-semibold uppercase tracking-wider text-[#00FFA7]">
              <span className="h-1.5 w-1.5 rounded-full bg-[#00FFA7]" />
              Active
            </span>
          ) : (
            <span className="flex items-center gap-1.5 px-2 py-1 rounded-full bg-[#5a6b7f]/10 border border-[#5a6b7f]/20 text-[10px] font-semibold uppercase tracking-wider text-[#5a6b7f]">
              <span className="h-1.5 w-1.5 rounded-full bg-[#5a6b7f]" />
              Offline
            </span>
          )}
        </div>

        {status?.connected && (
          <div className="px-6 py-4 grid grid-cols-2 gap-4">
            <div>
              <p className="text-[10px] font-semibold text-[#5a6b7f] uppercase tracking-[0.08em]">Hostname</p>
              <p className="text-[13px] text-[#e2e8f0] mt-1 font-mono">{status.hostname || '—'}</p>
            </div>
            <div>
              <p className="text-[10px] font-semibold text-[#5a6b7f] uppercase tracking-[0.08em]">Tailnet</p>
              <p className="text-[13px] text-[#e2e8f0] mt-1 font-mono">{status.tailnet || '—'}</p>
            </div>
            <div>
              <p className="text-[10px] font-semibold text-[#5a6b7f] uppercase tracking-[0.08em]">Tailscale IP</p>
              <p className="text-[13px] text-[#00FFA7] mt-1 font-mono">{status.ip || '—'}</p>
            </div>
            <div>
              <p className="text-[10px] font-semibold text-[#5a6b7f] uppercase tracking-[0.08em]">MagicDNS</p>
              <p className="text-[13px] text-[#00FFA7]/80 mt-1 font-mono">
                {status.hostname && status.tailnet
                  ? `${status.hostname}.${status.tailnet}`
                  : '—'}
              </p>
            </div>
          </div>
        )}
      </div>

      {/* Message banner */}
      {msg && (
        <div className={`mb-4 flex items-center gap-2 px-4 py-3 rounded-lg border text-sm ${
          msg.type === 'ok'
            ? 'bg-[#0a1a12] border-[#00FFA7]/20 text-[#4a9a6a]'
            : 'bg-[#1a0a0a] border-[#3a1515] text-[#f87171]'
        }`}>
          {msg.type === 'ok' ? <CheckCircle size={16} /> : <AlertTriangle size={16} />}
          {msg.text}
        </div>
      )}

      {/* Connect form */}
      {!status?.connected && (
        <div className="rounded-xl border border-[#152030] bg-[#0b1018] px-6 py-6">
          <p className="text-[14px] font-semibold text-[#e2e8f0] mb-1">Connect with Auth Key</p>
          <p className="text-[11px] text-[#5a6b7f] mb-4">
            Generate an Auth Key at{' '}
            <a
              href="https://login.tailscale.com/settings/keys"
              target="_blank"
              rel="noopener noreferrer"
              className="text-[#00FFA7] hover:underline inline-flex items-center gap-1"
            >
              console.tailscale.com → Settings → Keys
              <ExternalLink size={10} />
            </a>
            . Choose tag <code className="text-[#00FFA7]/80 bg-[#0f1520] px-1 rounded">evo-nexus</code> or any reusable key.
          </p>
          <div className="flex gap-2">
            <input
              type="password"
              value={authKey}
              onChange={(e) => setAuthKey(e.target.value)}
              className={`${inp} flex-1 font-mono`}
              placeholder="tskey-auth-[REDACTED]"
              onKeyDown={(e) => e.key === 'Enter' && handleConnect()}
              autoComplete="off"
            />
            <button
              onClick={handleConnect}
              disabled={connecting || !authKey.trim()}
              className="flex items-center gap-2 px-5 py-2 rounded-lg bg-[#00FFA7] text-[#080c14] hover:bg-[#00e69a] text-sm font-semibold transition-colors disabled:opacity-40 flex-shrink-0"
            >
              {connecting ? <Loader2 size={14} className="animate-spin" /> : <Plug size={14} />}
              {connecting ? 'Connecting…' : 'Connect'}
            </button>
          </div>
        </div>
      )}

      {/* Disconnect */}
      {status?.connected && (
        <div className="rounded-xl border border-[#3a1515] bg-[#0b1018] px-6 py-5">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-[14px] font-semibold text-[#e2e8f0]">Disconnect</p>
              <p className="text-[11px] text-[#5a6b7f] mt-0.5">
                Remove this container from your Tailscale network.
              </p>
            </div>
            <button
              onClick={handleDisconnect}
              disabled={disconnecting}
              className="flex items-center gap-2 px-4 py-2 rounded-lg border border-[#3a1515] text-[#f87171] hover:bg-[#1a0a0a] text-sm font-medium transition-colors disabled:opacity-40"
            >
              {disconnecting ? <Loader2 size={14} className="animate-spin" /> : <Unlink size={14} />}
              {disconnecting ? 'Disconnecting…' : 'Disconnect'}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
