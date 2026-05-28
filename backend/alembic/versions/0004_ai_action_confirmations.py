"""AI action confirmations

Revision ID: 0004_ai_action_confirmations
Revises: 0003_p8_maintenance_jobs
Create Date: 2026-05-06
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004_ai_action_confirmations"
down_revision: Union[str, None] = "0003_p8_maintenance_jobs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ai_action_confirmation_status = sa.Enum("PENDING", "APPROVED", "REJECTED", "EXPIRED", name="aiactionconfirmationstatus")


def upgrade() -> None:
    ai_action_confirmation_status.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "ai_action_confirmations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("status", ai_action_confirmation_status, nullable=False),
        sa.Column("action_key", sa.String(length=128), nullable=False),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column("risk_level", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("source_trace_id", sa.String(length=64), nullable=False),
        sa.Column("source_ai_job_id", sa.Integer(), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("rollback_hint", sa.Text(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("requested_by", sa.String(length=128), nullable=False),
        sa.Column("reviewed_by", sa.String(length=128), nullable=True),
        sa.Column("confirmation_note", sa.Text(), nullable=False),
        sa.Column("trace_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_action_confirmations")),
    )
    for column in ["id", "status", "action_key", "risk_level", "source", "source_trace_id", "source_ai_job_id", "trace_id", "created_at"]:
        op.create_index(op.f(f"ix_ai_action_confirmations_{column}"), "ai_action_confirmations", [column], unique=False)


def downgrade() -> None:
    for column in ["created_at", "trace_id", "source_ai_job_id", "source_trace_id", "source", "risk_level", "action_key", "status", "id"]:
        op.drop_index(op.f(f"ix_ai_action_confirmations_{column}"), table_name="ai_action_confirmations")
    op.drop_table("ai_action_confirmations")
    ai_action_confirmation_status.drop(op.get_bind(), checkfirst=True)