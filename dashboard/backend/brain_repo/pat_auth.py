"""Brain Repo — re-export shim so `brain_repo.pat_auth` keeps working.

The actual implementation lives in `github_oauth.py`.
"""

from brain_repo.github_oauth import (  # noqa: F401
    GitHubAuthProvider,
    PATAuthProvider,
    decrypt_token,
    get_master_key,
)
