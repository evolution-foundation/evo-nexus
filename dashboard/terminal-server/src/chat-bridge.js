/**
 * Chat Bridge — spawns Claude via Agent SDK with structured streaming events.
 * Supports conversation resume via SDK session IDs.
 */

const fs = require('fs');
const path = require('path');
const os = require('os');
const {
  loadProviderConfig,
  resolveProviderModel,
} = require('./provider-config');
let sdkModule = null;

// Workspace root is three levels up from this file (dashboard/terminal-server/src/).
const WORKSPACE_ROOT = path.resolve(__dirname, '..', '..', '..');

/**
 * Read chat.trustMode from config/workspace.yaml.
 * Uses a targeted regex — no YAML dep needed.
 * Returns false if the key is absent or parsing fails.
 */
function readTrustMode() {
  try {
    const yaml = fs.readFileSync(path.join(WORKSPACE_ROOT, 'config', 'workspace.yaml'), 'utf8');
    // Match `chat:` section followed by a line containing `trustMode: true`
    const m = yaml.match(/^chat:\s*\n(?:[ \t]+[^\n]*\n)*?[ \t]+trustMode:\s*(true|false)/m);
    return m ? m[1] === 'true' : false;
  } catch {
    return false;
  }
}

// Tools that run silently without user confirmation.
const AUTO_APPROVE = new Set([
  'Read', 'Glob', 'Grep', 'WebFetch', 'WebSearch', 'ToolSearch',
  'NotebookRead', 'Skill',
]);

// Tools that require explicit user approval before execution.
// Any tool NOT in either set also requires approval (conservative default).
const NEEDS_APPROVAL = new Set([
  'Write', 'Edit', 'Bash', 'NotebookEdit', 'Agent',
]);

/**
 * Parse a .claude/agents/{name}.md file into an AgentDefinition.
 * Extracts YAML frontmatter for metadata and the body as the prompt.
 */
