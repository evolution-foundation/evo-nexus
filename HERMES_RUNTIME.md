# Hermes Runtime Integration Guide

## Overview

EvoNexus now supports **Hermes Agent** as a runtime provider alongside Claude Code and OpenClaude. This allows you to run ADWs (Agent-Driven Workflows) using Hermes instead of the Claude CLI, maintaining full compatibility with your existing workflows.

## Architecture

```
EvoNexus ADWs
    ↓
ADWs/runner.py (provider-agnostic)
    ↓
hermes_adapter.py (wrapper)
    ↓
hermes chat (actual runtime)
    ↓
Provider (OpenRouter, Anthropic, OpenAI, etc.)
```

## Key Components

### 1. `ADWs/runner.py`
- Main runner that executes ADWs
- Provider-agnostic: uses the same interface for Claude, OpenClaude, and Hermes
- Now uses `hermes_adapter.py` when `cli_command == "hermes"`
- Outputs consistent JSON format for all providers

### 2. `ADWs/hermes_adapter.py`
- CLI adapter that makes Hermes compatible with Claude Code interface
- Accepts Claude Code flags (`--print`, `--output-format json`, `--agent`)
- Translates them to Hermes equivalents (`-q`, `--skills`)
- Returns Claude Code-compatible JSON output

### 3. `config/providers.json`
- Configuration for all AI providers
- Hermes provider pre-configured with sensible defaults
- Env vars for Hermes provider, model, and API keys

## Provider Comparison

| Feature | Claude Code | OpenClaude | Hermes |
|---------|-------------|------------|--------|
| CLI binary | `claude` | `openclaude` | `hermes` (via adapter) |
| JSON output | Native | Native | Via adapter |
| --print flag | ✓ | ✓ | ✓ (adapted) |
| --output-format json | ✓ | ✓ | ✓ (adapted) |
| --agent flag | ✓ | ✓ | ✓ (→ --skills) |
| Max turns | --max-turns | --max-turns | `HERMES_MAX_ITERATIONS` |
| Fallback | None | None | Yes (configurable) |
| Multi-provider | No | Yes (OpenRouter) | Yes (native) |
| Cost tracking | Yes | Yes | Yes (via adapter) |

## Configuration

### Setting Hermes as Active Provider

Edit `config/providers.json`:

```json
{
  "active_provider": "hermes",
  "providers": {
    "hermes": {
      "name": "Hermes Agent",
      "description": "Hermes Agent — agente AI multi-provedor",
      "cli_command": "hermes",
      "env_vars": {
        "HERMES_MODEL": "anthropic/claude-sonnet-4",
        "OPENROUTER_API_KEY": "[REDACTED]",
        "ANTHROPIC_API_KEY": "[REDACTED]",
        "HERMES_MAX_ITERATIONS": "30"
      }
    }
  }
}
```

### Environment Variables

Hermes provider supports the following env vars:

| Variable | Purpose | Example |
|----------|---------|---------|
| `HERMES_MODEL` | Default model | `anthropic/claude-sonnet-4`, `gpt-4.1` |
| `OPENROUTER_API_KEY` | OpenRouter API key | `[REDACTED]` |
| `ANTHROPIC_API_KEY` | Anthropic API key | `[REDACTED]` |
| `DEEPSEEK_API_KEY` | DeepSeek API key | `[REDACTED]` |
| `HERMES_MAX_ITERATIONS` | Maximum agent loop iterations | `30` |

### Provider-Specific Configuration

#### OpenRouter via Hermes

```json
{
  "hermes": {
    "env_vars": {
      "HERMES_MODEL": "anthropic/claude-sonnet-4",
      "OPENROUTER_API_KEY": "[REDACTED]"
    }
  }
}
```

#### Anthropic via Hermes

```json
{
  "hermes": {
    "env_vars": {
      "HERMES_MODEL": "claude-sonnet-4",
      "ANTHROPIC_API_KEY": "[REDACTED]"
    }
  }
}
```

## Usage in ADWs

### Running a Routine with Hermes

```python
from ADWs.runner import run_claude, run_skill

# Simple prompt
result = run_claude("Analyze this data", log_name="data-analysis")

# With agent/skills
result = run_claude(
    "Execute the monthly review",
    log_name="monthly-review",
    agent="financial-analyst"
)

# Run a skill
result = run_skill(
    "weekly-report",
    args="--month october",
    log_name="weekly-report",
    agent="report-generator",
    notify_telegram=True
)
```

### Rollout and fallback policy

