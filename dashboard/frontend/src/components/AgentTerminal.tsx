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

// Terminal HUD (see workspace/development/features/terminal-hud) — only
// populated for opencode REPL sessions; claude/openclaude (pty-interactive)
// sessions never send 'hud_update', so this stays null for them and the
// semaphore/gear panel just doesn't render.
interface HudState {
  busy: boolean
  heavy: boolean
  providerId: string
  providerModel: string
  tokensPerSec: number
  totalTokens: number | null
  bestTokensPerSec: number
  shift: boolean
}

function isEioMessage(message: unknown) {
  return /\bEIO\b|read EIO|write EIO/i.test(String(message || ''))
}

export default function AgentTerminal({ agent, sessionId: externalSessionId, workingDir, accentColor = '#00FFA7' }: AgentTerminalProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const termRef = useRef<Terminal | null>(null)
  const fitRef = useRef<FitAddon | null>(null)
  const wsRef = useRef<WebSocket | null>(null)
  const sessionIdRef = useRef<string | null>(null)
  const pingRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const [status, setStatus] = useState<Status>('connecting')
  const [errorMsg, setErrorMsg] = useState<string | null>(null)
  const [hud, setHud] = useState<HudState | null>(null)
  // Mirrors `status` for the onData closure below, which is registered once
  // on mount and would otherwise only ever see the 'connecting' status from
  // that first render (React state, not a ref, doesn't update in a stale
  // closure).
  const statusRef = useRef<Status>('connecting')
  useEffect(() => {
    statusRef.current = status
  }, [status])

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

    // Second line of defense: even though the parser-level handlers
    // above should prevent every known query reply, drop any onData
    // payload that still looks like a terminal auto-reply. Real user
    // keyboard input (arrows \x1b[A-D, Home/End \x1b[H/F, function
    // keys \x1b[<n>~, modified arrows \x1b[1;2A) don't match either
    // alternative.
    const AUTO_REPLY_RE = /^\x1b\[(\?|>)[0-9;]*[a-zA-Z]$|^\x1b\[[0-9;]*[nRct]$/
    term.onData((data) => {
      if (AUTO_REPLY_RE.test(data)) return
      const ws = wsRef.current
      if (!ws || ws.readyState !== WebSocket.OPEN) return

      // The CLI process can die mid-session (provider crash, exit 1, etc.)
      // without the user noticing beyond the small status badge. Forwarding
      // raw keystrokes to a dead PTY used to just vanish — the server has
      // nothing to write them to. Treat "user typed something" as an
      // implicit restart request instead of a dead end.
      if (statusRef.current === 'exited' || statusRef.current === 'error') {
        statusRef.current = 'starting'
        setStatus('starting')
        term!.write('\r\n\x1b[33m[Restarting agent]\x1b[0m\r\n')
        ws.send(JSON.stringify({
          type: 'start_claude',
          options: {
            dangerouslySkipPermissions: true,
            agent,
            cols: term!.cols,
            rows: term!.rows,
          },
        }))
        return
      }

      ws.send(JSON.stringify({ type: 'input', data }))
    })

    return () => {
      window.removeEventListener('resize', onResize)
      term.dispose()
      termRef.current = null
      fitRef.current = null
    }
  }, [])

  // Connect / start session for this agent
  useEffect(() => {
    let cancelled = false
    const term = termRef.current
    if (!term) return

    let reconnectTimer: ReturnType<typeof setTimeout> | null = null
    let reconnectAttempts = 0
    let alreadyActive = false

    // The server keeps the pty alive when the socket drops, so a dead WS
    // only needs a rejoin — reconnect with capped exponential backoff
    // instead of leaving the terminal dead until the component remounts.
    function scheduleReconnect(sessionId: string) {
      if (cancelled || reconnectTimer) return
      const delay = Math.min(1000 * 2 ** reconnectAttempts, 15000)
      reconnectAttempts++
      setStatus('connecting')
      reconnectTimer = setTimeout(() => {
        reconnectTimer = null
        connect(sessionId, true)
      }, delay)
    }

    async function run() {
      setStatus('connecting')
      setErrorMsg(null)
      term!.clear()

      // 1) Use provided sessionId or find-or-create for this agent
      let sessionId: string
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

      connect(sessionId, false)
    }

    function connect(sessionId: string, isReconnect: boolean) {
      if (cancelled) return

      // 2) Open WS
      const ws = new WebSocket(`${CC_WEB_WS}/ws`)
      wsRef.current = ws

      const startClaude = () => {
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

      ws.onopen = () => {
        setErrorMsg(null)
        ws.send(JSON.stringify({ type: 'join_session', sessionId }))
      }

      ws.onmessage = (ev) => {
        if (cancelled) return
        let msg: any
        try { msg = JSON.parse(ev.data) } catch { return }

        switch (msg.type) {
          case 'session_joined': {
            reconnectAttempts = 0
            // On reconnect the server replays the whole buffer — clear
            // first so it doesn't duplicate what's already on screen.
            if (isReconnect) term!.clear()
            // Replay any buffered output
            if (Array.isArray(msg.outputBuffer)) {
              msg.outputBuffer.forEach((chunk: string) => term!.write(chunk))
            }
            // If an agent is already running in this session, just attach.
            // alreadyActive is only trustworthy on the first join — after a
            // reconnect the process may have died while we were away.
            if (msg.active || (!isReconnect && alreadyActive)) {
              setStatus('running')
              // Nudge a resize so the pty matches the current terminal size
              const fit = fitRef.current
              if (fit) {
                try { fit.fit() } catch {}
                ws.send(JSON.stringify({ type: 'resize', cols: term!.cols, rows: term!.rows }))
              }
            } else if (isReconnect) {
              // Process ended while we were disconnected. Restart it in-place
              // so a transient proxy/browser socket drop doesn't force a full
              // page refresh to get the agent moving again.
              term!.write('\r\n\x1b[33m[Reconnected - restarting agent]\x1b[0m\r\n')
              startClaude()
            } else {
              // Start Claude with --agent <agent>
              // Pass cols/rows up-front so the pty is born at the right
              // size — otherwise claude's DA1 (\x1b[c) / cursor-position
              // queries during startup can echo back into the prompt as
              // literal text ("0?1;2c0?1;2c") before the first resize
              // message arrives.
              startClaude()
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
            // EIO is no longer sent as a signal from the server — the
            // server now forwards signal: null for PTY EIO events. But
            // keep backward-compat: if an old server still sends 'EIO',
            // treat it as a normal exit (not an error that restarts).
            if (msg.signal === 'EIO') {
              setStatus('exited')
              term!.write(`\r\n\x1b[33m[Process exited${msg.code != null ? ` with code ${msg.code}` : ''}]\x1b[0m\r\n`)
            } else {
              setStatus('exited')
              term!.write(`\r\n\x1b[33m[Process exited${msg.code != null ? ` with code ${msg.code}` : ''}]\x1b[0m\r\n`)
            }
            break
          case 'error':
            // EIO no longer reaches here — the server converts it to a
            // normal 'exit' event. If it somehow still arrives, treat it
            // as an exit, not a retryable error.
            if (isEioMessage(msg.message)) {
              setStatus('exited')
              term!.write('\r\n\x1b[33m[Process exited]\x1b[0m\r\n')
            } else {
              setStatus('error')
              setErrorMsg(msg.message || 'Unknown error')
              term!.write(`\r\n\x1b[31m[Error] ${msg.message || ''}\x1b[0m\r\n`)
            }
            break
          case 'pong':
            break
          case 'hud_update':
            setHud({
              busy: !!msg.busy,
              heavy: !!msg.heavy,
              providerId: msg.providerId || '',
              providerModel: msg.providerModel || '',
              tokensPerSec: typeof msg.tokensPerSec === 'number' ? msg.tokensPerSec : 0,
              totalTokens: typeof msg.totalTokens === 'number' ? msg.totalTokens : null,
              bestTokensPerSec: typeof msg.bestTokensPerSec === 'number' ? msg.bestTokensPerSec : 0,
              shift: !!msg.shift,
            })
            break
        }
      }

      ws.onerror = () => {
        // onclose always follows onerror — reconnect is handled there.
      }

      ws.onclose = () => {
        if (pingRef.current) {
          clearInterval(pingRef.current)
          pingRef.current = null
        }
        if (cancelled) return
        scheduleReconnect(sessionId)
      }

      // Keepalive
      pingRef.current = setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: 'ping' }))
        }
      }, 25000)
    }

    // Returning to the tab reconnects immediately instead of waiting out
    // the backoff (browsers also throttle timers in hidden tabs, so the
    // pending reconnect may not have fired while the tab was away).
    const onVisible = () => {
      if (document.hidden || cancelled) return
      const sessionId = sessionIdRef.current
      if (!sessionId) return
      const ws = wsRef.current
      if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return
      if (reconnectTimer) {
        clearTimeout(reconnectTimer)
        reconnectTimer = null
      }
      reconnectAttempts = 0
      connect(sessionId, true)
    }
    document.addEventListener('visibilitychange', onVisible)

    run()

    return () => {
      cancelled = true
      document.removeEventListener('visibilitychange', onVisible)
      if (reconnectTimer) {
        clearTimeout(reconnectTimer)
        reconnectTimer = null
      }
      if (pingRef.current) {
        clearInterval(pingRef.current)
        pingRef.current = null
      }
      if (wsRef.current) {
        try { wsRef.current.close() } catch {}
        wsRef.current = null
      }
    }
  }, [agent, externalSessionId, workingDir])

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
        {hud && (
          // Semaphore (terminal-hud Sprint 2): 3 fixed lights, only the
          // active one lit — green=waiting for a prompt, yellow=working,
          // red=last completed turn was heavy (>20k tokens). Priority
          // red > yellow > green when both could apply (a heavy turn just
          // finished right as a new one starts).
          <div className="ml-auto flex items-center gap-1" title={
            hud.heavy ? 'contexto/tokens grande no último turno'
              : hud.busy ? 'trabalhando…'
              : 'esperando prompt'
          }>
            {(['#22c55e', '#eab308', '#ef4444'] as const).map((color, i) => {
              const activeIdx = hud.heavy ? 2 : hud.busy ? 1 : 0
              const isActive = i === activeIdx
              return (
                <span
                  key={color}
                  className="inline-block h-1.5 w-1.5 rounded-full transition-opacity duration-200"
                  style={{
                    backgroundColor: color,
                    opacity: isActive ? 1 : 0.18,
                    boxShadow: isActive ? `0 0 5px ${color}aa` : 'none',
                  }}
                />
              )
            })}
          </div>
        )}
        {errorMsg && (
          <span
            className="ml-auto text-[10px] text-[#ef4444] truncate max-w-[50%]"
            title={errorMsg}
          >
            {errorMsg}
          </span>
        )}
      </div>

      {/* xterm */}
      <div ref={containerRef} className="flex-1 min-h-0 px-4 py-3 bg-[#0C111D]" />
    </div>
  )
}
