"""Plugin registry fetcher with 10-minute TTL in-process cache.

Fetches registry.json from a configurable URL (EVONEXUS_REGISTRY_URL env var,
default: the official EvoNexus plugin registry on GitHub).  Returns a list of
marketplace plugin entries.

Cache strategy: simple module-level dict with a timestamp — no external deps
required (no Redis, no disk).  TTL is 10 minutes (600 seconds).
"""

from __future__ import annotations

import os
import time
import threading
from typing import Any
from urllib.request import Request, urlopen
from urllib.error import URLError
import json

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_REGISTRY_URL = (
    "https://raw.githubusercontent.com/EvolutionAPI/evo-nexus/main/registry.json"
)
_CACHE_TTL = 600  # 10 minutes
_REQUEST_TIMEOUT = 10  # seconds

# ---------------------------------------------------------------------------
# In-process cache
# ---------------------------------------------------------------------------

_cache_lock = threading.Lock()
_cache: dict[str, Any] = {
    "data": None,
    "fetched_at": 0.0,
}


def _is_cache_valid() -> bool:
    return (
        _cache["data"] is not None
        and (time.time() - _cache["fetched_at"]) < _CACHE_TTL
    )


def _fetch_remote(url: str) -> list[dict[str, Any]]:
    """Download and parse registry JSON from *url*.

    Accepts two shapes:
    - A direct JSON array  → used as-is
    - An object with a ``"plugins"`` key → extracts that list
    """
    req = Request(
        url,
        headers={"User-Agent": "EvoNexus-Registry/1.0"},
    )
    with urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:  # noqa: S310
        raw = resp.read()

    parsed = json.loads(raw)

    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict) and "plugins" in parsed:
        return parsed["plugins"]

    return []


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def fetch_registry(*, force_refresh: bool = False) -> list[dict[str, Any]]:
    """Return the marketplace plugin list, using cache when possible.

    Args:
        force_refresh: Bypass the TTL cache and re-fetch immediately.

    Returns:
        List of plugin dicts from the registry.  Empty list on error.
    """
    url = os.environ.get("EVONEXUS_REGISTRY_URL", _DEFAULT_REGISTRY_URL)

    with _cache_lock:
        if not force_refresh and _is_cache_valid():
            return _cache["data"]  # type: ignore[return-value]

        try:
            data = _fetch_remote(url)
        except (URLError, TimeoutError, json.JSONDecodeError, Exception):
            # On any fetch error, return stale cache if available, else empty
            if _cache["data"] is not None:
                return _cache["data"]  # type: ignore[return-value]
            return []

        _cache["data"] = data
        _cache["fetched_at"] = time.time()
        return data


def invalidate_cache() -> None:
    """Force next call to fetch_registry() to re-fetch from the registry URL."""
    with _cache_lock:
        _cache["data"] = None
        _cache["fetched_at"] = 0.0