`config/runtime-rollout.json` controls workflow-level `off`, `shadow`, `canary`,
and `default` modes. `HERMES_KILL_SWITCH=1` forces the safe Claude path for new
policy decisions. A failed subprocess is recorded as failed; EvoNexus does not
silently replay a failed side-effecting action with another provider.

## Dashboard Integration

The EvoNexus dashboard now supports Hermes provider:

1. **Provider Management** (`/api/providers`)
   - View all providers including Hermes
   - See installation status and version
   - Check if env vars are configured

2. **Active Provider Switching**
   - Switch between providers without code changes
   - Dashboard UI shows current active provider

3. **Provider Testing**
   - Test Hermes installation via `/api/providers/hermes/test`
   - Verify env var configuration

4. **Scoped Control API** (`/api/control/v1/*`)
   - Requires `HERMES_CONTROL_API_TOKEN`; token rotation may temporarily accept
     `HERMES_CONTROL_API_TOKEN_PREVIOUS`
   - Denies every operation unless its exact scope is listed in
     `HERMES_CONTROL_API_SCOPES`
   - Supported scopes: `health:read`, `projects:read`, `tickets:read`,
     `tickets:write`, `comments:write`, `evidence:write`, `goals:read`,
     `goals:write`, `heartbeats:read`, `heartbeats:write`, `heartbeats:run`,
     `routines:read`, `routines:run`, `scheduled_tasks:read`,
     `scheduled_tasks:write`, and `scheduled_tasks:run`

## Troubleshooting

### Hermes Not Found

**Error**: `'hermes' not found in PATH`

**Solution**: Install Hermes Agent with `uv`:
```bash
uv tool install hermes-agent
```

See the current installation instructions at
https://hermes-agent.nousresearch.com/docs/getting-started/installation.

### JSON Parse Errors

**Error**: `JSONDecodeError` when running Hermes ADWs

**Cause**: Hermes adapter not returning proper JSON

**Solution**: Ensure `hermes_adapter.py` is executable:
```bash
chmod +x ADWs/hermes_adapter.py
```

### Missing Usage Metrics

**Cause**: Hermes adapter returns zero usage by default

**Solution**: Update `hermes_adapter.py` to parse Hermes log output for token usage

### Agent Not Found

**Error**: Agent/skills not loaded

**Cause**: `--agent` flag maps to `--skills` in Hermes

**Solution**: Ensure the skill exists in Hermes skills directory:
```
~/.hermes/skills/<agent-name>/SKILL.md
```

## Migration from Claude to Hermes

### Step 1: Install Hermes
```bash
npm install -g @nousresearch/hermes-agent
```

### Step 2: Configure Provider
```bash
cp config/providers.example.json config/providers.json
# Edit config/providers.json to set active_provider to "hermes"
```

### Step 3: Set Environment Variables
```bash
export HERMES_MODEL="anthropic/claude-sonnet-4"
export OPENROUTER_API_KEY="[REDACTED]"
```

### Step 4: Test Integration
```python
from ADWs.runner import run_claude
result = run_claude("Hello, Hermes!", log_name="test")
print(result)
```

### Step 5: Run ADWs
```bash
python ADWs/routines/weekly_review.py
```

## Advanced Configuration

### Custom Provider via Hermes

You can use any OpenAI-compatible provider with Hermes:

```json
{
  "hermes": {
    "env_vars": {
      "HERMES_MODEL": "custom-model-name",
      "OPENAI_API_KEY": "[REDACTED]",
      "OPENAI_BASE_URL": "https://your-custom-endpoint.com/v1"
    }
  }
}
```

### Hermes Profile Integration

You can use Hermes profiles instead of skills:

```python
result = run_claude(
    "Execute task",
    log_name="task",
    agent="my-hermes-profile"  # Maps to --profile or --skills
)
```

## Future Enhancements

- [ ] Stream processing for Hermes (currently subprocess waits for completion)
- [ ] Real-time token usage tracking from Hermes logs
- [ ] Hermes skill auto-discovery
- [ ] Provider-specific fallback chains
- [ ] Hermes MCP server integration
- [ ] Dashboard UI for Hermes configuration

## References

- [Hermes Agent Documentation](https://hermes-agent.nousresearch.com/docs)
- [EvoNexus ADWs Guide](../ADWs/README.md)
- [Provider Configuration](./providers.example.json)

## Support

For issues or questions:
1. Check this guide
2. Review `ADWs/logs/detail/` for detailed logs
3. Open an issue on GitHub

---

**Last Updated**: 2026-05-23
**Version**: 1.0.0
**Status**: Production Ready
