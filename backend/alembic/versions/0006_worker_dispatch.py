"""Worker dispatch leases and commands

Revision ID: 0006_worker_dispatch
Revises: 0005_score_loop_cases
Create Date: 2026-05-12
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006_worker_dispatch"
down_revision: Union[str, None] = "0005_score_loop_cases"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

worker_lease_status = sa.Enum("ACTIVE", "RELEASED", "RECLAIMED", "TIMED_OUT", "SUSPENDED", name="workerleasestatus")
worker_command_status = sa.Enum(
    "QUEUED",
    "CLAIMED",
    "RUNNING",
    "SUCCEEDED",
    "FAILED",
    "TIMED_OUT",
    "CANCELLED",
    name="workercommandstatus",
)


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TYPE workerstatus ADD VALUE IF NOT EXISTS 'PENDING_APPROVAL'")
        op.execute("ALTER TYPE workerstatus ADD VALUE IF NOT EXISTS 'REJECTED'")
        op.execute("ALTER TYPE workerstatus ADD VALUE IF NOT EXISTS 'DISABLED'")
        op.execute("ALTER TYPE workereventtype ADD VALUE IF NOT EXISTS 'COMMAND'")
        op.execute("ALTER TYPE workereventtype ADD VALUE IF NOT EXISTS 'LEASE'")
        op.execute("ALTER TYPE workerleasestatus ADD VALUE IF NOT EXISTS 'SUSPENDED'")

    worker_lease_status.create(bind, checkfirst=True)
    worker_command_status.create(bind, checkfirst=True)

    op.add_column("workers", sa.Column("is_platform_worker", sa.Boolean(), server_default=sa.false(), nullable=False))
    op.add_column("workers", sa.Column("estimated_http_account_slots", sa.Integer(), server_default="0", nullable=False))
    op.add_column("workers", sa.Column("configured_http_account_slots", sa.Integer(), server_default="0", nullable=False))
    op.add_column("workers", sa.Column("effective_http_account_slots", sa.Integer(), server_default="0", nullable=False))
    op.add_column("workers", sa.Column("health_status", sa.String(length=32), server_default="unknown", nullable=False))
    op.add_column("workers", sa.Column("health_checked_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("workers", sa.Column("health_fail_reasons", sa.Text(), server_default="", nullable=False))
    op.add_column("workers", sa.Column("disabled_reason", sa.Text(), server_default="", nullable=False))
    op.create_index(op.f("ix_workers_is_platform_worker"), "workers", ["is_platform_worker"], unique=False)
    op.create_index(op.f("ix_workers_health_status"), "workers", ["health_status"], unique=False)

    op.create_table(
        "worker_account_task_leases",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("lease_id", sa.String(length=64), nullable=False),
        sa.Column("worker_id", sa.String(length=128), nullable=False),
        sa.Column("account_user_id", sa.String(length=64), nullable=False),
        sa.Column("task_id", sa.String(length=128), nullable=False),
        sa.Column("status", worker_lease_status, nullable=False),
        sa.Column("failure_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("stop_reason", sa.Text(), server_default="", nullable=False),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reclaimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_worker_account_task_leases")),
        sa.UniqueConstraint("lease_id", name=op.f("uq_worker_account_task_leases_lease_id")),
    )
    for column in ["id", "lease_id", "worker_id", "account_user_id", "task_id", "status", "last_heartbeat_at"]:
        op.create_index(op.f(f"ix_worker_account_task_leases_{column}"), "worker_account_task_leases", [column], unique=False)

    op.create_table(
        "worker_commands",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("command_id", sa.String(length=64), nullable=False),
        sa.Column("retry_of_command_id", sa.String(length=64), server_default="", nullable=False),
        sa.Column("worker_id", sa.String(length=128), server_default="", nullable=False),
        sa.Column("command_type", sa.String(length=64), nullable=False),
        sa.Column("status", worker_command_status, nullable=False),
        sa.Column("account_user_id", sa.String(length=64), server_default="", nullable=False),
        sa.Column("task_id", sa.String(length=128), server_default="", nullable=False),
        sa.Column("payload_json", sa.Text(), server_default="", nullable=False),
        sa.Column("result_json", sa.Text(), server_default="", nullable=False),
        sa.Column("last_renewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("timed_out_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("timeout_seconds", sa.Integer(), server_default="180", nullable=False),
        sa.Column("audit_note", sa.Text(), server_default="", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_worker_commands")),
        sa.UniqueConstraint("command_id", name=op.f("uq_worker_commands_command_id")),
    )
    for column in [
        "id",
        "command_id",
        "retry_of_command_id",
        "worker_id",
        "command_type",
        "status",
        "account_user_id",
        "task_id",
        "last_renewed_at",
    ]:
        op.create_index(op.f(f"ix_worker_commands_{column}"), "worker_commands", [column], unique=False)


def downgrade() -> None:
    for column in ["last_renewed_at", "task_id", "account_user_id", "status", "command_type", "worker_id", "retry_of_command_id", "command_id", "id"]:
        op.drop_index(op.f(f"ix_worker_commands_{column}"), table_name="worker_commands")
    op.drop_table("worker_commands")

    for column in ["last_heartbeat_at", "status", "task_id", "account_user_id", "worker_id", "lease_id", "id"]:
        op.drop_index(op.f(f"ix_worker_account_task_leases_{column}"), table_name="worker_account_task_leases")
    op.drop_table("worker_account_task_leases")

    op.drop_index(op.f("ix_workers_health_status"), table_name="workers")
    op.drop_index(op.f("ix_workers_is_platform_worker"), table_name="workers")
    for column in [
        "disabled_reason",
        "health_fail_reasons",
        "health_checked_at",
        "health_status",
        "effective_http_account_slots",
        "configured_http_account_slots",
        "estimated_http_account_slots",
        "is_platform_worker",
    ]:
        op.drop_column("workers", column)

    worker_command_status.drop(op.get_bind(), checkfirst=True)
    worker_lease_status.drop(op.get_bind(), checkfirst=True)
