import { useState, useEffect, useRef, useCallback } from 'react'
import { AlertCircle, RefreshCw } from 'lucide-react'

const LOAD_TIMEOUT_MS = 8000

/**
 * Persistent host for the Hermes Agent dashboard iframe.
 *
 * Mounted ONCE outside <Routes> (see App.tsx) so navigating away from /hermes
 * never unmounts the iframe — the embedded Hermes SPA keeps its loaded bundle
 * and state. When the route is not /hermes the host is hidden via display:none
 * (NOT unmounted), so reopening the tab makes zero new requests.
 *
 * The iframe is created lazily: its src is only assigned the first time the
 * host becomes visible (`hasOpened`), so a user who never opens /hermes never
 * downloads the Hermes UI.
 *
 * Availability is detected without a pre-flight fetch (which previously caused
 * the root document to be downloaded twice). Instead we rely on the iframe's
 * onLoad / onError plus a fallback timeout: if onLoad never fires within
 * LOAD_TIMEOUT_MS the proxy is assumed down (502/503) and the error state shows.
 */
export default function HermesFrameHost({ visible }: { visible: boolean }) {
  const [error, setError] = useState(false)
  const [loading, setLoading] = useState(true)
  const [hasOpened, setHasOpened] = useState(false)
  const iframeRef = useRef<HTMLIFrameElement | null>(null)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Adjust state during render (React-idiomatic — avoids a setState-in-effect):
  // mark opened the first time the host becomes visible so the iframe mounts.
  if (visible && !hasOpened) {
    setHasOpened(true)
  }

  const clearTimer = useCallback(() => {
    if (timerRef.current) {
      clearTimeout(timerRef.current)
      timerRef.current = null
    }
  }, [])

  // Arm a fallback timeout while the iframe is mounted and still loading.
  // setState here runs inside the async setTimeout callback (not synchronously
  // in the effect body), so it does not cause cascading renders. If onLoad
  // never fires within the window, treat the proxy as unavailable (502/503).
  useEffect(() => {
    if (!hasOpened || !loading || error) return
    timerRef.current = setTimeout(() => {
      setError(true)
      setLoading(false)
    }, LOAD_TIMEOUT_MS)
    return clearTimer
  }, [hasOpened, loading, error, clearTimer])

  const handleLoad = useCallback(() => {
    clearTimer()
    setLoading(false)
    setError(false) // a real load wins over a prior timeout-driven error
  }, [clearTimer])

  const handleError = useCallback(() => {
    clearTimer()
    setError(true)
    setLoading(false)
  }, [clearTimer])

  const reload = useCallback(() => {
    setError(false)
    setLoading(true) // re-arms the timeout effect
    const iframe = iframeRef.current
    if (iframe) iframe.src = '/hermes-ui/'
  }, [])

  return (
    <div
      className="h-[calc(100vh-64px)] flex-col"
      style={{ display: visible ? 'flex' : 'none' }}
      aria-hidden={!visible}
    >
      <div className="flex items-center justify-between px-4 py-2 border-b border-[#152030] bg-[#0b1018]">
        <div className="flex items-center gap-2">
          <h1 className="text-sm font-semibold text-white">Hermes Dashboard</h1>
          <span className="text-[9px] px-1.5 py-0.5 rounded bg-[#152030] text-[#5a6b7f] border border-[#1e2a3a]">
            proxy :9119
          </span>
        </div>
        <button
          onClick={reload}
          className="flex items-center gap-1.5 text-[11px] px-2.5 py-1 rounded text-[#5a6b7f] hover:text-[#8a9aae] hover:bg-[#152030] transition-colors"
        >
          <RefreshCw size={12} className={loading && !error ? 'animate-spin' : ''} />
          Reload
        </button>
      </div>

      {/* The iframe stays mounted once opened — error/loading are overlays on top,
          never a conditional that unmounts it. This means a slow-but-working Hermes
          that finishes after the timeout still fires onLoad and clears the error. */}
      <div className="relative flex-1">
        {hasOpened && (
          <iframe
            ref={iframeRef}
            id="hermes-frame"
            src="/hermes-ui/"
            className="w-full h-full border-0"
            onLoad={handleLoad}
            onError={handleError}
          />
        )}

        {loading && !error && (
          <div className="absolute inset-0 flex items-center justify-center bg-[#080c14]">
            <RefreshCw size={20} className="animate-spin text-[#00FFA7]" />
          </div>
        )}

        {error && (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-4 text-[#5a6b7f] bg-[#080c14]">
            <div className="w-16 h-16 rounded-full bg-[#1a0a0a] border border-[#3a1515] flex items-center justify-center">
              <AlertCircle size={28} className="text-[#f87171]" />
            </div>
            <div className="text-center">
              <p className="text-sm font-medium text-white mb-1">Hermes Dashboard not available</p>
              <p className="text-xs text-[#3d4f65] max-w-sm">
                The Hermes Agent dashboard is not running on port 9119.
                Make sure Hermes is installed and the container was started with Hermes support.
              </p>
            </div>
            <button
              onClick={reload}
              className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg bg-[#152030] text-[#8a9aae] hover:bg-[#1e2a3a] transition-colors border border-[#1e2a3a]"
            >
              <RefreshCw size={12} />
              Try again
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
