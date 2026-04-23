import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from 'react'

// ─── Types ───────────────────────────────────────────────────────────────────

type ConfirmVariant = 'default' | 'danger'

export interface ConfirmOptions {
  title: string
  description?: string
  confirmText?: string
  cancelText?: string
  variant?: ConfirmVariant
}

type ConfirmFn = (opts: ConfirmOptions) => Promise<boolean>

interface ConfirmContextValue {
  confirm: ConfirmFn
}

// ─── Context ─────────────────────────────────────────────────────────────────

const ConfirmContext = createContext<ConfirmContextValue | null>(null)

export function useConfirm(): ConfirmFn {
  const ctx = useContext(ConfirmContext)
  if (!ctx) throw new Error('useConfirm must be used within <ConfirmProvider>')
  return ctx.confirm
}

// ─── Internal state ──────────────────────────────────────────────────────────

interface DialogState {
  open: boolean
  options: ConfirmOptions
  resolve: ((value: boolean) => void) | null
}

const INITIAL_STATE: DialogState = {
  open: false,
  options: { title: '' },
  resolve: null,
}

// ─── Dialog component ────────────────────────────────────────────────────────

function ConfirmDialogUI({
  state,
  onConfirm,
  onCancel,
}: {
  state: DialogState
  onConfirm: () => void
  onCancel: () => void
}) {
  const cancelBtnRef = useRef<HTMLButtonElement>(null)
  const confirmBtnRef = useRef<HTMLButtonElement>(null)
  const { options } = state
  const variant = options.variant ?? 'default'

  // Focus management: danger → focus Cancel (safer); default → focus Confirm
  useEffect(() => {
    if (!state.open) return
    if (variant === 'danger') {
      cancelBtnRef.current?.focus()
    } else {
      confirmBtnRef.current?.focus()
    }
  }, [state.open, variant])

  // Keyboard: Enter → confirm, Escape → cancel
  useEffect(() => {
    if (!state.open) return
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onCancel()
      if (e.key === 'Enter') onConfirm()
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [state.open, onConfirm, onCancel])

  if (!state.open) return null

  const confirmLabel = options.confirmText ?? 'Confirmar'
  const cancelLabel  = options.cancelText  ?? 'Cancelar'

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 10000,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: 'rgba(0,0,0,0.6)',
        backdropFilter: 'blur(4px)',
      }}
      onClick={onCancel}
      role="presentation"
    >
      <div
        className="dialog-enter"
        role="dialog"
        aria-modal="true"
        aria-labelledby="confirm-dialog-title"
        onClick={e => e.stopPropagation()}
        style={{
          background: '#161b22',
          border: '1px solid rgba(255,255,255,0.05)',
          borderRadius: 8,
          padding: 24,
          width: '100%',
          maxWidth: 448,
          margin: '0 16px',
          boxSizing: 'border-box',
        }}
      >
        <h2
          id="confirm-dialog-title"
          style={{
            margin: '0 0 8px',
            fontSize: 15,
            fontWeight: 600,
            color: '#e6edf3',
            lineHeight: 1.4,
          }}
        >
          {options.title}
        </h2>

        {options.description && (
          <p
            style={{
              margin: '0 0 20px',
              fontSize: 13,
              color: 'rgba(255,255,255,0.65)',
              lineHeight: 1.5,
            }}
          >
            {options.description}
          </p>
        )}

        {/* Spacer when no description */}
        {!options.description && <div style={{ height: 20 }} />}

        <div
          style={{
            display: 'flex',
            justifyContent: 'flex-end',
            gap: 8,
          }}
        >
          {/* Cancel */}
          <button
            ref={cancelBtnRef}
            onClick={onCancel}
            style={{
              padding: '7px 16px',
              borderRadius: 6,
              border: '1px solid rgba(255,255,255,0.1)',
              background: 'transparent',
              color: 'rgba(255,255,255,0.65)',
              fontSize: 13,
              fontWeight: 500,
              cursor: 'pointer',
              transition: 'background 140ms',
            }}
            onMouseEnter={e => {
              ;(e.currentTarget as HTMLButtonElement).style.background =
                'rgba(255,255,255,0.05)'
            }}
            onMouseLeave={e => {
              ;(e.currentTarget as HTMLButtonElement).style.background =
                'transparent'
            }}
          >
            {cancelLabel}
          </button>

          {/* Confirm */}
          {variant === 'danger' ? (
            <button
              ref={confirmBtnRef}
              onClick={onConfirm}
              style={{
                padding: '7px 16px',
                borderRadius: 6,
                border: '1px solid rgba(239,68,68,0.3)',
                background: 'rgba(239,68,68,0.12)',
                color: '#f87171',
                fontSize: 13,
                fontWeight: 500,
                cursor: 'pointer',
                transition: 'background 140ms',
              }}
              onMouseEnter={e => {
                ;(e.currentTarget as HTMLButtonElement).style.background =
                  'rgba(239,68,68,0.22)'
              }}
              onMouseLeave={e => {
                ;(e.currentTarget as HTMLButtonElement).style.background =
                  'rgba(239,68,68,0.12)'
              }}
            >
              {confirmLabel}
            </button>
          ) : (
            <button
              ref={confirmBtnRef}
              onClick={onConfirm}
              style={{
                padding: '7px 16px',
                borderRadius: 6,
                border: 'none',
                background: '#00FFA7',
                color: '#000',
                fontSize: 13,
                fontWeight: 600,
                cursor: 'pointer',
                transition: 'opacity 140ms',
              }}
              onMouseEnter={e => {
                ;(e.currentTarget as HTMLButtonElement).style.opacity = '0.88'
              }}
              onMouseLeave={e => {
                ;(e.currentTarget as HTMLButtonElement).style.opacity = '1'
              }}
            >
              {confirmLabel}
            </button>
          )}
        </div>
      </div>
    </div>
  )
}

// ─── Provider ────────────────────────────────────────────────────────────────

export function ConfirmProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<DialogState>(INITIAL_STATE)

  const confirm = useCallback<ConfirmFn>((options) => {
    return new Promise<boolean>((resolve) => {
      setState({ open: true, options, resolve })
    })
  }, [])

  const handleConfirm = useCallback(() => {
    state.resolve?.(true)
    setState(INITIAL_STATE)
  }, [state])

  const handleCancel = useCallback(() => {
    state.resolve?.(false)
    setState(INITIAL_STATE)
  }, [state])

  return (
    <ConfirmContext.Provider value={{ confirm }}>
      {children}
      <ConfirmDialogUI
        state={state}
        onConfirm={handleConfirm}
        onCancel={handleCancel}
      />
    </ConfirmContext.Provider>
  )
}
