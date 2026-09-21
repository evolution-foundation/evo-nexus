import { useEffect, useRef, useState } from 'react'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import { WebLinksAddon } from '@xterm/addon-web-links'
import '@xterm/xterm/css/xterm.css'

interface AgentTerminalProps {
  agent: string
  sessionId?: string
  workingDir?: string
  accentColor?: string
}

// Terminal connection URL resolution.
//
// We always go through the dashboard's /terminal proxy in production builds.
// Direct cross-port fetches (e.g. localhost:32352 from a page served at
// localhost:8080) are blocked by the dashboard's `connect-src 'self'` CSP
// directive even when the network path would work. The proxy gives us:
//   1. Same-origin requests pass CSP `'self'`.
//   2. No CORS preflight (same origin).
//   3. Works through SSH tunnels, Tailscale Funnel, or any reverse proxy
//      that only exposes the dashboard port.
//
// Escape hatch for cases where the proxy can't be used (e.g. a static
// dashboard build hosted somewhere unrelated to the terminal-server): set
// VITE_TERMINAL_URL at build time to force a specific base URL. When set,
// it overrides the proxy. Trailing slash is stripped so both
// `https://x.y/terminal` and `https://x.y/terminal/` work.
//
// In Vite's `npm run dev` mode (port 5173, no proxy mounted) we fall back
// to a direct connection to terminal-server. That path is local-only by
// definition.
const rawOverride = (import.meta.env.VITE_TERMINAL_URL as string | undefined)?.trim()
const terminalOverride = rawOverride ? rawOverride.replace(/\/+$/, '') : null

const hostname = window.location.hostname
const isViteDev = import.meta.env.DEV

// Resolve an override URL into the (httpBase, wsBase) pair the rest of the
// component expects. Accepts either http(s):// or ws(s):// — both schemes
// are mapped to their counterpart so users can paste whichever they have
// on hand. Invalid input falls back to the heuristic.
function resolveOverride(raw: string): { http: string; ws: string } | null {
  try {
    const u = new URL(raw)
    const isSecure = u.protocol === 'https:' || u.protocol === 'wss:'
    const httpProto = isSecure ? 'https:' : 'http:'
    const wsProto = isSecure ? 'wss:' : 'ws:'
    const path = u.pathname.replace(/\/+$/, '') + u.search
    return {
      http: `${httpProto}//${u.host}${path}`,
      ws: `${wsProto}//${u.host}${path}`,
    }
  } catch {
    return null
  }
}

const override = terminalOverride ? resolveOverride(terminalOverride) : null

const CC_WEB_HTTP = override
  ? override.http
  : isViteDev
    ? `http://${hostname}:32352`
    : `${window.location.origin}/terminal`

const CC_WEB_WS = override
  ? override.ws
  : isViteDev
    ? `ws://${hostname}:32352`
    : `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}/terminal`

type Status = 'connecting' | 'ready' | 'starting' | 'running' | 'error' | 'exited'

