#!/usr/bin/env node

const { startServer } = require('../src/server');
const { startSmartRouter, stopSmartRouter } = require('../src/utils/openrouter-smart-router');
const { loadProviderConfig } = require('../src/provider-config');

const args = process.argv.slice(2);
const getFlag = (name) => {
  const idx = args.indexOf(name);
  if (idx === -1) return null;
  const next = args[idx + 1];
  return next && !next.startsWith('--') ? next : true;
};

const portArg = getFlag('--port');
const port = portArg && portArg !== true ? parseInt(portArg, 10) : 32352;
const dev = args.includes('--dev');

if (isNaN(port) || port < 1 || port > 65535) {
  console.error('Error: Port must be a number between 1 and 65535');
  process.exit(1);
}

// Start the Smart Router only when the active provider is configured to
// proxy through localhost:4891 (i.e. OPENAI_BASE_URL points at the router).
// Opt-in, off by default.
function shouldStartSmartRouter() {
  try {
    const { env_vars = {} } = loadProviderConfig();
    const baseUrl = env_vars.OPENAI_BASE_URL || '';
    return baseUrl.includes('127.0.0.1:4891') || baseUrl.includes('localhost:4891');
  } catch {
    return false;
  }
}

function getOpenRouterApiKey() {
  if (process.env.OPENROUTER_API_KEY) return process.env.OPENROUTER_API_KEY;
  try {
    const { env_vars = {} } = loadProviderConfig();
    const key = env_vars.OPENAI_API_KEY || '';
    return key.startsWith('sk-or-') ? key : '';
  } catch {
    return '';
  }
}

async function main() {
  let routerServer = null;

  try {
    console.log('Starting EvoNexus terminal server...');
    console.log(`Port: ${port}`);

    if (shouldStartSmartRouter()) {
      const apiKey = getOpenRouterApiKey();
      if (apiKey) {
        console.log('\n📡 Starting Smart Router...');
        routerServer = await startSmartRouter({ apiKey });
      } else {
        console.warn('⚠  Smart Router skipped — no OpenRouter API key found\n');
      }
    }

    await startServer({ port, dev });

    console.log(`\n🚀 Terminal server running at http://localhost:${port}`);
    console.log('Press Ctrl+C to stop\n');

    const shutdown = (signal) => {
      console.log(`\nReceived ${signal}, shutting down...`);
      stopSmartRouter(routerServer);
      process.exit(0);
    };
    process.on('SIGINT', () => shutdown('SIGINT'));
    process.on('SIGTERM', () => shutdown('SIGTERM'));
  } catch (error) {
    console.error('Error starting server:', error.message);
    stopSmartRouter(routerServer);
    process.exit(1);
  }
}

main();
