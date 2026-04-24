"""Brain Repo — GitHub REST API integration."""

import logging
import urllib.error
import urllib.parse
import urllib.request
from json import dumps as _dumps, loads as _loads

log = logging.getLogger(__name__)

_API_BASE = "https://api.github.com"
_TIMEOUT = 15

_SNAPSHOT_PREFIXES = {
    "daily": "refs/tags/snapshot/",
    "weekly": "refs/tags/weekly/",
    "milestones": "refs/tags/milestone/",
}


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _get(url: str, token: str) -> tuple[int, dict | list | None, dict]:
    """Perform a GET request. Returns (status_code, body, response_headers)."""
    req = urllib.request.Request(url, headers=_headers(token), method="GET")
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            body = _loads(resp.read())
            return resp.status, body, dict(resp.headers)
    except urllib.error.HTTPError as exc:
        try:
            body = _loads(exc.read())
        except Exception:
            body = None
        return exc.code, body, {}
    except Exception as exc:
        log.warning("GitHub API GET %s failed: %s", url, exc)
        return 0, None, {}


def _post(url: str, token: str, payload: dict) -> tuple[int, dict | None]:
    """Perform a POST request with JSON body."""
    data = _dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={**_headers(token), "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            return resp.status, _loads(resp.read())
    except urllib.error.HTTPError as exc:
        try:
            body = _loads(exc.read())
        except Exception:
            body = None
        return exc.code, body
    except Exception as exc:
        log.warning("GitHub API POST %s failed: %s", url, exc)
        return 0, None


def detect_brain_repos(token: str) -> list[dict]:
    """Find repos owned by the authenticated user that carry a ``.evo-brain`` marker.

    Previous implementation used ``/search/code?q=filename:.evo-brain`` which had
    two bugs in practice: GitHub's code index doesn't reliably include dotfiles,
    and it lags push events by minutes-to-hours for small/new repos — so a
    just-connected brain repo would appear to vanish until the index caught up.

    This implementation is deterministic: list the user's private repos, then
    hit the Contents API for ``.evo-brain`` on each. Costs one extra request
    per private repo (cheap at ≤100 repos), but the result reflects the
    current state of the remote, not the indexer.

    Returns list of {name, full_name, html_url, private, description}.
    """
    repos = list_user_repos(token)
    results = []
    for repo in repos:
        full_name = repo.get("full_name", "")
        if not full_name or "/" not in full_name:
            continue
        owner, name = full_name.split("/", 1)
        contents_url = f"{_API_BASE}/repos/{owner}/{name}/contents/.evo-brain"
        status, _body, _ = _get(contents_url, token)
        if status == 200:
            results.append(repo)
        elif status not in (200, 404):
            # 403 rate-limit, 5xx, network hiccup — log and move on, the user
            # can retry. Don't error the whole listing for one flaky repo.
            log.debug("detect_brain_repos: %s returned %d", full_name, status)
    return results


def list_user_repos(token: str) -> list[dict]:
    """List up to 100 private repos for the authenticated user."""
    url = f"{_API_BASE}/user/repos?per_page=100&type=private"
    status, body, _ = _get(url, token)
    if status != 200 or not isinstance(body, list):
        log.warning("list_user_repos: status %d", status)
        return []
    return [
        {
            "name": r.get("name", ""),
            "full_name": r.get("full_name", ""),
            "html_url": r.get("html_url", ""),
            "private": r.get("private", False),
            "description": r.get("description", ""),
        }
        for r in body
    ]


def create_private_repo(token: str, name: str, description: str = "") -> dict:
    """Create a private repository for the authenticated user.

    Raises RuntimeError on 4xx/5xx responses.
    """
    url = f"{_API_BASE}/user/repos"
    payload = {
        "name": name,
        "description": description,
        "private": True,
        "auto_init": False,
    }
    status, body = _post(url, token, payload)
    if status not in (200, 201):
        detail = ""
        if isinstance(body, dict):
            detail = body.get("message", str(body))
        raise RuntimeError(f"create_private_repo failed ({status}): {detail}")
    return body or {}


def validate_repo_is_private(token: str, owner: str, repo: str) -> bool:
    """Return True if the repository exists and is private."""
    url = f"{_API_BASE}/repos/{owner}/{repo}"
    status, body, _ = _get(url, token)
    if status != 200 or not isinstance(body, dict):
        return False
    return bool(body.get("private", False))


def get_repo_info(token: str, repo_url: str) -> tuple[bool, dict]:
    """Parse repo_url, fetch repo info, return (is_private, info).

    repo_url examples:
        https://github.com/owner/name
        https://github.com/owner/name.git
        git@github.com:owner/name.git
    """
    import re
    m = re.match(
        r"(?:https?://github\.com/|git@github\.com:)([^/]+)/([^/.]+)(?:\.git)?/?$",
        repo_url.strip(),
    )
    if not m:
        return False, {}
    owner, name = m.group(1), m.group(2)
    url = f"{_API_BASE}/repos/{owner}/{name}"
    status, body, _ = _get(url, token)
    if status != 200 or not isinstance(body, dict):
        return False, {}
    return bool(body.get("private", False)), body


def list_snapshots(token: str, owner: str, repo: str) -> dict:
    """List snapshot tags grouped by type (daily, weekly, milestones, head).

    All list items AND the head item use the same shape so the frontend can
    render them uniformly without null checks on optional fields:

        {
          "daily":      [{ref, sha, label}, ...],
          "weekly":     [...],
          "milestones": [...],
          "head":        {ref, sha, label} | null,
        }

    ``label`` is derived from the ref (strips ``refs/tags/``) so the UI has
    one display string to render without needing to know git ref conventions.
    ``head`` uses ref="HEAD" and label="HEAD" (instead of an empty dict)
    which prevents the ``Cannot read properties of undefined`` crash the
    frontend used to hit when rendering the HEAD row.
    """
    url = f"{_API_BASE}/repos/{owner}/{repo}/git/refs/tags"
    status, body, _ = _get(url, token)

    result: dict[str, list | dict | None] = {
        "daily": [],
        "weekly": [],
        "milestones": [],
        "head": None,
    }

    def _label(ref: str) -> str:
        # refs/tags/milestone/teste → milestone/teste
        return ref[len("refs/tags/"):] if ref.startswith("refs/tags/") else ref

    if status != 200 or not isinstance(body, list):
        log.warning("list_snapshots: status %d", status)
        return result

    for ref_obj in body:
        ref = ref_obj.get("ref", "")
        sha = ref_obj.get("object", {}).get("sha", "")
        item = {"ref": ref, "sha": sha, "label": _label(ref)}
        if ref.startswith(_SNAPSHOT_PREFIXES["daily"]):
            result["daily"].append(item)  # type: ignore[union-attr]
        elif ref.startswith(_SNAPSHOT_PREFIXES["weekly"]):
            result["weekly"].append(item)  # type: ignore[union-attr]
        elif ref.startswith(_SNAPSHOT_PREFIXES["milestones"]):
            result["milestones"].append(item)  # type: ignore[union-attr]

    # Get HEAD commit SHA
    head_url = f"{_API_BASE}/repos/{owner}/{repo}/git/refs/heads"
    h_status, h_body, _ = _get(head_url, token)
    if h_status == 200 and isinstance(h_body, list) and h_body:
        result["head"] = {
            "ref": "HEAD",
            "sha": h_body[-1].get("object", {}).get("sha", ""),
            "label": "HEAD",
        }

    return result


def validate_pat_scopes(token: str) -> tuple[bool, list[str]]:
    """Verify that the PAT has the 'repo' scope.

    Returns (has_repo_scope, list_of_scopes).
    """
    url = f"{_API_BASE}/user"
    status, _, resp_headers = _get(url, token)
    if status != 200:
        return False, []

    scopes_header = resp_headers.get("X-OAuth-Scopes", "")
    scopes = [s.strip() for s in scopes_header.split(",") if s.strip()]
    has_repo = "repo" in scopes
    return has_repo, scopes


def get_github_username(token: str) -> str:
    """Return the login (username) for the authenticated user."""
    url = f"{_API_BASE}/user"
    status, body, _ = _get(url, token)
    if status != 200 or not isinstance(body, dict):
        log.warning("get_github_username: status %d", status)
        return ""
    return body.get("login", "")
