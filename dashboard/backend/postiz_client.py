"""PostizClient — the single server-side HTTP client for the self-hosted Postiz
instance (social-media-production feature).

Extracted from dashboard/backend/heartbeat_outcome.py, which used to make
these HTTP calls inline with bare `requests` calls. That logic is now here,
and heartbeat_outcome.py calls into this module instead — there must be only
ONE Postiz client in the codebase (explicit requirement of the
social-media-production briefing: "Não crie um segundo cliente Postiz").

Paths confirmed twice, not guessed: first against the official Postiz
public API docs (docs.postiz.com, 2026-07-19), then corrected against the
real self-hosted instance at POSTIZ_URL (also 2026-07-19) — the generic
docs describe the SaaS convention (`/public/v1/...`, own subdomain), but
this self-hosted single-domain deployment (postiz-vps.stack.yml, Next.js
API routes) serves the backend under an `/api` prefix. GET
/api/public/v1/integrations, GET/POST /api/public/v1/posts and POST
/api/public/v1/upload were each hit for real and returned sane JSON
(empty array / validation errors), confirming the prefix — never assume
the SaaS path works against a self-hosted instance without checking:
  - POST /api/public/v1/upload            multipart -> {id, path, name}
  - POST /api/public/v1/posts             {type: draft|schedule|now, date, posts:[...]}
  - GET  /api/public/v1/posts             polling for state confirmation
  - GET  /api/public/v1/integrations      list connected accounts
  - PUT  /api/public/v1/posts/{id}/status {status: draft|schedule}

Security invariants (do not relax without re-reading the ADR):
  - POSTIZ_API_KEY is never logged, never included in a raised exception
    message verbatim (see _redact).
  - Media referenced by URL (not uploaded via upload_file) must pass
    is_safe_media_url() — HTTPS only, no userinfo, host in the configured
    allowlist. Prevents an agent-influenced URL from becoming an SSRF
    primitive against the Postiz instance.
  - upload_file() streams the file from disk; it never reads the whole
    video into memory.
"""

from __future__ import annotations

import mimetypes
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

MEDIA_JOB_PLATFORMS = ("instagram", "youtube", "linkedin", "tiktok")


class PostizError(Exception):
    """Base class for all PostizClient errors. Message is always redacted."""


class PostizConfigError(PostizError):
    """POSTIZ_URL/POSTIZ_API_KEY missing or invalid configuration."""


class PostizAPIError(PostizError):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


# ── Per-platform settings builders (confirmed against docs.postiz.com/public-api/providers/*) ──

def build_instagram_payload(post_type: str = "post", standalone: bool = False) -> dict:
    if post_type not in ("post", "story"):
        raise ValueError(f"Instagram post_type inválido: {post_type!r} (aceita 'post' ou 'story')")
    return {"__type": "instagram-standalone" if standalone else "instagram", "post_type": post_type}


def build_youtube_payload(title: str, visibility: str = "unlisted", made_for_kids: str = "no") -> dict:
    if visibility not in ("public", "unlisted", "private"):
        raise ValueError(f"YouTube type/visibility inválido: {visibility!r}")
    if made_for_kids not in ("yes", "no"):
        raise ValueError(f"YouTube selfDeclaredMadeForKids inválido: {made_for_kids!r}")
    if not title or not (2 <= len(title) <= 100):
        raise ValueError("YouTube title deve ter entre 2 e 100 caracteres")
    return {"__type": "youtube", "title": title, "type": visibility, "selfDeclaredMadeForKids": made_for_kids}


def build_linkedin_payload(page: bool = False) -> dict:
    return {"__type": "linkedin-page" if page else "linkedin"}


def build_tiktok_payload(privacy_level: str = "SELF_ONLY") -> dict:
    allowed = {"PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS", "FOLLOWER_OF_CREATOR", "SELF_ONLY"}
    if privacy_level not in allowed:
        raise ValueError(f"TikTok privacy_level inválido: {privacy_level!r}")
    return {
        "__type": "tiktok",
        "privacy_level": privacy_level,
        "duet": False,
        "stitch": False,
        "comment": True,
        "autoAddMusic": "no",
        "brand_content_toggle": False,
        "brand_organic_toggle": False,
        "video_made_with_ai": False,
        "content_posting_method": "DIRECT_POST",
    }


