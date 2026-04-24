/**
 * OpenRouter Smart Router — free-first model cascade proxy.
 *
 * Local HTTP server that speaks the OpenAI-compatible /v1/chat/completions
 * API and forwards to OpenRouter with automatic retry across a configurable
 * free → paid model cascade. Per-model cooldown on 429/503, plus a global
 * circuit breaker to prevent request amplification during upstream outages.
 *
 * Model list is loaded from config/smart-router.json (gitignored; falls back
 * to config/smart-router.example.json). Everything except the API key is
 * configurable there.
 *
 * Usage:
 *   const { startSmartRouter, stopSmartRouter } = require('./openrouter-smart-router');
 *   const server = await startSmartRouter({ apiKey: 'sk-or-...' });
 *   // later …
 *   stopSmartRouter(server);
 */

const http = require('http');
const https = require('https');
const fs = require('fs');
const path = require('path');
const { URL } = require('url');

const WORKSPACE_ROOT = path.resolve(__dirname, '..', '..', '..', '..');
const CONFIG_PATH = path.join(WORKSPACE_ROOT, 'config', 'smart-router.json');
const CONFIG_EXAMPLE_PATH = path.join(WORKSPACE_ROOT, 'config', 'smart-router.example.json');
const OPENROUTER_BASE = 'https://openrouter.ai';

const DEFAULTS = {
  enabled: true,
  port: 4891,
  host: '127.0.0.1',
  cooldown_ms: 60_000,
  upstream_timeout_ms: 60_000,
  breaker: {
    enabled: true,
    failure_threshold: 20,
    window_ms: 10_000,
    reset_ms: 30_000,
  },
  free_models: [],
  paid_models: [],
};

function loadConfig() {
  const tryRead = (p) => {
    try {
      if (!fs.existsSync(p)) return null;
      return JSON.parse(fs.readFileSync(p, 'utf8'));
    } catch (err) {
      console.warn(`[smart-router] Failed to read ${p}: ${err.message}`);
      return null;
    }
  };

  const raw = tryRead(CONFIG_PATH) || tryRead(CONFIG_EXAMPLE_PATH) || {};
  const breaker = { ...DEFAULTS.breaker, ...(raw.breaker || {}) };
  return { ...DEFAULTS, ...raw, breaker };
}

// ── Circuit breaker ──────────────────────────────────────────────
// Tracks upstream failures in a sliding window. When failures exceed the
// threshold, the router opens (503 fast-fail) for reset_ms to prevent
// request amplification during OpenRouter outages.
function createBreaker(cfg) {
  let failureTimestamps = [];
  let openedAt = 0;

  return {
    recordFailure() {
      if (!cfg.enabled) return;
      const now = Date.now();
      failureTimestamps.push(now);
      failureTimestamps = failureTimestamps.filter((t) => now - t < cfg.window_ms);
      if (failureTimestamps.length >= cfg.failure_threshold && openedAt === 0) {
        openedAt = now;
        console.warn(
          `[smart-router] 🚨 Circuit breaker OPEN — ${failureTimestamps.length} failures in ${cfg.window_ms}ms. Fast-failing for ${cfg.reset_ms}ms.`,
        );
      }
    },
    recordSuccess() {
      if (!cfg.enabled) return;
      if (openedAt !== 0) {
        console.log('[smart-router] ✅ Circuit breaker CLOSED — upstream recovered.');
        openedAt = 0;
        failureTimestamps = [];
      }
    },
    isOpen() {
      if (!cfg.enabled || openedAt === 0) return false;
      if (Date.now() - openedAt > cfg.reset_ms) {
        // Half-open: let one request through
        openedAt = 0;
        failureTimestamps = [];
        return false;
      }
      return true;
    },
  };
}

