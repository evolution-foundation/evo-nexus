const express = require('express');
const http = require('http');
const path = require('path');
const WebSocket = require('ws');
const cors = require('cors');
const { v4: uuidv4 } = require('uuid');
const ClaudeBridge = require('./claude-bridge');
const { ChatBridge } = require('./chat-bridge');
const SessionStore = require('./utils/session-store');
const ChatLogger = require('./utils/chat-logger');

class TerminalServer {
  constructor(options = {}) {
    this.port = options.port || 32352;
    this.dev = options.dev || false;
    this.baseFolder = process.cwd();

    this.app = express();
    this.claudeSessions = new Map();
    this.webSocketConnections = new Map();
    this.globalSubscribers = new Set(); // wsIds subscribed to global notifications
    this.claudeBridge = new ClaudeBridge();
    this.chatBridge = new ChatBridge();
    this.sessionStore = new SessionStore();
    this.chatLogger = new ChatLogger(this.baseFolder);
    this.autoSaveInterval = null;
    this.isShuttingDown = false;

    this.setupExpress();
    this.loadPersistedSessions();
    this.setupAutoSave();
  }

  async loadPersistedSessions() {
    try {
      const sessions = await this.sessionStore.loadSessions();
      this.claudeSessions = sessions;
      if (sessions.size > 0) {
        console.log(`Loaded ${sessions.size} persisted sessions`);
      }
    } catch (error) {
      console.error('Failed to load persisted sessions:', error);
    }
  }

  setupAutoSave() {
    this.autoSaveInterval = setInterval(() => {
      this.saveSessionsToDisk();
    }, 30000);

    process.on('SIGINT', () => this.handleShutdown());
    process.on('SIGTERM', () => this.handleShutdown());
    process.on('beforeExit', () => this.saveSessionsToDisk());
  }

  async saveSessionsToDisk() {
    if (this.claudeSessions.size > 0) {
      await this.sessionStore.saveSessions(this.claudeSessions);
    }
  }

  async handleShutdown() {
    if (this.isShuttingDown) return;
    this.isShuttingDown = true;
    console.log('\nGracefully shutting down...');
    await this.saveSessionsToDisk();
    if (this.autoSaveInterval) clearInterval(this.autoSaveInterval);
    this.close();
    process.exit(0);
  }

  isPathWithinBase(targetPath) {
    try {
      const resolvedTarget = path.resolve(targetPath);
      const resolvedBase = path.resolve(this.baseFolder);
      return resolvedTarget.startsWith(resolvedBase);
    } catch {
      return false;
    }
  }

  validatePath(targetPath) {
    if (!targetPath) return { valid: false, error: 'Path is required' };
    const resolvedPath = path.resolve(targetPath);
    if (!this.isPathWithinBase(resolvedPath)) {
      return { valid: false, error: 'Access denied: Path is outside the allowed directory' };
    }
    return { valid: true, path: resolvedPath };
  }

