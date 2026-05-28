"""P7 rules and worker events

Revision ID: 0002_p7_rules_workers
Revises: 0001_p1_core_tables
Create Date: 2026-05-05
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_p7_rules_workers"
down_revision: Union[str, None] = "0001_p1_core_tables"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

rule_version_status = sa.Enum("DRAFT", "CANARY", "PUBLISHED", "ROLLED_BACK", name="ruleversionstatus")
worker_event_type = sa.Enum("HEARTBEAT", "BIND_ACCOUNT", "VERSION_UPDATE", "TASK_CLAIM", "EVENT_REPORT", "LOG_SUMMARY", name="workereventtype")


def upgrade() -> None:
    rule_version_status.create(op.get_bind(), checkfirst=True)
    worker_event_type.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "rule_versions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("status", rule_version_status, nullable=False),
        sa.Column("rule_json", sa.Text(), nullable=False),
        sa.Column("changelog", sa.Text(), nullable=False),
        sa.Column("canary_percent", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rule_versions")),
        sa.UniqueConstraint("version", name=op.f("uq_rule_versions_version")),
    )
    op.create_index(op.f("ix_rule_versions_created_at"), "rule_versions", ["created_at"], unique=False)
    op.create_index(op.f("ix_rule_versions_id"), "rule_versions", ["id"], unique=False)
    op.create_index(op.f("ix_rule_versions_published_at"), "rule_versions", ["published_at"], unique=False)
    op.create_index(op.f("ix_rule_versions_status"), "rule_versions", ["status"], unique=False)
    op.create_index(op.f("ix_rule_versions_version"), "rule_versions", ["version"], unique=False)

    op.create_table(
        "rule_publish_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("rule_version_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("from_status", sa.String(length=64), nullable=False),
        sa.Column("to_status", sa.String(length=64), nullable=False),
        sa.Column("canary_percent", sa.Integer(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("actor", sa.String(length=128), nullable=False),
        sa.Column("trace_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rule_publish_events")),
    )
    op.create_index(op.f("ix_rule_publish_events_action"), "rule_publish_events", ["action"], unique=False)
    op.create_index(op.f("ix_rule_publish_events_created_at"), "rule_publish_events", ["created_at"], unique=False)
    op.create_index(op.f("ix_rule_publish_events_id"), "rule_publish_events", ["id"], unique=False)
    op.create_index(op.f("ix_rule_publish_events_rule_version_id"), "rule_publish_events", ["rule_version_id"], unique=False)
    op.create_index(op.f("ix_rule_publish_events_trace_id"), "rule_publish_events", ["trace_id"], unique=False)

    op.create_table(
        "rule_hit_stats",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("rule_version_id", sa.Integer(), nullable=False),
        sa.Column("rule_key", sa.String(length=128), nullable=False),
        sa.Column("hits", sa.Integer(), nullable=False),
        sa.Column("misses", sa.Integer(), nullable=False),
        sa.Column("sample_task_name_id", sa.String(length=700), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rule_hit_stats")),
    )
    op.create_index(op.f("ix_rule_hit_stats_id"), "rule_hit_stats", ["id"], unique=False)
    op.create_index(op.f("ix_rule_hit_stats_rule_key"), "rule_hit_stats", ["rule_key"], unique=False)
    op.create_index(op.f("ix_rule_hit_stats_rule_version_id"), "rule_hit_stats", ["rule_version_id"], unique=False)

    op.create_table(
        "worker_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("worker_id", sa.String(length=128), nullable=False),
        sa.Column("event_type", worker_event_type, nullable=False),
        sa.Column("account_user_id", sa.String(length=64), nullable=False),
        sa.Column("task_id", sa.String(length=128), nullable=False),
        sa.Column("target_version", sa.String(length=64), nullable=False),
        sa.Column("severity", sa.String(length=32), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("trace_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_worker_events")),
    )
    op.create_index(op.f("ix_worker_events_account_user_id"), "worker_events", ["account_user_id"], unique=False)
    op.create_index(op.f("ix_worker_events_created_at"), "worker_events", ["created_at"], unique=False)
    op.create_index(op.f("ix_worker_events_event_type"), "worker_events", ["event_type"], unique=False)
    op.create_index(op.f("ix_worker_events_id"), "worker_events", ["id"], unique=False)
    op.create_index(op.f("ix_worker_events_severity"), "worker_events", ["severity"], unique=False)
    op.create_index(op.f("ix_worker_events_task_id"), "worker_events", ["task_id"], unique=False)
    op.create_index(op.f("ix_worker_events_trace_id"), "worker_events", ["trace_id"], unique=False)
    op.create_index(op.f("ix_worker_events_worker_id"), "worker_events", ["worker_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_worker_events_worker_id"), table_name="worker_events")
    op.drop_index(op.f("ix_worker_events_trace_id"), table_name="worker_events")
    op.drop_index(op.f("ix_worker_events_task_id"), table_name="worker_events")
    op.drop_index(op.f("ix_worker_events_severity"), table_name="worker_events")
    op.drop_index(op.f("ix_worker_events_id"), table_name="worker_events")
    op.drop_index(op.f("ix_worker_events_event_type"), table_name="worker_events")
    op.drop_index(op.f("ix_worker_events_created_at"), table_name="worker_events")
    op.drop_index(op.f("ix_worker_events_account_user_id"), table_name="worker_events")
    op.drop_table("worker_events")
    op.drop_index(op.f("ix_rule_hit_stats_rule_version_id"), table_name="rule_hit_stats")
    op.drop_index(op.f("ix_rule_hit_stats_rule_key"), table_name="rule_hit_stats")
    op.drop_index(op.f("ix_rule_hit_stats_id"), table_name="rule_hit_stats")
    op.drop_table("rule_hit_stats")
    op.drop_index(op.f("ix_rule_publish_events_trace_id"), table_name="rule_publish_events")
    op.drop_index(op.f("ix_rule_publish_events_rule_version_id"), table_name="rule_publish_events")
    op.drop_index(op.f("ix_rule_publish_events_id"), table_name="rule_publish_events")
    op.drop_index(op.f("ix_rule_publish_events_created_at"), table_name="rule_publish_events")
    op.drop_index(op.f("ix_rule_publish_events_action"), table_name="rule_publish_events")
    op.drop_table("rule_publish_events")
    op.drop_index(op.f("ix_rule_versions_version"), table_name="rule_versions")
    op.drop_index(op.f("ix_rule_versions_status"), table_name="rule_versions")
    op.drop_index(op.f("ix_rule_versions_published_at"), table_name="rule_versions")
    op.drop_index(op.f("ix_rule_versions_id"), table_name="rule_versions")
    op.drop_index(op.f("ix_rule_versions_created_at"), table_name="rule_versions")
    op.drop_table("rule_versions")
    worker_event_type.drop(op.get_bind(), checkfirst=True)
    rule_version_status.drop(op.get_bind(), checkfirst=True)