// ── Per-model cooldown tracker ───────────────────────────────────
function createCooldownTracker(cooldownMs) {
  const hits = new Map();
  return {
    isAvailable(model) {
      const last = hits.get(model);
      if (!last) return true;
      if (Date.now() - last > cooldownMs) {
        hits.delete(model);
        return true;
      }
      return false;
    },
    mark(model) {
      hits.set(model, Date.now());
      console.log(`[smart-router] ⚠ ${model} cooling down for ${cooldownMs / 1000}s`);
    },
    snapshot() {
      return Object.fromEntries(
        [...hits.entries()].map(([m, ts]) => [
          m,
          {
            since: new Date(ts).toISOString(),
            remaining_s: Math.max(0, cooldownMs - (Date.now() - ts)) / 1000,
          },
        ]),
      );
    },
  };
}

// ── Upstream call ────────────────────────────────────────────────
function proxyToOpenRouter(apiKey, model, bodyBuffer, timeoutMs) {
  return new Promise((resolve) => {
    const url = new URL('/api/v1/chat/completions', OPENROUTER_BASE);

    let parsed;
    try {
      parsed = JSON.parse(bodyBuffer.toString('utf8'));
    } catch {
      resolve({ status: 400, headers: {}, body: Buffer.from('{"error":"invalid JSON body"}') });
      return;
    }
    parsed.model = model;
    const payload = Buffer.from(JSON.stringify(parsed), 'utf8');
    const isStreaming = parsed.stream === true;

    const headers = {
      'Content-Type': 'application/json',
      'Content-Length': payload.length,
      'Authorization': `Bearer ${apiKey}`,
      'HTTP-Referer': 'https://evonexus.local',
      'X-Title': 'EvoNexus Smart Router',
    };

    const req = https.request(url, { method: 'POST', headers }, (res) => {
      if (isStreaming && res.statusCode >= 200 && res.statusCode < 300) {
        // Streaming path: hand the stream back without any readtime timeout.
        // The upstream/downstream socket will be closed if the client disconnects.
        resolve({ status: res.statusCode, headers: res.headers, stream: res, model });
      } else {
        const chunks = [];
        res.on('data', (chunk) => chunks.push(chunk));
        res.on('end', () => {
          resolve({
            status: res.statusCode,
            headers: res.headers,
            body: Buffer.concat(chunks),
            model,
          });
        });
      }
    });

    // Only timeout the connection/first-byte phase for non-streaming requests.
    // Streaming responses legitimately take many minutes; a readtime timeout
    // was the bug in the original PR (30s cut off agent responses mid-stream).
    if (!isStreaming) {
      req.setTimeout(timeoutMs, () => {
        req.destroy(new Error(`upstream timeout after ${timeoutMs}ms`));
      });
    }

    req.on('error', (err) => {
      resolve({
        status: 502,
        headers: {},
        body: Buffer.from(JSON.stringify({ error: `upstream error: ${err.message}` })),
        model,
      });
    });

    req.write(payload);
    req.end();
  });
}

