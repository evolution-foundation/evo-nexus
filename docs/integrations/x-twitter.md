# X / Twitter Integration

Automated posting to X via OAuth 2.0 (PKCE). The editorial calendar routine ("Publicar Posts Sociais (X)") dispatches scheduled posts through `scripts/publish_scheduled.py` → `scripts/post_to_x.py`, with automatic token refresh and rate-limit retry.

## How Tokens Are Stored

X rotates the **refresh token on every refresh** — the old one is invalidated immediately. Because of that, tokens live in a dedicated, writable, persistent store instead of static env vars:

| Store | Path | Notes |
|---|---|---|
| **Social token store** | `config/social.env` | Source of truth for all `SOCIAL_*` keys. Gitignored, `chmod 600`. On the VPS it sits in the `evonexus_config` volume — shared by the dashboard and the scheduler, survives redeploys |
| Root `.env` | `.env` | Holds `TWITTER_CLIENT_ID` / `TWITTER_CLIENT_SECRET`; also receives tokens when connecting via the standalone `social-auth` app |
| Process env | — | Env vars of the stack; lowest precedence for `SOCIAL_*` keys |

Precedence for `SOCIAL_*` keys: `config/social.env` wins (it holds the freshest rotated tokens), **except** when a manual reconnection wrote a newer `TOKEN_CREATED_AT` to `.env` — then that account's keys from `.env` win. `scripts/seed_social_env.py` exports the social keys from `.env` into `config/social.env` (use `--print` to paste the content on another machine).

## Connecting an Account

### Via the dashboard (recommended on the VPS)

1. Make sure `TWITTER_CLIENT_ID` (and optionally `TWITTER_CLIENT_SECRET`) are configured — the X app must list `https://<your-dashboard-domain>/callback/twitter` as a callback URL.
2. Open `https://<your-dashboard-domain>/connect/twitter` and authorize.
3. Tokens are written to `config/social.env` in the shared volume — the scheduler picks them up on the next posting run, no restart needed.

### Via the standalone app (local)

```bash
python3 social-auth/app.py     # opens localhost:8765
```

Connect X there; tokens land in the root `.env`. Run `python3 scripts/seed_social_env.py` afterwards to sync them into `config/social.env`.

## Posting

```bash
python3 scripts/post_to_x.py "Tweet text" [--media image.png] [--account N] [--dry-run]
```

- Auto-refreshes expired access tokens (X access tokens last ~2h) and persists the rotated pair back to `config/social.env`.
- Retries with backoff on 429 (free tier resets every 15 min).
- Multi-account via `SOCIAL_TWITTER_<N>_*` and `--account N`.

## Rules That Save You Pain

- **Only one side should post/refresh.** If both your local machine and the VPS refresh the same refresh token, each rotation invalidates the other side's token and the integration dies with `"Value passed for the token was invalid"`. Pick where the posting routine runs (normally the VPS scheduler) and let the other side go stale.
- **A bearer token is read-only.** Posting requires the OAuth user-context flow with `tweet.write` — an app-only `BEARER_TOKEN` cannot publish.
- If the refresh token is ever invalidated, reconnect via the dashboard (`/connect/twitter`) — there is no way to revive it.

## Troubleshooting

| Error | Cause | Fix |
|---|---|---|
| `No SOCIAL_TWITTER account found` | No tokens in `config/social.env` / `.env` | Connect via `/connect/twitter` (dashboard) or `social-auth/app.py` (local) |
| `Invalid state — CSRF protection` on callback | Session cookie not sent on the cross-site redirect | Fixed in the dashboard (SameSite=Lax + ProxyFix); update the image if you still see it |
| `Token expired (401) and refresh failed` | Refresh token rotated elsewhere or revoked | Reconnect the account |
| `Only SOCIAL_TWITTER bearer token found` | Account saved with app-only bearer | Redo the OAuth flow (needs `tweet.write`) |

## Related

- [Social Accounts env pattern](../reference/env-variables.md#social-accounts)
- Source: `scripts/post_to_x.py`, `scripts/publish_scheduled.py`, `social-auth/auth/twitter.py`, `social-auth/env_manager.py`
