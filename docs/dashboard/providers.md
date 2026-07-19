# AI Providers

The **Providers** page lets you choose which LLM backend powers EvoNexus, configure its credentials, and test the connection — all from the dashboard. It lives under **System → Providers** in the sidebar and requires the `config:manage` permission.

EvoNexus uses Anthropic's `claude` CLI by default. To run on any other backend (OpenRouter, OpenAI, Gemini, AWS Bedrock, Google Vertex AI, Codex Auth), it switches to [OpenClaude](https://www.npmjs.com/package/@gitlawb/openclaude) — a drop-in binary that speaks the same CLI protocol but dispatches to the provider of your choice via environment variables.

## Supported Providers

| Provider | Binary | Notes |
|---|---|---|
| **Anthropic** (default) | `claude` | Native Anthropic auth, no extra config |
| **NVIDIA NIM** | `openclaude` | OpenAI-compatible endpoint (`integrate.api.nvidia.com/v1`) — DeepSeek, GLM, Kimi, Qwen, Nemotron and other hosted models with generous free tiers |
| **OmniRoute** (`omnirouter`) | `openclaude` | Self-hosted gateway (237+ providers, automatic fallback, token compression). On the VPS stack it runs as the `omniroute` service and is reached via the internal Swarm alias `http://omniroute:20128/v1` — works even when the container has no external DNS |
| **OpenRouter** | `openclaude` | 200+ models via one API — Claude, GPT, Gemini, Llama, etc. |
| **OpenAI** | `openclaude` | GPT-4o, GPT-4.1, o3 via OpenAI API |
| **Google Gemini** | `openclaude` | Gemini 2.5 Pro/Flash via Google AI |
| **Codex Auth** | `openclaude` | Reuses Codex CLI's OAuth session to access OpenAI models |
| **AWS Bedrock** | `openclaude` | Claude via AWS Bedrock |
| **Google Vertex AI** | `openclaude` | Claude via GCP Vertex AI |

## Install OpenClaude (one-time)

If you plan to use any non-Anthropic provider, install OpenClaude globally:

```bash
npm install -g @gitlawb/openclaude
```

The Providers page shows a banner at the top indicating whether `claude` and `openclaude` are installed and in `$PATH`. If `openclaude` is missing, the banner shows the install command.

## Activating a Provider

1. Open **System → Providers** in the sidebar
2. Click **Configure** on the provider you want to use
3. Fill in the required fields (API key, base URL, model, region — depending on provider). Secrets are masked in the form and in every API response; placeholders like `sk-...` guide the format
4. Click **Save & Activate**

The active provider is stored in `config/providers.json`. Both the terminal-server and the ADW runner re-read this file on every session spawn, so switching takes effect **immediately** — no restart needed.

A green "Active" badge marks the currently selected provider. Every other provider can still be configured and tested without affecting the active one.

## Fallback Chain (heartbeats and background runs)

Heartbeats and other background invocations don't call the active provider blindly — they go through `dashboard/backend/provider_fallback.py`, which builds a **provider chain** and rotates on failure (429/quota, network errors, non-zero exits):

- The chain starts at `active_provider` and follows its `fallback_providers` list in `config/providers.json` (e.g. `nvidia → omnirouter → anthropic`).
- Within a provider, `fallback_models` rotates through alternate models before leaving the provider (NVIDIA ships a 12-model chain).
- The last link is usually `anthropic` (native `claude`) — note that on a fresh VPS container this requires a `claude` login, otherwise the final attempt exits with code 1.
- Failed provider/model pairs go on a short cooldown so parallel runs don't hammer a throttled endpoint.
- Disable with `HEARTBEAT_PROVIDER_FALLBACK=0`.

**VPS note:** containers on the Swarm stack may not resolve external hosts (ENOTFOUND on `integrate.api.nvidia.com`, `openrouter.ai`, …). Keep `omnirouter` in the chain — it resolves via internal Docker DNS and is the reliable path for heartbeats on the VPS.

## Per-Channel Provider: `telegram_provider`

`config/providers.json` also accepts a `telegram_provider` key that overrides the provider used by the **Telegram provider bot** (Magneto) without touching the terminal's `active_provider`. On the VPS this is set to `omnirouter` for the DNS reason above. See [Telegram Integration](../integrations/telegram.md#provider-mode-magneto).

## Testing a Provider

Each provider card has a **Test** button that runs `<binary> --version` with the configured env vars merged into the environment, then reports success or failure inline. This is a sanity check — it verifies that the binary is installed, in `$PATH`, and that the env var injection works. It does **not** validate that your API key actually authenticates against the remote service (use the terminal after activating for that).

## Security: Allowlists and Secret Masking

Both the Python runner (`ADWs/runner.py`) and the JS terminal bridge (`dashboard/terminal-server/src/claude-bridge.js`) enforce two allowlists when reading `config/providers.json`:

- **CLI allowlist** — only `claude` and `openclaude` are accepted as spawn targets. Any other value falls back to `claude`.
- **Env var allowlist** — only the 13 variables listed in [env-variables.md](../reference/env-variables.md#ai-provider-configuration) are injected. Anything else is silently dropped.

The REST API that backs the Providers page masks secrets (`*_KEY`, `*_SECRET`, `*_TOKEN`) as `first6****last4` on every response. When you open the config modal, the form starts empty for those fields — type a new value to replace, or leave empty to keep the current one. Values containing `****` are treated as masked placeholders and skipped on save (so a round-trip through the UI doesn't accidentally overwrite a real secret with the mask string).

The backend also rejects any env var value containing shell metacharacters (`;`, `&`, `|`, backtick, `$`, newlines) — defense in depth against injection if someone points EvoNexus at a compromised `providers.json`.

## Logout Warning

When you switch away from Anthropic to any other provider, OpenClaude inherits your Anthropic Claude Code login state. To avoid confusion, run `/logout` inside Claude Code **once** after activating a non-Anthropic provider if you were previously logged in. The dashboard surfaces this warning on any provider marked `requires_logout`.

## Where It's Stored

- `config/providers.json` — active provider + per-provider CLI + env vars (**gitignored**, contains secrets)
- `config/providers.example.json` — template copied on first boot if no real file exists (checked into git)
- `.env` — unchanged by this feature. AI provider env vars live in `providers.json`, not `.env`

## Configuring at Install Time

The interactive setup wizard (`make setup`) asks which provider to use as step 3. If you pick anything other than Anthropic, it checks whether OpenClaude is installed, offers to install it, then prompts for the provider-specific keys and saves them to `config/providers.json`. You can re-run the wizard or use the dashboard to change providers later.

## Related

- [Environment Variables Reference](../reference/env-variables.md#ai-provider-configuration)
- [Getting Started](../getting-started.md) — step 3 covers provider choice
- [OpenClaude on npm](https://www.npmjs.com/package/@gitlawb/openclaude)
