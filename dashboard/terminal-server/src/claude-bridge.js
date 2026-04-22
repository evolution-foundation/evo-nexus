const { spawn } = require('node-pty');
const path = require('path');
const fs = require('fs');

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
    const ALLOWED_CLI = new Set(['claude', 'openclaude']);
    const ALLOWED_VARS = new Set([
      'ANTHROPIC_API_KEY',
      'CLAUDE_CODE_USE_OPENAI', 'CLAUDE_CODE_USE_GEMINI',
      'CLAUDE_CODE_USE_BEDROCK', 'CLAUDE_CODE_USE_VERTEX',
      'OPENAI_BASE_URL', 'OPENAI_API_KEY', 'OPENAI_MODEL',
      // Codex OAuth support (OpenClaude 0.3+ reads ~/.codex/auth.json
      // automatically, but these allow overriding the path or token)
      'CODEX_AUTH_JSON_PATH', 'CODEX_API_KEY',
      'GEMINI_API_KEY', 'GEMINI_MODEL',
      'AWS_REGION', 'AWS_BEARER_TOKEN_BEDROCK',
      'ANTHROPIC_VERTEX_PROJECT_ID', 'CLOUD_ML_REGION',
    ]);

    try {
      // Resolve config relative to this file (src/ → terminal-server/ → dashboard/ → root)
      const workspaceRoot = path.resolve(__dirname, '..', '..', '..');
      const configPath = path.join(workspaceRoot, 'config', 'providers.json');
      if (!fs.existsSync(configPath)) {
        console.log(`[provider] providers.json not found at ${configPath}, using defaults`);
        return { cli_command: 'claude', env_vars: {}, active: 'anthropic' };      }
      const config = JSON.parse(fs.readFileSync(configPath, 'utf8'));
      const active = config.active_provider || 'anthropic';
      const provider = config.providers?.[active] || {};

      let cliCommand = provider.cli_command || 'claude';
      if (!ALLOWED_CLI.has(cliCommand)) {
        console.warn(`[provider] Rejected non-allowlisted CLI: ${cliCommand}, using claude`);
        cliCommand = 'claude';
      }

      const envVars = Object.fromEntries(
        Object.entries(provider.env_vars || {}).filter(
          ([k, v]) => v !== '' && ALLOWED_VARS.has(k)
        )
      );

      // Provider isolation — the active_provider in providers.json is the
      // user's explicit choice between API-key mode ('openai') and OAuth
      // mode ('codex_auth'). Respect it literally:
      //
      //   active === 'codex_auth' → OAuth mode: remove any stale
      //       OPENAI_API_KEY from the provider env so OpenClaude falls
      //       back to ~/.codex/auth.json (the OAuth token source).
      //
      //   active === 'openai'    → API-key mode: keep OPENAI_API_KEY.
      //       Even if ~/.codex/auth.json happens to exist on disk (from
      //       a past OAuth login), the user has chosen API-key mode now.
      //       Previously this branch also deleted the key, which caused
      //       the two cards to bleed into each other on toggle.
      //
      //   anything else          → untouched.
      if (active === 'codex_auth') {
        if ('OPENAI_API_KEY' in envVars) {
          delete envVars['OPENAI_API_KEY'];
          console.log('[provider] codex_auth active — stripping OPENAI_API_KEY, OpenClaude will use ~/.codex/auth.json');
        }
        const codexAuthPath = path.join(process.env.HOME || '/', '.codex', 'auth.json');
        if (!fs.existsSync(codexAuthPath)) {
          console.warn('[provider] codex_auth active but ~/.codex/auth.json is missing — run OAuth login in the Providers page');
        }
      }

      console.log(`[provider] Active provider: ${active} (cli: ${cliCommand})`);
      if (Object.keys(envVars).length > 0) {
        console.log(`[provider] Injecting env vars: ${Object.keys(envVars).join(', ')}`);
      }
      return { cli_command: cliCommand, env_vars: envVars, active };
    } catch (err) {
      console.warn(`[provider] Could not read providers.json: ${err.message}`);
      return { cli_command: 'claude', env_vars: {}, active: 'anthropic' };
    }
  }

  findClaudeCommand(cliCommand = 'claude') {
    const { execSync } = require('child_process');

    // Use shell-based `which` to resolve with full PATH (incl. nvm, fnm, etc.)
    // Hardcoded dispatch to satisfy semgrep — each branch is a literal string
    try {
      let resolved;
      if (cliCommand === 'openclaude') {
        resolved = execSync('which openclaude', { encoding: 'utf8', stdio: ['pipe', 'pipe', 'ignore'] }).trim();
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
    const paths = cliCommand === 'openclaude'
      ? [
          path.join(home, '.local', 'bin', 'openclaude'),
          '/usr/local/bin/openclaude',
          '/usr/bin/openclaude',
        ]
      : [
          path.join(home, '.claude', 'local', 'claude'),
          path.join(home, '.local', 'bin', 'claude'),
          '/usr/local/bin/claude',
          '/usr/bin/claude',
        ];

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

      // Block session if no provider is active
      if (!providerConfig.active || providerConfig.active === 'none') {
        const msg = '\r\n\x1b[1;33mNo AI provider is active.\x1b[0m\r\nGo to \x1b[1;32mProviders\x1b[0m in the dashboard to configure and activate a provider.\r\n';
        if (onOutput) onOutput(msg);
        if (onExit) onExit(1, null);
        return;
      }

      const cliCommand = this.findClaudeCommand(providerConfig.cli_command);

      console.log(`Starting session ${sessionId} with ${providerConfig.cli_command}`);
      console.log(`Command: ${cliCommand}`);
      console.log(`Working directory: ${workingDir}`);
      console.log(`Agent: ${agent || 'none'}`);
      console.log(`Terminal size: ${cols}x${rows}`);
      if (dangerouslySkipPermissions) {
        console.log(`⚠️ WARNING: Skipping permissions with --dangerously-skip-permissions flag`);
      }

      // Don't use --dangerously-skip-permissions when running as root —
      // Claude/OpenClaude block this flag for root users.
      // The trust prompt is auto-accepted via PTY detection below instead.
      const isRoot = process.getuid && process.getuid() === 0;
      const args = (dangerouslySkipPermissions && !isRoot) ? ['--dangerously-skip-permissions'] : [];
      if (agent) {
        args.push('--agent', agent);
      }

      // For non-Anthropic providers, use --system-prompt to force agent persona.
      // --append-system-prompt is too weak — GPT models ignore appended instructions.
      // --system-prompt REPLACES the default system prompt, ensuring the agent persona
      // takes priority over CLAUDE.md and other context that mentions "Claude".
      const active = providerConfig.active || 'anthropic';
      if (active !== 'anthropic' && agent) {
        // Read the agent definition file to build a strong system prompt
        const agentFile = path.join(workingDir, '.claude', 'agents', `${agent}.md`);
        let agentPrompt = '';
        try {
          const content = fs.readFileSync(agentFile, 'utf8');
          // Extract body (after YAML frontmatter ---)
          const match = content.match(/^---\n[\s\S]*?\n---\n([\s\S]*)$/);
          agentPrompt = match ? match[1].trim() : content;
        } catch {
          agentPrompt = `You are the ${agent} agent.`;
        }

        const enforcePrompt = agentPrompt + '\n\n' +
          'CRITICAL: You MUST fully embody this agent persona. ' +
          'You are NOT Claude, OpenClaude, or a generic assistant — you ARE ' + agent + '. ' +
          'When asked who you are, ALWAYS respond as ' + agent + '. ' +
          'Never break character. Follow ALL instructions above.';

        args.push('--system-prompt', enforcePrompt);
      }
      const providerEnv = providerConfig.env_vars || {};

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
      //   codexplan  → GPT-5.4 on Codex backend (high reasoning)
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
        killTimeout: null
      };

      this.sessions.set(sessionId, session);

      // Track if we've seen the trust prompt
      let trustPromptHandled = false;
      let dataBuffer = '';

      claudeProcess.onData((data) => {
        if (process.env.DEBUG) {
          console.log(`Session ${sessionId} output:`, data);
        }

        // Buffer data to check for trust prompt
        dataBuffer += data;
        
        // Check for trust prompt and auto-accept it
        if (!trustPromptHandled && dataBuffer.includes('Do you trust the files in this folder?')) {
          trustPromptHandled = true;
          console.log(`Auto-accepting trust prompt for session ${sessionId}`);
          // The prompt shows "Enter to confirm" which means option 1 is already selected
          // Just send Enter to confirm
          setTimeout(() => {
            claudeProcess.write('\r');
            console.log(`Sent Enter to accept trust prompt for session ${sessionId}`);
          }, 500);
        }
        
        // Clear buffer periodically to prevent memory issues
        if (dataBuffer.length > 10000) {
          dataBuffer = dataBuffer.slice(-5000);
        }
        
        onOutput(data);
      });

      claudeProcess.onExit((exitCode, signal) => {
        console.log(`Claude session ${sessionId} exited with code ${exitCode}, signal ${signal}`);
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
        console.error(`Claude session ${sessionId} error:`, error);
        // Clear kill timeout if process errored
        if (session.killTimeout) {
          clearTimeout(session.killTimeout);
          session.killTimeout = null;
        }
        session.active = false;
        this.sessions.delete(sessionId);
        onError(error);
      });

      console.log(`Claude session ${sessionId} started successfully`);
      return session;

    } catch (error) {
      console.error(`Failed to start Claude session ${sessionId}:`, error);
      throw new Error(`Failed to start Claude Code: ${error.message}`);
    }
  }

  async sendInput(sessionId, data) {
    const session = this.sessions.get(sessionId);
    if (!session || !session.active) {
      throw new Error(`Session ${sessionId} not found or not active`);
    }

    try {
      session.process.write(data);
    } catch (error) {
      throw new Error(`Failed to send input to session ${sessionId}: ${error.message}`);
    }
  }

  async resize(sessionId, cols, rows) {
    const session = this.sessions.get(sessionId);
    if (!session || !session.active) {
      throw new Error(`Session ${sessionId} not found or not active`);
    }

    try {
      session.process.resize(cols, rows);
    } catch (error) {
      console.warn(`Failed to resize session ${sessionId}:`, error.message);
    }
  }

  async stopSession(sessionId) {
    const session = this.sessions.get(sessionId);
    if (!session) {
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