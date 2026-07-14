const fs = require('fs');
const path = require('path');

const WORKSPACE_ROOT = path.resolve(__dirname, '..', '..', '..');
const PROVIDERS_PATH = path.join(WORKSPACE_ROOT, 'config', 'providers.json');
const PROVIDERS_EXAMPLE_PATH = path.join(WORKSPACE_ROOT, 'config', 'providers.example.json');

const ALLOWED_CLI = new Set(['claude', 'openclaude', 'opencode']);
const ALLOWED_MODES = new Set(['code', 'chat']);
const DEFAULT_CODE_PROVIDERS = new Set(['openrouter', 'omnirouter', 'nvidia', 'codex_auth']);
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

// providers.json stores API keys as "[REDACTED]" placeholders; the real secrets
// live in .env. The terminal-server doesn't load dotenv, so read them here and
// swap placeholders for the real value — otherwise openclaude gets a bogus key
// and every request 401s.
const _SECRET_KEYS = ['OPENAI_API_KEY', 'NVIDIA_API_KEY', 'GEMINI_API_KEY', 'ANTHROPIC_API_KEY', 'CODEX_API_KEY'];

function _isPlaceholderSecret(v) {
  return !v || /redact/i.test(v) || v.trim().startsWith('[');
}

let _envSecretsCache = null;
function _readEnvSecrets() {
  if (_envSecretsCache) return _envSecretsCache;
  const out = {};
  try {
    const txt = fs.readFileSync(path.join(WORKSPACE_ROOT, '.env'), 'utf8');
    for (const line of txt.split('\n')) {
      const t = line.trim();
      if (!t || t.startsWith('#') || !t.includes('=')) continue;
      const i = t.indexOf('=');
      out[t.slice(0, i).trim()] = t.slice(i + 1).trim();
    }
  } catch (_) { /* no .env file */ }
  _envSecretsCache = out;
  return out;
}

// For each secret, the .env may hold it under a related name (NVIDIA uses an
// OpenAI-compatible endpoint, so OPENAI_API_KEY is fed by NVIDIA_API_KEY).
const _SECRET_FALLBACKS = {
  OPENAI_API_KEY: ['OPENAI_API_KEY', 'NVIDIA_API_KEY'],
  NVIDIA_API_KEY: ['NVIDIA_API_KEY', 'OPENAI_API_KEY'],
  GEMINI_API_KEY: ['GEMINI_API_KEY'],
  ANTHROPIC_API_KEY: ['ANTHROPIC_API_KEY'],
  CODEX_API_KEY: ['CODEX_API_KEY'],
};

function _resolveSecrets(envObj) {
  const secrets = _readEnvSecrets();
  const lookup = (cands) => {
    for (const c of cands) {
      const v = process.env[c] || secrets[c];
      if (v && !_isPlaceholderSecret(v)) return v;
    }
    return null;
  };
  for (const k of _SECRET_KEYS) {
    if (k in envObj && _isPlaceholderSecret(envObj[k])) {
      const real = lookup(_SECRET_FALLBACKS[k] || [k]);
      if (real) envObj[k] = real;
      else delete envObj[k]; // no real value → drop placeholder so it can't 401
    }
  }
  return envObj;
}

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
  // These providers run through OpenClaude in the Terminal. Opaque hosted model
  // names such as z-ai/glm-5.2 should not be downgraded to Chat by heuristic.
  if (DEFAULT_CODE_PROVIDERS.has(active)) return 'code';
  const model = resolveProviderModel(providerConfig);
  if (isCodeModel(model)) return 'code';
  return 'chat';
}

function _mergeProviderDefaults(config) {
  try {
    if (!fs.existsSync(PROVIDERS_EXAMPLE_PATH)) return config;
    const defaults = JSON.parse(fs.readFileSync(PROVIDERS_EXAMPLE_PATH, 'utf8'));
    if (!config || typeof config !== 'object' || Array.isArray(config)) config = {};

    for (const [key, value] of Object.entries(defaults)) {
      if (key === 'providers') continue;
      if (!(key in config)) config[key] = value;
    }

    if (!config.providers || typeof config.providers !== 'object' || Array.isArray(config.providers)) {
      config.providers = {};
    }

    for (const [providerId, defaultProvider] of Object.entries(defaults.providers || {})) {
      const existing = config.providers[providerId];
      if (!existing || typeof existing !== 'object' || Array.isArray(existing)) {
        config.providers[providerId] = defaultProvider;
        continue;
      }

      for (const [key, value] of Object.entries(defaultProvider)) {
        if (key === 'env_vars') {
          if (!existing.env_vars || typeof existing.env_vars !== 'object' || Array.isArray(existing.env_vars)) {
            existing.env_vars = {};
          }
          for (const [envKey, envDefault] of Object.entries(value || {})) {
            if (!(envKey in existing.env_vars)) existing.env_vars[envKey] = envDefault;
          }
        } else if (!(key in existing)) {
          existing[key] = value;
        }
      }
    }
  } catch (_) {
    return config;
  }
  return config;
}

function loadProviderConfig() {
  try {
    if (!fs.existsSync(PROVIDERS_PATH)) {
      return { cli_command: 'claude', env_vars: {}, active: 'anthropic', fallback_models: [], fallback_providers: [], providers: {}, model_tiers: {} };
    }

    const config = _mergeProviderDefaults(JSON.parse(fs.readFileSync(PROVIDERS_PATH, 'utf8')));
    const active = config.active_provider || 'anthropic';
    const provider = config.providers?.[active] || {};

    let cliCommand = provider.cli_command || 'claude';
    if (!ALLOWED_CLI.has(cliCommand)) cliCommand = 'claude';

    const sanitizeEnv = (rawEnv = {}) => Object.fromEntries(
      Object.entries(rawEnv).filter(
        ([k, v]) => v !== '' && ALLOWED_ENV_VARS.has(k)
      )
    );

    const envVars = _resolveSecrets(sanitizeEnv(provider.env_vars || {}));

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
      const pEnv = _resolveSecrets(sanitizeEnv(p.env_vars || {}));
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
