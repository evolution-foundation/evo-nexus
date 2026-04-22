import { useEffect, useState, useRef } from 'react'
import { Play, Square, RefreshCw, Terminal, X, Clock, RotateCcw } from 'lucide-react'
import { api } from '../lib/api'
import StatusDot from '../components/StatusDot'
import { useTranslation } from 'react-i18next'

interface Service {
  id: string
  name: string
  running: boolean
  command: string
  description: string
  detail: string
}

interface ScheduledTask {
  name: string
  schedule: string
  frequency: string
  time: string
  agent: string
  script: string
  custom?: boolean
  command?: string
}

const DAY_ORDER: Record<string, number> = {
  monday: 1, tuesday: 2, wednesday: 3, thursday: 4,
  friday: 5, saturday: 6, sunday: 7,
}

const FREQ_BUCKET: Record<string, number> = {
  daily: 0,
  weekly: 1,
  monthly: 2,
}

function bucketOf(freq: string): number {
  if (freq in FREQ_BUCKET) return FREQ_BUCKET[freq]
  if (freq.startsWith('every')) return 3
  return 4
}

function timeToMinutes(t: string): number {
  const m = /^(\d{1,2}):(\d{2})/.exec(t || '')
  if (!m) return 99 * 60
  return parseInt(m[1], 10) * 60 + parseInt(m[2], 10)
}

function dayOf(task: ScheduledTask): number {
  const match = /^([A-Za-z]+)/.exec(task.schedule || '')
  if (!match) return 99
  return DAY_ORDER[match[1].toLowerCase()] ?? 99
}

function sortTasks(tasks: ScheduledTask[]): ScheduledTask[] {
  return [...tasks].sort((a, b) => {
    const ba = bucketOf(a.frequency)
    const bb = bucketOf(b.frequency)
    if (ba !== bb) return ba - bb
    if (a.frequency === 'weekly' && b.frequency === 'weekly') {
      const da = dayOf(a)
      const db = dayOf(b)
      if (da !== db) return da - db
    }
    const ta = timeToMinutes(a.time)
    const tb = timeToMinutes(b.time)
    if (ta !== tb) return ta - tb
    return (a.name || '').localeCompare(b.name || '')
  })
}

