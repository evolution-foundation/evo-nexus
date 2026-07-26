from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2] / "dashboard" / "backend"
sys.path.insert(0, str(BACKEND_DIR))


def test_proxy_does_not_forward_dashboard_credentials_to_hermes():
    from routes.hermes_proxy import _forward_headers

    forwarded = _forward_headers(
        {
            "Authorization": "Bearer dashboard-token",
            "Cookie": "session=dashboard-secret",
            "X-CSRFToken": "dashboard-csrf",
            "Accept": "application/json",
        }
    )

    assert forwarded == {"Accept": "application/json"}
