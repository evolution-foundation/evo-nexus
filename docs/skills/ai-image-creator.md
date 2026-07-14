# AI Image Creator

Generate PNG images using AI models — Gemini, FLUX.2, Riverflow, SeedDream, GPT-5 Image, GPT Image 2 — routed through Cloudflare AI Gateway (BYOK), directly via OpenRouter/Google AI Studio/NVIDIA NIM/OpenAI, or through the self-hosted OmniRoute gateway. Also analyzes and describes existing images using multimodal vision.

**Skill name:** `ai-image-creator`
**Trigger:** "generate an image", "create a PNG", "make an icon", "describe this image", "analyze this image"

---

## Overview

The `ai-image-creator` skill wraps a Python script (`generate-image.py`) that calls image generation APIs and saves the result as a PNG. It supports:

| Model Keyword | Model ID | Provider | Type |
|---------------|----------|----------|------|
| `gemini` (default) | `google/gemini-3.1-flash-image-preview` | OpenRouter / Google AI Studio | Multimodal |
| `riverflow` | `sourceful/riverflow-v2-pro` | OpenRouter | Image-only |
| `flux2` | `black-forest-labs/flux.2-max` | OpenRouter | Image-only |
| `seedream` | `bytedance-seed/seedream-4.5` | OpenRouter | Image-only |
| `gpt5` | `openai/gpt-5-image` | OpenRouter | Multimodal |
| `image2` | `gpt-image-2` | OpenAI Images API / OmniRoute | Image-only |
| `nvidia-flux2-klein-4b` | `black-forest-labs/flux.2-klein-4b` | NVIDIA NIM | Image-only |
| `nvidia-flux-dev` | `black-forest-labs/flux.1-dev` | NVIDIA NIM | Image-only |
| `nvidia-flux-schnell` | `black-forest-labs/flux.1-schnell` | NVIDIA NIM | Image-only |

**Multimodal** models (gemini, gpt5) accept reference images for editing/style transfer and can analyze existing images. **Image-only** models (riverflow, flux2, seedream) generate from text prompts only.

A companion script (`composite-banners.py`) generates consistent logo banners across multiple standard sizes using ImageMagick — no API calls required.

---

## Setup Option A: Cloudflare AI Gateway (Recommended)

Cloudflare AI Gateway acts as a proxy that stores your provider API keys server-side (BYOK — Bring Your Own Key). Your actual keys never leave Cloudflare; only the gateway token is stored locally. This also gives you request logs, caching, and rate limiting.

### Step 1: Create a Cloudflare account

