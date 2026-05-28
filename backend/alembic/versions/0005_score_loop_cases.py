"""Score loop cases

Revision ID: 0005_score_loop_cases
Revises: 0004_ai_action_confirmations
Create Date: 2026-05-06
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005_score_loop_cases"
down_revision: Union[str, None] = "0004_ai_action_confirmations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

score_loop_case_status = sa.Enum(
    "CAPTURED",
    "UNSUPPORTED_PAUSED",
    "DRAFT_READY",
    "MANUAL_APPROVED",
    "MANUAL_REJECTED",
    "SUBMIT_CONFIRMATION_REQUIRED",
    name="scoreloopcasestatus",
)


def upgrade() -> None:
    score_loop_case_status.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "score_loop_cases",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("status", score_loop_case_status, nullable=False),
        sa.Column("task_type_key", sa.String(length=128), nullable=False),
        sa.Column("task_type_name", sa.String(length=256), nullable=False),
        sa.Column("task_catalog_item_id", sa.Integer(), nullable=True),
        sa.Column("account_user_id", sa.String(length=64), nullable=False),
        sa.Column("question_hash", sa.String(length=64), nullable=False),
        sa.Column("question_text", sa.Text(), nullable=False),
        sa.Column("choices_json", sa.Text(), nullable=False),
        sa.Column("ai_answer", sa.Text(), nullable=False),
        sa.Column("ai_reason", sa.Text(), nullable=False),
        sa.Column("final_answer", sa.Text(), nullable=False),
        sa.Column("manual_decision", sa.String(length=32), nullable=False),
        sa.Column("manual_note", sa.Text(), nullable=False),
        sa.Column("submit_confirmation_id", sa.Integer(), nullable=True),
        sa.Column("trace_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_score_loop_cases")),
    )
    for column in ["id", "status", "task_type_key", "task_catalog_item_id", "account_user_id", "question_hash", "manual_decision", "submit_confirmation_id", "trace_id", "created_at"]:
        op.create_index(op.f(f"ix_score_loop_cases_{column}"), "score_loop_cases", [column], unique=False)


def downgrade() -> None:
    for column in ["created_at", "trace_id", "submit_confirmation_id", "manual_decision", "question_hash", "account_user_id", "task_catalog_item_id", "task_type_key", "status", "id"]:
        op.drop_index(op.f(f"ix_score_loop_cases_{column}"), table_name="score_loop_cases")
    op.drop_table("score_loop_cases")
    score_loop_case_status.drop(op.get_bind(), checkfirst=True)