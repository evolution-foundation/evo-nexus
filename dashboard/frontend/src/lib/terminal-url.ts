/**
 * Shared terminal-server URL constants.
 * Exported here so AgentChat, useGlobalNotifications, and any future consumer
 * all resolve the same base URL without duplication.
 *
 * The dashboard backend mounts an HTTP+WebSocket proxy at /terminal that
 * forwards to the local terminal-server. Going through it (rather than
 * hitting :32352 directly) wins on three fronts:
 *   1. Same-origin requests pass the dashboard's CSP `connect-src 'self'`
 *      directive — direct cross-port fetches are blocked even from localhost.
 *   2. CORS preflights are avoided (same origin).
 *   3. Browsers behind SSH tunnels, Tailscale Funnel, or any reverse proxy
 *      that only exposes the dashboard port still get terminal access.
 *
 * The vite dev server (port 5173) does not yet proxy /terminal, so in DEV
 * mode we fall back to a direct connection — that path is local-only by
 * definition.
 */
const isViteDev = import.meta.env.DEV

export const TS_HTTP = isViteDev
  ? `http://${window.location.hostname}:32352`
  : `${window.location.protocol}//${window.location.host}/terminal`

export const TS_WS = isViteDev
  ? `ws://${window.location.hostname}:32352`
  : `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}/terminal`