  setupExpress() {
    this.app.use(cors());
    this.app.use(express.json());

    this.app.get('/api/health', (req, res) => {
      res.json({
        status: 'ok',
        claudeSessions: this.claudeSessions.size,
        activeConnections: this.webSocketConnections.size,
      });
    });

    // Find-or-create a session for a specific subagent (e.g. 'oracle')
    this.app.post('/api/sessions/for-agent', (req, res) => {
      const { agentName, workingDir, ticketId, systemPromptExtras } = req.body;
      if (!agentName) {
        return res.status(400).json({ error: 'agentName is required' });
      }

      // Scope reuse by (agentName, ticketId) when ticketId is provided.
      // Without ticketId the old behaviour is preserved (reuse by agentName alone).
      for (const [id, s] of this.claudeSessions.entries()) {
        const agentMatch = s.agentName === agentName;
        const ticketMatch = ticketId ? s.ticketId === ticketId : !s.ticketId;
        if (agentMatch && ticketMatch) {
          return res.json({
            success: true,
            sessionId: id,
            reused: true,
            session: {
              id,
              name: s.name,
              workingDir: s.workingDir,
              active: s.active,
              agentName: s.agentName,
              ticketId: s.ticketId || null,
            },
          });
        }
      }

      let validWorkingDir = this.baseFolder;
      if (workingDir) {
        const validation = this.validatePath(workingDir);
        if (!validation.valid) {
          return res.status(403).json({
            error: validation.error,
            message: 'Cannot create session with working directory outside the allowed area',
          });
        }
        validWorkingDir = validation.path;
      }

      const sessionId = uuidv4();
      const session = {
        id: sessionId,
        name: `${agentName} — ${new Date().toLocaleString()}`,
        created: new Date(),
        lastActivity: new Date(),
        active: false,
        agent: null,
        agentName,
        ticketId: ticketId || null,
        systemPromptExtras: systemPromptExtras || null,
        workingDir: validWorkingDir,
        connections: new Set(),
        outputBuffer: [],
        maxBufferSize: 1000,
      };
      this.claudeSessions.set(sessionId, session);
      this.saveSessionsToDisk();

      res.json({
        success: true,
        sessionId,
        reused: false,
        session: {
          id: sessionId,
          name: session.name,
          workingDir: session.workingDir,
          active: false,
          agentName,
          ticketId: ticketId || null,
        },
      });
    });

    // List all sessions for a given agent
    this.app.get('/api/sessions/by-agent/:agentName', (req, res) => {
      const { agentName } = req.params;
      const sessions = [];
      for (const [id, s] of this.claudeSessions.entries()) {
        if (s.agentName === agentName) {
          // Build preview and find last message timestamp
          let preview = '';
          let lastMessageTs = 0;
          if (Array.isArray(s.chatHistory) && s.chatHistory.length > 0) {
            // Last message timestamp for sorting
            const lastMsg = s.chatHistory[s.chatHistory.length - 1];
            lastMessageTs = lastMsg.ts || 0;
            // Preview from last user message
            for (let i = s.chatHistory.length - 1; i >= 0; i--) {
              if (s.chatHistory[i].role === 'user' && s.chatHistory[i].text) {
                preview = s.chatHistory[i].text.slice(0, 80);
                break;
              }
            }
          }
          sessions.push({
            id,
            name: s.name,
            created: s.created,
            active: s.active,
            agentName: s.agentName,
            ticketId: s.ticketId || null,
            archived: s.archived || false,
            lastActivity: lastMessageTs || (s.lastActivity ? new Date(s.lastActivity).getTime() : 0),
            preview,
            messageCount: Array.isArray(s.chatHistory) ? s.chatHistory.length : 0,
          });
        }
      }
      // Sort by lastActivity descending (most recent first)
      sessions.sort((a, b) => (b.lastActivity || 0) - (a.lastActivity || 0));
      res.json({ sessions });
    });

    // Create a NEW session for an agent (always creates, never reuses)
    this.app.post('/api/sessions/create', (req, res) => {
      const { agentName, workingDir } = req.body;
      if (!agentName) {
        return res.status(400).json({ error: 'agentName is required' });
      }

      let validWorkingDir = this.baseFolder;
      if (workingDir) {
        const validation = this.validatePath(workingDir);
        if (!validation.valid) {
          return res.status(403).json({ error: validation.error });
        }
        validWorkingDir = validation.path;
      }

      // Count existing sessions for this agent to number them
      let count = 0;
      for (const s of this.claudeSessions.values()) {
        if (s.agentName === agentName) count++;
      }

      const sessionId = uuidv4();
      const session = {
        id: sessionId,
        name: `${agentName} #${count + 1}`,
        created: new Date(),
        lastActivity: new Date(),
        active: false,
        agent: null,
        agentName,
        workingDir: validWorkingDir,
        connections: new Set(),
        outputBuffer: [],
        maxBufferSize: 1000,
      };
      this.claudeSessions.set(sessionId, session);
      this.saveSessionsToDisk();

      res.json({
        success: true,
        sessionId,
        session: {
          id: sessionId,
          name: session.name,
          workingDir: session.workingDir,
          active: false,
          agentName,
        },
      });
    });

    this.app.get('/api/sessions/:sessionId', (req, res) => {
      const session = this.claudeSessions.get(req.params.sessionId);
      if (!session) return res.status(404).json({ error: 'Session not found' });
      res.json({
        id: session.id,
        name: session.name,
        created: session.created,
        active: session.active,
        workingDir: session.workingDir,
        connectedClients: session.connections.size,
        lastActivity: session.lastActivity,
        ticketId: session.ticketId || null,
      });
    });

    // Bind a session to a ticket (Feature 1.3 — session binding)
    this.app.post('/api/sessions/:sessionId/ticket', (req, res) => {
      const session = this.claudeSessions.get(req.params.sessionId);
      if (!session) return res.status(404).json({ error: 'Session not found' });
      const { ticketId } = req.body || {};
      // ticketId can be null to unbind
      session.ticketId = ticketId || null;
      session.lastActivity = new Date();
      this.saveSessionsToDisk();
      res.json({ success: true, sessionId: session.id, ticketId: session.ticketId });
    });

    // Rename or archive a session
    this.app.patch('/api/sessions/:sessionId', (req, res) => {
      const session = this.claudeSessions.get(req.params.sessionId);
      if (!session) return res.status(404).json({ error: 'Session not found' });
      const { name, archived } = req.body || {};
      if (name !== undefined) {
        if (typeof name !== 'string' || !name.trim()) {
          return res.status(400).json({ error: 'name must be a non-empty string' });
        }
        session.name = name.trim();
      }
      if (archived !== undefined) {
        session.archived = Boolean(archived);
      }
      session.lastActivity = new Date();
      this.saveSessionsToDisk();
      res.json({
        id: session.id,
        name: session.name,
        archived: session.archived || false,
        lastActivity: session.lastActivity,
      });
    });

    this.app.delete('/api/sessions/:sessionId', (req, res) => {
      const sessionId = req.params.sessionId;
      const session = this.claudeSessions.get(sessionId);
      if (!session) return res.status(404).json({ error: 'Session not found' });

      if (session.active) this.claudeBridge.stopSession(sessionId);

      session.connections.forEach(wsId => {
        const wsInfo = this.webSocketConnections.get(wsId);
        if (wsInfo && wsInfo.ws.readyState === WebSocket.OPEN) {
          wsInfo.ws.send(JSON.stringify({ type: 'session_deleted', message: 'Session has been deleted' }));
          wsInfo.ws.close();
        }
      });

      this.claudeSessions.delete(sessionId);
      this.saveSessionsToDisk();
      res.json({ success: true, message: 'Session deleted' });
    });

    // Return all unresolved permission requests across all active sessions
    this.app.get('/api/notifications/pending', (req, res) => {
      const notifications = [];
      for (const [sessionId, session] of this.claudeSessions.entries()) {
        const bridgeSession = this.chatBridge.sessions.get(sessionId);
        if (!bridgeSession?.pendingApprovals) continue;
        for (const [requestId] of bridgeSession.pendingApprovals.entries()) {
          notifications.push({
            id: `agent_awaiting-${sessionId}-${requestId}`,
            event: 'agent_awaiting',
            sessionId,
            agentName: session.agentName || '',
            toolName: undefined,
            createdAt: Date.now(),
          });
        }
      }
      res.json({ notifications });
    });

    // Chat mode is handled via WebSocket (chat_send / chat_stop messages)
  }

