const assert = require('assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');
const test = require('node:test');
const http = require('http');
const WebSocket = require('ws');

const SessionStore = require('../src/utils/session-store');
const { TerminalServer } = require('../src/server');

function makeTempDir(prefix) {
  return fs.mkdtempSync(path.join(os.tmpdir(), prefix));
}

test('SessionStore drops stale sessions when loading', async () => {
  const tempDir = makeTempDir('evonexus-session-store-');
  const sessionsFile = path.join(tempDir, 'sessions.json');
  const now = new Date();
  const staleAt = new Date(Date.now() - 48 * 60 * 60 * 1000).toISOString();
  const freshAt = new Date(Date.now() - 2 * 1000).toISOString();

  fs.writeFileSync(
    sessionsFile,
    JSON.stringify({
      version: '1.0',
      savedAt: now.toISOString(),
      sessions: [
        {
          id: 'stale',
          name: 'Stale session',
          created: staleAt,
          lastActivity: staleAt,
          archived: false,
        },
        {
          id: 'fresh',
          name: 'Fresh session',
          created: freshAt,
          lastActivity: freshAt,
          archived: false,
        },
      ],
    }, null, 2)
  );

  const store = new SessionStore({ storageDir: tempDir, sessionTtlMs: 5000, maxFileAgeDays: 30 });
  const sessions = await store.loadSessions();

  assert.equal(sessions.size, 1);
  assert.equal(sessions.has('fresh'), true);
  assert.equal(sessions.has('stale'), false);
});

test('TerminalServer purges stale sessions and reports health', async () => {
  const tempDir = makeTempDir('evonexus-terminal-server-');
  const sessionsFile = path.join(tempDir, 'sessions.json');
  fs.writeFileSync(
    sessionsFile,
    JSON.stringify({
      version: '1.0',
      savedAt: new Date().toISOString(),
      sessions: [],
    }, null, 2)
  );

  const server = new TerminalServer({
    port: 0,
    dev: false,
    sessionTtlMs: 1000,
    sessionGcIntervalMs: 0,
    autoSaveIntervalMs: 0,
  });

  await server.ready;
  server.claudeSessions = new Map();

  server.sessionStore.storageDir = tempDir;
  server.sessionStore.sessionsFile = sessionsFile;

  const staleAt = new Date(Date.now() - 2 * 60 * 60 * 1000);
  const freshAt = new Date();

  server.claudeSessions.set('stale', {
    id: 'stale',
    name: 'Stale session',
    created: staleAt,
    lastActivity: staleAt,
    active: false,
    archived: false,
    connections: new Set(),
    workingDir: tempDir,
  });
  server.claudeSessions.set('fresh', {
    id: 'fresh',
    name: 'Fresh session',
    created: freshAt,
    lastActivity: freshAt,
    active: false,
    archived: false,
    connections: new Set(),
    workingDir: tempDir,
  });

  const result = await server.purgeStaleSessions();
  const health = server.getHealthSnapshot(true);

  assert.equal(result.removed, 1);
  assert.equal(server.claudeSessions.has('stale'), false);
  assert.equal(server.claudeSessions.has('fresh'), true);
  assert.equal(health.counts.staleSessions, 0);
  assert.equal(health.checks.storage.status, 'ok');
  assert.equal(health.checks.workspace.status, 'ok');
  assert.equal(typeof health.checks.providers.status, 'string');
  assert.equal(['ok', 'warning', 'error'].includes(health.status), true);

  server.close();
});

// ---------------------------------------------------------------------------
// WebSocket token auth tests
// ---------------------------------------------------------------------------

/**
 * Start a real TerminalServer on a random port and return { server, addr }.
 * The caller must call server.close() in cleanup.
 */
async function startTestServer(token) {
  const orig = process.env.TERMINAL_WS_TOKEN;
  if (token !== undefined) {
    process.env.TERMINAL_WS_TOKEN = token;
  } else {
    delete process.env.TERMINAL_WS_TOKEN;
  }

  // Re-require server so it picks up the updated env (module is cached, so
  // we need to invalidate the cache for the token to take effect).
  Object.keys(require.cache).forEach((k) => {
    if (k.includes('terminal-server') && !k.includes('node_modules')) {
      delete require.cache[k];
    }
  });
  const { TerminalServer: TS } = require('../src/server');

  const srv = new TS({ port: 0, dev: false, sessionGcIntervalMs: 0, autoSaveIntervalMs: 0 });
  const httpServer = await srv.start();

  // Restore env
  if (orig !== undefined) {
    process.env.TERMINAL_WS_TOKEN = orig;
  } else {
    delete process.env.TERMINAL_WS_TOKEN;
  }

  const addr = httpServer.address();
  return { server: srv, addr };
}

function wsConnect(addr, tokenQuery) {
  return new Promise((resolve, reject) => {
    const url = `ws://127.0.0.1:${addr.port}/ws${tokenQuery ? `?token=${tokenQuery}` : ''}`;
    const ws = new WebSocket(url);
    ws.on('open', () => resolve({ ws, opened: true }));
    ws.on('unexpected-response', (req, res) => {
      resolve({ ws: null, opened: false, statusCode: res.statusCode });
    });
    ws.on('error', (err) => {
      // Connection refused or similar — treat as rejection
      resolve({ ws: null, opened: false, error: err.message });
    });
    // Timeout safety
    setTimeout(() => reject(new Error('WS connect timeout')), 3000);
  });
}

test('WS token auth — accepts connection with correct token', async () => {
  const TOKEN = 'test-secret-abc123';
  const { server, addr } = await startTestServer(TOKEN);

  try {
    const result = await wsConnect(addr, TOKEN);
    assert.equal(result.opened, true, 'WebSocket should open with correct token');
    if (result.ws) result.ws.close();
  } finally {
    server.close();
  }
});

test('WS token auth — rejects connection without token (401)', async () => {
  const TOKEN = 'test-secret-abc123';
  const { server, addr } = await startTestServer(TOKEN);

  try {
    const result = await wsConnect(addr, null);
    assert.equal(result.opened, false, 'WebSocket should be rejected without token');
    assert.equal(result.statusCode, 401, 'Expected HTTP 401 on upgrade rejection');
  } finally {
    server.close();
  }
});

test('WS token auth — rejects connection with wrong token (401)', async () => {
  const TOKEN = 'test-secret-abc123';
  const { server, addr } = await startTestServer(TOKEN);

  try {
    const result = await wsConnect(addr, 'wrong-token');
    assert.equal(result.opened, false, 'WebSocket should be rejected with wrong token');
    assert.equal(result.statusCode, 401, 'Expected HTTP 401 on upgrade rejection');
  } finally {
    server.close();
  }
});

test('WS token auth — allows all connections when TERMINAL_WS_TOKEN is unset (dev mode)', async () => {
  const { server, addr } = await startTestServer(undefined);

  try {
    // No token in query — should still succeed in dev mode
    const result = await wsConnect(addr, null);
    assert.equal(result.opened, true, 'WebSocket should open in dev mode without token');
    if (result.ws) result.ws.close();
  } finally {
    server.close();
  }
});