// ── Request handler ──────────────────────────────────────────────
function makeHandler({ apiKey, config, cooldown, breaker }) {
  const ALL_MODELS = [...config.free_models, ...config.paid_models];

  return async function handleChatCompletions(bodyBuffer, res) {
    if (breaker.isOpen()) {
      res.writeHead(503, { 'Content-Type': 'application/json' });
      res.end(
        JSON.stringify({
          error: {
            message: 'Smart Router circuit breaker open — upstream degraded. Retry shortly.',
            type: 'smart_router_breaker_open',
          },
        }),
      );
      return;
    }

    let available = ALL_MODELS.filter((m) => cooldown.isAvailable(m));
    if (available.length === 0) {
      console.log('[smart-router] All models cooling down — forcing retry on free tier');
      available = config.free_models.slice();
    }

    const freeAvail = available.filter((m) => m.includes(':free'));
    const paidAvail = available.filter((m) => !m.includes(':free'));
    const ordered = [...freeAvail, ...paidAvail];

    console.log(
      `[smart-router] 🔄 New request — ${freeAvail.length} free + ${paidAvail.length} paid available`,
    );

    for (let i = 0; i < ordered.length; i++) {
      const model = ordered[i];
      const tier = model.includes(':free') ? 'free' : 'paid';
      console.log(`[smart-router]   → [${i + 1}/${ordered.length}] ${tier}: ${model}`);

      const result = await proxyToOpenRouter(
        apiKey,
        model,
        bodyBuffer,
        config.upstream_timeout_ms,
      );

      // Success: stream or buffer back
      if (result.status >= 200 && result.status < 300) {
        console.log(`[smart-router] ✅ Success with ${model}`);
        breaker.recordSuccess();

        if (result.stream) {
          res.writeHead(result.status, {
            'Content-Type': result.headers['content-type'] || 'text/event-stream',
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'X-SmartRouter-Model': model,
            'X-SmartRouter-Tier': tier,
          });
          result.stream.pipe(res);
        } else {
          let responseBody = result.body;
          try {
            const parsed = JSON.parse(result.body.toString('utf8'));
            parsed._smart_router = { model, tier };
            responseBody = Buffer.from(JSON.stringify(parsed), 'utf8');
          } catch {}

          res.writeHead(result.status, {
            'Content-Type': 'application/json',
            'X-SmartRouter-Model': model,
            'X-SmartRouter-Tier': tier,
          });
          res.end(responseBody);
        }
        return;
      }

      // Auth errors: no point cascading, return to client
      if (result.status === 401 || result.status === 403) {
        console.log(`[smart-router] ❌ Auth error (${result.status}) — check API key`);
        res.writeHead(result.status, { 'Content-Type': 'application/json' });
        res.end(result.body);
        return;
      }

      // Rate/overload: cool down this model, try next
      if (result.status === 429 || result.status === 503) {
        cooldown.mark(model);
        breaker.recordFailure();
        continue;
      }

      // 5xx upstream / 502 network errors: count toward breaker, try next model
      if (result.status >= 500) {
        breaker.recordFailure();
        continue;
      }

      // 4xx other (400, 404 model not found, etc.): try next without blaming upstream
      continue;
    }

    // Cascade exhausted
    console.log('[smart-router] ❌ All models exhausted');
    res.writeHead(503, { 'Content-Type': 'application/json' });
    res.end(
      JSON.stringify({
        error: {
          message: 'All models rate-limited or unavailable. Retry in ~60s.',
          type: 'smart_router_exhausted',
          models_tried: ordered.length,
        },
      }),
    );
  };
}