  async start() {
    const server = http.createServer(this.app);

    this.wss = new WebSocket.Server({ server });
    this.wss.on('connection', (ws, req) => this.handleWebSocketConnection(ws, req));

    return new Promise((resolve, reject) => {
      server.listen(this.port, '0.0.0.0', (err) => {
        if (err) return reject(err);
        this.server = server;
        resolve(server);
      });
    });
  }

  handleWebSocketConnection(ws, req) {
    const wsId = uuidv4();
    const url = new URL(req.url, 'ws://localhost');
    const claudeSessionId = url.searchParams.get('sessionId');

    if (this.dev) console.log(`New WebSocket connection: ${wsId}`);

    const wsInfo = { id: wsId, ws, claudeSessionId: null, created: new Date() };
    this.webSocketConnections.set(wsId, wsInfo);

    // Send pending chat history if session is in chat mode
    if (claudeSessionId) {
      const sess = this.claudeSessions.get(claudeSessionId);
      if (sess?.chatHistory?.length > 0) {
        this.sendToWebSocket(ws, { type: 'chat_history', messages: sess.chatHistory });
      }
    }

    ws.on('message', async (message) => {
      try {
        const data = JSON.parse(message);
        await this.handleMessage(wsId, data);
      } catch (error) {
        if (this.dev) console.error('Error handling message:', error);
        this.sendToWebSocket(ws, { type: 'error', message: 'Failed to process message' });
      }
    });

    ws.on('close', () => {
      if (this.dev) console.log(`WebSocket connection closed: ${wsId}`);
      this.cleanupWebSocketConnection(wsId);
    });

    ws.on('error', (error) => {
      if (this.dev) console.error(`WebSocket error for connection ${wsId}:`, error);
      this.cleanupWebSocketConnection(wsId);
    });

    this.sendToWebSocket(ws, { type: 'connected', connectionId: wsId });

    if (claudeSessionId && this.claudeSessions.has(claudeSessionId)) {
      this.joinClaudeSession(wsId, claudeSessionId);
    }
  }

