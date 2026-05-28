"""Worker lease recovery metadata

Revision ID: 0007_worker_lease_recovery
Revises: 0006_worker_dispatch
Create Date: 2026-05-12
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007_worker_lease_recovery"
down_revision: Union[str, None] = "0006_worker_dispatch"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("worker_account_task_leases", sa.Column("last_error_code", sa.String(length=64), server_default="", nullable=False))
    op.add_column("worker_account_task_leases", sa.Column("recovery_type", sa.String(length=64), server_default="", nullable=False))
    op.add_column("worker_account_task_leases", sa.Column("cooldown_until", sa.DateTime(timezone=True), nullable=True))
    op.add_column("worker_account_task_leases", sa.Column("recovery_attempt_count", sa.Integer(), server_default="0", nullable=False))
    op.add_column("worker_account_task_leases", sa.Column("recovered_at", sa.DateTime(timezone=True), nullable=True))
    for column in ["last_error_code", "recovery_type", "cooldown_until"]:
        op.create_index(op.f(f"ix_worker_account_task_leases_{column}"), "worker_account_task_leases", [column], unique=False)


def downgrade() -> None:
    for column in ["cooldown_until", "recovery_type", "last_error_code"]:
        op.drop_index(op.f(f"ix_worker_account_task_leases_{column}"), table_name="worker_account_task_leases")
    for column in ["recovered_at", "recovery_attempt_count", "cooldown_until", "recovery_type", "last_error_code"]:
        op.drop_column("worker_account_task_leases", column)
