const fs = require('fs');
const path = require('path');

const WORKSPACE_ROOT = path.resolve(__dirname, '..', '..', '..');
const PROVIDERS_PATH = path.join(WORKSPACE_ROOT, 'config', 'providers.json');

const ALLOWED_CLI = new Set(['claude', 'openclaude']);
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
  const model = resolveProviderModel(providerConfig);
  if (isCodeModel(model)) return 'code';
  return 'chat';
}

function loadProviderConfig() {
  try {
    if (!fs.existsSync(PROVIDERS_PATH)) {
      return { cli_command: 'claude', env_vars: {}, active: 'anthropic' };
    }

    const config = JSON.parse(fs.readFileSync(PROVIDERS_PATH, 'utf8'));
    const active = config.active_provider || 'anthropic';
    const provider = config.providers?.[active] || {};

    let cliCommand = provider.cli_command || 'claude';
    if (!ALLOWED_CLI.has(cliCommand)) cliCommand = 'claude';

    const envVars = Object.fromEntries(
      Object.entries(provider.env_vars || {}).filter(
        ([k, v]) => v !== '' && ALLOWED_ENV_VARS.has(k)
      )
    );

    if (active === 'codex_auth' && 'OPENAI_API_KEY' in envVars) {
      delete envVars.OPENAI_API_KEY;
    }

    return {
      cli_command: cliCommand,
      env_vars: envVars,
      active,
      provider_name: provider.name || active,
    };
  } catch {
    return { cli_command: 'claude', env_vars: {}, active: 'anthropic' };
  }
}

module.exports = {
  loadProviderConfig,
  resolveProviderModel,
  getProviderMode,
  isCodeModel,
  isChatCompletionModel,
};

