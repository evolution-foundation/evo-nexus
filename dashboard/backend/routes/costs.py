"""Costs endpoint — aggregated and daily cost breakdowns."""

import json
from datetime import date, timedelta
from flask import Blueprint, jsonify, request
from routes._helpers import WORKSPACE, safe_read
from models import db, Heartbeat, HeartbeatRun
from sqlalchemy import func

bp = Blueprint("costs", __name__)

METRICS_PATH = WORKSPACE / "ADWs" / "logs" / "metrics.json"
LOGS_DIR = WORKSPACE / "ADWs" / "logs"


def _resolve_agent_slug(short_or_full: str, known_assignee_slugs: set[str]) -> str | None:
    """Routine/heartbeat cost records use two different agent-name shapes:
    heartbeats.agent already carries the full slug ("pixel-social-media"),
    but routine cost records (ADWs metrics.json) carry the SHORT docstring
    alias ("pixel" — see _helpers.py::_AGENT_ALIASES), which never appears
    on a Ticket.assignee_agent. Resolve by prefix match against slugs that
    actually show up on tickets, rather than hardcoding a name map that
    would silently drift if an agent's .md file gets renamed.
    Returns None on no match or on an ambiguous (2+) match — better to drop
    that agent's cost into "unallocated" than guess wrong.
    """
    if short_or_full in known_assignee_slugs:
        return short_or_full
    matches = [s for s in known_assignee_slugs if s.startswith(f"{short_or_full}-")]
    return matches[0] if len(matches) == 1 else None


def _estimated_cost_by_mission_and_project(by_agent: list[dict]) -> dict:
    """Proportional-allocation ESTIMATE of spend per Mission/Project.

    There is no direct link from a cost record (routine run, heartbeat run)
    to the specific Goal/Ticket it was working on — costs are per-agent
    totals, not per-task. This allocates each agent's total cost across the
    Missions/Projects that agent's tickets belong to, weighted by ticket
    count, since ticket count is the only per-agent-per-mission signal that
    actually exists in the schema today. Explicitly labeled "estimated" in
    the response — never presented as an exact ledger.
    """
    ticket_rows = db.session.execute(db.text(
        "SELECT t.assignee_agent AS agent, "
        "       COALESCE(t.project_id, g.project_id) AS project_id, "
        "       p.title AS project_title, p.mission_id AS mission_id, m.title AS mission_title "
        "FROM tickets t "
        "LEFT JOIN goals g ON g.id = t.goal_id "
        "LEFT JOIN projects p ON p.id = COALESCE(t.project_id, g.project_id) "
        "LEFT JOIN missions m ON m.id = p.mission_id "
        "WHERE t.assignee_agent IS NOT NULL"
    )).fetchall()

    known_slugs = {r.agent for r in ticket_rows}

    # agent -> total ticket count (denominator for proportional allocation)
    agent_ticket_totals: dict[str, int] = {}
    # agent -> [(mission_id, mission_title, project_id, project_title, count)]
    agent_breakdown: dict[str, list] = {}
    for r in ticket_rows:
        agent_ticket_totals[r.agent] = agent_ticket_totals.get(r.agent, 0) + 1
        agent_breakdown.setdefault(r.agent, []).append(r)

    mission_totals: dict[int, dict] = {}
    project_totals: dict[int, dict] = {}
    unallocated = 0.0

    for entry in by_agent:
        agent_cost = float(entry.get("cost") or 0)
        if agent_cost <= 0:
            continue
        resolved = _resolve_agent_slug(entry["agent"], known_slugs)
        total_tickets = agent_ticket_totals.get(resolved or "", 0)
        if not resolved or total_tickets == 0:
            unallocated += agent_cost
            continue

        for r in agent_breakdown[resolved]:
            share = agent_cost / total_tickets
            if r.mission_id:
                bucket = mission_totals.setdefault(
                    r.mission_id, {"mission_id": r.mission_id, "title": r.mission_title, "estimated_cost": 0.0, "ticket_count": 0}
                )
                bucket["estimated_cost"] += share
                bucket["ticket_count"] += 1
            if r.project_id:
                bucket = project_totals.setdefault(
                    r.project_id, {"project_id": r.project_id, "title": r.project_title, "estimated_cost": 0.0, "ticket_count": 0}
                )
                bucket["estimated_cost"] += share
                bucket["ticket_count"] += 1
            if not r.mission_id and not r.project_id:
                unallocated += share

    by_mission = sorted(
        [{**v, "estimated_cost": round(v["estimated_cost"], 4)} for v in mission_totals.values()],
        key=lambda x: x["estimated_cost"], reverse=True,
    )
    by_project = sorted(
        [{**v, "estimated_cost": round(v["estimated_cost"], 4)} for v in project_totals.values()],
        key=lambda x: x["estimated_cost"], reverse=True,
    )
    return {
        "by_mission": by_mission,
        "by_project": by_project,
        "unallocated_cost": round(unallocated, 4),
        "methodology": "estimativa por alocação proporcional (custo do agente / tickets do agente) — "
                        "não é um ledger exato, pois custos são registrados por agente/rotina, não por ticket",
    }