PLATFORM_SETTINGS_BUILDERS = {
    "instagram": build_instagram_payload,
    "youtube": build_youtube_payload,
    "linkedin": build_linkedin_payload,
    "tiktok": build_tiktok_payload,
}


def build_platform_settings(platform: str, **kwargs) -> dict:
    builder = PLATFORM_SETTINGS_BUILDERS.get(platform)
    if builder is None:
        raise ValueError(
            f"Plataforma sem payload builder implementado: {platform!r}. "
            f"Suportadas: {sorted(PLATFORM_SETTINGS_BUILDERS)}"
        )
    return builder(**kwargs)


@dataclass
class PostizClient:
    base_url: str
    api_key: str
    request_timeout: float = 30.0
    upload_timeout: float = 900.0
    allowed_media_hosts: frozenset[str] = frozenset()
    integration_ids: dict[str, str] | None = None  # {"instagram": "...", ...}

    def __post_init__(self):
        self.base_url = (self.base_url or "").strip().rstrip("/")
        self.api_key = (self.api_key or "").strip()
        if not self.base_url or not self.api_key:
            raise PostizConfigError("POSTIZ_URL/POSTIZ_API_KEY não configurados.")
        parsed = urlparse(self.base_url)
        if parsed.scheme != "https":
            raise PostizConfigError("POSTIZ_URL deve ser HTTPS.")

    @classmethod
    def from_env(cls) -> "PostizClient | None":
        """Build a client from environment variables, or None if unconfigured.

        Never raises for "just not configured" — callers check for None and
        report that as a 4xx, not a 500.
        """
        url = os.environ.get("POSTIZ_URL", "").strip()
        key = os.environ.get("POSTIZ_API_KEY", "").strip()
        if not url or not key:
            return None
        hosts = frozenset(
            h.strip().lower()
            for h in os.environ.get("POSTIZ_ALLOWED_MEDIA_HOSTS", "").split(",")
            if h.strip()
        )
        integration_ids = {
            platform: os.environ.get(f"POSTIZ_INTEGRATION_{platform.upper()}_ID", "").strip()
            for platform in MEDIA_JOB_PLATFORMS
        }
        try:
            return cls(
                base_url=url,
                api_key=key,
                # POSTIZ_REQUEST_TIMEOUT_SECONDS is the current name (Etapa 15);
                # POSTIZ_HTTP_TIMEOUT_SECONDS is the pre-existing name used by
                # the legacy publish-gate flow — honor it if set so an already
                # deployed .env keeps working without a forced rename.
                request_timeout=float(
                    os.environ.get("POSTIZ_REQUEST_TIMEOUT_SECONDS")
                    or os.environ.get("POSTIZ_HTTP_TIMEOUT_SECONDS")
                    or "120"
                ),
                upload_timeout=float(os.environ.get("POSTIZ_UPLOAD_TIMEOUT_SECONDS", "900")),
                allowed_media_hosts=hosts,
                integration_ids={k: v for k, v in integration_ids.items() if v},
            )
        except PostizConfigError:
            return None

    # ── internal helpers ─────────────────────────────────────────────────

    def _headers(self) -> dict:
        return {"Authorization": self.api_key}

    def _redact(self, text: str) -> str:
        """Strip the API key out of any string before it can reach a log/exception."""
        if not text or not self.api_key:
            return text
        return text.replace(self.api_key, "***REDACTED***")

    def _request(self, method: str, path: str, *, timeout: float, **kwargs) -> requests.Response:
        url = f"{self.base_url}{path}"
        try:
            response = requests.request(method, url, timeout=timeout, **kwargs)
        except Exception as exc:
            # Broad on purpose (not just requests.RequestException): any
            # unexpected exception's message still gets redacted before it
            # can propagate — the API key must never leak into a log/UI
            # error string via an exception type this didn't anticipate.
            raise PostizAPIError(f"Falha de rede ao chamar Postiz {path}: {self._redact(str(exc))}") from None
        if response.status_code >= 400:
            body_preview = self._redact((response.text or "")[:500])
            raise PostizAPIError(
                f"Postiz respondeu {response.status_code} em {path}: {body_preview}",
                status_code=response.status_code,
            )
        return response

    # ── integrations ─────────────────────────────────────────────────────

    def list_integrations(self) -> list[dict]:
        response = self._request(
            "GET", "/api/public/v1/integrations", headers=self._headers(), timeout=self.request_timeout
        )
        try:
            data = response.json()
        except ValueError:
            raise PostizAPIError("Postiz retornou um corpo não-JSON em /integrations.") from None
        if not isinstance(data, list):
            raise PostizAPIError(f"Formato inesperado de /integrations: {type(data).__name__}")
        return data

    def select_integration(self, platform: str, integrations: list[dict] | None = None) -> dict | None:
        if integrations is None:
            integrations = self.list_integrations()
        configured_id = (self.integration_ids or {}).get(platform, "")
        candidates = [
            item for item in integrations
            if isinstance(item, dict) and item.get("identifier") == platform and not item.get("disabled")
        ]
        if configured_id:
            return next((item for item in candidates if item.get("id") == configured_id), None)
        return candidates[0] if len(candidates) == 1 else None

    def test_connection(self) -> dict:
        """Best-effort health probe used by the admin config screen."""
        try:
            integrations = self.list_integrations()
        except PostizError as exc:
            return {"ok": False, "detail": str(exc)}
        by_platform = {}
        for platform in MEDIA_JOB_PLATFORMS:
            match = self.select_integration(platform, integrations)
            by_platform[platform] = {"connected": match is not None, "id": match.get("id") if match else None}
        return {"ok": True, "detail": f"{len(integrations)} integrações encontradas.", "platforms": by_platform}

    # ── media URL safety (for URL-referenced media, not local uploads) ───

    def is_safe_media_url(self, value: str) -> bool:
        parsed = urlparse(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            return False
        hostname = parsed.hostname.lower()
        return bool(self.allowed_media_hosts) and any(
            hostname == allowed or hostname.endswith(f".{allowed}") for allowed in self.allowed_media_hosts
        )

    # ── upload ────────────────────────────────────────────────────────────

    def upload_file(self, file_path: Path) -> dict:
        """Multipart-upload a local file to POST /api/public/v1/upload.

        Streams from disk (requests reads file-like objects in chunks — the
        whole video is never materialized in memory). Returns the raw
        {id, path, name} the Postiz API hands back; the caller is
        responsible for persisting these onto the MediaJob row before doing
        anything else (idempotency — see media_state_machine ADR-7).
        """
        file_path = Path(file_path)
        if not file_path.is_file():
            raise PostizError(f"Arquivo de upload não encontrado: {file_path}")
        content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        with file_path.open("rb") as fh:
            files = {"file": (file_path.name, fh, content_type)}
            response = self._request(
                "POST", "/api/public/v1/upload", headers=self._headers(), files=files, timeout=self.upload_timeout
            )
        try:
            data = response.json()
        except ValueError:
            raise PostizAPIError("Postiz retornou um corpo não-JSON em /upload.") from None
        if isinstance(data, list):
            data = data[0] if data else {}
        if not isinstance(data, dict) or not data.get("id") or not data.get("path"):
            raise PostizAPIError(f"Resposta de upload sem id/path: {data!r}")
        return {"id": data["id"], "path": data["path"], "name": data.get("name") or file_path.name}

    # ── post creation ────────────────────────────────────────────────────

    def _create_post_raw(
        self,
        *,
        integration_id: str,
        content: str,
        media: list[dict],
        settings: dict,
        post_type: str,
        date_iso_utc: str,
        extra: dict | None = None,
    ) -> list[dict]:
        if post_type not in ("draft", "schedule", "now"):
            raise ValueError(f"post_type inválido: {post_type!r}")
        body = {
            "type": post_type,
            "date": date_iso_utc,
            "shortLink": False,
            "tags": [],
            **(extra or {}),
            "posts": [{
                "integration": {"id": integration_id},
                "value": [{"content": content, "image": media}],
                "settings": settings,
            }],
        }
        response = self._request(
            "POST", "/api/public/v1/posts", headers={**self._headers(), "Content-Type": "application/json"},
            json=body, timeout=self.request_timeout,
        )
        try:
            created = response.json()
        except ValueError:
            raise PostizAPIError("Postiz retornou um corpo não-JSON em POST /posts.") from None
        if not isinstance(created, list):
            raise PostizAPIError(f"Resposta inesperada de POST /posts: {created!r}")
        return created

    def create_draft(self, *, integration_id: str, content: str, media: list[dict], settings: dict,
                      now_iso_utc: str) -> list[dict]:
        """type=draft — 'the post is created and stored against the integration
        but not scheduled or published' (confirmed via docs.postiz.com). The
        `date` field is still required by the schema even though it has no
        scheduling effect in draft state; pass the current UTC timestamp.
        """
        return self._create_post_raw(
            integration_id=integration_id, content=content, media=media, settings=settings,
            post_type="draft", date_iso_utc=now_iso_utc,
        )

    def schedule_post(self, *, integration_id: str, content: str, media: list[dict], settings: dict,
                       scheduled_at_utc: str) -> list[dict]:
        return self._create_post_raw(
            integration_id=integration_id, content=content, media=media, settings=settings,
            post_type="schedule", date_iso_utc=scheduled_at_utc,
        )

    def create_post_now(self, *, integration_id: str, content: str, media: list[dict], settings: dict,
                         now_iso_utc: str) -> list[dict]:
        """type=now — publishes immediately. Never called by the media-jobs
        pipeline in this feature (SOCIAL_DEFAULT_POST_MODE=draft always);
        kept for parity with the existing publish-gate flow in
        heartbeat_outcome.py, which this class now backs. `creationMethod`
        is not part of the documented public API schema but was present in
        the pre-refactor payload for this legacy flow — preserved here
        verbatim rather than silently dropped; the new draft/schedule paths
        used by MediaJob do NOT set it (only documented fields).
        """
        return self._create_post_raw(
            integration_id=integration_id, content=content, media=media, settings=settings,
            post_type="now", date_iso_utc=now_iso_utc, extra={"creationMethod": "API"},
        )

    def change_status(self, post_id: str, status: str) -> dict:
        if status not in ("draft", "schedule"):
            raise ValueError(f"status inválido: {status!r}")
        response = self._request(
            "PUT", f"/api/public/v1/posts/{post_id}/status",
            headers={**self._headers(), "Content-Type": "application/json"},
            json={"status": status}, timeout=self.request_timeout,
        )
        try:
            return response.json()
        except ValueError:
            return {"id": post_id, "state": status}

    # ── publication confirmation (fail-closed polling) ──────────────────

    def wait_for_publication(self, post_ids: list[str], *, wait_seconds: float, poll_seconds: float,
                              window: tuple[str, str]) -> dict:
        """Poll GET /api/public/v1/posts until every id reaches state=PUBLISHED.

        Fail-closed by design (ADR SPEC 3f, ported verbatim from
        heartbeat_outcome._wait_for_postiz_publication): QUEUE/ERROR/timeout
        never resolve as published=True. Not used for draft-mode jobs (a
        draft never enters a publishing workflow), only for the legacy
        publish-gate 'now' path this client also backs.
        """
        import time as _time

        deadline = _time.monotonic() + wait_seconds
        start_iso, end_iso = window
        while _time.monotonic() <= deadline:
            response = self._request(
                "GET", "/api/public/v1/posts", headers=self._headers(),
                params={"startDate": start_iso, "endDate": end_iso}, timeout=self.request_timeout,
            )
            try:
                body = response.json()
            except ValueError:
                raise PostizAPIError("Postiz retornou um corpo não-JSON em GET /posts.") from None
            posts = body.get("posts", body) if isinstance(body, dict) else body
            states = {
                item.get("id"): item.get("state")
                for item in posts or []
                if isinstance(item, dict) and item.get("id") in post_ids
            }
            if any(states.get(pid) == "ERROR" for pid in post_ids):
                return {"published": False, "detail": f"Postiz marcou publicação como ERROR: {states}."}
            if all(states.get(pid) == "PUBLISHED" for pid in post_ids):
                return {"published": True, "detail": f"Postiz confirmou PUBLISHED para {', '.join(post_ids)}."}
            _time.sleep(poll_seconds)
        return {
            "published": False,
            "detail": f"Postiz não confirmou PUBLISHED em {wait_seconds:g}s para {', '.join(post_ids)}.",
        }