  async handleMessage(wsId, data) {
    const wsInfo = this.webSocketConnections.get(wsId);
    if (!wsInfo) return;

    switch (data.type) {
      case 'subscribe_global':
        this.globalSubscribers.add(wsId);
        break;

      case 'unsubscribe_global':
        this.globalSubscribers.delete(wsId);
        break;

      case 'join_session':
        await this.joinClaudeSession(wsId, data.sessionId);
        break;

      case 'leave_session':
        await this.leaveClaudeSession(wsId);
        break;

      case 'start_claude':
        await this.startClaude(wsId, data.options || {});
        break;

      case 'input':
        if (wsInfo.claudeSessionId) {
          const session = this.claudeSessions.get(wsInfo.claudeSessionId);
          if (session && session.connections.has(wsId) && session.active && session.agent === 'claude') {
            try {
              await this.claudeBridge.sendInput(wsInfo.claudeSessionId, data.data);
            } catch (error) {
              if (this.dev) console.error(`Failed to send input to session ${wsInfo.claudeSessionId}:`, error.message);
              this.sendToWebSocket(wsInfo.ws, {
                type: 'error',
                message: 'Agent is not running in this session. Please start an agent first.',
              });
            }
          }
        }
        break;

      case 'chat_send':
        // Send a chat message via Agent SDK
        if (wsInfo.claudeSessionId) {
          const chatSession = this.claudeSessions.get(wsInfo.claudeSessionId);
          if (chatSession && data.prompt) {
            chatSession.mode = 'chat';
            chatSession.active = true;
            chatSession.lastActivity = new Date();
            if (!chatSession.chatHistory) chatSession.chatHistory = [];

            // --- Rewind handling ---
            if (data.rewindFromUuid) {
              const rewindUuid = data.rewindFromUuid;

              // Find uuid in in-memory cache
              let cutIdx = chatSession.chatHistory.findIndex(m => m.uuid === rewindUuid);

              // Fall back to JSONL scan if cache is cold or uuid not found
              if (cutIdx === -1 && chatSession.agentName) {
                const fromLog = this.chatLogger.read(chatSession.agentName, wsInfo.claudeSessionId);
                cutIdx = fromLog.findIndex(m => m.uuid === rewindUuid);
                if (cutIdx !== -1) {
                  // Sync cache from JSONL
                  chatSession.chatHistory = fromLog;
                  cutIdx = chatSession.chatHistory.findIndex(m => m.uuid === rewindUuid);
                }
              }

              if (cutIdx === -1) {
                // uuid not found — error out without mutating state
                this.sendToWebSocket(wsInfo.ws, {
                  type: 'chat_error',
                  message: `Rewind target not found: ${rewindUuid}`,
                });
                chatSession.active = false;
                break;
              }

              // 1. If a turn is streaming, stop it first
              if (this.chatBridge.isActive(wsInfo.claudeSessionId)) {
                await this.chatBridge.stopSession(wsInfo.claudeSessionId);
              }

              // 2. Null out sdkSessionId so the next turn starts fresh
              chatSession.sdkSessionId = null;
              await this.saveSessionsToDisk();

              // 3. Truncate in-memory cache to everything strictly before rewindUuid
              chatSession.chatHistory = chatSession.chatHistory.slice(0, cutIdx);

              // 4. Append rewind marker to JSONL
              this.chatLogger.appendRewindMarker(chatSession.agentName, wsInfo.claudeSessionId, rewindUuid);
            }
            // --- End rewind handling ---

            // Store user message in history
            const userMsg = {
              role: 'user',
              text: data.prompt,
              files: data.files || undefined,
              ts: Date.now(),
            };
            chatSession.chatHistory.push(userMsg);
            this.chatLogger.append(chatSession.agentName, wsInfo.claudeSessionId, userMsg);

            // Accumulate assistant response for history
            let assistantBlocks = [];
            let isStreaming = false;

            try {
              await this.chatBridge.startSession(wsInfo.claudeSessionId, {
                agentName: chatSession.agentName,
                workingDir: chatSession.workingDir,
                prompt: data.prompt,
                files: data.files,
                sdkSessionId: chatSession.sdkSessionId || undefined,
                systemPromptExtras: chatSession.systemPromptExtras || undefined,
                onMessage: (msg) => {
                  // Track SDK session ID
                  if (msg.type === 'session_id' && msg.sdkSessionId) {
                    chatSession.sdkSessionId = msg.sdkSessionId;
                    return; // Don't forward internal event
                  }

                  // Forward permission requests to the WebSocket client.
                  if (msg.type === 'permission_request') {
                    const permPayload = {
                      type: 'permission_request',
                      sessionId: wsInfo.claudeSessionId,
                      requestId: msg.requestId,
                      toolName: msg.toolName,
                      input: msg.input,
                      title: msg.title || null,
                      description: msg.description || null,
                    };
                    this.broadcastToSession(wsInfo.claudeSessionId, permPayload);
                    // Global notification
                    const createdAt = Date.now();
                    const inputPreview = msg.input
                      ? (typeof msg.input === 'string' ? msg.input : JSON.stringify(msg.input)).slice(0, 80)
                      : undefined;
                    this.broadcastToGlobalSubscribers({
                      type: 'notification',
                      id: `agent_awaiting-${wsInfo.claudeSessionId}-${msg.requestId}`,
                      event: 'agent_awaiting',
                      sessionId: wsInfo.claudeSessionId,
                      agentName: chatSession.agentName || '',
                      toolName: msg.toolName,
                      inputPreview,
                      createdAt,
                    }, wsInfo.claudeSessionId);
                    return;
                  }

                  // Auto-bind session to a ticket when the agent creates one.
                  // Only binds if not already bound — doesn't overwrite user's manual pick.
                  if (msg.type === 'ticket_detected' && msg.ticketId) {
                    if (!chatSession.ticketId) {
                      chatSession.ticketId = msg.ticketId;
                      this.broadcastToSession(wsInfo.claudeSessionId, {
                        type: 'ticket_bound',
                        ticketId: msg.ticketId,
                        auto: true,
                      });
                    }
                    return;
                  }

                  // Build assistant blocks for history
                  if (msg.type === 'text_start' || msg.type === 'message_start') {
                    if (!isStreaming) {
                      isStreaming = true;
                      assistantBlocks = [];
                    }
                  }
                  if (msg.type === 'text_delta') {
                    const last = assistantBlocks[assistantBlocks.length - 1];
                    if (last?.type === 'text') {
                      last.text += msg.text || '';
                    } else {
                      assistantBlocks.push({ type: 'text', text: msg.text || '' });
                    }
                  }
                  if (msg.type === 'tool_use_start') {
                    assistantBlocks.push({
                      type: 'tool_use',
                      toolName: msg.toolName,
                      toolId: msg.toolId,
                      input: '',
                      done: false,
                    });
                  }
                  if (msg.type === 'tool_input_delta') {
                    const last = assistantBlocks[assistantBlocks.length - 1];
                    if (last?.type === 'tool_use') {
                      last.input += msg.json || '';
                    }
                  }
                  if (msg.type === 'block_stop') {
                    const last = assistantBlocks[assistantBlocks.length - 1];
                    if (last?.type === 'tool_use') last.done = true;
                  }

                  // Also broadcast the current assistant blocks for live history
                  this.broadcastToSession(wsInfo.claudeSessionId, { type: 'chat_event', event: msg });
                },
                onError: (err) => {
                  chatSession.active = false;
                  this.broadcastToSession(wsInfo.claudeSessionId, {
                    type: 'chat_error',
                    message: err.message || 'Unknown error',
                  });
                },
                onComplete: (info) => {
                  chatSession.active = false;
                  // Store SDK session ID for future resume
                  if (info?.sdkSessionId) {
                    chatSession.sdkSessionId = info.sdkSessionId;
                  }
                  // Save assistant message to history
                  if (assistantBlocks.length > 0) {
                    const assistantMsg = {
                      role: 'assistant',
                      blocks: assistantBlocks,
                      ts: Date.now(),
                      streaming: false,
                    };
                    chatSession.chatHistory.push(assistantMsg);
                    this.chatLogger.append(chatSession.agentName, wsInfo.claudeSessionId, assistantMsg);
                  }
                  this.saveSessionsToDisk();
                  this.broadcastToSession(wsInfo.claudeSessionId, { type: 'chat_complete' });
                  // Global notification — skip if a client is already watching this session
                  const hasWatcher = chatSession.connections && chatSession.connections.size > 0;
                  if (!hasWatcher) {
                    this.broadcastToGlobalSubscribers({
                      type: 'notification',
                      id: `agent_finished-${wsInfo.claudeSessionId}-${Date.now()}`,
                      event: 'agent_finished',
                      sessionId: wsInfo.claudeSessionId,
                      agentName: chatSession.agentName || '',
                      createdAt: Date.now(),
                    }, wsInfo.claudeSessionId);
                  }
                },
              });
            } catch (err) {
              console.error('[chat_send] Error starting chat session:', err);
              chatSession.active = false;
              this.sendToWebSocket(wsInfo.ws, { type: 'chat_error', message: err.message || String(err) });
            }
          }
        }
        break;

      case 'chat_stop':
        if (wsInfo.claudeSessionId) {
          await this.chatBridge.stopSession(wsInfo.claudeSessionId);
          const s = this.claudeSessions.get(wsInfo.claudeSessionId);
          if (s) s.active = false;
        }
        break;

      case 'permission_response':
        if (wsInfo.claudeSessionId && data.requestId !== undefined) {
          this.chatBridge.respondToApproval(wsInfo.claudeSessionId, data.requestId, !!data.approved);
        }
        break;

      case 'resize':
        if (wsInfo.claudeSessionId) {
          const session = this.claudeSessions.get(wsInfo.claudeSessionId);
          if (session && session.connections.has(wsId) && session.active && session.agent === 'claude') {
            try {
              await this.claudeBridge.resize(wsInfo.claudeSessionId, data.cols, data.rows);
            } catch {
              if (this.dev) console.log(`Resize ignored - agent not active in session ${wsInfo.claudeSessionId}`);
            }
          }
        }
        break;

      case 'stop':
        if (wsInfo.claudeSessionId) {
          await this.stopClaude(wsInfo.claudeSessionId);
        }
        break;

      case 'ping':
        this.sendToWebSocket(wsInfo.ws, { type: 'pong' });
        break;

      default:
        if (this.dev) console.log(`Unknown message type: ${data.type}`);
    }
  }

