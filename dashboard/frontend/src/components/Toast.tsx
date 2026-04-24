import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from 'react'
import { AlertCircle, AlertTriangle, CheckCircle2, Info, X } from 'lucide-react'

// ─── Types ───────────────────────────────────────────────────────────────────

type ToastVariant = 'success' | 'error' | 'warning' | 'info'

interface ToastOptions {
  duration?: number
}

interface ToastItem {
  id: string
  variant: ToastVariant
  title: string
  detail?: string
  duration: number
  exiting: boolean
}

interface ToastContextValue {
  success: (title: string, detail?: string, opts?: ToastOptions) => void
  error:   (title: string, detail?: string, opts?: ToastOptions) => void
  warning: (title: string, detail?: string, opts?: ToastOptions) => void
  info:    (title: string, detail?: string, opts?: ToastOptions) => void
}

// ─── Context ─────────────────────────────────────────────────────────────────

const ToastContext = createContext<ToastContextValue | null>(null)

export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext)
  if (!ctx) throw new Error('useToast must be used within <ToastProvider>')
  return ctx
}

// ─── Visual config per variant ───────────────────────────────────────────────

const VARIANT_CONFIG: Record<
  ToastVariant,
  { icon: React.ReactNode; borderColor: string; iconColor: string }
> = {
  success: {
    icon: <CheckCircle2 size={16} />,
    borderColor: '#00FFA7',
    iconColor: '#00FFA7',
  },
  error: {
    icon: <AlertCircle size={16} />,
    borderColor: '#ef4444',
    iconColor: '#ef4444',
  },
  warning: {
    icon: <AlertTriangle size={16} />,
    borderColor: '#f59e0b',
    iconColor: '#f59e0b',
  },
  info: {
    icon: <Info size={16} />,
    borderColor: '#3b82f6',
    iconColor: '#3b82f6',
  },
}

const MAX_TOASTS = 5
const DEFAULT_DURATION = 4000

// ─── Single Toast card ───────────────────────────────────────────────────────

function ToastCard({
  item,
  onDismiss,
}: {
  item: ToastItem
  onDismiss: (id: string) => void
}) {
  const cfg = VARIANT_CONFIG[item.variant]

  return (
    <div
      className={item.exiting ? 'toast-exit' : 'toast-enter'}
      style={{
        width: 360,
        padding: '12px 14px',
        borderRadius: 8,
        background: '#161b22',
        borderLeft: `3px solid ${cfg.borderColor}`,
        boxShadow: '0 8px 24px rgba(0,0,0,0.4)',
        display: 'flex',
        alignItems: 'flex-start',
        gap: 10,
        pointerEvents: 'all',
      }}
      role="status"
      aria-live="polite"
    >
      {/* Icon */}
      <span style={{ color: cfg.iconColor, flexShrink: 0, marginTop: 1 }}>
        {cfg.icon}
      </span>

      {/* Content */}
      <div style={{ flex: 1, minWidth: 0 }}>
        <p
          style={{
            margin: 0,
            fontSize: 13,
            fontWeight: 500,
            color: '#e6edf3',
            lineHeight: 1.4,
          }}
        >
          {item.title}
        </p>
        {item.detail && (
          <p
            style={{
              margin: '4px 0 0',
              fontSize: 12,
              color: 'rgba(255,255,255,0.55)',
              lineHeight: 1.4,
              wordBreak: 'break-word',
            }}
          >
            {item.detail}
          </p>
        )}
      </div>

      {/* Close */}
      <button
        onClick={() => onDismiss(item.id)}
        aria-label="Fechar notificação"
        style={{
          background: 'none',
          border: 'none',
          cursor: 'pointer',
          color: 'rgba(255,255,255,0.4)',
          padding: 2,
          flexShrink: 0,
          display: 'flex',
          alignItems: 'center',
          lineHeight: 1,
        }}
      >
        <X size={14} />
      </button>
    </div>
  )
}

// ─── Provider ────────────────────────────────────────────────────────────────

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([])
  // Map of toast id → timer id for auto-dismiss
  const timers = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map())

  const scheduleExit = useCallback((id: string, duration: number) => {
    const timer = setTimeout(() => {
      // Mark as exiting (triggers slide-out animation)
      setToasts(prev =>
        prev.map(t => (t.id === id ? { ...t, exiting: true } : t)),
      )
      // Remove after animation completes
      const removeTimer = setTimeout(() => {
        setToasts(prev => prev.filter(t => t.id !== id))
        timers.current.delete(id)
      }, 200)
      timers.current.set(id + '-remove', removeTimer)
    }, duration)
    timers.current.set(id, timer)
  }, [])

  const push = useCallback(
    (
      variant: ToastVariant,
      title: string,
      detail?: string,
      opts?: ToastOptions,
    ) => {
      const id = Math.random().toString(36).slice(2)
      const duration = opts?.duration ?? DEFAULT_DURATION

      setToasts(prev => {
        let next = [...prev, { id, variant, title, detail, duration, exiting: false }]
        // Evict oldest if over cap
        if (next.length > MAX_TOASTS) {
          const evicted = next.shift()!
          // Clear its timer
          const t = timers.current.get(evicted.id)
          if (t) { clearTimeout(t); timers.current.delete(evicted.id) }
        }
        return next
      })

      scheduleExit(id, duration)
    },
    [scheduleExit],
  )

  const dismiss = useCallback((id: string) => {
    // Clear auto-dismiss timer
    const t = timers.current.get(id)
    if (t) { clearTimeout(t); timers.current.delete(id) }

    setToasts(prev =>
      prev.map(toast => (toast.id === id ? { ...toast, exiting: true } : toast)),
    )
    const removeTimer = setTimeout(() => {
      setToasts(prev => prev.filter(t => t.id !== id))
    }, 200)
    timers.current.set(id + '-remove', removeTimer)
  }, [])

  // Cleanup all timers on unmount
  useEffect(() => {
    const map = timers.current
    return () => {
      map.forEach(t => clearTimeout(t))
      map.clear()
    }
  }, [])

  const value: ToastContextValue = {
    success: (title, detail, opts) => push('success', title, detail, opts),
    error:   (title, detail, opts) => push('error', title, detail, opts),
    warning: (title, detail, opts) => push('warning', title, detail, opts),
    info:    (title, detail, opts) => push('info', title, detail, opts),
  }

  return (
    <ToastContext.Provider value={value}>
      {children}
      {/* Container — fixed bottom-right, pointer-events none so it doesn't block clicks */}
      <div
        style={{
          position: 'fixed',
          bottom: 24,
          right: 24,
          zIndex: 9999,
          display: 'flex',
          flexDirection: 'column-reverse',
          gap: 8,
          pointerEvents: 'none',
        }}
        aria-label="Notificações"
      >
        {toasts.map(item => (
          <ToastCard key={item.id} item={item} onDismiss={dismiss} />
        ))}
      </div>
    </ToastContext.Provider>
  )
}
