/**
 * ChatLogger — append-only JSONL logs for chat conversations.
 *
 * Stores chat messages in workspace/ADWs/logs/chat/{agentName}_{sessionId}.jsonl
 * Each line is a JSON object: { role, text?, blocks?, files?, ts, uuid? }
 *
 * Special event lines:
 *   { type: "rewind", at: <uuid>, ts }  — marks a rewind point; messages after
 *     the referenced uuid are considered dropped. The reader applies all rewind
 *     markers before returning results (append-only, no destructive truncation).
 *
 * This is the durable source of truth for chat history.
 * sessions.json is a fast-access cache; JSONL survives restarts and cleanups.
 */

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

class ChatLogger {
  constructor(workspaceRoot) {
    this.logsDir = path.join(workspaceRoot || process.cwd(), 'workspace', 'ADWs', 'logs', 'chat');
    this._ensureDir();
  }

  _ensureDir() {
    try {
      fs.mkdirSync(this.logsDir, { recursive: true });
    } catch {}
  }

  _logPath(agentName, sessionId) {
    const safe = (agentName || 'unknown').replace(/[^a-zA-Z0-9_-]/g, '_');
    const shortId = sessionId.slice(0, 8);
    return path.join(this.logsDir, `${safe}_${shortId}.jsonl`);
  }

  /**
   * Append a message to the chat log.
   * Assigns a uuid if the message doesn't already have one.
   * Returns the (possibly assigned) uuid.
   */
  append(agentName, sessionId, message) {
    try {
      if (!message.uuid) {
        message.uuid = crypto.randomUUID();
      }
      const logPath = this._logPath(agentName, sessionId);
      const line = JSON.stringify(message) + '\n';
      fs.appendFileSync(logPath, line, 'utf8');
      return message.uuid;
    } catch (err) {
      console.error(`[chat-logger] Failed to append: ${err.message}`);
      return null;
    }
  }

  /**
   * Append a rewind marker to the JSONL log.
   * The marker records which message uuid was rewound from.
   */
  appendRewindMarker(agentName, sessionId, atUuid) {
    try {
      const marker = { type: 'rewind', at: atUuid, ts: Date.now() };
      const logPath = this._logPath(agentName, sessionId);
      fs.appendFileSync(logPath, JSON.stringify(marker) + '\n', 'utf8');
    } catch (err) {
      console.error(`[chat-logger] Failed to append rewind marker: ${err.message}`);
    }
  }

  /**
   * Read full chat history from JSONL log, applying rewind markers.
   * Returns array of messages, or empty array if not found.
   *
   * Algorithm: play forward. When a { type:"rewind", at:<uuid> } line is
   * encountered, drop all messages strictly after the message with that uuid.
   * Legacy messages without uuid get synthesized deterministic ids.
   */
  read(agentName, sessionId) {
    try {
      const logPath = this._logPath(agentName, sessionId);
      if (!fs.existsSync(logPath)) return [];

      const content = fs.readFileSync(logPath, 'utf8').trim();
      if (!content) return [];

      const rawLines = [];
      for (const line of content.split('\n')) {
        if (!line.trim()) continue;
        try {
          rawLines.push(JSON.parse(line));
        } catch {
          // Skip malformed lines
        }
      }

      // First pass: assign synthesized uuids to legacy messages (no uuid, not a marker)
      let idx = 0;
      for (const entry of rawLines) {
        if (entry.type !== 'rewind' && !entry.uuid) {
          entry.uuid = `legacy-${sessionId}-${idx}`;
        }
        if (entry.type !== 'rewind') idx++;
      }

      // Second pass: play forward, applying rewind markers
      const messages = [];
      for (const entry of rawLines) {
        if (entry.type === 'rewind') {
          // Drop the message with entry.at uuid AND everything after it
          const cutIdx = messages.findIndex(m => m.uuid === entry.at);
          if (cutIdx !== -1) {
            messages.splice(cutIdx);
          }
          // If uuid not found (e.g. marker for already-rewound content), no-op
        } else {
          messages.push(entry);
        }
      }

      return messages;
    } catch (err) {
      console.error(`[chat-logger] Failed to read: ${err.message}`);
      return [];
    }
  }

  /**
   * Check if a log exists for a session.
   */
  exists(agentName, sessionId) {
    return fs.existsSync(this._logPath(agentName, sessionId));
  }

  /**
   * List every chat session that has a durable log on disk.
   *
   * Parses the JSONL filenames ({agentName}_{shortId}.jsonl). Agent names may
   * contain underscores (see _logPath), so the session id is the segment after
   * the LAST underscore. Returns [{ agentName, sessionId }] (empty on error).
   */
  listSessions() {
    try {
      const out = [];
      for (const file of fs.readdirSync(this.logsDir)) {
        if (!file.endsWith('.jsonl')) continue;
        const base = file.slice(0, -'.jsonl'.length);
        const i = base.lastIndexOf('_');
        if (i <= 0 || i === base.length - 1) continue; // need non-empty agent and id
        out.push({ agentName: base.slice(0, i), sessionId: base.slice(i + 1) });
      }
      return out;
    } catch {
      return [];
    }
  }
}

module.exports = ChatLogger;
