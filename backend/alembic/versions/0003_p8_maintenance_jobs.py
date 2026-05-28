"""P8 maintenance jobs

Revision ID: 0003_p8_maintenance_jobs
Revises: 0002_p7_rules_workers
Create Date: 2026-05-05
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_p8_maintenance_jobs"
down_revision: Union[str, None] = "0002_p7_rules_workers"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

maintenance_job_status = sa.Enum("RUNNING", "COMPLETED", "WARNING", "FAILED", name="maintenancejobstatus")


def upgrade() -> None:
    maintenance_job_status.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "maintenance_job_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_key", sa.String(length=128), nullable=False),
        sa.Column("status", maintenance_job_status, nullable=False),
        sa.Column("trigger_type", sa.String(length=64), nullable=False),
        sa.Column("dry_run", sa.Integer(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=False),
        sa.Column("trace_id", sa.String(length=64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_maintenance_job_runs")),
    )
    op.create_index(op.f("ix_maintenance_job_runs_id"), "maintenance_job_runs", ["id"], unique=False)
    op.create_index(op.f("ix_maintenance_job_runs_job_key"), "maintenance_job_runs", ["job_key"], unique=False)
    op.create_index(op.f("ix_maintenance_job_runs_started_at"), "maintenance_job_runs", ["started_at"], unique=False)
    op.create_index(op.f("ix_maintenance_job_runs_status"), "maintenance_job_runs", ["status"], unique=False)
    op.create_index(op.f("ix_maintenance_job_runs_trace_id"), "maintenance_job_runs", ["trace_id"], unique=False)
    op.create_index(op.f("ix_maintenance_job_runs_trigger_type"), "maintenance_job_runs", ["trigger_type"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_maintenance_job_runs_trigger_type"), table_name="maintenance_job_runs")
    op.drop_index(op.f("ix_maintenance_job_runs_trace_id"), table_name="maintenance_job_runs")
    op.drop_index(op.f("ix_maintenance_job_runs_status"), table_name="maintenance_job_runs")
    op.drop_index(op.f("ix_maintenance_job_runs_started_at"), table_name="maintenance_job_runs")
    op.drop_index(op.f("ix_maintenance_job_runs_job_key"), table_name="maintenance_job_runs")
    op.drop_index(op.f("ix_maintenance_job_runs_id"), table_name="maintenance_job_runs")
    op.drop_table("maintenance_job_runs")
    maintenance_job_status.drop(op.get_bind(), checkfirst=True)
