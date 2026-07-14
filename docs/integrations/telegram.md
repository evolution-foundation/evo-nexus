# Telegram Integration

Telegram provides real-time messaging and notifications via a bot connected through MCP. The @clawdia agent uses it to send alerts, receive commands, and communicate with the user on the go.

> **Channel mode:** Telegram also works as a [Channel](../guides/channels.md#setup-telegram) — a bidirectional chat bridge that pushes messages into your Claude Code session. Start with `make telegram`. See the [Channels Guide](../guides/channels.md) for full setup including pairing and security.

> **Provider mode:** on headless deployments (VPS/Swarm) where Channels can't run — non-Anthropic providers can't reliably call the `reply` MCP tool — the bot runs in **provider mode** instead. See [Provider Mode (Magneto)](#provider-mode-magneto) below.

## Setup

### 1. Create a Bot

1. Open Telegram and message [@BotFather](https://t.me/BotFather)
2. Send `/newbot` and follow the prompts
3. Copy the **Bot Token** provided

### 2. Get Your Chat ID

1. Send `/start` to your new bot
2. Visit `https://api.telegram.org/bot<TOKEN>/getUpdates`
3. Find `chat.id` in the response

### 3. Configure .env

```env
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_CHAT_ID=your_chat_id_here
```

### 4. Start the Bot

```bash
make telegram
```

This starts the Telegram bot in a background screen session.

## Available Tools

Telegram is connected via MCP plugin. The following tools are available:

| Tool | What it does |
|---|---|
| `reply` | Send a message or reply to a specific message |
| `edit_message` | Edit an already sent message |
| `react` | Add an emoji reaction to a message |
| `download_attachment` | Download photo, file, or audio from a message |

### Sending Messages

```
reply(chat_id="...", text="Your message here")
```

### Sending Attachments

```
reply(chat_id="...", text="Here is the report", files=["/path/to/file.pdf"])
```

### Replying to a Specific Message

```
reply(chat_id="...", text="Your reply", reply_to="message_id")
```

## Key Behaviors

- Messages sent in the Claude session are **not visible** to Telegram -- everything must go through the `reply` tool
- Edits do not trigger push notifications -- send a new message for important updates
- The Bot API has no history or search -- only incoming messages are visible
- Access control is managed via the `/telegram:access` skill

## Provider Mode (Magneto)

`scripts/telegram_provider_bot.py` is a standalone bot runtime that answers chat messages using the workspace's configured LLM provider via plain chat completions — no Claude Code session, no MCP. It's the default on the VPS stack (`TELEGRAM_MODE=provider`), where it runs as the `evonexus_telegram` service.

Key facts:

- **Provider**: reads `telegram_provider` from `config/providers.json` (falls back to `active_provider`). On the VPS it points at `omnirouter` — the internal OmniRoute gateway — because containers can't resolve external provider hosts. Switch at runtime with the `/provider <id>` chat command.
- **No tools**: the model behind the bot is a plain chat completion — it cannot execute HTTP calls or shell commands by itself. Everything it should "know how to do" is fetched **server-side by the runtime and injected into the prompt**:
  - **URL contents** — links in your message are fetched and their text injected, so the bot can "read" pages.
  - **MemPalace recall** — the message is used as a semantic query against `GET /api/mempalace/search`; top hits are injected as persistent workspace memory.
  - **Nexus status** — when the message mentions crons/rotinas/heartbeats/scheduler, the runtime calls `GET /api/heartbeats` (last run + error per heartbeat) and `GET /api/routines` and injects real data, so "how are the crons doing?" gets answered with facts instead of "I don't have access".
- **Voice notes**: audio messages are transcribed via Groq Whisper (`GROQ_API_KEY`) and treated as text input.
- **Memory**: a rolling window of the last messages per chat is kept on disk and injected as conversation memory.
- **Heartbeat notifications**: heartbeat failures and outcomes are pushed to the configured chat — this is where `⚠️ Heartbeat FAIL` alerts come from.

To extend what the bot can answer, follow the same pattern: add a `fetch_*_context()` function that detects the topic, calls the API server-side, and injects the result into `build_prompt()` — do **not** add prompt instructions telling the model to call APIs itself (it can't).

## Skills That Use Telegram

| Skill | What it does |
|---|---|
| `int-telegram` | Send, reply, react, edit messages via MCP |

## Automated Routines

The Telegram bot runs continuously (`make telegram`) and receives messages in real time. Many routines send notification summaries to Telegram as part of their output.
