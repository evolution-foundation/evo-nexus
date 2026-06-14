const fs = require('fs');
const path = require('path');

const WORKSPACE_ROOT = path.resolve(__dirname, '..', '..', '..');
const PROVIDERS_PATH = path.join(WORKSPACE_ROOT, 'config', 'providers.json');

const ALLOWED_CLI = new Set(['claude', 'openclaude']);
const ALLOWED_MODES = new Set(['code', 'chat']);
const ALLOWED_ENV_VARS = new Set([
  'ANTHROPIC_API_KEY',
  'CLAUDE_CODE_USE_OPENAI',
  'CLAUDE_CODE_USE_GEMINI',
  'CLAUDE_CODE_USE_BEDROCK',
  'CLAUDE_CODE_USE_VERTEX',
  'OPENAI_BASE_URL',
  'OPENAI_API_KEY',
  'OPENAI_MODEL',
  'CODEX_AUTH_JSON_PATH',
  'CODEX_API_KEY',
  'GEMINI_API_KEY',
  'GEMINI_MODEL',
  'NVIDIA_API_KEY',
  'AWS_REGION',
  'AWS_BEARER_TOKEN_BEDROCK',
  'ANTHROPIC_VERTEX_PROJECT_ID',
  'CLOUD_ML_REGION',
]);

function _normalizeModel(model) {
  return (model || '').trim().toLowerCase();
}

function isCodeModel(model) {
  const m = _normalizeModel(model);
  if (!m) return false;
  if (m === 'codexplan' || m === 'codexspark') return true;
  if (m.includes('memory-output') || m.includes('memory_output')) return false;
  if (m.includes('coder') || m.includes('codex') || m.includes('devstral')) return true;
  return /(^|[/:._-])code([/:._-]|$)/i.test(m);
}

function isChatCompletionModel(model) {
  const m = _normalizeModel(model);
  if (!m) return true;
  if (m.includes('memory-output') || m.includes('memory_output')) return true;
  return !isCodeModel(m);
}

function resolveProviderModel(providerConfig) {
  const env = providerConfig?.env_vars || {};
  const active = providerConfig?.active || 'anthropic';
  const fromEnv = (env.OPENAI_MODEL || '').trim();
  if (fromEnv) return fromEnv;
  if (active === 'codex_auth') return 'codexplan';
  if (active === 'openai') return 'gpt-4.1';
  return '';
}

function getProviderMode(providerConfig) {
  const active = providerConfig?.active || 'anthropic';
  if (active === 'anthropic') return 'anthropic';
  // Explicit per-provider mode in providers.json wins over the name heuristic —
  // model names like "openrouter/owl-alpha" are agentic but don't match isCodeModel.
  if (ALLOWED_MODES.has(providerConfig?.mode)) return providerConfig.mode;
  const model = resolveProviderModel(providerConfig);
  if (isCodeModel(model)) return 'code';
  return 'chat';
}

function loadProviderConfig() {
  try {
    if (!fs.existsSync(PROVIDERS_PATH)) {
      return { cli_command: 'claude', env_vars: {}, active: 'anthropic', fallback_models: [], fallback_providers: [], providers: {}, model_tiers: {} };
    }

    const config = JSON.parse(fs.readFileSync(PROVIDERS_PATH, 'utf8'));
    const active = config.active_provider || 'anthropic';
    const provider = config.providers?.[active] || {};

    let cliCommand = provider.cli_command || 'claude';
    if (!ALLOWED_CLI.has(cliCommand)) cliCommand = 'claude';

    const sanitizeEnv = (rawEnv = {}) => Object.fromEntries(
      Object.entries(rawEnv).filter(
        ([k, v]) => v !== '' && ALLOWED_ENV_VARS.has(k)
      )
    );

    const envVars = sanitizeEnv(provider.env_vars || {});

    if (active === 'codex_auth' && 'OPENAI_API_KEY' in envVars) {
      delete envVars.OPENAI_API_KEY;
    }

    // OpenClaude ≥0.18 detects the NVIDIA NIM base URL and requires the key
    // in NVIDIA_API_KEY — derive it so the UI only asks for one key field.
    if (
      !envVars.NVIDIA_API_KEY &&
      envVars.OPENAI_API_KEY &&
      /\bnvidia\.com\b/i.test(envVars.OPENAI_BASE_URL || '')
    ) {
      envVars.NVIDIA_API_KEY = envVars.OPENAI_API_KEY;
    }

    // Ordered fallback chain — the CLI consumes the first entry
    // (--fallback-model); the full list is used by Chat Completion fallback.
    const fallbackModels = Array.isArray(provider.fallback_models)
      ? provider.fallback_models
          .filter((m) => typeof m === 'string' && m.trim())
          .map((m) => m.trim())
      : [];

    const fallbackProviders = Array.isArray(provider.fallback_providers)
      ? provider.fallback_providers
          .filter((p) => typeof p === 'string' && p.trim())
          .map((p) => p.trim())
      : [];

    const providers = {};
    for (const [id, p] of Object.entries(config.providers || {})) {
      let pCliCommand = p.cli_command || 'claude';
      if (!ALLOWED_CLI.has(pCliCommand)) pCliCommand = 'claude';
      const pEnv = sanitizeEnv(p.env_vars || {});
      if (id === 'codex_auth' && 'OPENAI_API_KEY' in pEnv) delete pEnv.OPENAI_API_KEY;
      if (!pEnv.NVIDIA_API_KEY && pEnv.OPENAI_API_KEY && /\bnvidia\.com\b/i.test(pEnv.OPENAI_BASE_URL || '')) {
        pEnv.NVIDIA_API_KEY = pEnv.OPENAI_API_KEY;
      }
      providers[id] = {
        cli_command: pCliCommand,
        env_vars: pEnv,
        active: id,
        provider_name: p.name || id,
        mode: ALLOWED_MODES.has(p.mode) ? p.mode : null,
        fallback_models: Array.isArray(p.fallback_models)
          ? p.fallback_models.filter((m) => typeof m === 'string' && m.trim()).map((m) => m.trim())
          : [],
        fallback_providers: Array.isArray(p.fallback_providers)
          ? p.fallback_providers.filter((fp) => typeof fp === 'string' && fp.trim()).map((fp) => fp.trim())
          : [],
        model_tiers: {},
      };
      if (p.model_tiers && typeof p.model_tiers === 'object' && !Array.isArray(p.model_tiers)) {
        for (const [tier, model] of Object.entries(p.model_tiers)) {
          if (typeof model === 'string' && model.trim()) {
            providers[id].model_tiers[tier.toLowerCase()] = model.trim();
          }
        }
      }
    }

    // Per-tier model map: agents declare model: opus|sonnet|haiku in their
    // frontmatter; providers.json maps each tier to a provider model.
    const modelTiers = providers[active]?.model_tiers || {};

    return {
      cli_command: cliCommand,
      env_vars: envVars,
      active,
      provider_name: provider.name || active,
      mode: ALLOWED_MODES.has(provider.mode) ? provider.mode : null,
      fallback_models: fallbackModels,
      fallback_providers: fallbackProviders,
      providers,
      model_tiers: modelTiers,
    };
  } catch {
    return { cli_command: 'claude', env_vars: {}, active: 'anthropic', fallback_models: [], fallback_providers: [], providers: {}, model_tiers: {} };
  }
}

module.exports = {
  loadProviderConfig,
  resolveProviderModel,
  getProviderMode,
  isCodeModel,
  isChatCompletionModel,
};

