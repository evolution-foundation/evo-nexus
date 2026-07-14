// Teste E2E MANUAL do fallback do chat-bridge (fora do `npm test` — precisa
// do binário openclaude instalado e sobe processos reais do Agent SDK).
//
//   node test/manual/fallback-e2e.js
//
// Provider primário responde 503 "Maximum combo retry limit reached" (mock A);
// fallback (mock B) responde um chat completion válido em SSE.
// Esperado: a sessão NÃO crasha — rotaciona pro mock B em segundos e completa
// com o texto "FALLBACK-OK", zero results de erro entregues ao UI.
const http = require('http');
const path = require('path');

const TS_ROOT = path.resolve(__dirname, '..', '..');

// ---- Mock A: sempre 503 combo ----
const serverA = http.createServer((req, res) => {
  console.log(`[mockA] ${req.method} ${req.url}`);
  res.writeHead(503, { 'Content-Type': 'application/json' });
  res.end(JSON.stringify({ error: { message: 'Maximum combo retry limit reached', code: 503 } }));
});

// ---- Mock B: OpenAI-compatible SSE ----
const serverB = http.createServer((req, res) => {
  console.log(`[mockB] ${req.method} ${req.url}`);
  let body = '';
  req.on('data', (c) => (body += c));
  req.on('end', () => {
    if (!req.url.includes('/chat/completions')) {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ data: [{ id: 'good-model' }] }));
      return;
    }
    res.writeHead(200, {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache',
      Connection: 'keep-alive',
    });
    const send = (obj) => res.write(`data: ${JSON.stringify(obj)}\n\n`);
    const base = { id: 'chatcmpl-mock', object: 'chat.completion.chunk', created: Date.now() / 1000 | 0, model: 'good-model' };
    send({ ...base, choices: [{ index: 0, delta: { role: 'assistant', content: 'FALLBACK-OK' }, finish_reason: null }] });
    send({ ...base, choices: [{ index: 0, delta: {}, finish_reason: 'stop' }] });
    res.write('data: [DONE]\n\n');
    res.end();
  });
});

function mkProvider(id, port, model) {
  return {
    cli_command: 'openclaude',
    env_vars: {
      CLAUDE_CODE_USE_OPENAI: '1',
      OPENAI_BASE_URL: `http://127.0.0.1:${port}/v1`,
      OPENAI_API_KEY: `${id}-key`,
      OPENAI_MODEL: model,
    },
    active: id,
    mode: 'code',
    fallback_models: [],
    fallback_providers: id === 'mockbad' ? ['mockgood'] : [],
    model_tiers: {},
  };
}

async function main() {
  await new Promise((r) => serverA.listen(45031, '127.0.0.1', r));
  await new Promise((r) => serverB.listen(45032, '127.0.0.1', r));

  const fakeConfig = {
    ...mkProvider('mockbad', 45031, 'bad-model'),
    providers: {
      mockbad: mkProvider('mockbad', 45031, 'bad-model'),
      mockgood: mkProvider('mockgood', 45032, 'good-model'),
    },
  };

  // Injeta o loadProviderConfig fake ANTES de carregar o chat-bridge
  const providerConfigMod = require(path.join(TS_ROOT, 'src/provider-config'));
  providerConfigMod.loadProviderConfig = () => JSON.parse(JSON.stringify(fakeConfig));
  const { ChatBridge } = require(path.join(TS_ROOT, 'src/chat-bridge'));

  const bridge = new ChatBridge();
  const events = [];
  let done, fail;
  const finished = new Promise((res, rej) => { done = res; fail = rej; });
  const timer = setTimeout(() => fail(new Error('TIMEOUT 120s')), 120000);

  await bridge.startSession('e2e-test', {
    agentName: null,
    workingDir: '/tmp',
    prompt: 'diga apenas: oi',
    onMessage: (m) => {
      events.push(m);
      if (m.type === 'text_delta' || m.type === 'result') {
        console.log('[event]', JSON.stringify(m).slice(0, 200));
      }
    },
    onError: (err) => { clearTimeout(timer); fail(err); },
    onComplete: () => { clearTimeout(timer); done(); },
  });

  try {
    await finished;
    const text = events.filter((e) => e.type === 'text_delta').map((e) => e.text).join('');
    console.log('\n=== RESULTADO ===');
    console.log('completou sem crash:', true);
    console.log('texto recebido:', JSON.stringify(text.slice(0, 200)));
    const errResults = events.filter((e) => e.type === 'result' && e.isError);
    console.log('results de erro entregues ao UI:', errResults.length);
    process.exit(0);
  } catch (err) {
    console.error('\n=== FALHOU ===');
    console.error(err.message || err);
    process.exit(1);
  } finally {
    serverA.close(); serverB.close();
  }
}

main();