export default function AgentTerminal({ agent, sessionId: externalSessionId, workingDir, accentColor = '#00FFA7' }: AgentTerminalProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const termRef = useRef<Terminal | null>(null)
  const fitRef = useRef<FitAddon | null>(null)
  const wsRef = useRef<WebSocket | null>(null)
  const sessionIdRef = useRef<string | null>(null)
  const pingRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const [status, setStatus] = useState<Status>('connecting')
  const [errorMsg, setErrorMsg] = useState<string | null>(null)
  const [reconnectKey, setReconnectKey] = useState(0)
  // Set by the visibilitychange handler right before it forces a silent
  // background reconnect (mobile OSes kill the socket while backgrounded).
  // Distinguishes that case from a real user-initiated open/switch, so the
  // session_joined handler below knows not to yank the view back to the
  // bottom out from under someone who scrolled up to read history —
  // otherwise every backgrounding (screen lock, app switch, flaky mobile
  // network) snaps back to the tail, which reads as "can't scroll up".
  const isSilentReconnectRef = useRef(false)

  // Mount xterm once
  useEffect(() => {
    if (!containerRef.current) return
    const term = new Terminal({
      cursorBlink: true,
      fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
      fontSize: 13,
      theme: {
        background: '#0C111D',
        foreground: '#e6edf3',
        cursor: accentColor,
        cursorAccent: '#0C111D',
        black: '#484f58',
        red: '#ff7b72',
        green: '#7ee787',
        yellow: '#d29922',
        blue: '#79c0ff',
        magenta: '#d2a8ff',
        cyan: '#a5d6ff',
        white: '#b1bac4',
      },
      scrollback: 5000,
      allowProposedApi: true,
    })
    const fit = new FitAddon()
    term.loadAddon(fit)
    term.loadAddon(new WebLinksAddon())
    term.open(containerRef.current)
    try { fit.fit() } catch {}
    termRef.current = term
    fitRef.current = fit

    // Silence terminal query replies at the parser level — before
    // xterm.js gets a chance to generate them. The pty already knows
    // its own capabilities; forwarding emulator-side replies made
    // claude see them as keyboard input and print bytes like "0?1;2c"
    // or "000000" into the prompt on startup.
    //
    // Registering a handler that returns `true` marks the CSI as
    // "handled" and prevents the default sendDeviceAttributesPrimary /
    // sendDeviceAttributesSecondary / deviceStatus / reportWindow*
    // paths from firing. No reply is emitted at all.
    //
    // - final 'c'            → DA1 (\x1b[c) and DA2 (\x1b[>c)
    // - final 'n'            → DSR status (\x1b[5n) and cursor pos (\x1b[6n)
    // - final 't'            → window manipulation reports (xterm
    //                          CSI Ps ; Ps ; Ps t)
    const noReply = () => true
    term.parser.registerCsiHandler({ final: 'c' }, noReply)
    term.parser.registerCsiHandler({ final: 'c', prefix: '>' }, noReply)
    term.parser.registerCsiHandler({ final: 'n' }, noReply)
    term.parser.registerCsiHandler({ final: 'n', prefix: '?' }, noReply)
    term.parser.registerCsiHandler({ final: 't' }, noReply)

    const onResize = () => {
      try {
        fit.fit()
        if (wsRef.current?.readyState === WebSocket.OPEN) {
          wsRef.current.send(JSON.stringify({
            type: 'resize',
            cols: term.cols,
            rows: term.rows,
          }))
        }
      } catch {}
    }
    window.addEventListener('resize', onResize)

    // window 'resize' alone misses cases where the *container* changes size
    // without the window doing so — e.g. a mobile browser's address bar
    // collapsing/expanding (fires a visualViewport resize, not always a
    // window one), or this pane's flex box changing when a sidebar/drawer
    // toggles. When fit() lags behind the container's real width, xterm's
    // internal .xterm-viewport ends up wider than its box and becomes its
    // own horizontally scrollable region (the CSS-level fix in index.css
    // covers that as a backstop) — but re-fitting on every real container
    // resize is the actual root-cause fix: cols always match what's visible.
    const resizeObserver = new ResizeObserver(onResize)
    if (containerRef.current) resizeObserver.observe(containerRef.current)

    // Second line of defense: even though the parser-level handlers
    // above should prevent every known query reply, drop any onData
    // payload that still looks like a terminal auto-reply. Real user
    // keyboard input (arrows \x1b[A-D, Home/End \x1b[H/F, function
    // keys \x1b[<n>~, modified arrows \x1b[1;2A) don't match either
    // alternative.
    const AUTO_REPLY_RE = /^\x1b\[(\?|>)[0-9;]*[a-zA-Z]$|^\x1b\[[0-9;]*[nRct]$/
    term.onData((data) => {
      // TEMP DEBUG: log every onData payload so we can see what's being
      // sent to the pty on startup
      const hex = Array.from(data).map((c) => (c as unknown as string).charCodeAt(0).toString(16).padStart(2, '0')).join('')
      // eslint-disable-next-line no-console
      console.log('[xterm onData]', data.length, 'B  hex:', hex, '  match:', AUTO_REPLY_RE.test(data))
      if (AUTO_REPLY_RE.test(data)) return
      if (wsRef.current?.readyState === WebSocket.OPEN) {
        wsRef.current.send(JSON.stringify({ type: 'input', data }))
      }
    })

    return () => {
      window.removeEventListener('resize', onResize)
      resizeObserver.disconnect()
      term.dispose()
      termRef.current = null
      fitRef.current = null
    }
  }, [])

  // Touch-to-scroll.
  //
  // @xterm/xterm v6's bundled scrollable widget (a Monaco/VS Code-derived
  // custom scrollbar, not native CSS overflow) only wires mouse wheel and
  // dragging its own ~10px scrollbar thumb — there is no touch-pan support
  // anywhere in the dependency (confirmed by direct inspection of the
  // bundle; see workspace/development/debug/[C]bug-mobile-terminal-scroll-
  // 2026-09-19.md). This wires a vertical swipe on the mount container to
  // a synthetic 'wheel' event dispatched on xterm's own screen element.
  //
  // IMPORTANT: this must NOT call term.scrollLines() directly (an earlier
  // version of this fix did, and it visibly did nothing on a real device).
  // term.scrollLines() only moves xterm's scrollback offset, which is a
  // no-op whenever the terminal has no scrollback — and it has none
  // whenever the Claude Code CLI's own interactive UI is on screen, because
  // that UI runs inside the terminal's alternate screen buffer (confirmed
  // via a captured real session: `\x1b[?1049h` in the raw pty bytes, and
  // `term.buffer.active.type === 'alternate'` with 0 scrollback), which by
  // terminal convention never has scrollback (same as vim/htop in any
  // desktop terminal). A real mouse/trackpad wheel already works there —
  // not via scrollback, but because the CLI also enables real mouse
  // tracking (`\x1b[?1000h\x1b[?1002h\x1b[?1003h\x1b[?1006h`, confirmed in
  // the same capture) specifically so it can scroll its own view, and
  // xterm.js's *native* wheel handler forwards wheel input to the pty as an
  // SGR mouse report in that case (confirmed empirically: a real wheel
  // event over a no-scrollback buffer produced `\x1b[<64;...M` on
  // `term.onData`). Dispatching a synthetic 'wheel' event reuses that
  // already-correct pipeline — scrollLines when there's real scrollback,
  // mouse-report forwarding to the CLI when there isn't — instead of
  // reimplementing only the half of it that doesn't apply to the CLI's
  // normal (alt-screen) state. See workspace/development/debug/
  // [C]bug-mobile-terminal-scroll-2026-09-19.md for the full investigation.
  useEffect(() => {
    const el = containerRef.current
    if (!el) return

    // Undecided until enough movement has happened to tell a vertical swipe
    // apart from a horizontal one or a tap; null keeps both taps (focus) and
    // horizontal gestures (e.g. any future text-selection support) untouched
    // until we're sure this is a scroll.
    let isVerticalScroll: boolean | null = null
    let startX = 0
    let startY = 0
    let lastY = 0

    // Minimum total displacement before committing to a gesture direction —
    // filters out finger jitter on tap.
    const DIRECTION_THRESHOLD_PX = 10

    const onTouchStart = (e: TouchEvent) => {
      if (e.touches.length !== 1) return // ignore multi-touch (pinch, etc.)
      startX = e.touches[0].clientX
      startY = e.touches[0].clientY
      lastY = startY
      isVerticalScroll = null
    }

    const onTouchMove = (e: TouchEvent) => {
      if (e.touches.length !== 1) return
      const x = e.touches[0].clientX
      const y = e.touches[0].clientY

      if (isVerticalScroll === null) {
        const dx = Math.abs(x - startX)
        const dy = Math.abs(y - startY)
        if (dx < DIRECTION_THRESHOLD_PX && dy < DIRECTION_THRESHOLD_PX) return
        isVerticalScroll = dy > dx
      }
      if (!isVerticalScroll) return // horizontal drag — leave default behavior alone

      // Only now that this is confirmed to be a vertical scroll do we stop
      // the gesture from doing anything else (e.g. pull-to-refresh).
      e.preventDefault()

      const deltaY = lastY - y // finger moving up => scroll forward (down)
      lastY = y

      // Must target xterm's own screen element (the innermost element a
      // real pointer would be over), not the outer mount container — a
      // dispatched event only bubbles *up* through ancestors, and the
      // container is an ancestor of xterm's internal DOM, not a descendant.
      const screenEl = el.querySelector<HTMLElement>('.xterm-screen')
      if (!screenEl) return
      screenEl.dispatchEvent(new WheelEvent('wheel', {
        deltaY,
        deltaMode: WheelEvent.DOM_DELTA_PIXEL,
        bubbles: true,
        cancelable: true,
        clientX: x,
        clientY: y,
      }))
    }

    const onTouchEnd = () => {
      isVerticalScroll = null
    }

    el.addEventListener('touchstart', onTouchStart, { passive: true })
    el.addEventListener('touchmove', onTouchMove, { passive: false })
    el.addEventListener('touchend', onTouchEnd, { passive: true })
    el.addEventListener('touchcancel', onTouchEnd, { passive: true })

    return () => {
      el.removeEventListener('touchstart', onTouchStart)
      el.removeEventListener('touchmove', onTouchMove)
      el.removeEventListener('touchend', onTouchEnd)
      el.removeEventListener('touchcancel', onTouchEnd)
    }
  }, [])

  // Connect / start session for this agent
  useEffect(() => {
    let cancelled = false
    const term = termRef.current
    if (!term) return

    async function run() {
      setStatus('connecting')
      setErrorMsg(null)
      term!.clear()

      // 1) Use provided sessionId or find-or-create for this agent
      let sessionId: string
      let alreadyActive = false
      try {
        if (externalSessionId) {
          // Use the specific session provided by the parent (multi-tab mode)
          sessionId = externalSessionId
          const infoRes = await fetch(`${CC_WEB_HTTP}/api/sessions/${externalSessionId}`)
          if (infoRes.ok) {
            const info = await infoRes.json()
            alreadyActive = !!info.active
          }
        } else {
          // Default: find-or-create session for this agent
          const res = await fetch(`${CC_WEB_HTTP}/api/sessions/for-agent`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ agentName: agent, workingDir }),
          })
          if (!res.ok) throw new Error(`HTTP ${res.status}`)
          const data = await res.json()
          sessionId = data.sessionId
          alreadyActive = !!data.session?.active
        }
      } catch (e: any) {
        if (cancelled) return
        setStatus('error')
        setErrorMsg(`Could not reach terminal-server at ${CC_WEB_HTTP}. Is it running?`)
        return
      }

      if (cancelled) return
      sessionIdRef.current = sessionId

      // 2) Open WS
      const ws = new WebSocket(`${CC_WEB_WS}/ws`)
      wsRef.current = ws

      ws.onopen = () => {
        ws.send(JSON.stringify({ type: 'join_session', sessionId }))
      }

      ws.onmessage = (ev) => {
        if (cancelled) return
        let msg: any
        try { msg = JSON.parse(ev.data) } catch { return }

        switch (msg.type) {
          case 'session_joined': {
            // Replay any buffered output
            if (Array.isArray(msg.outputBuffer)) {
              msg.outputBuffer.forEach((chunk: string) => term!.write(chunk))
            }
            // Land on the latest output when opening/reopening a session,
            // matching normal terminal/chat UX. term.write() above normally
            // keeps the viewport pinned to the tail on its own, but that
            // auto-follow silently turns itself off the moment xterm thinks
            // the user has scrolled away from the bottom — which a stray
            // touch during a long buffer replay (thousands of lines, up to
            // the 5000-line scrollback) can trigger on mobile before the
            // replay even finishes. Forcing it here guarantees the session
            // always opens showing the latest content rather than wherever
            // that race left it.
            //
            // Skip it on a silent background reconnect though — that path
            // re-joins behind the user's back (mobile backgrounding kills
            // the socket; visibilitychange reconnects it), and forcing the
            // scroll there overrides wherever they'd deliberately scrolled
            // to, making the terminal feel like it can't be scrolled up.
            if (isSilentReconnectRef.current) {
              isSilentReconnectRef.current = false
            } else {
              term!.scrollToBottom()
            }
            // If an agent is already running in this session, just attach
            if (msg.active || alreadyActive) {
              setStatus('running')
              // Nudge a resize so the pty matches the current terminal size
              const fit = fitRef.current
              if (fit) {
                try { fit.fit() } catch {}
                ws.send(JSON.stringify({ type: 'resize', cols: term!.cols, rows: term!.rows }))
              }
            } else {
              // Start Claude with --agent <agent>
              // Pass cols/rows up-front so the pty is born at the right
              // size — otherwise claude's DA1 (\x1b[c) / cursor-position
              // queries during startup can echo back into the prompt as
              // literal text ("0?1;2c0?1;2c") before the first resize
              // message arrives.
              setStatus('starting')
              const fit = fitRef.current
              if (fit) {
                try { fit.fit() } catch {}
              }
              ws.send(JSON.stringify({
                type: 'start_claude',
                options: {
                  dangerouslySkipPermissions: true,
                  agent,
                  cols: term!.cols,
                  rows: term!.rows,
                },
              }))
            }
            break
          }
          case 'output':
            term!.write(msg.data)
            break
          case 'claude_started':
            setStatus('running')
            // resize after start
            {
              const fit = fitRef.current
              if (fit) {
                try { fit.fit() } catch {}
                ws.send(JSON.stringify({ type: 'resize', cols: term!.cols, rows: term!.rows }))
              }
            }
            break
          case 'exit':
            setStatus('exited')
            term!.write(`\r\n\x1b[33m[Process exited${msg.code != null ? ` with code ${msg.code}` : ''}]\x1b[0m\r\n`)
            break
          case 'error':
            setStatus('error')
            setErrorMsg(msg.message || 'Unknown error')
            term!.write(`\r\n\x1b[31m[Error] ${msg.message || ''}\x1b[0m\r\n`)
            break
          case 'pong':
            break
        }
      }

      ws.onerror = () => {
        if (cancelled) return
        setStatus('error')
        setErrorMsg('WebSocket error')
      }

      ws.onclose = () => {
        if (pingRef.current) {
          clearInterval(pingRef.current)
          pingRef.current = null
        }
      }

      // Keepalive
      pingRef.current = setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: 'ping' }))
        }
      }, 25000)
    }

    run()

    return () => {
      cancelled = true
      if (pingRef.current) {
        clearInterval(pingRef.current)
        pingRef.current = null
      }
      if (wsRef.current) {
        try { wsRef.current.close() } catch {}
        wsRef.current = null
      }
    }
  }, [agent, externalSessionId, workingDir, reconnectKey])

  // Reconnect when the tab becomes visible again if the socket died while
  // backgrounded (mobile OSes aggressively close background tabs' network
  // connections — without this, coming back to the tab leaves the terminal
  // dead with no way to interact until a manual reload).
  useEffect(() => {
    function onVisible() {
      if (document.visibilityState !== 'visible') return
      if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) return
      isSilentReconnectRef.current = true
      setReconnectKey(k => k + 1)
    }
    document.addEventListener('visibilitychange', onVisible)
    return () => document.removeEventListener('visibilitychange', onVisible)
  }, [])

  const statusDotColor =
    status === 'running'
      ? accentColor
      : status === 'starting' || status === 'connecting'
      ? '#F59E0B'
      : status === 'error'
      ? '#ef4444'
      : '#4b5563'

  const statusLabel =
    status === 'connecting' ? 'connecting…' :
    status === 'starting'   ? 'starting…' :
    status === 'running'    ? 'live' :
    status === 'error'      ? 'error' :
    status === 'exited'     ? 'exited' : ''

  return (
    <div className="relative flex h-full w-full flex-col overflow-hidden">
      {/* Status bar */}
      <div className="flex-shrink-0 h-8 flex items-center gap-3 px-4 border-b border-[#21262d] bg-[#0d1117]">
        <span
          className="inline-block h-1.5 w-1.5 rounded-full"
          style={{
            backgroundColor: statusDotColor,
            boxShadow: status === 'running' ? `0 0 6px ${accentColor}aa` : 'none',
          }}
        />
        <code className="font-mono text-[10.5px] text-[#8b949e] truncate">
          @{agent}
        </code>
        <span className="text-[#21262d]">·</span>
        <span className="text-[10px] uppercase tracking-[0.12em] text-[#667085]">
          {statusLabel}
        </span>
        {errorMsg && (
          <span
            className="ml-auto text-[10px] text-[#ef4444] truncate max-w-[50%]"
            title={errorMsg}
          >
            {errorMsg}
          </span>
        )}
      </div>

      {/* xterm — padding lives on the outer div so FitAddon measures the
          inner div's exact content area (clientHeight excludes padding only
          when the element itself has no padding). Putting py/px on the same
          div FitAddon uses as parentElement causes it to over-count rows/cols
          by the padding amount, clipping the last 1-2 lines. */}
      <div className="flex-1 min-h-0 px-4 py-3 bg-[#0C111D]">
        <div ref={containerRef} className="h-full w-full" />
      </div>
    </div>
  )
}