Sign up at [dash.cloudflare.com](https://dash.cloudflare.com) (free tier works).

### Step 2: Create an AI Gateway

1. In the Cloudflare dashboard, go to **AI** > **AI Gateway**
2. Click **Create Gateway**
3. Enter a name (e.g., `my-ai-gateway`) — this becomes your `AI_IMG_CREATOR_CF_GATEWAY_ID`
4. Click **Create**

Note your **Account ID** from the dashboard URL: `dash.cloudflare.com/{account_id}/...`

### Step 3: Enable authentication

1. In your gateway settings, enable **Authentication**
2. Copy the **auth token** — this is your `AI_IMG_CREATOR_CF_TOKEN`

### Step 4: Add BYOK provider keys

Store your provider keys inside Cloudflare so the script never sends them in request headers.

**Add OpenRouter key:**
1. In gateway dashboard, go to **Provider Keys** > **Add**
2. Select **OpenRouter** as the provider
3. Paste your OpenRouter API key (`sk-or-...`)
4. Set alias to `default` > **Save**

**Add Google AI Studio key:**
1. **Provider Keys** > **Add** again
2. Select **Google AI Studio**
3. Paste your Google AI Studio key (`AI...`)
4. Set alias to `aistudio` > **Save**

### Step 5: Set environment variables

```bash
# ~/.zshrc (macOS) or ~/.bashrc (Linux)
export AI_IMG_CREATOR_CF_ACCOUNT_ID="your-account-id"
export AI_IMG_CREATOR_CF_GATEWAY_ID="your-gateway-name"
export AI_IMG_CREATOR_CF_TOKEN="your-gateway-auth-token"
```

Apply: `source ~/.zshrc`

---

## Setup Option B: OpenRouter Direct (Simplest)

No Cloudflare account needed. One environment variable.

### Step 1: Create an OpenRouter account

Sign up at [openrouter.ai](https://openrouter.ai).

### Step 2: Get an API key

Go to [openrouter.ai/keys](https://openrouter.ai/keys) > **Create Key**. Copy the key (starts with `sk-or-...`).

### Step 3: Add credits

Go to [openrouter.ai/credits](https://openrouter.ai/credits) and add pay-as-you-go credits. Check model pricing at [openrouter.ai/models](https://openrouter.ai/models).

### Step 4: Set environment variable

```bash
export AI_IMG_CREATOR_OPENROUTER_KEY="sk-or-your-key-here"
```

This is the only variable needed for Option B. All models are available through OpenRouter.

---

## Setup Option C: Google AI Studio Direct

For using the Gemini model via Google's API directly (without OpenRouter).

### Step 1: Get an API key

Go to [aistudio.google.com/apikey](https://aistudio.google.com/apikey) > **Create API Key**. Select or create a Google Cloud project. Copy the key (starts with `AI...`).

### Step 2: Enable billing (required for image generation)

The free tier has a quota of **0** for `gemini-3.1-flash-image` image generation. You must enable billing on the linked Google Cloud project. Without billing, requests return `429 RESOURCE_EXHAUSTED`. OpenRouter (Option B) is simpler if you want to avoid GCP billing setup.

### Step 3: Set environment variable

```bash
export AI_IMG_CREATOR_GEMINI_KEY="AIyour-key-here"
```

---

## Setup Option D: OpenAI Images API (gpt-image-2)

Direct access to OpenAI's `gpt-image-2` via the Images API. Excellent text rendering (including Portuguese accents) — the best pick for text-critical art.

1. Get a **platform API key** at [platform.openai.com](https://platform.openai.com) (billing required). **ChatGPT Plus / Codex OAuth tokens cannot generate images** — they lack the images scope and return 401.
2. Set the key (or paste it in the dashboard **Integrations → AI Image Creator** card):

```bash
export AI_IMG_CREATOR_OPENAI_KEY="sk-..."   # falls back to OPENAI_API_KEY
```

3. Use `--provider openai` (or just say "image2" — the keyword auto-selects the provider). `-a` maps to the supported sizes (1024x1024, 1536x1024, 1024x1536); `-r` and `--analyze` are not supported here.

---

## Setup Option E: OmniRoute Gateway (self-hosted)

Routes the same `gpt-image-2` Images API call through the self-hosted [OmniRoute](../dashboard/providers.md) gateway instead of api.openai.com. Only the key is needed — the base URL defaults to the VPS stack alias `http://omniroute:20128/v1`.

```bash
export AI_IMG_CREATOR_OMNIROUTE_KEY="your-omniroute-key"
# outside the Swarm stack, point at your gateway:
export AI_IMG_CREATOR_OMNIROUTE_BASE_URL="https://your-gateway/v1"   # optional
```

Use `--provider omniroute`. The gateway must proxy `POST /v1/images/generations`.

---

## Environment Variables Reference

| Variable | Option A | Option B | Option C | Option D | Option E | Description |
|----------|----------|----------|----------|----------|----------|-------------|
| `AI_IMG_CREATOR_CF_ACCOUNT_ID` | Required | — | — | — | — | Cloudflare account ID |
| `AI_IMG_CREATOR_CF_GATEWAY_ID` | Required | — | — | — | — | AI Gateway name |
| `AI_IMG_CREATOR_CF_TOKEN` | Required | — | — | — | — | Gateway auth token |
| `AI_IMG_CREATOR_OPENROUTER_KEY` | Optional* | Required | — | — | — | OpenRouter API key |
| `AI_IMG_CREATOR_GEMINI_KEY` | Optional* | — | Required | — | — | Google AI Studio API key |
| `AI_IMG_CREATOR_OPENAI_KEY` | — | — | — | Required | — | OpenAI platform key for gpt-image-2 (falls back to `OPENAI_API_KEY`) |
| `AI_IMG_CREATOR_OMNIROUTE_KEY` | — | — | — | — | Required | OmniRoute gateway key |
| `AI_IMG_CREATOR_OMNIROUTE_BASE_URL` | — | — | — | — | Optional | Gateway base URL (default `http://omniroute:20128/v1`) |
| `NVIDIA_API_KEY` | — | — | — | — | — | Enables `--provider nvidia` (FLUX models, free tier) |

*For Option A (gateway), provider keys are stored in Cloudflare as BYOK — you do not need to set `OPENROUTER_KEY` or `GEMINI_KEY` locally. Gateway mode activates when all three `CF_*` vars are set and falls back to direct mode if the gateway fails.

All of these can also be configured from the dashboard: **Integrations → AI Image Creator**.

Add these to your `.env` file (used by the workspace) or directly to your shell profile. See [env-variables.md](env-variables.md) for full reference.

---

## Usage Examples

### Basic image generation (default model: gemini)

```
/ai-image-creator

"Generate a flat-design globe icon with timezone band lines in blue and teal, white background, clean vector style, 512x512 pixels"
```

### Specific model

```
"Generate a product hero shot using riverflow — a sleek wireless headphone on a dark gradient background, cinematic lighting"
```

### Transparent background

```
"Create a friendly robot mascot with transparent background — make it transparent"
```

Requires `brew install ffmpeg imagemagick`.

### Reference image editing (multimodal models only)

```
"Using this existing logo as reference, create a version with a dark background"
[attach logo.png]
```

### Image analysis

```
"What's in this image?" [attach any PNG/JPG]
```

### Composite banners (existing logo → multiple sizes)

```
"Generate banners for our brand using the logo at assets/logo.png — standard social and IAB sizes"
```

### Cost tracking

```
"Show me the image generation costs for this project"
```

---

## Agent Integration

### @canvas-designer

Canvas uses `/ai-image-creator` to generate visual assets when implementing UI components — icons, hero images, product shots, backgrounds, and mockup assets. Canvas does not rely on external stock imagery; it generates what the design requires.

Typical Canvas workflow:
1. Identify required image assets from the design spec
2. Write detailed prompts aligned with the design direction (colors, style, mood)
3. Generate with `ai-image-creator`, selecting the model best suited to the asset type
4. Post-process with ImageMagick if resizing or format conversion is needed
5. Reference the generated files in the component code

### @pixel (Social Media)

Pixel uses `/ai-image-creator` to generate original imagery for social media posts when assets are not available — thumbnails, banners, carousel visuals, story backgrounds, post artwork.

Typical Pixel workflow:
1. Determine the visual direction from the content calendar entry
2. Generate platform-specific images (using `-a` for aspect ratio and `-s` for size)
3. Use `composite-banners.py` when the brand needs consistent logo treatment across multiple sizes

---

## Troubleshooting

| Error | Cause | Fix |
|-------|-------|-----|
| `No API credentials configured` | Env vars not set or not exported | Add exports to `~/.zshrc` and run `source ~/.zshrc` |
| `HTTP 401: Unauthorized` | Invalid or expired API key/token | Regenerate `AI_IMG_CREATOR_CF_TOKEN` (gateway) or `AI_IMG_CREATOR_OPENROUTER_KEY` (direct) |
| `No images in response` | Model safety filter, unclear prompt | Make the prompt more specific; avoid prohibited content |
| `429 RESOURCE_EXHAUSTED` | Google AI Studio free tier has no image quota | Enable billing on the GCP project, or switch to OpenRouter (Option B) |
| `Connection error` / timeout | Network issue or slow generation (120s timeout) | Retry; try `--provider google` as alternative; check CF gateway status |
| `uv: command not found` | uv not installed | `curl -LsSf https://astral.sh/uv/install.sh \| sh` or `brew install uv` |
| `BYOK key not found` | Wrong alias in CF Provider Keys | Verify aliases: `default` for OpenRouter, `aistudio` for Google AI Studio |
| Image-only model + reference image | riverflow/flux2/seedream don't accept reference images | Switch to a multimodal model: `gemini` or `gpt5` |
| `401` on `--provider openai` with a ChatGPT Plus/Codex token | OAuth tokens lack the images scope | Use a **platform** API key (billing on platform.openai.com) |
| `AI_IMG_CREATOR_OMNIROUTE_KEY not set` | OmniRoute provider selected without a key | Paste the gateway key in **Integrations → AI Image Creator** or the `.env` |
| 404/error via OmniRoute | Gateway doesn't proxy `/v1/images/generations` | Use `--provider openai` with a platform key instead |