function loadAgentFile(agentName, cwd) {
  // Agent definitions live at the workspace root — cwd varies per ticket session.
  const rootPath = path.join(WORKSPACE_ROOT, '.claude', 'agents', `${agentName}.md`);
  const cwdPath = path.join(cwd, '.claude', 'agents', `${agentName}.md`);
  const agentPath = fs.existsSync(rootPath) ? rootPath : cwdPath;
  if (!fs.existsSync(agentPath)) {
    console.warn(`[chat-bridge] Agent file not found: ${agentPath}`);
    return null;
  }

  const raw = fs.readFileSync(agentPath, 'utf8');
  const fmMatch = raw.match(/^---\n([\s\S]*?)\n---\n?([\s\S]*)$/);
  if (!fmMatch) {
    return { description: agentName, prompt: raw.trim() };
  }

  // Simple YAML frontmatter parser (no dependency needed)
  const fmText = fmMatch[1];
  const meta = {};
  for (const line of fmText.split('\n')) {
    const m = line.match(/^(\w+):\s*(.+)$/);
    if (m) {
      const val = m[2].trim().replace(/^["']|["']$/g, '');
      meta[m[1]] = val;
    }
    // Parse tools list
    if (line.match(/^\s+-\s+\w+/)) {
      if (!meta._lastKey) continue;
      if (!Array.isArray(meta[meta._lastKey])) meta[meta._lastKey] = [];
      meta[meta._lastKey].push(line.trim().replace(/^-\s*/, ''));
    }
    if (line.match(/^\w+:$/)) {
      meta._lastKey = line.replace(':', '').trim();
      meta[meta._lastKey] = [];
    }
  }
  delete meta._lastKey;

  const prompt = fmMatch[2].trim();
  const def = {
    description: typeof meta.description === 'string' ? meta.description : agentName,
    prompt,
  };
  if (meta.model) def.model = meta.model;
  if (Array.isArray(meta.tools)) def.tools = meta.tools;

  return def;
}

async function loadSDK() {
  if (!sdkModule) {
    sdkModule = await import('@anthropic-ai/claude-agent-sdk');
  }
  return sdkModule;
}

/**
 * Resolve the Claude Code native binary path, working around a bug in
 * @anthropic-ai/claude-agent-sdk where Linux auto-discovery tries the
 * `-musl` variant before glibc regardless of the host's actual libc.
 * On glibc hosts with both packages installed, the SDK picks the musl
 * binary, which fails to spawn because its dynamic loader is absent.
 * See https://github.com/anthropics/claude-agent-sdk-typescript/issues/296
 *
 * Resolution order:
 *   1. CLAUDE_CODE_EXECUTABLE env var (explicit override)
 *   2. Platform-specific sibling package under node_modules, preferring the
 *      libc variant that matches this host (glibc → no suffix; musl → -musl)
 *   3. null → fall back to the SDK's own (buggy) discovery
 */
let _claudeExecutablePath = undefined;
function resolveClaudeExecutable() {
  if (_claudeExecutablePath !== undefined) return _claudeExecutablePath;

  const envPath = process.env.CLAUDE_CODE_EXECUTABLE;
  if (envPath && fs.existsSync(envPath)) {
    console.log(`[chat-bridge] Using CLAUDE_CODE_EXECUTABLE=${envPath}`);
    _claudeExecutablePath = envPath;
    return _claudeExecutablePath;
  }

  const { platform, arch } = process;
  const suffix = platform === 'win32' ? '.exe' : '';
  const isMusl = platform === 'linux' && (() => {
    try {
      return fs.readdirSync('/lib').some((f) => f.startsWith('ld-musl-'))
          || fs.readdirSync('/usr/lib').some((f) => f.startsWith('ld-musl-'));
    } catch { return false; }
  })();

  const candidates = platform === 'linux'
    ? (isMusl
        ? [`claude-agent-sdk-linux-${arch}-musl`, `claude-agent-sdk-linux-${arch}`]
        : [`claude-agent-sdk-linux-${arch}`, `claude-agent-sdk-linux-${arch}-musl`])
    : [`claude-agent-sdk-${platform}-${arch}`];

  for (const pkg of candidates) {
    try {
      const p = require.resolve(`@anthropic-ai/${pkg}/claude${suffix}`);
      console.log(`[chat-bridge] Resolved Claude binary: ${p} (libc: ${isMusl ? 'musl' : 'glibc'})`);
      _claudeExecutablePath = p;
      return _claudeExecutablePath;
    } catch { /* try next */ }
  }

  console.warn('[chat-bridge] Could not resolve Claude binary; letting SDK auto-discover');
  _claudeExecutablePath = null;
  return _claudeExecutablePath;
}

/**
 * Scan a tool_result text for a ticket-creation response.
 * Returns the ticket id if a POST /api/tickets response is detected, else null.
 * Heuristic: a JSON object with a UUID `id`, `status` in ticket statuses, and a `priority` field.
 */
const TICKET_STATUSES = new Set(['open', 'in_progress', 'blocked', 'review', 'resolved', 'closed']);
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function _looksLikeTicket(obj) {
  return (
    obj &&
    typeof obj.id === 'string' &&
    UUID_RE.test(obj.id) &&
    typeof obj.status === 'string' &&
    TICKET_STATUSES.has(obj.status) &&
    typeof obj.priority === 'string' &&
    Object.prototype.hasOwnProperty.call(obj, 'title')
  );
}

// Scan `text` for balanced {...} JSON objects and try to parse each.
function _extractJsonObjects(text) {
  const results = [];
  for (let i = 0; i < text.length; i++) {
    if (text[i] !== '{') continue;
    let depth = 0;
    let inStr = false;
    let esc = false;
    for (let j = i; j < text.length; j++) {
      const c = text[j];
      if (esc) { esc = false; continue; }
      if (c === '\\') { esc = true; continue; }
      if (c === '"') { inStr = !inStr; continue; }
      if (inStr) continue;
      if (c === '{') depth++;
      else if (c === '}') {
        depth--;
        if (depth === 0) {
          results.push(text.slice(i, j + 1));
          i = j;
          break;
        }
      }
    }
  }
  return results;
}

// Regex fallback — works for both JSON ("id":"...") and Python repr ('id': '...')
// and extracts a plausible ticket id when the structure has the expected fields.
function _regexScanForTicket(text) {
  const hasIdPriorityStatus =
    /["']id["']\s*:\s*["']([0-9a-f-]{36})["']/i.test(text) &&
    /["']priority["']\s*:\s*["'](urgent|high|medium|low)["']/i.test(text) &&
    /["']status["']\s*:\s*["'](open|in_progress|blocked|review|resolved|closed)["']/i.test(text) &&
    /["']title["']\s*:/i.test(text);
  if (!hasIdPriorityStatus) return null;
  const m = text.match(/["']id["']\s*:\s*["']([0-9a-f-]{36})["']/i);
  return m && UUID_RE.test(m[1]) ? m[1] : null;
}

function detectCreatedTicketId(text) {
  if (!text || typeof text !== 'string') return null;
  const hasIdKey = text.includes('"id"') || text.includes("'id'");
  const hasPriorityKey = text.includes('"priority"') || text.includes("'priority'");
  if (!hasIdKey || !hasPriorityKey) return null;
  // First: strict JSON parse attempts.
  for (const candidate of _extractJsonObjects(text)) {
    try {
      const obj = JSON.parse(candidate);
      if (_looksLikeTicket(obj)) return obj.id;
    } catch {}
  }
  try {
    const obj = JSON.parse(text.trim());
    if (_looksLikeTicket(obj)) return obj.id;
  } catch {}
  // Fallback: regex scan — handles Python repr output (single quotes).
  return _regexScanForTicket(text);
}

class ChatBridge {
  constructor() {
    this.sessions = new Map(); // sessionId -> { query, abortController, active, sdkSessionId }
  }

  _buildChatCompletionSystemPrompt(agentName, cwd, sessionId) {
    const lines = [
      'You are running inside EvoNexus dashboard chat.',
      `Current chat session id: ${sessionId}`,
    ];
    if (agentName) lines.push(`Current agent slug: ${agentName}`);
    if (agentName) {
      const agentDef = loadAgentFile(agentName, cwd);
      if (agentDef?.prompt) {
        lines.push('');
        lines.push('Follow this agent persona strictly:');
        lines.push(agentDef.prompt);
      }
    }
    lines.push('');
    lines.push('If this model does not support tool calling, answer directly with best effort.');
    return lines.join('\n');
  }

  _extractChatDeltaText(delta) {
    if (!delta) return '';
    if (typeof delta.content === 'string') return delta.content;
    if (Array.isArray(delta.content)) {
      return delta.content
        .map((part) => (part?.type === 'text' ? part.text || '' : ''))
        .join('');
    }
    if (typeof delta.reasoning === 'string') return delta.reasoning;
    return '';
  }

  async _startOpenAICompatibleSession(sessionId, options, providerConfig) {
    const {
      agentName,
      workingDir,
      prompt,
      files,
      onMessage,
      onError,
      onComplete,
    } = options;

    if (this.sessions.has(sessionId)) {
      await this.stopSession(sessionId);
    }

    const abortController = new AbortController();
    const cwd = workingDir || process.cwd();
    const env = providerConfig.env_vars || {};
    const model = resolveProviderModel(providerConfig);
    const baseUrl = (env.OPENAI_BASE_URL || 'https://api.openai.com/v1').replace(/\/+$/, '');
    const apiKey = env.OPENAI_API_KEY || env.CODEX_API_KEY || '';

    if (!model) {
      throw new Error(`Provider "${providerConfig.active}" sem modelo configurado. Defina o campo Model em Providers.`);
    }
    if (!apiKey) {
      throw new Error(`Provider "${providerConfig.active}" sem API key configurada para Chat Completion.`);
    }

    const runtimePrompt = this._buildChatCompletionSystemPrompt(agentName, cwd, sessionId);

    let finalPrompt = prompt || '';
    if (files && files.length > 0) {
      const names = files.map((f) => `- ${f.name}`).join('\n');
      finalPrompt += `\n\n[Attached files]\n${names}\n`;
    }

    const session = { active: true, abortController, sdkSessionId: null };
    this.sessions.set(sessionId, session);

    (async () => {
      try {
        const resp = await fetch(`${baseUrl}/chat/completions`, {
          method: 'POST',
          headers: {
            Authorization: `Bearer ${apiKey}`,
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            model,
            stream: true,
            messages: [
              { role: 'system', content: runtimePrompt },
              { role: 'user', content: finalPrompt },
            ],
          }),
          signal: abortController.signal,
        });

        if (!resp.ok || !resp.body) {
          const body = await resp.text().catch(() => '');
          throw new Error(`Chat Completion falhou (${resp.status}): ${body.slice(0, 240)}`);
        }

        if (onMessage) {
          onMessage({ type: 'message_start' });
          onMessage({ type: 'text_start' });
        }

        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        while (session.active) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split('\n');
          buffer = lines.pop() || '';

          for (const raw of lines) {
            const line = raw.trim();
            if (!line.startsWith('data:')) continue;
            const payload = line.slice(5).trim();
            if (!payload || payload === '[DONE]') continue;
            let parsed;
            try {
              parsed = JSON.parse(payload);
            } catch {
              continue;
            }
            const delta = parsed?.choices?.[0]?.delta || {};
            const text = this._extractChatDeltaText(delta);
            if (text && onMessage) {
              onMessage({ type: 'text_delta', text });
            }
          }
        }

        if (onMessage) {
          onMessage({ type: 'message_stop' });
          onMessage({ type: 'result', subtype: 'success', isError: false });
        }
        session.active = false;
        this.sessions.delete(sessionId);
        if (onComplete) onComplete({ sdkSessionId: null });
      } catch (err) {
        session.active = false;
        this.sessions.delete(sessionId);
        if (err.name === 'AbortError') {
          if (onComplete) onComplete({ sdkSessionId: null });
        } else if (onError) {
          onError(err);
        }
      }
    })();

    return { sessionId, sdkSessionId: null };
  }

  async startSession(sessionId, options = {}) {
    const {
      agentName,
      workingDir,
      prompt,
      files,
      sdkSessionId,
      systemPromptExtras,
      onMessage,
      onError,
      onComplete,
    } = options;

    const providerConfig = loadProviderConfig();
    if (providerConfig.active !== 'anthropic') {
      return this._startOpenAICompatibleSession(sessionId, options, providerConfig);
    }

    const { query: sdkQuery } = await loadSDK();

    if (this.sessions.has(sessionId)) {
      await this.stopSession(sessionId);
    }

    const abortController = new AbortController();

    const queryOptions = {
      cwd: workingDir || process.cwd(),
      includePartialMessages: true,
      abortController,
    };

    const claudeExe = resolveClaudeExecutable();
    if (claudeExe) queryOptions.pathToClaudeCodeExecutable = claudeExe;

    // Load agent definition from .claude/agents/{name}.md
    if (agentName) {
      const agentDef = loadAgentFile(agentName, queryOptions.cwd);
      if (agentDef) {
        // Build runtime context block for ticket source attribution
        const runtimeLines = [
          '## Runtime context',
          'You are running inside the EvoNexus dashboard.',
        ];
        if (agentName) {
          runtimeLines.push(`- Current agent slug: ${agentName}`);
        }
        runtimeLines.push(`- Current chat session id: ${sessionId}`);
        runtimeLines.push('');
        runtimeLines.push('When you create a ticket via `evo.post("/api/tickets", {...})`, include `source_agent: "' + agentName + '"` and `source_session_id: "' + sessionId + '"` in the payload so the ticket records who created it.');
        runtimeLines.push('');
        runtimeLines.push('## Tool permission policy');
        runtimeLines.push('Read/Glob/Grep/WebFetch/ToolSearch/Skill run automatically.');
        runtimeLines.push('Write/Edit/Bash/Agent/NotebookEdit need user approval per call — the UI shows a card with Allow/Deny buttons. Don\'t ask for permission in text; just call the tool and the user will respond in the UI.');

        const runtimeBlock = runtimeLines.join('\n');

        // Use systemPrompt with claude_code preset + agent prompt appended.
        // systemPromptExtras (e.g. thread memory.md content) is only injected on
        // fresh sessions — when resuming, the context is already in the conversation.
        let promptAppend = agentDef.prompt + '\n\n' + runtimeBlock;
        if (systemPromptExtras && !sdkSessionId) {
          promptAppend = promptAppend + '\n\n' + systemPromptExtras;
        }
        queryOptions.systemPrompt = {
          type: 'preset',
          preset: 'claude_code',
          append: promptAppend,
        };
        if (agentDef.model) queryOptions.model = agentDef.model;
        console.log(`[chat-bridge] Loaded agent "${agentName}" via systemPrompt.append (${agentDef.prompt.length} chars, model: ${agentDef.model || 'inherit'})`);
      } else {
        console.warn(`[chat-bridge] Agent "${agentName}" not found, running without agent`);
      }
    }

    queryOptions.allowedTools = [
      'Read', 'Write', 'Edit', 'Bash', 'Glob', 'Grep',
      'Agent', 'Skill', 'WebSearch', 'WebFetch',
      'NotebookEdit', 'ToolSearch',
    ];

    // trustMode is read fresh on EVERY tool decision below, so toggling it in
    // the UI takes effect mid-session without needing a restart.
    if (readTrustMode()) {
      console.log(`[chat-bridge] Trust mode ON at session start (${sessionId})`);
    }

    // Per-tool approval flow — works for main thread AND spawned subagents.
    //
    // The SDK provides two hooks: `canUseTool` (main thread only) and the
    // PreToolUse hook event (fires for both main thread AND subagents, with
    // `agent_id` set when inside a subagent). We register both so the flow
    // is uniform regardless of who invoked the tool.
    const requestApprovalFromUser = (toolName, input, requestId, agentId) => {
      const currentSession = this.sessions.get(sessionId);
      if (!currentSession || !currentSession.active) {
        return Promise.resolve({ behavior: 'deny', message: 'Session is no longer active.' });
      }
      return new Promise((resolve) => {
        if (!currentSession.pendingApprovals) currentSession.pendingApprovals = new Map();
        currentSession.pendingApprovals.set(requestId, { resolve, toolInput: input });
        if (onMessage) {
          onMessage({
            type: 'permission_request',
            requestId,
            toolName,
            input,
            agentId: agentId || null,
          });
        }
      });
    };

    queryOptions.canUseTool = async (toolName, input, sdkOptions) => {
      if (readTrustMode() || AUTO_APPROVE.has(toolName)) {
        return { behavior: 'allow', updatedInput: input ?? {} };
      }
      const requestId = sdkOptions.toolUseID || `req-${Date.now()}-${Math.random().toString(36).slice(2)}`;
      return requestApprovalFromUser(toolName, input, requestId, null);
    };

    queryOptions.hooks = {
      ...(queryOptions.hooks || {}),
      PreToolUse: [{
        hooks: [async (hookInput, toolUseID) => {
          const toolName = hookInput.tool_name;
          const toolInput = hookInput.tool_input;
          const agentId = hookInput.agent_id || null;
          if (readTrustMode() || AUTO_APPROVE.has(toolName)) {
            return {
              hookSpecificOutput: {
                hookEventName: 'PreToolUse',
                permissionDecision: 'allow',
              },
            };
          }
          const requestId = toolUseID || `req-${Date.now()}-${Math.random().toString(36).slice(2)}`;
          // If main-thread canUseTool already handled this requestId, skip to avoid double-prompt.
          const currentSession = this.sessions.get(sessionId);
          if (currentSession?.pendingApprovals?.has(requestId)) {
            // Another handler already opened the prompt — wait on the same resolver.
            return { hookSpecificOutput: { hookEventName: 'PreToolUse', permissionDecision: 'defer' } };
          }
          const decision = await requestApprovalFromUser(toolName, toolInput, requestId, agentId);
          return {
            hookSpecificOutput: {
              hookEventName: 'PreToolUse',
              permissionDecision: decision.behavior === 'allow' ? 'allow' : 'deny',
              permissionDecisionReason: decision.message || undefined,
            },
          };
        }],
      }],
    };

    // Enable subagent progress summaries
    queryOptions.agentProgressSummaries = true;

    // Resume existing conversation if we have an SDK session ID
    if (sdkSessionId) {
      queryOptions.resume = sdkSessionId;
    }

    // Save attached files to temp dir and reference in prompt
    let finalPrompt = prompt || '';
    if (files && files.length > 0) {
      const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'evo-chat-'));
      const savedPaths = [];
      for (const f of files) {
        if (f.base64) {
          const safeName = f.name.replace(/[^a-zA-Z0-9._-]/g, '_');
          const filePath = path.join(tmpDir, safeName);
          fs.writeFileSync(filePath, Buffer.from(f.base64, 'base64'));
          savedPaths.push({ name: f.name, path: filePath, type: f.type });
        }
      }
      if (savedPaths.length > 0) {
        const fileList = savedPaths
          .map(f => `- ${f.name}: ${f.path}`)
          .join('\n');
        const fileNote = `\n\n[Attached files — use Read tool to view them]\n${fileList}`;
        finalPrompt = finalPrompt + fileNote;
      }
    }

    const session = {
      active: true,
      abortController,
      agentName,
      sdkSessionId: sdkSessionId || null,
    };
    this.sessions.set(sessionId, session);

    // Run query in background
    (async () => {
      try {
        console.log(`[chat-bridge] Starting query for session ${sessionId}, agent: ${agentName}, resume: ${sdkSessionId || 'new'}`);
        console.log(`[chat-bridge] Query options:`, JSON.stringify({ cwd: queryOptions.cwd, agent: queryOptions.agent, resume: queryOptions.resume, allowedTools: queryOptions.allowedTools?.length }, null, 2));
        const q = sdkQuery({ prompt: finalPrompt, options: queryOptions });
        console.log(`[chat-bridge] Query created, starting iteration...`);

        for await (const message of q) {
          if (!session.active) break;

          const eventDetail = message.type === 'stream_event' ? ` event=${message.event?.type} cb=${message.event?.content_block?.type || message.event?.delta?.type || ''}` : '';
          if (message.type === 'system') {
            console.log(`[chat-bridge] System message: subtype=${message.subtype}, agent=${message.agent || 'none'}, data=${JSON.stringify(message).slice(0, 200)}`);
          } else {
            console.log(`[chat-bridge] Message received: type=${message.type}${eventDetail}`);
          }

          // Capture SDK session ID from any message that has it
          if (message.session_id && !session.sdkSessionId) {
            session.sdkSessionId = message.session_id;
            if (onMessage) {
              onMessage({ type: 'session_id', sdkSessionId: message.session_id });
            }
          }

          // Auto-detect ticket creation in tool_result blocks.
          if (message.type === 'user') {
            const content = message.message?.content || message.content;
            if (Array.isArray(content)) {
              for (const block of content) {
                if (block.type !== 'tool_result') continue;
                const raw = Array.isArray(block.content)
                  ? block.content.map(c => (typeof c === 'string' ? c : c?.text || '')).join('\n')
                  : (typeof block.content === 'string' ? block.content : '');
                const ticketId = detectCreatedTicketId(raw);
                if (ticketId) {
                  console.log(`[chat-bridge] ✓ Detected ticket creation: ${ticketId} — auto-binding to session ${sessionId}`);
                  if (onMessage) {
                    onMessage({ type: 'ticket_detected', ticketId });
                  }
                }
              }
            }
          }

          if (onMessage) {
            onMessage(this._transformMessage(message));
          }
        }
        console.log(`[chat-bridge] Query iteration finished for session ${sessionId}`);

        session.active = false;
        this.sessions.delete(sessionId);
        if (onComplete) onComplete({ sdkSessionId: session.sdkSessionId });
      } catch (err) {
        console.error(`[chat-bridge] Error in session ${sessionId}:`, err.message || err);
        session.active = false;
        this.sessions.delete(sessionId);
        if (err.name === 'AbortError') {
          if (onComplete) onComplete({ sdkSessionId: session.sdkSessionId });
        } else {
          if (onError) onError(err);
        }
      }
    })();

    return { sessionId, sdkSessionId: session.sdkSessionId };
  }

  async stopSession(sessionId) {
    const session = this.sessions.get(sessionId);
    if (!session) return;

    const sdkSessionId = session.sdkSessionId;
    session.active = false;
    // Deny all pending approval requests so awaiting canUseTool promises resolve.
    if (session.pendingApprovals && session.pendingApprovals.size > 0) {
      for (const entry of session.pendingApprovals.values()) {
        entry.resolve({ behavior: 'deny', message: 'Session stopped by user.' });
      }
      session.pendingApprovals.clear();
    }
    try {
      session.abortController.abort();
    } catch {}
    this.sessions.delete(sessionId);
    return { sdkSessionId };
  }

  getSession(sessionId) {
    return this.sessions.get(sessionId);
  }

  /**
   * Resolve a pending tool approval request.
   * Called by server.js when the user clicks Allow/Deny in the UI.
   */
  respondToApproval(sessionId, requestId, approved) {
    const session = this.sessions.get(sessionId);
    if (!session?.pendingApprovals) return false;
    const entry = session.pendingApprovals.get(requestId);
    if (!entry) return false;
    session.pendingApprovals.delete(requestId);
    entry.resolve(
      approved
        ? { behavior: 'allow', updatedInput: entry.toolInput ?? {} }
        : { behavior: 'deny', message: 'User denied this tool use.' }
    );
    return true;
  }

  getSdkSessionId(sessionId) {
    const session = this.sessions.get(sessionId);
    return session?.sdkSessionId || null;
  }

  isActive(sessionId) {
    const session = this.sessions.get(sessionId);
    return session?.active ?? false;
  }

  _transformMessage(msg) {
    switch (msg.type) {
      case 'stream_event': {
        const event = msg.event;
        if (!event) return { type: 'unknown', raw: msg };

        switch (event.type) {
          case 'content_block_start': {
            const cb = event.content_block;
            if (cb?.type === 'tool_use') {
              return {
                type: 'tool_use_start',
                toolName: cb.name,
                toolId: cb.id,
                input: {},
                parentToolUseId: msg.parent_tool_use_id || undefined,
              };
            }
            if (cb?.type === 'text') {
              return { type: 'text_start' };
            }
            if (cb?.type === 'thinking') {
              return { type: 'thinking_start' };
            }
            return { type: 'block_start', blockType: cb?.type };
          }

          case 'content_block_delta': {
            const delta = event.delta;
            if (delta?.type === 'text_delta') {
              return { type: 'text_delta', text: delta.text };
            }
            if (delta?.type === 'input_json_delta') {
              return { type: 'tool_input_delta', json: delta.partial_json, parentToolUseId: msg.parent_tool_use_id || undefined };
            }
            if (delta?.type === 'thinking_delta') {
              return { type: 'thinking_delta', text: delta.thinking };
            }
            return { type: 'delta', deltaType: delta?.type };
          }

          case 'content_block_stop': {
            return { type: 'block_stop', index: event.index, parentToolUseId: msg.parent_tool_use_id || undefined };
          }

          case 'message_start':
            return { type: 'message_start' };

          case 'message_delta':
            return {
              type: 'message_delta',
              stopReason: event.delta?.stop_reason,
              usage: event.usage,
            };

          case 'message_stop':
            return { type: 'message_stop' };

          default:
            return { type: 'stream_other', eventType: event.type };
        }
      }

      case 'assistant': {
        const content = msg.message?.content || [];
        const blocks = content.map(block => {
          if (block.type === 'text') {
            return { type: 'text', text: block.text };
          }
          if (block.type === 'tool_use') {
            return {
              type: 'tool_use',
              toolName: block.name,
              toolId: block.id,
              input: block.input,
            };
          }
          if (block.type === 'tool_result') {
            return {
              type: 'tool_result',
              toolId: block.tool_use_id,
              content: block.content,
            };
          }
          return { type: block.type };
        });
        return {
          type: 'assistant_message',
          blocks,
          uuid: msg.uuid,
          sessionId: msg.session_id,
        };
      }

      case 'result': {
        return {
          type: 'result',
          subtype: msg.subtype,
          isError: msg.is_error ?? msg.subtype !== 'success',
          durationMs: msg.duration_ms,
          totalCost: msg.total_cost_usd,
          numTurns: msg.num_turns,
          usage: msg.usage,
          errors: msg.errors,
          sessionId: msg.session_id,
        };
      }

      case 'system': {
        // Subagent lifecycle events
        if (msg.subtype === 'task_started') {
          return {
            type: 'task_started',
            taskId: msg.task_id,
            toolUseId: msg.tool_use_id,
            description: msg.description,
            prompt: msg.prompt,
          };
        }
        if (msg.subtype === 'task_progress') {
          return {
            type: 'task_progress',
            taskId: msg.task_id,
            description: msg.description,
            summary: msg.summary,
          };
        }
        if (msg.subtype === 'task_notification') {
          return {
            type: 'task_complete',
            taskId: msg.task_id,
            toolUseId: msg.tool_use_id,
            status: msg.status,
          };
        }
        return {
          type: 'system',
          subtype: msg.subtype,
          sessionId: msg.session_id,
        };
      }

      case 'tool_use_summary': {
        return {
          type: 'tool_use_summary',
          summary: msg.summary,
          toolUseIds: msg.preceding_tool_use_ids,
        };
      }

      default:
        return { type: msg.type || 'unknown', sessionId: msg.session_id };
    }
  }
}

module.exports = { ChatBridge };
