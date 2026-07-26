"""Hermes runtime schema on the Alembic-managed database.

Revision ID: 0012
Revises: 0011
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(conn, name: str) -> bool:
    return inspect(conn).has_table(name)


def _has_column(conn, table: str, column: str) -> bool:
    return column in {item["name"] for item in inspect(conn).get_columns(table)}


def _has_index(conn, table: str, name: str) -> bool:
    return name in {item["name"] for item in inspect(conn).get_indexes(table)}


def _add_column(conn, table: str, column: sa.Column) -> None:
    if _has_table(conn, table) and not _has_column(conn, table, column.name):
        op.add_column(table, column)


def _set_ticket_status_constraint(conn, statuses: tuple[str, ...]) -> None:
    if not _has_table(conn, "tickets"):
        return
    constraint_names = {
        item["name"] for item in inspect(conn).get_check_constraints("tickets")
    }
    quoted = ",".join(f"'{status}'" for status in statuses)
    recreate = "always" if conn.dialect.name == "sqlite" else "auto"
    with op.batch_alter_table("tickets", recreate=recreate) as batch:
        if "ck_ticket_status" in constraint_names:
            batch.drop_constraint("ck_ticket_status", type_="check")
        batch.create_check_constraint("ck_ticket_status", f"status IN ({quoted})")


def upgrade() -> None:
    conn = op.get_bind()

    _set_ticket_status_constraint(
        conn,
        ("open", "in_progress", "blocked", "review", "resolved", "closed", "archived"),
    )

    _add_column(conn, "heartbeats", sa.Column("handler", sa.Text, nullable=True))
    for name, column_type in (
        ("decision_action", sa.Text()),
        ("decision_json", sa.Text()),
        ("provider", sa.String(64)),
        ("requested_profile", sa.String(64)),
        ("resolved_profile", sa.String(64)),
        ("exit_code", sa.Integer()),
        ("fallback_from", sa.String(64)),
        ("stdout_tail", sa.Text()),
        ("stderr_tail", sa.Text()),
        ("runtime_run_id", sa.String(36)),
    ):
        _add_column(conn, "heartbeat_runs", sa.Column(name, column_type, nullable=True))

    for name, column_type, default in (
        ("hermes_profile", sa.String(64), None),
        ("ticket_id", sa.String(36), None),
        ("attempt", sa.Integer(), "0"),
        ("provider", sa.String(64), None),
        ("resolved_profile", sa.String(64), None),
        ("workflow_policy", sa.String(100), None),
        ("fallback_reason", sa.Text(), None),
        ("runtime_run_id", sa.String(36), None),
    ):
        kwargs = {"nullable": True}
        if name == "attempt":
            kwargs = {"nullable": False, "server_default": default}
        _add_column(conn, "scheduled_tasks", sa.Column(name, column_type, **kwargs))

    if _has_table(conn, "scheduled_tasks"):
        if not _has_index(conn, "scheduled_tasks", "ix_scheduled_tasks_ticket_id"):
            op.create_index("ix_scheduled_tasks_ticket_id", "scheduled_tasks", ["ticket_id"])
        if not _has_index(conn, "scheduled_tasks", "ix_scheduled_tasks_runtime_run_id"):
            op.create_index("ix_scheduled_tasks_runtime_run_id", "scheduled_tasks", ["runtime_run_id"])

    if not _has_table(conn, "runtime_runs"):
        op.create_table(
            "runtime_runs",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("task_id", sa.Integer, sa.ForeignKey("scheduled_tasks.id"), nullable=True),
            sa.Column("origin_type", sa.String(32), nullable=False, server_default="scheduled_task"),
            sa.Column("origin_id", sa.String(128), nullable=True),
            sa.Column("ticket_id", sa.String(36), sa.ForeignKey("tickets.id"), nullable=True),
            sa.Column("goal_id", sa.Integer, sa.ForeignKey("goals.id"), nullable=True),
            sa.Column("agent_slug", sa.String(100), nullable=True),
            sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
            sa.Column("attempt", sa.Integer, nullable=False, server_default="1"),
            sa.Column("requested_profile", sa.String(64), nullable=True),
            sa.Column("resolved_profile", sa.String(64), nullable=True),
            sa.Column("runtime_provider", sa.String(64), nullable=True),
            sa.Column("workflow_slug", sa.String(100), nullable=True),
            sa.Column("workflow_hash", sa.String(64), nullable=True),
            sa.Column("correlation_id", sa.String(128), nullable=False),
            sa.Column("queued_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("result_summary", sa.Text, nullable=True),
            sa.Column("error", sa.Text, nullable=True),
            sa.Column("exit_code", sa.Integer, nullable=True),
        )
        for name, columns in (
            ("ix_runtime_runs_task_id", ["task_id"]),
            ("ix_runtime_runs_origin", ["origin_type", "origin_id"]),
            ("ix_runtime_runs_ticket_id", ["ticket_id"]),
            ("ix_runtime_runs_goal_id", ["goal_id"]),
            ("ix_runtime_runs_agent_slug", ["agent_slug"]),
            ("ix_runtime_runs_status", ["status"]),
            ("ix_runtime_runs_correlation_id", ["correlation_id"]),
        ):
            op.create_index(name, "runtime_runs", columns)

    if not _has_table(conn, "runtime_run_approvals"):
        op.create_table(
            "runtime_run_approvals",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("run_id", sa.String(36), sa.ForeignKey("runtime_runs.id", ondelete="CASCADE"), nullable=False),
            sa.Column("action_hash", sa.String(64), nullable=False),
            sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
            sa.Column("decided_by", sa.String(80), nullable=True),
            sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
        op.create_index("ix_runtime_run_approvals_run_id", "runtime_run_approvals", ["run_id"])

    if not _has_table(conn, "runtime_run_evidence"):
        op.create_table(
            "runtime_run_evidence",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("run_id", sa.String(36), sa.ForeignKey("runtime_runs.id", ondelete="CASCADE"), nullable=False),
            sa.Column("reference", sa.String(1000), nullable=False),
            sa.Column("mime_type", sa.String(100), nullable=True),
            sa.Column("checksum", sa.String(64), nullable=True),
            sa.Column("size_bytes", sa.Integer, nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
        op.create_index("ix_runtime_run_evidence_run_id", "runtime_run_evidence", ["run_id"])

    if not _has_table(conn, "event_outbox"):
        op.create_table(
            "event_outbox",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("event_type", sa.String(100), nullable=False),
            sa.Column("subject", sa.String(200), nullable=False),
            sa.Column("correlation_id", sa.String(128), nullable=False),
            sa.Column("payload_json", sa.Text, nullable=False),
            sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
            sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )

    if not _has_table(conn, "event_inbox"):
        op.create_table(
            "event_inbox",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("source", sa.String(100), nullable=False),
            sa.Column("external_event_id", sa.String(255), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("source", "external_event_id", name="uq_event_inbox_source_id"),
        )

    if not _has_table(conn, "control_api_idempotency"):
        op.create_table(
            "control_api_idempotency",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("operation", sa.String(100), nullable=False),
            sa.Column("key", sa.String(255), nullable=False),
            sa.Column("request_hash", sa.String(64), nullable=False),
            sa.Column("response_status", sa.Integer, nullable=False),
            sa.Column("response_json", sa.Text, nullable=False),
            sa.Column("created_at", sa.String(30), nullable=False),
            sa.UniqueConstraint("operation", "key", name="uq_control_api_idempotency"),
        )

    if not _has_table(conn, "control_api_audit_log"):
        op.create_table(
            "control_api_audit_log",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("operation", sa.String(100), nullable=False),
            sa.Column("resource", sa.String(200), nullable=False),
            sa.Column("outcome", sa.String(30), nullable=False),
            sa.Column("correlation_id", sa.String(128), nullable=False),
            sa.Column("created_at", sa.String(30), nullable=False),
        )


def downgrade() -> None:
    conn = op.get_bind()
    for index_name in (
        "ix_scheduled_tasks_runtime_run_id",
        "ix_scheduled_tasks_ticket_id",
    ):
        if _has_index(conn, "scheduled_tasks", index_name):
            op.drop_index(index_name, table_name="scheduled_tasks")

    for table in (
        "control_api_audit_log",
        "control_api_idempotency",
        "event_inbox",
        "event_outbox",
        "runtime_run_evidence",
        "runtime_run_approvals",
        "runtime_runs",
    ):
        if _has_table(conn, table):
            op.drop_table(table)

    if _has_table(conn, "tickets"):
        conn.execute(sa.text("UPDATE tickets SET status = 'closed' WHERE status = 'archived'"))
        _set_ticket_status_constraint(
            conn,
            ("open", "in_progress", "blocked", "review", "resolved", "closed"),
        )

    for column in (
        "runtime_run_id", "stderr_tail", "stdout_tail", "fallback_from",
        "exit_code", "resolved_profile", "requested_profile", "provider",
        "decision_json", "decision_action",
    ):
        if _has_column(conn, "heartbeat_runs", column):
            op.drop_column("heartbeat_runs", column)
    if _has_column(conn, "heartbeats", "handler"):
        op.drop_column("heartbeats", "handler")
    for column in (
        "runtime_run_id", "fallback_reason", "workflow_policy", "resolved_profile",
        "provider", "attempt", "ticket_id", "hermes_profile",
    ):
        if _has_column(conn, "scheduled_tasks", column):
            op.drop_column("scheduled_tasks", column)
