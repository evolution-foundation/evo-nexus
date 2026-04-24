import { useEffect, useRef, useState, useCallback } from 'react'
import { TS_HTTP, TS_WS } from '../lib/terminal-url'

export type NotificationEvent = 'agent_awaiting' | 'agent_finished'

export interface GlobalNotification {
  id: string
  event: NotificationEvent
  sessionId: string
  agentName: string
  toolName?: string
  inputPreview?: string
  createdAt: number
  read: boolean
}

const STORAGE_KEY = 'evonexus.notifications.state'
const MAX_STORED = 50

function loadFromStorage(): GlobalNotification[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    // Keep only last MAX_STORED
    return parsed.slice(-MAX_STORED)
  } catch {
    return []
  }
}

function saveToStorage(notifications: GlobalNotification[]) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(notifications.slice(-MAX_STORED)))
  } catch {}
}

export function useGlobalNotifications() {
  const [notifications, setNotifications] = useState<GlobalNotification[]>(loadFromStorage)
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const reconnectDelayRef = useRef(1000)
  const mountedRef = useRef(true)

  const upsert = useCallback((notif: GlobalNotification) => {
    setNotifications(prev => {
      // Deduplicate by id
      const exists = prev.findIndex(n => n.id === notif.id)
      let next: GlobalNotification[]
      if (exists >= 0) {
        // Update existing (e.g. re-sent duplicate)
        next = [...prev]
        next[exists] = notif
      } else {
        next = [...prev, notif].slice(-MAX_STORED)
      }
      saveToStorage(next)
      return next
    })
  }, [])

  const dismiss = useCallback((id: string) => {
    setNotifications(prev => {
      const next = prev.filter(n => n.id !== id)
      saveToStorage(next)
      return next
    })
  }, [])

  const dismissBySession = useCallback((sessionId: string) => {
    setNotifications(prev => {
      const next = prev.filter(n => n.sessionId !== sessionId)
      saveToStorage(next)
      return next
    })
  }, [])

  const dismissAll = useCallback(() => {
    setNotifications([])
    saveToStorage([])
  }, [])

  const serverDownRef = useRef(false)

  const connect = useCallback(() => {
    if (!mountedRef.current) return
    if (wsRef.current && wsRef.current.readyState < 2) {
      // CONNECTING or OPEN — skip
      return
    }
    // If the tab is hidden, defer — no point flooding the console with
    // reconnect attempts while nobody's watching. We'll retry on visibility.
    if (typeof document !== 'undefined' && document.visibilityState === 'hidden') {
      return
    }

    // Health-probe the terminal-server BEFORE opening a WS. When the server
    // is down (e.g. user is in a workspace without terminal-server, or the
    // :32352 process crashed), the previous code spammed
    // "WebSocket connection to 'ws://localhost:32352/ws' failed" every
    // 1-30s in the console. One health check per retry cycle is cheap and
    // cuts the noise: if health fails we back off to 30s without ever
    // opening the WS.
    const schedule = (delay: number) => {
      if (!mountedRef.current) return
      reconnectTimerRef.current = setTimeout(() => {
        if (mountedRef.current) connect()
      }, delay)
    }

    fetch(`${TS_HTTP}/api/health`).then(res => {
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      serverDownRef.current = false
    }).catch(() => {
      if (!serverDownRef.current) {
        // Log once, not on every retry
        console.info(
          `[notifications] terminal-server at ${TS_HTTP} unreachable — ` +
          `notifications disabled until it comes back online`,
        )
        serverDownRef.current = true
      }
      reconnectDelayRef.current = 30000
      schedule(30000)
      throw new Error('health_down')
    }).then(() => {
      if (!mountedRef.current) return

      const ws = new WebSocket(`${TS_WS}/ws`)
      wsRef.current = ws

      ws.onopen = () => {
        reconnectDelayRef.current = 1000
        ws.send(JSON.stringify({ type: 'subscribe_global' }))

        // Seed from pending endpoint
        fetch(`${TS_HTTP}/api/notifications/pending`)
          .then(r => r.ok ? r.json() : null)
          .then(data => {
            if (!mountedRef.current || !Array.isArray(data?.notifications)) return
            for (const n of data.notifications) {
              upsert(n)
            }
          })
          .catch(() => {})
      }

      ws.onmessage = (ev) => {
        if (!mountedRef.current) return
        let msg: any
        try { msg = JSON.parse(ev.data) } catch { return }
        if (msg.type !== 'notification') return

        const notif: GlobalNotification = {
          id: msg.id || `${msg.event}-${msg.sessionId}-${msg.createdAt || Date.now()}`,
          event: msg.event,
          sessionId: msg.sessionId,
          agentName: msg.agentName || '',
          toolName: msg.toolName,
          inputPreview: msg.inputPreview,
          createdAt: msg.createdAt || Date.now(),
          read: false,
        }
        upsert(notif)
      }

      ws.onclose = () => {
        if (!mountedRef.current) return
        // Exponential backoff, cap at 30s
        const delay = Math.min(reconnectDelayRef.current, 30000)
        reconnectDelayRef.current = Math.min(delay * 2, 30000)
        schedule(delay)
      }

      ws.onerror = () => {
        ws.close()
      }
    }).catch(() => {
      // health check failed — scheduled already via the catch above
    })
  }, [upsert])

  useEffect(() => {
    mountedRef.current = true
    connect()

    // When the tab becomes visible again, retry connection if it's currently
    // down. Avoids stale disconnected state after a sleep/wake cycle.
    const onVisibility = () => {
      if (document.visibilityState === 'visible' && mountedRef.current) {
        if (!wsRef.current || wsRef.current.readyState > 1) {
          connect()
        }
      }
    }
    document.addEventListener('visibilitychange', onVisibility)

    return () => {
      mountedRef.current = false
      document.removeEventListener('visibilitychange', onVisibility)
      if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current)
      if (wsRef.current) {
        wsRef.current.onclose = null
        wsRef.current.close()
        wsRef.current = null
      }
    }
  }, [connect])

  const unreadCount = notifications.filter(n => !n.read).length

  return { notifications, unreadCount, dismiss, dismissBySession, dismissAll }
}