_EMPTY_COSTS_RESPONSE = {
    "total_cost": 0, "by_routine": [], "by_agent": [], "today": 0, "week": 0, "month_estimate": 0,
    "by_mission": [], "by_project": [], "unallocated_cost": 0, "methodology": "",
}


@bp.route("/api/costs")
def costs_summary():
    content = safe_read(METRICS_PATH)
    if not content:
        return jsonify(_EMPTY_COSTS_RESPONSE)

    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return jsonify(_EMPTY_COSTS_RESPONSE)

    total = 0.0
    by_routine = []
    agent_costs = {}

    if isinstance(data, dict):
        for name, val in data.items():
            if isinstance(val, dict):
                cost = float(val.get("total_cost_usd", 0) or 0)
                tokens = int(val.get("total_input_tokens", 0) or 0) + int(val.get("total_output_tokens", 0) or 0)
                runs = int(val.get("runs", 0) or 0)
                avg_cost = float(val.get("avg_cost_usd", 0) or 0)
                agent = val.get("agent", "unknown")

                total += cost
                by_routine.append({
                    "name": name,
                    "cost": round(cost, 5),
                    "total_cost": round(cost, 5),
                    "avg_cost": round(avg_cost, 5),
                    "tokens": tokens,
                    "runs": runs,
                    "agent": agent,
                })
                agent_costs[agent] = agent_costs.get(agent, 0.0) + cost

    by_agent = [{"agent": a, "cost": round(c, 4)} for a, c in sorted(agent_costs.items(), key=lambda x: x[1], reverse=True)]

    # Aggregate heartbeat costs from DB
    hb_rows = (
        db.session.query(
            HeartbeatRun.heartbeat_id,
            func.count(HeartbeatRun.run_id).label("runs"),
            func.sum(func.coalesce(HeartbeatRun.cost_usd, 0)).label("total_cost_usd"),
            func.avg(func.coalesce(HeartbeatRun.cost_usd, 0)).label("avg_cost_usd"),
        )
        .group_by(HeartbeatRun.heartbeat_id)
        .all()
    )

    # Build a lookup for heartbeat agent names
    hb_agents = {h.id: h.agent for h in db.session.query(Heartbeat.id, Heartbeat.agent).all()}

    hb_total = 0.0
    hb_runs_total = 0
    by_heartbeat = []
    for row in hb_rows:
        hb_cost = float(row.total_cost_usd or 0)
        hb_avg = float(row.avg_cost_usd or 0)
        hb_count = int(row.runs or 0)
        hb_total += hb_cost
        hb_runs_total += hb_count
        by_heartbeat.append({
            "name": row.heartbeat_id,
            "agent": hb_agents.get(row.heartbeat_id, "unknown"),
            "runs": hb_count,
            "total_cost": round(hb_cost, 5),
            "avg_cost": round(hb_avg, 5),
        })
        hb_agent_name = hb_agents.get(row.heartbeat_id)
        if hb_agent_name:
            agent_costs[hb_agent_name] = agent_costs.get(hb_agent_name, 0.0) + hb_cost

    # Combined per-agent totals (routines + heartbeats) feed the Mission/
    # Project estimate below — kept separate from `by_agent` above (routines
    # only) so that response shape stays backward compatible.
    combined_by_agent = [{"agent": a, "cost": round(c, 4)} for a, c in agent_costs.items()]
    mission_project_estimate = _estimated_cost_by_mission_and_project(combined_by_agent)

    # Calculate today and week from JSONL logs (routines)
    today_cost = 0.0
    week_cost = 0.0
    daily_costs = {}
    today_str = date.today().isoformat()
    week_start = (date.today() - timedelta(days=7)).isoformat()

    if LOGS_DIR.is_dir():
        for f in LOGS_DIR.iterdir():
            if f.suffix != ".jsonl":
                continue
            text = safe_read(f)
            if not text:
                continue
            for line in text.strip().splitlines():
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = entry.get("timestamp", "")[:10]
                cost_val = float(entry.get("cost_usd", 0) or 0)
                if ts:
                    daily_costs[ts] = daily_costs.get(ts, 0.0) + cost_val
                if ts == today_str:
                    today_cost += cost_val
                if ts >= week_start:
                    week_cost += cost_val

    # Add heartbeat daily costs from DB
    hb_daily_rows = (
        db.session.query(
            func.substr(HeartbeatRun.started_at, 1, 10).label("day"),
            func.sum(func.coalesce(HeartbeatRun.cost_usd, 0)).label("cost"),
        )
        .group_by("day")
        .all()
    )
    for row in hb_daily_rows:
        day = row.day
        cost_val = float(row.cost or 0)
        if day:
            daily_costs[day] = daily_costs.get(day, 0.0) + cost_val
        if day == today_str:
            today_cost += cost_val
        if day and day >= week_start:
            week_cost += cost_val

    daily = [{"date": k, "cost": round(v, 4)} for k, v in sorted(daily_costs.items())]

    grand_total = total + hb_total
    routine_runs_total = sum(r["runs"] for r in by_routine)

    return jsonify({
        "total_cost": round(grand_total, 4),
        "routines_total_cost": round(total, 4),
        "heartbeats_total_cost": round(hb_total, 4),
        "today": round(today_cost, 4),
        "week": round(week_cost, 4),
        "month_estimate": round(grand_total, 4),
        "total_runs": routine_runs_total + hb_runs_total,
        "daily": daily,
        "by_routine": by_routine,
        "by_heartbeat": by_heartbeat,
        "by_agent": by_agent,
        "by_mission": mission_project_estimate["by_mission"],
        "by_project": mission_project_estimate["by_project"],
        "unallocated_cost": mission_project_estimate["unallocated_cost"],
        "methodology": mission_project_estimate["methodology"],
    })


