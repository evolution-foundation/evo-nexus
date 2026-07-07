"""Overview endpoint — summary data for the dashboard home."""

import json
from datetime import datetime
from flask import Blueprint, jsonify
from routes._helpers import WORKSPACE, safe_read

bp = Blueprint("overview", __name__)

# Top-level workspace dirs skipped when scanning for recent reports.
# - projects: vendored third-party repos (tens of thousands of files, not reports)
# - meetings: raw Fathom transcripts, not dashboard-facing reports
_REPORTS_SKIP_DIRS = {"projects", "meetings"}


def _recent_reports(limit: int = 10) -> list[dict]:
    """Scan workspace/ for recent HTML/MD report files.

    Uses shallow iteration over top-level folders and skips _REPORTS_SKIP_DIRS
    to keep the endpoint fast — rglob'ing the whole workspace with the
    vendored projects/ repos inside takes 15+ seconds.
    """
    files = []
    workspace_dir = WORKSPACE / "workspace"
    if not workspace_dir.is_dir():
        return files
    for area_dir in workspace_dir.iterdir():
        if not area_dir.is_dir() or area_dir.name in _REPORTS_SKIP_DIRS or area_dir.name.startswith("."):
            continue
        for f in area_dir.rglob("*"):
            if f.is_file() and f.suffix.lower() in (".html", ".md") and not f.name.startswith("."):
                try:
                    files.append({
                        "name": f.name,
                        "path": str(f.relative_to(WORKSPACE)),
                        "area": area_dir.name,
                        "extension": f.suffix,
                        "modified": f.stat().st_mtime,
                    })
                except Exception:
                    continue
    files.sort(key=lambda x: x.get("modified", 0), reverse=True)
    return files[:limit]


def _metrics_summary() -> dict:
    """Load routine metrics summary from ADWs/logs/metrics.json."""
    path = WORKSPACE / "ADWs" / "logs" / "metrics.json"
    content = safe_read(path)
    if content:
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            pass
    return {}


def _integration_count() -> int:
    """Count integrations with configured env vars."""
    import os
    keys = [
        "OMIE_APP_KEY", "STRIPE_SECRET_KEY", "TODOIST_API_TOKEN",
        "FATHOM_API_KEY", "DISCORD_BOT_TOKEN", "TELEGRAM_BOT_TOKEN",
        "WHATSAPP_API_KEY", "LICENSING_ADMIN_TOKEN",
    ]
    return sum(1 for k in keys if os.environ.get(k))


def _build_overview_metrics(raw_metrics: dict, integration_count: int) -> list[dict]:
    """Transform raw metrics.json into overview KPI cards."""
    total_runs = sum(v.get("runs", 0) for v in raw_metrics.values())
    total_cost = sum(v.get("total_cost_usd", 0) for v in raw_metrics.values())
    total_success = sum(v.get("successes", 0) for v in raw_metrics.values())
    success_rate = round((total_success / total_runs * 100), 1) if total_runs > 0 else 0

    agents_count = len(list((WORKSPACE / ".claude" / "agents").glob("*.md"))) if (WORKSPACE / ".claude" / "agents").is_dir() else 0
    skills_count = len([d for d in (WORKSPACE / ".claude" / "skills").iterdir() if d.is_dir()]) if (WORKSPACE / ".claude" / "skills").is_dir() else 0

    return [
        {"label": "Routines Executed", "value": total_runs, "delta": f"{success_rate}% success", "deltaType": "up" if success_rate >= 90 else "neutral"},
        {"label": "Total Cost", "value": f"${total_cost:.2f}", "delta": f"${total_cost / max(total_runs, 1):.2f}/run", "deltaType": "neutral"},
        {"label": "Agents", "value": agents_count, "delta": f"{skills_count} skills", "deltaType": "neutral"},
        {"label": "Active Integrations", "value": integration_count},
    ]


def _build_routines(raw_metrics: dict) -> list[dict]:
    """Transform raw metrics into routines table."""
    routines = []
    for name, v in sorted(raw_metrics.items(), key=lambda x: x[1].get("last_run", ""), reverse=True):
        rate = v.get("success_rate", 0)
        last_success = v.get("last_success")
        if last_success is False:
            status = "critical"
        elif last_success is True and rate < 90:
            status = "warning"
        else:
            status = "healthy" if rate >= 90 else ("warning" if rate >= 50 else "critical")
        routines.append({
            "name": name,
            "last_run": (v.get("last_run") or "")[:16],
            "status": status,
            "runs": v.get("runs", 0),
        })
    return routines[:10]


@bp.route("/api/overview")
def overview():
    raw_metrics = _metrics_summary()
    ic = _integration_count()
    reports = _recent_reports()

    return jsonify({
        "recent_reports": [
            {
                "title": r["name"],
                "path": r["path"],
                "date": datetime.fromtimestamp(r["modified"]).strftime("%Y-%m-%d %H:%M"),
                "area": r["area"],
            }
            for r in reports
        ],
        "metrics": _build_overview_metrics(raw_metrics, ic),
        "routines": _build_routines(raw_metrics),
        "integration_count": ic,
    })