  async joinClaudeSession(wsId, claudeSessionId) {
    const wsInfo = this.webSocketConnections.get(wsId);
    if (!wsInfo) return;

    const session = this.claudeSessions.get(claudeSessionId);
    if (!session) {
      this.sendToWebSocket(wsInfo.ws, { type: 'error', message: 'Session not found' });
      return;
    }

    if (wsInfo.claudeSessionId) await this.leaveClaudeSession(wsId);

    wsInfo.claudeSessionId = claudeSessionId;
    session.connections.add(wsId);
    session.lastActivity = new Date();
    session.lastAccessed = Date.now();

    // Restore chat history from JSONL logs if session cache is empty
    let chatHistory = session.chatHistory || [];
    if (chatHistory.length === 0 && session.agentName && session.mode === 'chat') {
      const restored = this.chatLogger.read(session.agentName, claudeSessionId);
      if (restored.length > 0) {
        session.chatHistory = restored;
        chatHistory = restored;
        if (this.dev) console.log(`[chat-logger] Restored ${restored.length} messages for session ${claudeSessionId}`);
      }
    }

    this.sendToWebSocket(wsInfo.ws, {
      type: 'session_joined',
      sessionId: claudeSessionId,
      sessionName: session.name,
      workingDir: session.workingDir,
      active: session.active,
      outputBuffer: session.outputBuffer.slice(-200),
      chatHistory,
      ticketId: session.ticketId || null,
    });

    if (this.dev) console.log(`WebSocket ${wsId} joined session ${claudeSessionId}`);
  }

