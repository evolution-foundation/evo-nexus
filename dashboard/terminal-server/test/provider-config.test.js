const assert = require('assert/strict');
const test = require('node:test');

const {
  getProviderMode,
  isCodeModel,
} = require('../src/provider-config');

test('getProviderMode returns anthropic for the native provider', () => {
  assert.equal(getProviderMode({ active: 'anthropic' }), 'anthropic');
});

test('getProviderMode falls back to the model-name heuristic', () => {
  assert.equal(
    getProviderMode({ active: 'openrouter', env_vars: { OPENAI_MODEL: 'qwen-coder' } }),
    'code'
  );
  assert.equal(
    getProviderMode({ active: 'openrouter', env_vars: { OPENAI_MODEL: 'openrouter/owl-alpha' } }),
    'chat'
  );
});

test('explicit mode overrides the model-name heuristic', () => {
  assert.equal(
    getProviderMode({ active: 'openrouter', mode: 'code', env_vars: { OPENAI_MODEL: 'openrouter/owl-alpha' } }),
    'code'
  );
  assert.equal(
    getProviderMode({ active: 'openrouter', mode: 'chat', env_vars: { OPENAI_MODEL: 'qwen-coder' } }),
    'chat'
  );
});

test('invalid mode values are ignored', () => {
  assert.equal(
    getProviderMode({ active: 'openrouter', mode: 'bogus', env_vars: { OPENAI_MODEL: 'openrouter/owl-alpha' } }),
    'chat'
  );
});

test('isCodeModel recognizes codex aliases and coder names', () => {
  assert.equal(isCodeModel('codexplan'), true);
  assert.equal(isCodeModel('codexspark'), true);
  assert.equal(isCodeModel('devstral-small'), true);
  assert.equal(isCodeModel('gpt-4.1'), false);
});
