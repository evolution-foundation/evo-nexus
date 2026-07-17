"""Overview endpoint — summary data for the dashboard home."""

import json
from datetime import datetime, timedelta, timezone
from flask import Blueprint, jsonify
from routes._helpers import WORKSPACE, safe_read
from models import db

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
        # Derived fresh from raw counts, not the stored success_rate field —
        # see the comment in Routines.tsx's transformRoutineMetrics for why
        # (one routine used to write it as a 0-1 fraction instead of 0-100).
        runs_v = v.get("runs", 0)
        rate = round((v.get("successes", 0) / runs_v) * 100, 1) if runs_v > 0 else 0
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


def _needs_attention() -> dict:
    """Aggregated cross-system health (panorama 2026-07-17, item 19) — before
    this, answering "is anything wrong right now" meant visiting Heartbeats,
    Kanban and the Telegram approval history separately. One query per
    signal, each cheap and best-effort (a failure in one must never blank
    the whole card).
    """
    heartbeat_failures: list[dict] = []
    try:
        rows = db.session.execute(db.text(
            "SELECT hr.heartbeat_id, hr.error, hr.started_at "
            "FROM heartbeat_runs hr "
            "JOIN (SELECT heartbeat_id, MAX(started_at) AS max_started "
            "      FROM heartbeat_runs GROUP BY heartbeat_id) latest "
            "  ON latest.heartbeat_id = hr.heartbeat_id AND latest.max_started = hr.started_at "
            "WHERE hr.status = 'fail' "
            "ORDER BY hr.started_at DESC LIMIT 20"
        )).fetchall()
        heartbeat_failures = [
            {"heartbeat_id": r.heartbeat_id, "error": (r.error or "")[:200], "started_at": r.started_at}
            for r in rows
        ]
    except Exception:
        pass

    stale_locked_tickets: list[dict] = []
    try:
        rows = db.session.execute(db.text(
            "SELECT id, title, locked_by, locked_at, lock_timeout_seconds FROM tickets "
            "WHERE locked_at IS NOT NULL "
            "AND datetime(locked_at, '+' || lock_timeout_seconds || ' seconds') < datetime('now') "
            "LIMIT 20"
        )).fetchall()
        stale_locked_tickets = [
            {"id": r.id, "title": r.title, "locked_by": r.locked_by, "locked_at": r.locked_at}
            for r in rows
        ]
    except Exception:
        pass

    aged_approvals: list[dict] = []
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        rows = db.session.execute(db.text(
            "SELECT id, gate_type, created_at FROM pending_approvals "
            "WHERE status = 'pending' AND created_at < :cutoff "
            "ORDER BY created_at ASC LIMIT 20"
        ), {"cutoff": cutoff}).fetchall()
        aged_approvals = [
            {"id": r.id, "gate_type": r.gate_type, "created_at": r.created_at}
            for r in rows
        ]
    except Exception:
        pass

    return {
        "heartbeat_failures": heartbeat_failures,
        "stale_locked_tickets": stale_locked_tickets,
        "aged_approvals": aged_approvals,
        "total": len(heartbeat_failures) + len(stale_locked_tickets) + len(aged_approvals),
    }


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
        "needs_attention": _needs_attention(),
    })