@bp.route("/api/costs/daily")
def costs_daily():
    from_date = request.args.get("from", (date.today() - timedelta(days=7)).isoformat())
    to_date = request.args.get("to", date.today().isoformat())

    routines_daily = {}
    if LOGS_DIR.is_dir():
        for f in sorted(LOGS_DIR.iterdir()):
            if f.suffix != ".jsonl":
                continue
            text = safe_read(f)
            if not text:
                continue
            for line in text.strip().splitlines():
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = entry.get("timestamp", "")
                if not ts:
                    continue
                day = ts[:10]
                if from_date <= day <= to_date:
                    cost = float(entry.get("cost_usd", entry.get("cost", 0)) or 0)
                    routines_daily[day] = routines_daily.get(day, 0.0) + cost

    # Add heartbeat daily costs from DB
    heartbeats_daily = {}
    hb_rows = (
        db.session.query(
            func.substr(HeartbeatRun.started_at, 1, 10).label("day"),
            func.sum(func.coalesce(HeartbeatRun.cost_usd, 0)).label("cost"),
        )
        .filter(
            func.substr(HeartbeatRun.started_at, 1, 10) >= from_date,
            func.substr(HeartbeatRun.started_at, 1, 10) <= to_date,
        )
        .group_by("day")
        .all()
    )
    for row in hb_rows:
        if row.day:
            heartbeats_daily[row.day] = float(row.cost or 0)

    # Merge all days
    all_days = set(routines_daily.keys()) | set(heartbeats_daily.keys())
    combined = []
    for day in sorted(all_days):
        r_cost = routines_daily.get(day, 0.0)
        h_cost = heartbeats_daily.get(day, 0.0)
        combined.append({
            "date": day,
            "cost": round(r_cost + h_cost, 4),
            "routines_cost": round(r_cost, 4),
            "heartbeats_cost": round(h_cost, 4),
        })

    return jsonify(combined)
