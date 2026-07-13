const { spawn } = require('node-pty');
const cp = require('child_process');
const path = require('path');
const fs = require('fs');

// Workspace root is three levels up from this file (dashboard/terminal-server/src/).
const WORKSPACE_ROOT = path.resolve(__dirname, '..', '..', '..');
const {
  loadProviderConfig,
  resolveProviderModel,
  getProviderMode,
} = require('./provider-config');


function readTerminalTrustMode() {
  try {
    const yaml = fs.readFileSync(path.join(WORKSPACE_ROOT, 'config', 'workspace.yaml'), 'utf8');
    const m = yaml.match(/^chat:\s*\n(?:[ \t]+[^\n]*\n)*?[ \t]+trustMode:\s*(true|false)/m);
    return m ? m[1] === 'true' : false;
  } catch {
    return false;
  }
}

function terminalPromptAcceptInput(buffer) {
  const clean = String(buffer || '').replace(/\x1b\[[0-9;?]*[ -/]*[@-~]/g, '');
  if (/Do you trust the files in this folder\?/i.test(clean)) return '\r';
  // First-run bypass-permissions confirmation dialog (Claude Code /
  // OpenClaude v0.22+): "1. No, exit / 2. Yes, I accept" — accept is option 2.
  if (/\b2\.\s*Yes, I accept/i.test(clean)) return '2\r';
  if (/Do you want to proceed\?/i.test(clean)
    || /Allow (this )?(command|tool|operation)/i.test(clean)
    || /permission (request|required|to use|to run)/i.test(clean)
    || /\b1\.\s*(Yes|Allow|Proceed)/i.test(clean)) {
    return '1\r';
  }
  return null;
}


function isPtyEio(error) {
  return error?.code === 'EIO' || /\bEIO\b|read EIO|write EIO/i.test(error?.message || '');
}

// Max time a single `opencode run` turn may take before we kill it and
// report a timeout instead of leaving the user waiting forever. Provisional
// — Fase 3 burn-in (runtime-harness-agnostic-eval feature folder) should
// replace it with an evidence-based number once real latencies are observed.
const OPENCODE_TURN_TIMEOUT_MS = 120_000;

/**
 * Read an agent's persistent memory (.claude/agent-memory/{agent}/), if any.
 *
 * Native Claude Code sessions get CLAUDE.md + .claude/rules/*.md auto-loaded
 * on startup, including memory-recall.md, which tells the agent to go read
 * its own agent-memory folder before acting. Non-Anthropic providers
 * (openclaude/opencode) don't go through that auto-load — the persona
 * embedding here only injects the agent's own .md file — so without this,
 * every non-Anthropic session starts cold with zero memory of prior
 * sessions. Reads learnings.md (dated lessons) and MEMORY.md (curated
 * summary) when present and concatenates them; learnings.md is append-only
 * so only the tail (most recent entries) is kept to bound prompt size.
 */
function loadAgentMemory(agent) {
  if (!agent) return '';
  const dir = path.join(WORKSPACE_ROOT, '.claude', 'agent-memory', agent);
  const parts = [];
  for (const file of ['learnings.md', 'MEMORY.md']) {
    try {
      const content = fs.readFileSync(path.join(dir, file), 'utf8').trim();
      if (content) parts.push(`### ${file}\n${content}`);
    } catch {
      // File doesn't exist yet — this agent has no persisted memory. Fine.
    }
  }
  if (!parts.length) return '';
  const MAX_CHARS = 4000;
  let combined = parts.join('\n\n');
  if (combined.length > MAX_CHARS) combined = combined.slice(-MAX_CHARS);
  return combined;
}

/**
 * Build the agent persona block (persona .md + enforce-character text +
 * prior-session memory) shared by the openclaude --system-prompt path and
 * the opencode REPL path below.
 */
function buildAgentPersona(agent, workingDir) {
  const rootAgentFile = path.join(WORKSPACE_ROOT, '.claude', 'agents', `${agent}.md`);
  const cwdAgentFile = path.join(workingDir, '.claude', 'agents', `${agent}.md`);
  const agentFile = fs.existsSync(rootAgentFile) ? rootAgentFile : cwdAgentFile;
  let agentPrompt = '';
  let agentTier = null;
  try {
    const content = fs.readFileSync(agentFile, 'utf8');
    const match = content.match(/^---\n[\s\S]*?\n---\n([\s\S]*)$/);
    agentPrompt = match ? match[1].trim() : content;
    for (const marker of ['\n# Persistent Agent Memory', '\n## MEMORY.md']) {
      if (agentPrompt.includes(marker)) {
        agentPrompt = agentPrompt.split(marker, 1)[0].trim();
      }
    }
    const fmMatch = content.match(/^---\n([\s\S]*?)\n---\n/);
    if (fmMatch) {
      const tierMatch = fmMatch[1].match(/^model:\s*["']?([a-z0-9.-]+)["']?\s*$/mi);
      if (tierMatch) agentTier = tierMatch[1].toLowerCase();
    }
  } catch {
    agentPrompt = `You are the ${agent} agent.`;
  }

  const priorMemory = loadAgentMemory(agent);
  if (priorMemory) {
    agentPrompt += '\n\n## Previous Session Memory (yours — resume/summarize before acting)\n' + priorMemory;
  }

  const enforcePrompt = agentPrompt + '\n\n' +
    'CRITICAL: You MUST fully embody this agent persona. ' +
    'You are NOT Claude, OpenClaude, or a generic assistant — you ARE ' + agent + '. ' +
    'When asked who you are, ALWAYS respond as ' + agent + '. ' +
    'Never break character. Follow ALL instructions above.';

  return { prompt: enforcePrompt, agentTier };
}

class ClaudeBridge {
  constructor() {
    this.sessions = new Map();
  }

  /**
   * Load active provider config from config/providers.json.
   * Returns the CLI command to use and env vars to inject.
   * Only allowlisted CLI commands and env var names are accepted.
   */
  _loadProviderConfig() {
    return loadProviderConfig();
  }

  findClaudeCommand(cliCommand = 'claude') {
    const { execSync } = require('child_process');

    // Use shell-based `which` to resolve with full PATH (incl. nvm, fnm, etc.)
    // Hardcoded dispatch to satisfy semgrep — each branch is a literal string
    try {
      let resolved;
      if (cliCommand === 'openclaude') {
        resolved = execSync('which openclaude', { encoding: 'utf8', stdio: ['pipe', 'pipe', 'ignore'] }).trim();
      } else if (cliCommand === 'opencode') {
        resolved = execSync('which opencode', { encoding: 'utf8', stdio: ['pipe', 'pipe', 'ignore'] }).trim();
      } else {
        resolved = execSync('which claude', { encoding: 'utf8', stdio: ['pipe', 'pipe', 'ignore'] }).trim();
      }
      if (resolved) {
        console.log(`[provider] Found ${cliCommand} at: ${resolved}`);
        return resolved;
      }
    } catch {
      // which failed — try hardcoded paths below
    }

    // Fallback: check common hardcoded paths
    const home = process.env.HOME || '/';
    let paths;
    if (cliCommand === 'openclaude') {
      paths = [
        path.join(home, '.local', 'bin', 'openclaude'),
        '/usr/local/bin/openclaude',
        '/usr/bin/openclaude',
      ];
    } else if (cliCommand === 'opencode') {
      paths = [
        path.join(home, '.local', 'bin', 'opencode'),
        '/usr/local/bin/opencode',
        '/usr/bin/opencode',
      ];
    } else {
      paths = [
        path.join(home, '.claude', 'local', 'claude'),
        path.join(home, '.local', 'bin', 'claude'),
        '/usr/local/bin/claude',
        '/usr/bin/claude',
      ];
    }

    for (const p of paths) {
      try {
        if (fs.existsSync(p)) {
          console.log(`[provider] Found ${cliCommand} at hardcoded path: ${p}`);
          return p;
        }
      } catch {
        continue;
      }
    }

    console.error(`[provider] ${cliCommand} not found anywhere, using bare command name`);
    return cliCommand;
  }

  /**
   * True when the CLI has a persisted conversation file for this session id
   * in this workingDir — i.e. a previous run got far enough to save state,
   * so `--resume <id>` will succeed. Checks the config dirs used by both
   * claude (~/.claude) and openclaude (~/.openclaude), plus
   * CLAUDE_CONFIG_DIR when set.
   */
  _hasPersistedConversation(sessionId, workingDir) {
    const home = process.env.HOME || '/';
    const slug = String(workingDir).replace(/[^a-zA-Z0-9]/g, '-');
    const configDirs = [
      process.env.CLAUDE_CONFIG_DIR,
      path.join(home, '.openclaude'),
      path.join(home, '.claude'),
    ].filter(Boolean);
    return configDirs.some((dir) => {
      try {
        return fs.existsSync(path.join(dir, 'projects', slug, `${sessionId}.jsonl`));
      } catch {
        return false;
      }
    });
  }

  async startSession(sessionId, options = {}) {
    if (this.sessions.has(sessionId)) {
      const existing = this.sessions.get(sessionId);
      if (existing.active) {
        // Idempotent: a duplicate startSession can arrive when the WebSocket
        // reconnects through a reverse proxy (Traefik) and the frontend
        // re-sends start_claude before learning the session is still alive.
        // Returning the existing session instead of throwing prevents a
        // confusing "Session already exists" toast on the user's terminal
        // while keeping the original PTY intact.
        console.log(`[bridge] startSession(${sessionId}) — already active, returning existing session`);
        return existing;
      }
      // Orphaned dead session — clean up and restart
      if (existing.process) {
        try { existing.process.kill('SIGKILL'); } catch (_) {}
      }
      if (existing.currentChild) {
        try { existing.currentChild.kill('SIGKILL'); } catch (_) {}
      }
      this.sessions.delete(sessionId);
    }

    const {
      workingDir = process.cwd(),
      dangerouslySkipPermissions = false,
      agent = null,
      onOutput = () => {},
      onExit = () => {},
      onError = () => {},
      cols = 80,
      rows = 24
    } = options;

    try {
      // Reload provider config fresh on every session start
      // so switching provider in the dashboard takes effect immediately
      const providerConfig = this._loadProviderConfig();
      const providerMode = getProviderMode(providerConfig);
      const providerModel = resolveProviderModel(providerConfig);

      // Block session if no provider is active
      if (!providerConfig.active || providerConfig.active === 'none') {
        const msg = '\r\n\x1b[1;33mNo AI provider is active.\x1b[0m\r\nGo to \x1b[1;32mProviders\x1b[0m in the dashboard to configure and activate a provider.\r\n';
        if (onOutput) onOutput(msg);
        if (onExit) onExit(1, null);
        return;
      }
      if (providerConfig.active !== 'anthropic' && providerMode !== 'code') {
        throw new Error(
          `Provider "${providerConfig.active}" com modelo "${providerModel || 'não definido'}" está em modo Chat Completion/Memory Output. Use o Chat para esse modelo. O Terminal aceita apenas modelos Code.`
        );
      }

      // opencode's own interactive TUI (default full-screen and --mini) don't
      // render reliably inside this embedded pty — confirmed live on the VPS
      // (2026-07-13, see runtime-harness-agnostic-eval feature folder):
      // default mode overlaps frames from resize/redraw, --mini hides
      // response text in a self-overwriting status area. Drive it headlessly
      // instead — `opencode run --format json` + NDJSON parsing, the same
      // approach already proven in provider_fallback.py for every heartbeat
      // — and render the text ourselves.
      if (providerConfig.cli_command === 'opencode') {
        return this._startOpencodeReplSession(sessionId, {
          workingDir, agent, onOutput, onExit, onError,
        }, providerConfig.active || 'anthropic', providerModel);
      }

      const cliCommand = this.findClaudeCommand(providerConfig.cli_command);

      console.log(`Starting session ${sessionId} with ${providerConfig.cli_command}`);
      console.log(`Command: ${cliCommand}`);
      console.log(`Working directory: ${workingDir}`);
      console.log(`Agent: ${agent || 'none'}`);
      console.log(`Terminal size: ${cols}x${rows}`);
      const terminalTrustMode = dangerouslySkipPermissions || readTerminalTrustMode();
      if (terminalTrustMode) {
        console.log(`⚠️ WARNING: Terminal trust mode enabled`);
      }

      // Claude Code and OpenClaude v0.22+ refuse --dangerously-skip-permissions
      // as root unless IS_SANDBOX=1 marks a containerized environment — the
      // --allow-dangerously-skip-permissions flag does NOT lift that check (it
      // only makes bypass mode available as an option). So always pass the skip
      // flag in trust mode and inject IS_SANDBOX=1 into the child env when
      // running as root (the clean-env whitelist below would otherwise drop it).
      const isRoot = process.getuid && process.getuid() === 0;
      const active = providerConfig.active || 'anthropic';
      const args = [];
      let agentTier = null;

      if (terminalTrustMode) {
        args.push('--dangerously-skip-permissions');
        if (isRoot) {
          console.log('[permissions] Running as root in trust mode — injecting IS_SANDBOX=1 for the CLI root check');
        }
      }
      if (agent && active === 'anthropic') {
        args.push('--agent', agent);
      }

      // For non-Anthropic providers, use --system-prompt to force agent persona.
      // --append-system-prompt is too weak — GPT models ignore appended instructions.
      // --system-prompt REPLACES the default system prompt, ensuring the agent persona
      // takes priority over CLAUDE.md and other context that mentions "Claude".
      if (active !== 'anthropic' && agent) {
        const persona = buildAgentPersona(agent, workingDir);
        agentTier = persona.agentTier;
        args.push('--system-prompt', persona.prompt);
      }

      // Pin the CLI conversation to the terminal-server session UUID so a
      // crash, provider error, or terminal-server restart doesn't lose the
      // conversation: the first start registers the id with --session-id,
      // and any later start of the same session resumes it with --resume.
      const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
      if (UUID_RE.test(sessionId)) {
        if (this._hasPersistedConversation(sessionId, workingDir)) {
          console.log(`[bridge] Resuming persisted conversation for session ${sessionId}`);
          args.push('--resume', sessionId);
        } else {
          args.push('--session-id', sessionId);
        }
      }

      // Copy so per-session overrides don't leak into the shared config object.
      const providerEnv = { ...(providerConfig.env_vars || {}) };

      // Per-agent model tier: agents declare model: opus|sonnet|haiku in
      // their frontmatter; providers.json maps each tier to a provider model
      // via the provider's "model_tiers" field.
      const tierModel = agentTier && providerConfig.model_tiers
        ? providerConfig.model_tiers[agentTier]
        : null;
      if (active !== 'anthropic' && tierModel) {
        providerEnv['OPENAI_MODEL'] = tierModel;
        console.log(`[provider] Agent ${agent} tier "${agentTier}" → model ${tierModel}`);
      }

      // Automatic model fallback when the primary is overloaded. The CLI
      // accepts a single fallback — pass the first configured entry that
      // differs from the primary model.
      const fallbackModels = Array.isArray(providerConfig.fallback_models)
        ? providerConfig.fallback_models
        : [];
      if (active !== 'anthropic') {
        const primary = providerEnv['OPENAI_MODEL'] || '';
        const fallback = fallbackModels.find((m) => m && m !== primary);
        if (fallback) {
          args.push('--fallback-model', fallback);
        }
      }

      // Build a CLEAN environment for the spawned CLI process.
      // We DON'T spread process.env — it may contain stale/cached vars
      // (OPENAI_API_KEY, etc.) that override Codex OAuth auth.json.
      // Instead, whitelist only essential system vars + provider config.
      const SYSTEM_VARS = [
        'HOME', 'USER', 'SHELL', 'PATH', 'LANG', 'LC_ALL', 'LC_CTYPE',
        'LOGNAME', 'HOSTNAME', 'XDG_RUNTIME_DIR', 'XDG_DATA_HOME',
        'XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'TMPDIR',
        'SSH_AUTH_SOCK', 'SSH_AGENT_PID',
        'NVM_DIR', 'NVM_BIN', 'NVM_INC',
        'CODEX_HOME', 'CLAUDE_CONFIG_DIR',
        // Container marker exported by entrypoint.sh — required for
        // --dangerously-skip-permissions to work as root.
        'IS_SANDBOX',
      ];
      const cleanEnv = {};
      for (const key of SYSTEM_VARS) {
        if (process.env[key]) cleanEnv[key] = process.env[key];
      }

      // Ensure OPENAI_MODEL is set when using an OpenAI-based provider.
      // OpenClaude's Codex mode requires 'codexplan' or 'codexspark' aliases
      // to route to the Codex backend — a raw 'gpt-5.x' falls back to the
      // regular chat completions API, which bypasses Codex OAuth entirely.
      //
      //   codexplan  → GPT-5.5 on Codex backend (high reasoning)
      //   codexspark → GPT-5.3 Codex Spark (faster)
      //
      // For the plain 'openai' provider (API key mode), default to gpt-4.1.
      if (!providerEnv['OPENAI_MODEL']) {
        if (active === 'codex_auth') {
          providerEnv['OPENAI_MODEL'] = 'codexplan';
          console.log('[provider] OPENAI_MODEL not set — defaulting to codexplan (Codex OAuth)');
        } else if (active === 'openai') {
          providerEnv['OPENAI_MODEL'] = 'gpt-4.1';
          console.log('[provider] OPENAI_MODEL not set — defaulting to gpt-4.1');
        }
      }

      console.log(`[spawn] Args: ${JSON.stringify(args)}`);
      const claudeProcess = spawn(cliCommand, args, {
        cwd: workingDir,
        env: {
          ...cleanEnv,
          ...providerEnv,
          // Lift the CLI's root/sudo guard for --dangerously-skip-permissions
          // inside the container (see comment above spawn args).
          ...(terminalTrustMode && isRoot ? { IS_SANDBOX: '1' } : {}),
          // O auto-updater do CLI migra a instalação npm pro instalador
          // nativo no meio da sessão e mata o processo com exit 1
          // ("OpenClaude has switched from npm to the native installer").
          DISABLE_AUTOUPDATER: '1',
          TERM: 'xterm-256color',
          FORCE_COLOR: '1',
          COLORTERM: 'truecolor'
        },
        cols,
        rows,
        name: 'xterm-color'
      });

      const session = {
        process: claudeProcess,
        workingDir,
        created: new Date(),
        active: true,
        killTimeout: null,
        // Tracks whether onExit has already fired so the late-arriving
        // 'error' (EIO) event from node-pty doesn't double-fire onExit
        // or surface a misleading "read EIO" toast to the user.
        exited: false,
      };

      this.sessions.set(sessionId, session);

      let dataBuffer = '';
      let lastAutoAcceptAt = 0;

      claudeProcess.onData((data) => {
        if (process.env.DEBUG) {
          console.log(`Session ${sessionId} output:`, data);
        }

        // Buffer data to check for interactive trust / permission prompts.
        dataBuffer += data;

        const acceptInput = terminalTrustMode ? terminalPromptAcceptInput(dataBuffer) : null;
        if (acceptInput) {
          const now = Date.now();
          if (now - lastAutoAcceptAt > 1200) {
            lastAutoAcceptAt = now;
            dataBuffer = '';
            console.log(`Auto-accepting terminal permission prompt for session ${sessionId}`);
            setTimeout(() => {
              claudeProcess.write(acceptInput);
            }, 250);
          }
        }

        // Clear buffer periodically to prevent memory issues
        if (dataBuffer.length > 10000) {
          dataBuffer = dataBuffer.slice(-5000);
        }

        onOutput(data);
      });

      claudeProcess.onExit((exitCode, signal) => {
        // node-pty 1.1.0+ passes exitCode as an object {exitCode, signal}
        // in some code paths. Normalize so the rest of the pipeline always
        // sees a number (or null).
        if (exitCode && typeof exitCode === 'object') {
          signal = exitCode.signal != null ? exitCode.signal : signal;
          exitCode = exitCode.exitCode != null ? exitCode.exitCode : exitCode;
        }
        console.log(`Claude session ${sessionId} exited with code ${exitCode}, signal ${signal}`);
        // Mark as exited so the late-arriving 'error' (EIO) handler
        // knows the exit has already been reported and stays silent.
        session.exited = true;
        // Clear kill timeout if process exited naturally
        if (session.killTimeout) {
          clearTimeout(session.killTimeout);
          session.killTimeout = null;
        }
        session.active = false;
        this.sessions.delete(sessionId);
        onExit(exitCode, signal);
      });

      claudeProcess.on('error', (error) => {
        if (isPtyEio(error)) {
          // EIO on the master side of the PTY is a known artifact when the
          // child process has already exited — node-pty's read loop hits
          // the closed slave FD and emits 'error' before (or alongside)
          // the 'exit' event. If onExit already fired, we're done — don't
          // double-report. If not, treat it as a normal exit (code 1,
          // signal null) instead of an error so the frontend shows a
          // clean "[Process exited]" rather than a scary "read EIO".
          if (session.exited) {
            console.warn(`Claude session ${sessionId} EIO after exit — suppressed`);
            return;
          }
          console.warn(`Claude session ${sessionId} PTY closed with EIO; treating as exit`);
        } else {
          console.error(`Claude session ${sessionId} error:`, error);
        }
        // Clear kill timeout if process errored
        if (session.killTimeout) {
          clearTimeout(session.killTimeout);
          session.killTimeout = null;
        }
        session.exited = true;
        session.active = false;
        this.sessions.delete(sessionId);
        if (isPtyEio(error)) {
          // Signal null (not 'EIO') so the frontend doesn't restart-loop
          onExit(1, null);
        } else {
          onError(error);
        }
      });

      console.log(`Claude session ${sessionId} started successfully`);
      return session;

    } catch (error) {
      console.error(`Failed to start Claude session ${sessionId}:`, error);
      throw new Error(`Failed to start Claude Code: ${error.message}`);
    }
  }

  /**
   * Start an opencode session as a headless REPL instead of an interactive
   * pty process. No long-lived child process — each submitted line spawns
   * its own `opencode run` call (see _runOpencodeTurn), so there's no CLI
   * "process" for this session to crash or hang mid-conversation; a turn
   * that fails or times out just reports an error and leaves the session
   * ready for the next message.
   */
  async _startOpencodeReplSession(sessionId, options, providerId, providerModel) {
    const { workingDir, agent, onOutput, onExit, onError } = options;
    const cliBin = this.findClaudeCommand('opencode');

    let personaPrefix = null;
    if (agent) {
      const persona = buildAgentPersona(agent, workingDir);
      personaPrefix = persona.prompt;
    }

    const session = {
      workingDir,
      created: new Date(),
      active: true,
      exited: false,
      isOpencode: true,
      process: null,
      currentChild: null,
      busy: false,
      lineBuffer: '',
      opencodeSessionId: null,
      personaPrefix,
      providerId,
      providerModel: providerModel || 'auto',
      cliBin,
      onOutput,
      onExit,
      onError,
    };
    this.sessions.set(sessionId, session);

    onOutput(
      `\x1b[36mopencode (${providerId}/${session.providerModel}) — modo REPL headless.\x1b[0m\r\n` +
      `Digite sua mensagem e pressione Enter.\r\n\r\n`
    );

    console.log(`[bridge] opencode REPL session ${sessionId} ready (agent: ${agent || 'none'})`);
    return session;
  }

  /**
   * Build the environment for an `opencode run` child process. Mirrors the
   * SYSTEM_VARS whitelist used for the pty-spawned CLIs above — no full
   * process.env spread, so a stale OPENAI_API_KEY etc. can't hijack the
   * call — plus OMNIROUTE_SPIKE_API_KEY, which opencode.json resolves via
   * {env:OMNIROUTE_SPIKE_API_KEY} for the opencode_omnirouter provider.
   */
  _buildOpencodeEnv() {
    const SYSTEM_VARS = [
      'HOME', 'USER', 'SHELL', 'PATH', 'LANG', 'LC_ALL', 'LC_CTYPE',
      'LOGNAME', 'HOSTNAME', 'XDG_RUNTIME_DIR', 'XDG_DATA_HOME',
      'XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'TMPDIR',
      'SSH_AUTH_SOCK', 'SSH_AGENT_PID',
      'NVM_DIR', 'NVM_BIN', 'NVM_INC',
      'CODEX_HOME', 'CLAUDE_CONFIG_DIR', 'IS_SANDBOX',
      'OMNIROUTE_SPIKE_API_KEY',
    ];
    const env = {};
    for (const key of SYSTEM_VARS) {
      if (process.env[key]) env[key] = process.env[key];
    }
    env.DISABLE_AUTOUPDATER = '1';
    return env;
  }

  /**
   * Handle raw keystrokes for an opencode REPL session: echo printable
   * characters, support backspace and Ctrl+C (clears the pending line —
   * can't interrupt an in-flight turn without killing it), and submit the
   * buffered line on Enter. No cursor movement / history — MVP REPL, not a
   * full line editor.
   */
  _handleOpencodeKeystrokes(sessionId, session, data) {
    // xterm sends escape sequences (arrows, function keys, etc.) as a
    // single multi-byte chunk starting with ESC. Swallow the whole thing
    // instead of leaking its raw bytes into the message buffer.
    if (data.length > 1 && data.charCodeAt(0) === 0x1b) return;

    for (const ch of data) {
      if (ch === '\r' || ch === '\n') {
        session.onOutput('\r\n');
        const line = session.lineBuffer;
        session.lineBuffer = '';
        if (!line.trim()) continue;
        if (session.busy) {
          session.onOutput('\x1b[33m(ainda processando a mensagem anterior — aguarde)\x1b[0m\r\n');
          continue;
        }
        this._runOpencodeTurn(sessionId, session, line);
      } else if (ch === '\x7f' || ch === '\b') {
        if (session.lineBuffer.length > 0) {
          session.lineBuffer = session.lineBuffer.slice(0, -1);
          session.onOutput('\b \b');
        }
      } else if (ch === '\x03') {
        session.lineBuffer = '';
        session.onOutput('^C\r\n');
      } else if (ch >= ' ') {
        session.lineBuffer += ch;
        session.onOutput(ch);
      }
    }
  }

  /**
   * Run one `opencode run` turn headlessly and stream its NDJSON `text`
   * events into session.onOutput as they arrive. Same event shape already
   * parsed in provider_fallback.py::_parse_opencode_ndjson — kept as a
   * separate JS implementation since these two bridges don't share a util
   * module today.
   */
  _runOpencodeTurn(sessionId, session, message) {
    session.busy = true;

    let fullMessage = message;
    if (session.personaPrefix) {
      fullMessage = `${session.personaPrefix}\n\n---\n\nTask:\n${message}`;
      session.personaPrefix = null; // embed only once — -s keeps the rest of the context
    }

    const modelRef = `${session.providerId}/${session.providerModel}`;
    // No --agent flag — defaults to opencode's "build" agent, which is what
    // the original spike validated for real tool-use (bash calls + reported
    // text). The zero-token/no-response symptom seen live on 2026-07-13
    // turned out to be unrelated to agent choice (--agent plan didn't fix
    // it either) — root cause was the OmniRoute "auto" combo routing to a
    // stuck "auggie" candidate (see runtime-harness-agnostic-eval feature
    // folder + omniroute-gateway memory). Fixed on the gateway side by
    // removing "auggie" from the auto/* combo pools.
    const args = ['run', fullMessage, '-m', modelRef, '--format', 'json', '--auto'];
    if (session.opencodeSessionId) {
      args.push('-s', session.opencodeSessionId);
    }

    session.onOutput('\x1b[90m…\x1b[0m');

    let child;
    try {
      child = cp.spawn(session.cliBin, args, {
        cwd: session.workingDir,
        env: this._buildOpencodeEnv(),
        // Node leaves a child's stdin as an open, never-EOF'd pipe by
        // default — opencode blocks reading it before ever calling the
        // model, so the call just hangs forever with no output, no exit,
        // no error (confirmed locally, 2026-07-13: identical spawn args
        // via bash with stdin inherited from a real TTY complete in
        // under a second; the same spawn() call in Node without this
        // hangs indefinitely). `run` never reads stdin for anything we
        // use here, so closing it costs nothing.
        stdio: ['ignore', 'pipe', 'pipe'],
      });
    } catch (error) {
      session.busy = false;
      session.onOutput(`\r\x1b[K\r\n\x1b[31m[opencode] falha ao executar: ${error.message}\x1b[0m\r\n\r\n`);
      return;
    }
    session.currentChild = child;

    let stdoutBuffer = '';
    let stderrBuffer = '';
    let sawText = false;
    let sawError = false;
    let errorMessage = '';

    const killTimer = setTimeout(() => {
      console.warn(`[bridge] opencode turn for session ${sessionId} exceeded ${OPENCODE_TURN_TIMEOUT_MS}ms — killing`);
      try { child.kill('SIGKILL'); } catch (_) {}
    }, OPENCODE_TURN_TIMEOUT_MS);

    const processLine = (rawLine) => {
      const line = rawLine.trim();
      if (!line) return;
      let event;
      try {
        event = JSON.parse(line);
      } catch {
        return;
      }
      const sid = event.sessionID || event.session_id;
      if (sid && !session.opencodeSessionId) {
        session.opencodeSessionId = sid;
      }
      if (event.type === 'text') {
        const text = event.part && event.part.text;
        if (text) {
          if (!sawText) {
            session.onOutput('\r\x1b[K'); // clear the "…" placeholder once real text starts
            sawText = true;
          }
          session.onOutput(text);
        }
      } else if (event.type === 'error') {
        sawError = true;
        const err = event.error || {};
        errorMessage = (err.data && err.data.message) || err.name || 'erro desconhecido';
      }
    };

    child.stdout.setEncoding('utf8');
    child.stdout.on('data', (chunk) => {
      stdoutBuffer += chunk;
      let idx;
      while ((idx = stdoutBuffer.indexOf('\n')) !== -1) {
        processLine(stdoutBuffer.slice(0, idx));
        stdoutBuffer = stdoutBuffer.slice(idx + 1);
      }
    });

    child.stderr.setEncoding('utf8');
    child.stderr.on('data', (chunk) => {
      stderrBuffer += chunk;
    });

    child.on('close', (code) => {
      clearTimeout(killTimer);
      if (stdoutBuffer.trim()) processLine(stdoutBuffer);
      session.currentChild = null;
      session.busy = false;

      if (!sawText) session.onOutput('\r\x1b[K'); // clear the "…" if nothing textual ever came

      if (sawError) {
        session.onOutput(`\r\n\x1b[31m[opencode] ${errorMessage}\x1b[0m\r\n`);
      } else if (code !== 0) {
        const stderrTail = stderrBuffer.trim().slice(0, 300);
        session.onOutput(`\r\n\x1b[31m[opencode] processo saiu com código ${code}${stderrTail ? ': ' + stderrTail : ''}\x1b[0m\r\n`);
      } else if (!sawText) {
        session.onOutput('\r\n\x1b[33m[opencode] sem resposta de texto nesse turno.\x1b[0m\r\n');
      }
      session.onOutput('\r\n');
    });

    child.on('error', (error) => {
      clearTimeout(killTimer);
      session.currentChild = null;
      session.busy = false;
      session.onOutput(`\r\x1b[K\r\n\x1b[31m[opencode] falha ao executar: ${error.message}\x1b[0m\r\n\r\n`);
    });
  }

  async sendInput(sessionId, data) {
    const session = this.sessions.get(sessionId);
    if (!session || !session.active) {
      throw new Error(`Session ${sessionId} not found or not active`);
    }

    if (session.isOpencode) {
      this._handleOpencodeKeystrokes(sessionId, session, data);
      return true;
    }

    try {
      session.process.write(data);
      return true;
    } catch (error) {
      if (isPtyEio(error)) {
        // Process already exited — don't surface EIO; treat as silent exit
        if (!session.exited) {
          session.exited = true;
        }
        session.active = false;
        this.sessions.delete(sessionId);
        return false;
      }
      throw new Error(`Failed to send input to session ${sessionId}: ${error.message}`);
    }
  }

  async resize(sessionId, cols, rows) {
    const session = this.sessions.get(sessionId);
    if (!session || !session.active) {
      throw new Error(`Session ${sessionId} not found or not active`);
    }

    if (session.isOpencode) return; // no real pty to resize in REPL mode

    try {
      session.process.resize(cols, rows);
    } catch (error) {
      if (isPtyEio(error)) {
        session.active = false;
        this.sessions.delete(sessionId);
        return;
      }
      console.warn(`Failed to resize session ${sessionId}:`, error.message);
    }
  }

  async stopSession(sessionId) {
    const session = this.sessions.get(sessionId);
    if (!session) {
      return;
    }

    if (session.isOpencode) {
      if (session.currentChild) {
        try { session.currentChild.kill('SIGKILL'); } catch (_) {}
      }
      session.active = false;
      this.sessions.delete(sessionId);
      return;
    }

    try {
      // Clear any existing kill timeout
      if (session.killTimeout) {
        clearTimeout(session.killTimeout);
        session.killTimeout = null;
      }

      if (session.active && session.process) {
        session.process.kill('SIGTERM');

        session.killTimeout = setTimeout(() => {
          if (session.active && session.process) {
            session.process.kill('SIGKILL');
          }
        }, 5000);
      }
    } catch (error) {
      console.warn(`Error stopping session ${sessionId}:`, error.message);
    }

    session.active = false;
    this.sessions.delete(sessionId);
  }

  getSession(sessionId) {
    return this.sessions.get(sessionId);
  }

  getAllSessions() {
    return Array.from(this.sessions.entries()).map(([id, session]) => ({
      id,
      workingDir: session.workingDir,
      created: session.created,
      active: session.active
    }));
  }

  async cleanup() {
    const sessionIds = Array.from(this.sessions.keys());
    for (const sessionId of sessionIds) {
      await this.stopSession(sessionId);
    }
  }

}

module.exports = ClaudeBridge;
