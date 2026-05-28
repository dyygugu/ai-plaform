"""P1 core tables

Revision ID: 0001_p1_core_tables
Revises:
Create Date: 2026-05-05
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001_p1_core_tables"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

account_status = sa.Enum("ACTIVE", "NEEDS_LOGIN", "STALE", "DISABLED", name="accountstatus")
task_status_color = sa.Enum("GREEN", "BLUE", "GRAY", "RED", "YELLOW", name="taskstatuscolor")
task_visibility = sa.Enum("VISIBLE", "HIDDEN", "RESTORED", name="taskvisibility")
audit_severity = sa.Enum("INFO", "WARNING", "ERROR", "CRITICAL", name="auditseverity")
backup_status = sa.Enum("PLANNED", "RUNNING", "COMPLETED", "FAILED", name="backupstatus")
ai_job_status = sa.Enum("PLANNED", "MOCK_COMPLETED", "PROVIDER_GATED", "FAILED", name="aijobstatus")
worker_status = sa.Enum("ONLINE", "OFFLINE", "DEGRADED", name="workerstatus")
restore_drill_status = sa.Enum("PLANNED", "RUNNING", "PASSED", "FAILED", name="restoredrillstatus")


def upgrade() -> None:
    account_status.create(op.get_bind(), checkfirst=True)
    task_status_color.create(op.get_bind(), checkfirst=True)
    task_visibility.create(op.get_bind(), checkfirst=True)
    audit_severity.create(op.get_bind(), checkfirst=True)
    backup_status.create(op.get_bind(), checkfirst=True)
    ai_job_status.create(op.get_bind(), checkfirst=True)
    worker_status.create(op.get_bind(), checkfirst=True)
    restore_drill_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "aidp_accounts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=False),
        sa.Column("status", account_status, nullable=False),
        sa.Column("is_task_source", sa.Boolean(), nullable=False),
        sa.Column("auth_mode", sa.String(length=64), nullable=False),
        sa.Column("last_health_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_aidp_accounts")),
        sa.UniqueConstraint("user_id", name=op.f("uq_aidp_accounts_user_id")),
    )
    op.create_index(op.f("ix_aidp_accounts_id"), "aidp_accounts", ["id"], unique=False)
    op.create_index(op.f("ix_aidp_accounts_is_task_source"), "aidp_accounts", ["is_task_source"], unique=False)
    op.create_index(op.f("ix_aidp_accounts_status"), "aidp_accounts", ["status"], unique=False)
    op.create_index(op.f("ix_aidp_accounts_user_id"), "aidp_accounts", ["user_id"], unique=False)

    op.create_table(
        "task_catalog_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_account_user_id", sa.String(length=64), nullable=False),
        sa.Column("raw_task_name", sa.String(length=512), nullable=False),
        sa.Column("task_short_name", sa.String(length=512), nullable=False),
        sa.Column("task_id", sa.String(length=128), nullable=False),
        sa.Column("task_name_id", sa.String(length=700), nullable=False),
        sa.Column("task_status_raw", sa.String(length=128), nullable=False),
        sa.Column("task_status_color", task_status_color, nullable=False),
        sa.Column("pending_raw", sa.String(length=128), nullable=False),
        sa.Column("visibility", task_visibility, nullable=False),
        sa.Column("last_task_page_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_task_page_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_task_catalog_items")),
        sa.UniqueConstraint("source_account_user_id", "task_id", name="uq_task_catalog_source_task"),
    )
    op.create_index(op.f("ix_task_catalog_items_id"), "task_catalog_items", ["id"], unique=False)
    op.create_index(op.f("ix_task_catalog_items_source_account_user_id"), "task_catalog_items", ["source_account_user_id"], unique=False)
    op.create_index(op.f("ix_task_catalog_items_task_id"), "task_catalog_items", ["task_id"], unique=False)
    op.create_index(op.f("ix_task_catalog_items_task_name_id"), "task_catalog_items", ["task_name_id"], unique=False)
    op.create_index(op.f("ix_task_catalog_items_visibility"), "task_catalog_items", ["visibility"], unique=False)

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("severity", audit_severity, nullable=False),
        sa.Column("actor", sa.String(length=128), nullable=False),
        sa.Column("target_type", sa.String(length=128), nullable=False),
        sa.Column("target_id", sa.String(length=128), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("trace_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_logs")),
    )
    op.create_index(op.f("ix_audit_logs_created_at"), "audit_logs", ["created_at"], unique=False)
    op.create_index(op.f("ix_audit_logs_event_type"), "audit_logs", ["event_type"], unique=False)
    op.create_index(op.f("ix_audit_logs_id"), "audit_logs", ["id"], unique=False)
    op.create_index(op.f("ix_audit_logs_severity"), "audit_logs", ["severity"], unique=False)
    op.create_index(op.f("ix_audit_logs_trace_id"), "audit_logs", ["trace_id"], unique=False)


    op.create_table(
        "backup_jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("backup_type", sa.String(length=64), nullable=False),
        sa.Column("status", backup_status, nullable=False),
        sa.Column("local_retention_days", sa.Integer(), nullable=False),
        sa.Column("external_retention_days", sa.Integer(), nullable=False),
        sa.Column("cleanup_time", sa.String(length=16), nullable=False),
        sa.Column("target_path", sa.String(length=512), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("trace_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_backup_jobs")),
    )
    op.create_index(op.f("ix_backup_jobs_backup_type"), "backup_jobs", ["backup_type"], unique=False)
    op.create_index(op.f("ix_backup_jobs_created_at"), "backup_jobs", ["created_at"], unique=False)
    op.create_index(op.f("ix_backup_jobs_id"), "backup_jobs", ["id"], unique=False)
    op.create_index(op.f("ix_backup_jobs_status"), "backup_jobs", ["status"], unique=False)
    op.create_index(op.f("ix_backup_jobs_trace_id"), "backup_jobs", ["trace_id"], unique=False)


    op.create_table(
        "ai_jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("status", ai_job_status, nullable=False),
        sa.Column("prompt_summary", sa.String(length=256), nullable=False),
        sa.Column("result_summary", sa.Text(), nullable=False),
        sa.Column("queue_wait_ms", sa.Integer(), nullable=False),
        sa.Column("upstream_ms", sa.Integer(), nullable=False),
        sa.Column("total_ms", sa.Integer(), nullable=False),
        sa.Column("trace_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_jobs")),
    )
    op.create_index(op.f("ix_ai_jobs_created_at"), "ai_jobs", ["created_at"], unique=False)
    op.create_index(op.f("ix_ai_jobs_id"), "ai_jobs", ["id"], unique=False)
    op.create_index(op.f("ix_ai_jobs_status"), "ai_jobs", ["status"], unique=False)
    op.create_index(op.f("ix_ai_jobs_trace_id"), "ai_jobs", ["trace_id"], unique=False)

    op.create_table(
        "workers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("worker_id", sa.String(length=128), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=False),
        sa.Column("status", worker_status, nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("current_account_user_id", sa.String(length=64), nullable=False),
        sa.Column("current_task_id", sa.String(length=128), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workers")),
        sa.UniqueConstraint("worker_id", name=op.f("uq_workers_worker_id")),
    )
    op.create_index(op.f("ix_workers_id"), "workers", ["id"], unique=False)
    op.create_index(op.f("ix_workers_last_heartbeat_at"), "workers", ["last_heartbeat_at"], unique=False)
    op.create_index(op.f("ix_workers_status"), "workers", ["status"], unique=False)
    op.create_index(op.f("ix_workers_worker_id"), "workers", ["worker_id"], unique=False)


    op.create_table(
        "restore_drills",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("status", restore_drill_status, nullable=False),
        sa.Column("checklist_json", sa.Text(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("trace_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_restore_drills")),
    )
    op.create_index(op.f("ix_restore_drills_created_at"), "restore_drills", ["created_at"], unique=False)
    op.create_index(op.f("ix_restore_drills_id"), "restore_drills", ["id"], unique=False)
    op.create_index(op.f("ix_restore_drills_status"), "restore_drills", ["status"], unique=False)
    op.create_index(op.f("ix_restore_drills_trace_id"), "restore_drills", ["trace_id"], unique=False)

    op.create_table(
        "earnings_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_user_id", sa.String(length=64), nullable=False),
        sa.Column("source_label", sa.String(length=128), nullable=False),
        sa.Column("income_1_name", sa.String(length=128), nullable=False),
        sa.Column("income_1_value", sa.Numeric(12, 2), nullable=False),
        sa.Column("income_2_name", sa.String(length=128), nullable=False),
        sa.Column("income_2_value", sa.Numeric(12, 2), nullable=False),
        sa.Column("income_3_name", sa.String(length=128), nullable=False),
        sa.Column("income_3_value", sa.Numeric(12, 2), nullable=False),
        sa.Column("today_income", sa.Numeric(12, 2), nullable=False),
        sa.Column("hourly_income", sa.Numeric(12, 2), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_earnings_snapshots")),
    )
    op.create_index(op.f("ix_earnings_snapshots_account_user_id"), "earnings_snapshots", ["account_user_id"], unique=False)
    op.create_index(op.f("ix_earnings_snapshots_captured_at"), "earnings_snapshots", ["captured_at"], unique=False)
    op.create_index(op.f("ix_earnings_snapshots_id"), "earnings_snapshots", ["id"], unique=False)

    op.create_table(
        "task_catalog_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("task_catalog_item_id", sa.Integer(), nullable=False),
        sa.Column("source_account_user_id", sa.String(length=64), nullable=False),
        sa.Column("task_id", sa.String(length=128), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("status_raw", sa.String(length=128), nullable=False),
        sa.Column("pending_raw", sa.String(length=128), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_task_catalog_events")),
    )
    op.create_index(op.f("ix_task_catalog_events_created_at"), "task_catalog_events", ["created_at"], unique=False)
    op.create_index(op.f("ix_task_catalog_events_event_type"), "task_catalog_events", ["event_type"], unique=False)
    op.create_index(op.f("ix_task_catalog_events_id"), "task_catalog_events", ["id"], unique=False)
    op.create_index(op.f("ix_task_catalog_events_source_account_user_id"), "task_catalog_events", ["source_account_user_id"], unique=False)
    op.create_index(op.f("ix_task_catalog_events_task_catalog_item_id"), "task_catalog_events", ["task_catalog_item_id"], unique=False)
    op.create_index(op.f("ix_task_catalog_events_task_id"), "task_catalog_events", ["task_id"], unique=False)

    op.create_table(
        "task_rule_configs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("prefix_rules_json", sa.Text(), nullable=False),
        sa.Column("manual_short_names_json", sa.Text(), nullable=False),
        sa.Column("updated_by", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_task_rule_configs")),
    )
    op.create_index(op.f("ix_task_rule_configs_id"), "task_rule_configs", ["id"], unique=False)

    op.create_table(
        "runtime_configs",
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("updated_by", sa.String(length=128), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_runtime_configs")),
    )

def downgrade() -> None:
    op.drop_table("runtime_configs")
    op.drop_index(op.f("ix_task_rule_configs_id"), table_name="task_rule_configs")
    op.drop_table("task_rule_configs")
    op.drop_index(op.f("ix_task_catalog_events_task_id"), table_name="task_catalog_events")
    op.drop_index(op.f("ix_task_catalog_events_task_catalog_item_id"), table_name="task_catalog_events")
    op.drop_index(op.f("ix_task_catalog_events_source_account_user_id"), table_name="task_catalog_events")
    op.drop_index(op.f("ix_task_catalog_events_id"), table_name="task_catalog_events")
    op.drop_index(op.f("ix_task_catalog_events_event_type"), table_name="task_catalog_events")
    op.drop_index(op.f("ix_task_catalog_events_created_at"), table_name="task_catalog_events")
    op.drop_table("task_catalog_events")
    op.drop_index(op.f("ix_earnings_snapshots_id"), table_name="earnings_snapshots")
    op.drop_index(op.f("ix_earnings_snapshots_captured_at"), table_name="earnings_snapshots")
    op.drop_index(op.f("ix_earnings_snapshots_account_user_id"), table_name="earnings_snapshots")
    op.drop_table("earnings_snapshots")
    op.drop_index(op.f("ix_restore_drills_trace_id"), table_name="restore_drills")
    op.drop_index(op.f("ix_restore_drills_status"), table_name="restore_drills")
    op.drop_index(op.f("ix_restore_drills_id"), table_name="restore_drills")
    op.drop_index(op.f("ix_restore_drills_created_at"), table_name="restore_drills")
    op.drop_table("restore_drills")
    op.drop_index(op.f("ix_workers_worker_id"), table_name="workers")
    op.drop_index(op.f("ix_workers_status"), table_name="workers")
    op.drop_index(op.f("ix_workers_last_heartbeat_at"), table_name="workers")
    op.drop_index(op.f("ix_workers_id"), table_name="workers")
    op.drop_table("workers")
    op.drop_index(op.f("ix_ai_jobs_trace_id"), table_name="ai_jobs")
    op.drop_index(op.f("ix_ai_jobs_status"), table_name="ai_jobs")
    op.drop_index(op.f("ix_ai_jobs_id"), table_name="ai_jobs")
    op.drop_index(op.f("ix_ai_jobs_created_at"), table_name="ai_jobs")
    op.drop_table("ai_jobs")
    op.drop_index(op.f("ix_backup_jobs_trace_id"), table_name="backup_jobs")
    op.drop_index(op.f("ix_backup_jobs_status"), table_name="backup_jobs")
    op.drop_index(op.f("ix_backup_jobs_id"), table_name="backup_jobs")
    op.drop_index(op.f("ix_backup_jobs_created_at"), table_name="backup_jobs")
    op.drop_index(op.f("ix_backup_jobs_backup_type"), table_name="backup_jobs")
    op.drop_table("backup_jobs")
    op.drop_index(op.f("ix_audit_logs_trace_id"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_severity"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_id"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_event_type"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_created_at"), table_name="audit_logs")
    op.drop_table("audit_logs")
    op.drop_index(op.f("ix_task_catalog_items_visibility"), table_name="task_catalog_items")
    op.drop_index(op.f("ix_task_catalog_items_task_name_id"), table_name="task_catalog_items")
    op.drop_index(op.f("ix_task_catalog_items_task_id"), table_name="task_catalog_items")
    op.drop_index(op.f("ix_task_catalog_items_source_account_user_id"), table_name="task_catalog_items")
    op.drop_index(op.f("ix_task_catalog_items_id"), table_name="task_catalog_items")
    op.drop_table("task_catalog_items")
    op.drop_index(op.f("ix_aidp_accounts_user_id"), table_name="aidp_accounts")
    op.drop_index(op.f("ix_aidp_accounts_status"), table_name="aidp_accounts")
    op.drop_index(op.f("ix_aidp_accounts_is_task_source"), table_name="aidp_accounts")
    op.drop_index(op.f("ix_aidp_accounts_id"), table_name="aidp_accounts")
    op.drop_table("aidp_accounts")
    restore_drill_status.drop(op.get_bind(), checkfirst=True)
    worker_status.drop(op.get_bind(), checkfirst=True)
    ai_job_status.drop(op.get_bind(), checkfirst=True)
    backup_status.drop(op.get_bind(), checkfirst=True)
    audit_severity.drop(op.get_bind(), checkfirst=True)
    task_visibility.drop(op.get_bind(), checkfirst=True)
    task_status_color.drop(op.get_bind(), checkfirst=True)
    account_status.drop(op.get_bind(), checkfirst=True)


