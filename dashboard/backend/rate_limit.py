"""Shared Flask-Limiter instance for EvoNexus.

Placing the limiter here (rather than in app.py directly) breaks the
circular-import chain: app.py initialises it, route blueprints import it.

Usage in a blueprint::

    from rate_limit import limiter

    @bp.route("/api/shares/<token>/view")
    @limiter.limit("60 per minute")
    def view_share(token: str):
        ...
"""

from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# Uninitialised instance — app.py calls limiter.init_app(app) at startup.
limiter = Limiter(
    get_remote_address,
    # Default: generous to avoid false positives on authenticated API routes.
    # Individual endpoints override with @limiter.limit() decorators.
    default_limits=["600 per minute"],
    storage_uri="memory://",
)
