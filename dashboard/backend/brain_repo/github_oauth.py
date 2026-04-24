"""Brain Repo — GitHub authentication providers."""

import logging
import os
from abc import ABC, abstractmethod

from cryptography.fernet import Fernet

log = logging.getLogger(__name__)


class FeatureDisabledError(Exception):
    """Raised when an OAuth feature is not configured."""


class GitHubAuthProvider(ABC):
    """Abstract base class for GitHub authentication providers."""

    @abstractmethod
    def get_token(self) -> str:
        """Return the plain-text GitHub token."""

    @abstractmethod
    def encrypt_token(self) -> bytes:
        """Return the Fernet-encrypted token bytes."""


class PATAuthProvider(GitHubAuthProvider):
    """Personal Access Token auth provider."""

    def __init__(self, pat: str, master_key: bytes) -> None:
        self._pat = pat
        self._master_key = master_key

    def get_token(self) -> str:
        return self._pat

    def encrypt_token(self) -> bytes:
        f = Fernet(self._master_key)
        return f.encrypt(self._pat.encode())


class OAuthAuthProvider(GitHubAuthProvider):
    """GitHub OAuth app provider (skeleton — requires EVO_NEXUS_GITHUB_CLIENT_ID)."""

    def __init__(self, access_token: str | None = None, master_key: bytes | None = None) -> None:
        self._access_token = access_token or ""
        self._master_key = master_key or get_master_key()

    def get_token(self) -> str:
        if not self._access_token:
            raise FeatureDisabledError("OAuth token not set")
        return self._access_token

    def encrypt_token(self) -> bytes:
        if not self._access_token:
            raise FeatureDisabledError("OAuth token not set")
        f = Fernet(self._master_key)
        return f.encrypt(self._access_token.encode())

    @staticmethod
    def start_oauth_flow(client_id: str, redirect_uri: str) -> str:
        """Build and return the GitHub OAuth authorization URL."""
        if not is_oauth_enabled():
            raise FeatureDisabledError(
                "GitHub OAuth not configured — set EVO_NEXUS_GITHUB_CLIENT_ID"
            )
        scope = "repo"
        return (
            f"https://github.com/login/oauth/authorize"
            f"?client_id={client_id}"
            f"&redirect_uri={redirect_uri}"
            f"&scope={scope}"
        )

    @staticmethod
    def handle_callback(code: str, client_id: str, client_secret: str) -> bytes:
        """Exchange OAuth code for an access token, return encrypted bytes.

        Raises FeatureDisabledError if OAuth is not configured.
        Raises RuntimeError on GitHub API error.
        """
        if not is_oauth_enabled():
            raise FeatureDisabledError(
                "GitHub OAuth not configured — set EVO_NEXUS_GITHUB_CLIENT_ID"
            )

        import urllib.request
        import urllib.parse
        import json as _json

        payload = urllib.parse.urlencode({
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
        }).encode()

        req = urllib.request.Request(
            "https://github.com/login/oauth/access_token",
            data=payload,
            headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = _json.loads(resp.read())
        except Exception as exc:
            raise RuntimeError(f"GitHub OAuth callback failed: {exc}") from exc

        token = data.get("access_token", "")
        if not token:
            raise RuntimeError(f"No access_token in GitHub response: {data.get('error_description', '')}")

        master_key = get_master_key()
        f = Fernet(master_key)
        return f.encrypt(token.encode())


def decrypt_token(encrypted: bytes, master_key: bytes) -> str:
    """Decrypt a Fernet-encrypted token."""
    f = Fernet(master_key)
    return f.decrypt(encrypted).decode()


def is_oauth_enabled() -> bool:
    """Return True if GitHub OAuth is configured via environment."""
    return bool(os.getenv("EVO_NEXUS_GITHUB_CLIENT_ID"))


def get_master_key() -> bytes:
    """Return the Fernet master key from environment."""
    return os.getenv("BRAIN_REPO_MASTER_KEY", "").encode()