  async leaveClaudeSession(wsId) {
    const wsInfo = this.webSocketConnections.get(wsId);
    if (!wsInfo || !wsInfo.claudeSessionId) return;

    const session = this.claudeSessions.get(wsInfo.claudeSessionId);
    if (session) {
      session.connections.delete(wsId);
      session.lastActivity = new Date();
    }

    wsInfo.claudeSessionId = null;
    this.sendToWebSocket(wsInfo.ws, { type: 'session_left' });
  }

  async startClaude(wsId, options) {
    const wsInfo = this.webSocketConnections.get(wsId);
    if (!wsInfo || !wsInfo.claudeSessionId) {
      this.sendToWebSocket(wsInfo?.ws, { type: 'error', message: 'No session joined' });
      return;
    }

    const session = this.claudeSessions.get(wsInfo.claudeSessionId);
    if (!session) return;

    if (session.active) {
      // Frontend may re-send start_claude on WebSocket reconnect (common
      // through reverse proxies like Traefik). The session is already
      // running — replay the buffer and tell the client it's attached
      // instead of surfacing a misleading error toast.
      this.sendToWebSocket(wsInfo.ws, { type: 'claude_started', sessionId: wsInfo.claudeSessionId });
      return;
    }

    const sessionId = wsInfo.claudeSessionId;

    try {
      // Ensure agent name from session is passed even if options don't include it
      const agentForSession = (options && options.agent) || session.agentName || null;
      if (this.dev) console.log(`Starting agent: ${agentForSession} for session ${sessionId}`);

      console.log(`[startClaude] Agent for session: ${agentForSession}, options.agent: ${options?.agent}`);
      await this.claudeBridge.startSession(sessionId, {
        ...options,
        workingDir: session.workingDir,
        agent: agentForSession,
        onOutput: (data) => {
          const currentSession = this.claudeSessions.get(sessionId);
          if (!currentSession) return;
          currentSession.outputBuffer.push(data);
          if (currentSession.outputBuffer.length > currentSession.maxBufferSize) {
            currentSession.outputBuffer.shift();
          }
          this.broadcastToSession(sessionId, { type: 'output', data });
        },
        onExit: (code, signal) => {
          const currentSession = this.claudeSessions.get(sessionId);
          if (currentSession) currentSession.active = false;
          this.broadcastToSession(sessionId, { type: 'exit', code, signal });
        },
        onError: (error) => {
          const currentSession = this.claudeSessions.get(sessionId);
          if (currentSession) currentSession.active = false;
          this.broadcastToSession(sessionId, { type: 'error', message: error.message });
        },
      });

      session.active = true;
      session.agent = 'claude';
      if (options && options.agent) session.agentName = options.agent;
      session.lastActivity = new Date();
      if (!session.sessionStartTime) session.sessionStartTime = new Date();

      this.broadcastToSession(sessionId, { type: 'claude_started', sessionId });
    } catch (error) {
      if (this.dev) console.error(`Error starting Claude in session ${wsInfo.claudeSessionId}:`, error);
      this.sendToWebSocket(wsInfo.ws, { type: 'error', message: `Failed to start Claude Code: ${error.message}` });
    }
  }