export default function Scheduler() {
  const { t } = useTranslation()
  const [services, setServices] = useState<Service[]>([])
  const [tasks, setTasks] = useState<ScheduledTask[]>([])
  const [loading, setLoading] = useState(true)
  const [actionLoading, setActionLoading] = useState<string | null>(null)
  const [terminalService, setTerminalService] = useState<string | null>(null)
  const [terminalLines, setTerminalLines] = useState<string[]>([])
  const [terminalLoading, setTerminalLoading] = useState(false)
  const terminalRef = useRef<HTMLDivElement>(null)
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const fetchData = () => {
    Promise.all([
      api.get('/services').catch(() => []),
      api.get('/scheduler').catch(() => []),
    ]).then(([svc, sched]) => {
      setServices(Array.isArray(svc) ? svc : [])
      setTasks(Array.isArray(sched) ? sortTasks(sched) : [])
    }).finally(() => setLoading(false))
  }

  useEffect(() => { fetchData() }, [])

  const [restarting, setRestarting] = useState(false)

  const handleRestartAll = async () => {
    if (!confirm('Restart EvoNexus? Dashboard, scheduler, and terminal-server will restart.')) return
    setRestarting(true)
    try {
      await api.post('/services/restart-all')
      // Wait for service to come back
      setTimeout(() => {
        window.location.reload()
      }, 5000)
    } catch (e: any) {
      alert(e?.message || 'Failed to restart. Is the systemd service installed?')
      setRestarting(false)
    }
  }

  const handleAction = async (serviceId: string, action: 'start' | 'stop') => {
    setActionLoading(serviceId)
    try {
      await api.post(`/services/${serviceId}/${action}`)
      setTimeout(() => {
        fetchData()
        setActionLoading(null)
      }, 2000)
    } catch {
      setActionLoading(null)
    }
  }

  const openTerminal = (serviceId: string) => {
    setTerminalService(serviceId)
    setTerminalLines([])
    fetchLogs(serviceId)
    if (pollingRef.current) clearInterval(pollingRef.current)
    pollingRef.current = setInterval(() => fetchLogs(serviceId), 3000)
  }

  const closeTerminal = () => {
    setTerminalService(null)
    setTerminalLines([])
    if (pollingRef.current) {
      clearInterval(pollingRef.current)
      pollingRef.current = null
    }
  }

  const fetchLogs = async (serviceId: string) => {
    setTerminalLoading(true)
    try {
      const data = await api.get(`/services/${serviceId}/logs`)
      setTerminalLines(data?.lines || [])
      setTimeout(() => {
        if (terminalRef.current) {
          terminalRef.current.scrollTop = terminalRef.current.scrollHeight
        }
      }, 50)
    } catch {
      setTerminalLines(['Failed to fetch logs'])
    }
    setTerminalLoading(false)
  }

  useEffect(() => {
    return () => { if (pollingRef.current) clearInterval(pollingRef.current) }
  }, [])

  if (loading) {
    return (
      <div>
        <div className="mb-8">
          <h1 className="text-2xl font-bold text-[#e6edf3]">{t('scheduler.title')}</h1>
        </div>
        <div className="space-y-3">
          {[...Array(3)].map((_, i) => <div key={i} className="skeleton h-24 rounded-xl" />)}
        </div>
      </div>
    )
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-8">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl bg-[#161b22] border border-[#21262d] flex items-center justify-center">
            <Clock size={20} className="text-[#00FFA7]" />
          </div>
          <div>
            <h1 className="text-2xl font-bold text-[#e6edf3]">{t('scheduler.title')}</h1>
            <p className="text-[#667085] mt-0.5 text-sm">Background services and scheduled routines</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={handleRestartAll}
            disabled={restarting}
            className={`flex items-center gap-2 px-3 py-2 rounded-lg border transition-colors ${
              restarting
                ? 'border-[#F59E0B]/30 bg-[#F59E0B]/10 text-[#F59E0B]'
                : 'border-[#21262d] bg-[#161b22] text-[#667085] hover:text-[#F59E0B] hover:border-[#F59E0B]/30'
            }`}
          >
            <RotateCcw size={16} className={restarting ? 'animate-spin' : ''} />
            {restarting ? 'Restarting...' : 'Restart All'}
          </button>
          <button
            onClick={() => { setLoading(true); fetchData() }}
            className="flex items-center gap-2 px-3 py-2 rounded-lg border border-[#21262d] bg-[#161b22] text-[#667085] hover:text-[#00FFA7] hover:border-[#00FFA7]/30 transition-colors"
          >
            <RefreshCw size={16} /> Refresh
          </button>
        </div>
      </div>

      {/* Service Card renderer */}
      {(['service', 'channel'] as const).map((section) => {
        const sectionServices = section === 'service'
          ? services.filter(s => !(s as any).category)
          : services.filter(s => (s as any).category === 'channel')
        if (sectionServices.length === 0) return null
        return (
          <div key={section}>
            <h2 className="text-base font-semibold text-[#e6edf3] mb-3">
              {section === 'service' ? 'Background Services' : 'Channels'}
            </h2>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3 mb-10">
              {sectionServices.map((svc) => (
                <div key={svc.name} className="bg-[#161b22] border border-[#21262d] rounded-xl p-5 hover:border-[#00FFA7]/30 transition-all">
                  <div className="flex items-center justify-between mb-3">
                    <div className="flex items-center gap-2.5">
                      <StatusDot status={svc.running ? 'ok' : 'error'} />
                      <h3 className="font-semibold text-[#e6edf3] text-sm">{svc.name}</h3>
                    </div>
                    <span className={`inline-flex items-center gap-1.5 text-[11px] font-medium px-2.5 py-0.5 rounded-full ${
                      svc.running
                        ? 'bg-[#00FFA7]/10 text-[#00FFA7] border border-[#00FFA7]/20'
                        : 'bg-red-500/10 text-red-400 border border-red-500/20'
                    }`}>
                      <span className={`w-1.5 h-1.5 rounded-full ${svc.running ? 'bg-[#00FFA7] animate-pulse' : 'bg-red-400'}`} />
                      {svc.running ? 'Running' : 'Stopped'}
                    </span>
                  </div>
                  <p className="text-xs text-[#667085] mb-4">{svc.description}</p>
                  <div className="flex items-center justify-between gap-2">
                    <code className="text-[11px] text-[#e6edf3] bg-[#0d1117] border border-[#21262d] px-2 py-1 rounded font-mono truncate">{svc.command}</code>
                    <div className="flex items-center gap-2 shrink-0">
                      {svc.running && svc.id !== 'dashboard' && (
                        <button
                          onClick={() => openTerminal(svc.id)}
                          className="flex items-center gap-1 text-xs px-2.5 py-1.5 rounded-lg border border-[#21262d] bg-[#0d1117] text-[#667085] hover:text-[#00FFA7] hover:border-[#00FFA7]/30 transition-colors"
                        >
                          <Terminal size={12} /> Logs
                        </button>
                      )}
                      {svc.id !== 'dashboard' && svc.id !== 'scheduler' && (
                        <button
                          onClick={() => handleAction(svc.id, svc.running ? 'stop' : 'start')}
                          disabled={actionLoading === svc.id}
                          className={`flex items-center gap-1 text-xs px-2.5 py-1.5 rounded-lg border transition-colors ${
                            svc.running
                              ? 'bg-red-500/10 text-red-400 hover:bg-red-500/20 border-red-500/20'
                              : 'bg-[#00FFA7]/10 text-[#00FFA7] hover:bg-[#00FFA7]/20 border-[#00FFA7]/20'
                          } ${actionLoading === svc.id ? 'opacity-50' : ''}`}
                        >
                          {actionLoading === svc.id ? (
                            <RefreshCw size={12} className="animate-spin" />
                          ) : svc.running ? (
                            <><Square size={12} /> Stop</>
                          ) : (
                            <><Play size={12} /> Start</>
                          )}
                        </button>
                      )}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )
      })}

      {/* Terminal Viewer */}
      {terminalService && (
        <div className="mb-10">
          <div className="bg-[#0d1117] border border-[#21262d] rounded-xl overflow-hidden">
            <div className="flex items-center justify-between px-4 py-3 bg-black/30 border-b border-[#21262d]">
              <div className="flex items-center gap-2">
                <Terminal size={14} className="text-[#00FFA7]" />
                <span className="text-sm font-medium text-[#e6edf3]">
                  {services.find(s => s.id === terminalService)?.name || terminalService} — Logs
                </span>
                {terminalLoading && <RefreshCw size={12} className="text-[#667085] animate-spin" />}
              </div>
              <div className="flex items-center gap-2">
                <button
                  onClick={() => fetchLogs(terminalService)}
                  className="p-1.5 rounded-lg hover:bg-white/5 text-[#667085] hover:text-[#e6edf3] transition-colors"
                  title="Refresh"
                >
                  <RefreshCw size={14} />
                </button>
                <button
                  onClick={closeTerminal}
                  className="p-1.5 rounded-lg hover:bg-white/5 text-[#667085] hover:text-red-400 transition-colors"
                  title="Close"
                >
                  <X size={14} />
                </button>
              </div>
            </div>
            <div
              ref={terminalRef}
              className="p-4 font-mono text-xs leading-5 text-[#e6edf3] overflow-y-auto"
              style={{ maxHeight: '400px', minHeight: '200px' }}
            >
              {terminalLines.length > 0 ? (
                terminalLines.map((line, i) => (
                  <div key={i} className="whitespace-pre-wrap hover:bg-white/[0.03] px-1 rounded">
                    {line}
                  </div>
                ))
              ) : (
                <div className="text-[#667085] italic">No output yet. Waiting for logs...</div>
              )}
            </div>
            <div className="px-4 py-2 bg-black/20 border-t border-[#21262d] text-[10px] text-[#667085]">
              Auto-refresh every 3s — Showing last 100 lines
            </div>
          </div>
        </div>
      )}

      {/* Scheduled Tasks */}
      <div className="flex items-center gap-3 mb-3">
        <h2 className="text-base font-semibold text-[#e6edf3]">Scheduled Routines</h2>
        <span className="text-[11px] font-medium text-[#667085] bg-[#21262d] px-2 py-0.5 rounded-full">{tasks.length}</span>
      </div>
      <div className="bg-[#161b22] border border-[#21262d] rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-[#667085] text-xs uppercase tracking-wider bg-[#0d1117]/50 border-b border-[#21262d]">
              <th className="text-left p-4 font-medium">Task</th>
              <th className="text-left p-4 font-medium">Schedule</th>
              <th className="text-left p-4 font-medium">Agent</th>
              <th className="text-left p-4 font-medium">Command</th>
              <th className="text-left p-4 font-medium">Script</th>
            </tr>
          </thead>
          <tbody>
            {tasks.map((task, i) => (
              <tr key={i} className="border-t border-[#21262d]/50 hover:bg-white/[0.02] transition-colors">
                <td className="p-4 text-[#e6edf3] font-medium">
                  <div className="flex items-center gap-2">
                    {task.name}
                    {task.custom ? (
                      <span className="text-[10px] font-medium px-2 py-0.5 rounded-full border bg-[#21262d]/60 border-[#21262d] text-[#667085]">custom</span>
                    ) : (
                      <span className="text-[10px] font-medium px-2 py-0.5 rounded-full border bg-[#00FFA7]/8 border-[#00FFA7]/20 text-[#00FFA7]">core</span>
                    )}
                  </div>
                </td>
                <td className="p-4">
                  <code className="text-[11px] bg-[#0d1117] border border-[#21262d] px-2 py-1 rounded text-[#e6edf3] font-mono">{task.schedule}</code>
                </td>
                <td className="p-4">
                  {task.agent === 'system' ? (
                    <span className="inline-flex items-center gap-1 text-[11px] font-medium px-2.5 py-0.5 rounded-full bg-[#8b949e]/10 border border-[#8b949e]/20 text-[#8b949e]">
                      systematic
                    </span>
                  ) : task.agent ? (
                    <span className="inline-flex items-center gap-1 text-[11px] font-medium px-2.5 py-0.5 rounded-full bg-[#00FFA7]/8 border border-[#00FFA7]/20 text-[#00FFA7]">
                      @{task.agent}
                    </span>
                  ) : (
                    <span className="text-[#667085]">--</span>
                  )}
                </td>
                <td className="p-4">
                  {task.command ? (
                    <code className="text-[11px] bg-[#0d1117] border border-[#21262d] px-2 py-1 rounded text-[#00FFA7] font-mono">{task.command}</code>
                  ) : (
                    <span className="text-[#667085]">--</span>
                  )}
                </td>
                <td className="p-4 text-[#667085] text-xs font-mono">{task.script}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
