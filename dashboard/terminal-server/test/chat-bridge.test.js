const assert = require('assert/strict');
const test = require('node:test');

const {
  buildHermesReplayContext,
  formatHermesExitLog,
  HERMES_REPLAY_MAX_CHARS,
  HERMES_REPLAY_MAX_MESSAGES,
} = require('../src/chat-bridge');

test('Hermes replay preserves bounded prior conversation context', () => {
  const context = buildHermesReplayContext([
    { role: 'user', text: 'A primeira informação é azul.' },
    { role: 'assistant', text: 'Entendi: azul.' },
    { role: 'user', text: 'Qual foi a primeira informação?' },
  ]);

  assert.match(context, /A primeira informação é azul\./);
  assert.match(context, /Assistant: Entendi: azul\./);
  assert.match(context, /Bounded persisted conversation context/);
  assert.match(context, /End persisted conversation context/);
});

test('Hermes replay deterministically limits messages and characters', () => {
  const messages = Array.from({ length: HERMES_REPLAY_MAX_MESSAGES + 4 }, (_, index) => ({
    role: index % 2 ? 'assistant' : 'user',
    text: `${index}: ${'x'.repeat(1000)}`,
  }));
  const context = buildHermesReplayContext(messages);

  assert.doesNotMatch(context, /User: 0: /);
  assert.match(context, new RegExp(`${HERMES_REPLAY_MAX_MESSAGES + 3}: `));
  assert.ok(context.length <= HERMES_REPLAY_MAX_CHARS + 256);
});

test('Hermes stderr exit log excludes sensitive stderr content', () => {
  const secret = 'sk-secret-token';
  const prompt = 'private prompt and replay context';
  const line = formatHermesExitLog('corr-123', 1, true);

  assert.match(line, /corr-123/);
  assert.match(line, /code 1/);
  assert.match(line, /stderr received/);
  assert.doesNotMatch(line, new RegExp(secret));
  assert.doesNotMatch(line, new RegExp(prompt));
});
