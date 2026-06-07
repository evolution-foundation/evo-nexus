const assert = require('assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');
const test = require('node:test');

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

const ChatLogger = require('../src/utils/chat-logger');

function makeChatLog(baseFolder, fileName, lines) {
  const chatDir = path.join(baseFolder, 'workspace', 'ADWs', 'logs', 'chat');
  fs.mkdirSync(chatDir, { recursive: true });
  fs.writeFileSync(path.join(chatDir, fileName), lines.map((l) => JSON.stringify(l)).join('\n') + '\n');
}

test('ChatLogger.listSessions parses agent and session id (agent names may contain underscores)', () => {
  const baseFolder = makeTempDir('evonexus-listsessions-');
  const chatDir = path.join(baseFolder, 'workspace', 'ADWs', 'logs', 'chat');
  fs.mkdirSync(chatDir, { recursive: true });
  fs.writeFileSync(path.join(chatDir, 'oracle_abc12345.jsonl'), '');
  fs.writeFileSync(path.join(chatDir, 'my_agent_def67890.jsonl'), '');
  fs.writeFileSync(path.join(chatDir, 'not-a-log.txt'), 'ignored');

  const logger = new ChatLogger(baseFolder);
  const sessions = logger.listSessions().sort((a, b) => a.sessionId.localeCompare(b.sessionId));

  assert.equal(sessions.length, 2);
  assert.deepEqual(sessions[0], { agentName: 'oracle', sessionId: 'abc12345' });
  assert.deepEqual(sessions[1], { agentName: 'my_agent', sessionId: 'def67890' });
});

test('TerminalServer rebuilds the session index from chat logs when the cache is empty', async () => {
  const baseFolder = makeTempDir('evonexus-selfheal-');
  makeChatLog(baseFolder, 'oracle_abc12345.jsonl', [
    { role: 'user', text: 'Hello there', ts: Date.now(), uuid: 'u1' },
    { role: 'assistant', text: 'Hi!', ts: Date.now(), uuid: 'a1' },
  ]);

  const server = new TerminalServer({
    port: 0,
    dev: false,
    sessionGcIntervalMs: 0,
    autoSaveIntervalMs: 0,
  });
  await server.ready;

  // Point the logger at our temp logs and start from an empty cache.
  server.chatLogger = new ChatLogger(baseFolder);
  server.claudeSessions = new Map();

  const recovered = server.rebuildSessionsFromLogs();

  assert.equal(recovered, 1);
  assert.equal(server.claudeSessions.has('abc12345'), true);
  const recoveredSession = server.claudeSessions.get('abc12345');
  assert.equal(recoveredSession.agentName, 'oracle');
  assert.equal(recoveredSession.mode, 'chat');
  assert.equal(recoveredSession.name, 'Hello there');
  assert.equal(recoveredSession.chatHistory.length, 2);

  // Idempotent: a second pass must not duplicate already-present sessions.
  assert.equal(server.rebuildSessionsFromLogs(), 0);

  server.close();
});