  async stopClaude(claudeSessionId) {
    const session = this.claudeSessions.get(claudeSessionId);
    if (!session || !session.active) return;

    await this.claudeBridge.stopSession(claudeSessionId);
    session.active = false;
    session.agent = null;
    session.lastActivity = new Date();

    this.broadcastToSession(claudeSessionId, { type: 'claude_stopped' });
  }

  sendToWebSocket(ws, data) {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(data));
    }
  }

  broadcastToSession(claudeSessionId, data) {
    const session = this.claudeSessions.get(claudeSessionId);
    if (!session) return;

    session.connections.forEach(wsId => {
      const wsInfo = this.webSocketConnections.get(wsId);
      if (wsInfo && wsInfo.claudeSessionId === claudeSessionId && wsInfo.ws.readyState === WebSocket.OPEN) {
        this.sendToWebSocket(wsInfo.ws, data);
      }
    });
  }

  /**
   * Broadcast a notification to all global subscribers.
   * Skips subscribers that are already watching the originating session
   * (they see events inline and don't need a global notification).
   *
   * @param {object} data - Notification payload
   * @param {string} [originSessionId] - Session that generated the event (used for suppression)
   */
  broadcastToGlobalSubscribers(data, originSessionId) {
    for (const wsId of this.globalSubscribers) {
      const wsInfo = this.webSocketConnections.get(wsId);
      if (!wsInfo || wsInfo.ws.readyState !== WebSocket.OPEN) continue;
      // Suppress if this subscriber is already watching the origin session
      if (originSessionId && wsInfo.claudeSessionId === originSessionId) continue;
      this.sendToWebSocket(wsInfo.ws, data);
    }
  }

  cleanupWebSocketConnection(wsId) {
    const wsInfo = this.webSocketConnections.get(wsId);
    if (!wsInfo) return;

    if (wsInfo.claudeSessionId) {
      const session = this.claudeSessions.get(wsInfo.claudeSessionId);
      if (session) {
        session.connections.delete(wsId);
        session.lastActivity = new Date();
        if (session.connections.size === 0 && this.dev) {
          console.log(`No more connections to session ${wsInfo.claudeSessionId}`);
        }
      }
    }

    this.globalSubscribers.delete(wsId);
    this.webSocketConnections.delete(wsId);
  }

  close() {
    this.saveSessionsToDisk();
    if (this.autoSaveInterval) clearInterval(this.autoSaveInterval);
    if (this.wss) this.wss.close();
    if (this.server) this.server.close();

    for (const [sessionId, session] of this.claudeSessions.entries()) {
      if (session.active) this.claudeBridge.stopSession(sessionId);
    }

    this.claudeSessions.clear();
    this.webSocketConnections.clear();
  }
}

async function startServer(options) {
  const server = new TerminalServer(options);
  return await server.start();
}

module.exports = { startServer, TerminalServer };