// ── Server lifecycle ─────────────────────────────────────────────
function startSmartRouter(options = {}) {
  const config = { ...loadConfig(), ...options.configOverrides };
  const apiKey = options.apiKey || process.env.OPENROUTER_API_KEY || '';
  const port = options.port ?? config.port;
  const host = options.host ?? config.host;

  if (!config.enabled) {
    console.log('[smart-router] disabled via config');
    return Promise.resolve(null);
  }

  if (!apiKey) {
    console.error('[smart-router] ❌ No API key — set OPENROUTER_API_KEY or pass options.apiKey');
    return Promise.resolve(null);
  }

  const allModels = [...config.free_models, ...config.paid_models];
  if (allModels.length === 0) {
    console.error(
      `[smart-router] ❌ No models configured — add free_models/paid_models to ${CONFIG_PATH}`,
    );
    return Promise.resolve(null);
  }

  const cooldown = createCooldownTracker(config.cooldown_ms);
  const breaker = createBreaker(config.breaker);
  const handle = makeHandler({ apiKey, config, cooldown, breaker });

  // Allowed Host header values — defends against DNS rebinding.
  // Only accept requests addressed to the exact loopback bind.
  const ALLOWED_HOSTS = new Set([
    `${host}:${port}`,
    `127.0.0.1:${port}`,
    `localhost:${port}`,
    `[::1]:${port}`,
  ]);

  // The router is a local process-to-process proxy (terminal-server → router →
  // OpenRouter). It MUST NOT be reachable from browsers: it has no auth and
  // spends the user's OpenRouter balance on paid models. Browsers always send
  // Origin and/or Referer on cross-origin requests, so presence of either
  // header is a reliable signal that the caller is a browser context, not a
  // trusted local process. Combined with a Host allowlist, this also blocks
  // DNS rebinding attacks against 127.0.0.1:4891.
  function rejectIfBrowserOrBadHost(req, res) {
    const hostHeader = (req.headers.host || '').toLowerCase();
    if (!ALLOWED_HOSTS.has(hostHeader)) {
      res.writeHead(403, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ error: 'forbidden host' }));
      return true;
    }
    if (req.headers.origin || req.headers.referer) {
      res.writeHead(403, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ error: 'browser-origin requests are not allowed' }));
      return true;
    }
    return false;
  }

  return new Promise((resolve) => {
    const server = http.createServer((req, res) => {
      if (rejectIfBrowserOrBadHost(req, res)) return;

      const url = new URL(req.url, `http://${host}:${port}`);
      const pathname = url.pathname.replace(/\/+$/, '');

      if (req.method === 'POST' && (pathname === '/v1/chat/completions' || pathname === '/chat/completions')) {
        const chunks = [];
        req.on('data', (c) => chunks.push(c));
        req.on('end', () => {
          handle(Buffer.concat(chunks), res).catch((err) => {
            console.error('[smart-router] Unhandled error:', err);
            if (!res.headersSent) res.writeHead(500, { 'Content-Type': 'application/json' });
            res.end(JSON.stringify({ error: err.message }));
          });
        });
        return;
      }

      if (req.method === 'GET' && (pathname === '/v1/models' || pathname === '/models')) {
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(
          JSON.stringify({
            object: 'list',
            data: allModels.map((id) => ({
              id,
              object: 'model',
              owned_by: id.split('/')[0] || 'openrouter',
              available: cooldown.isAvailable(id),
              tier: id.includes(':free') ? 'free' : 'paid',
            })),
          }),
        );
        return;
      }

      if (req.method === 'GET' && pathname === '/status') {
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(
          JSON.stringify(
            {
              status: 'running',
              models: allModels.length,
              free_available: config.free_models.filter((m) => cooldown.isAvailable(m)).length,
              paid_available: config.paid_models.filter((m) => cooldown.isAvailable(m)).length,
              breaker_open: breaker.isOpen(),
              cooldowns: cooldown.snapshot(),
            },
            null,
            2,
          ),
        );
        return;
      }

      if (req.method === 'GET' && (pathname === '/health' || pathname === '/' || pathname === '')) {
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ status: 'ok', name: 'EvoNexus Smart Router' }));
        return;
      }

      res.writeHead(404, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ error: 'Not found', path: pathname }));
    });

    server.listen(port, host, () => {
      console.log(`[smart-router] 🚀 listening on http://${host}:${port}`);
      console.log(
        `[smart-router]    ${config.free_models.length} free + ${config.paid_models.length} paid; cooldown ${config.cooldown_ms / 1000}s; breaker ${config.breaker.enabled ? 'on' : 'off'}`,
      );
      resolve(server);
    });

    server.on('error', (err) => {
      if (err.code === 'EADDRINUSE') {
        console.warn(`[smart-router] ⚠ Port ${port} already in use — router may already be running`);
        resolve(null);
      } else {
        console.error(`[smart-router] ❌ Server error: ${err.message}`);
        resolve(null);
      }
    });
  });
}

function stopSmartRouter(server) {
  if (server && typeof server.close === 'function') {
    server.close();
    console.log('[smart-router] 🛑 stopped');
  }
}

// Standalone mode: `node openrouter-smart-router.js`
if (require.main === module) {
  startSmartRouter().then((server) => {
    if (!server) process.exit(1);
    const shutdown = () => {
      stopSmartRouter(server);
      process.exit(0);
    };
    process.on('SIGINT', shutdown);
    process.on('SIGTERM', shutdown);
  });
}

module.exports = { startSmartRouter, stopSmartRouter, loadConfig };
