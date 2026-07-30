"""新增高频 FAQ 候选及去重出现记录。

Revision ID: 0002_frequent_faq_candidates
Revises: 0001_initial
Create Date: 2026-07-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_frequent_faq_candidates"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建候选主题、草稿状态和滚动窗口出现记录。"""
    op.create_table(
        "knowledge_candidates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("canonical_key", sa.String(length=64), nullable=False, unique=True),
        sa.Column("canonical_question", sa.Text(), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("total_occurrences", sa.Integer(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_threshold_total", sa.Integer(), nullable=False),
        sa.Column("last_reminded_total", sa.Integer(), nullable=False),
        sa.Column("last_reminded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notification_pending", sa.Boolean(), nullable=False),
        sa.Column("examples", sa.JSON(), nullable=False),
        sa.Column("examples_version", sa.Integer(), nullable=False),
        sa.Column("draft_status", sa.String(length=24), nullable=False),
        sa.Column("draft_generation", sa.Integer(), nullable=False),
        sa.Column("draft_attempts", sa.Integer(), nullable=False),
        sa.Column("draft_examples_version", sa.Integer(), nullable=False),
        sa.Column("draft_payload", sa.JSON(), nullable=True),
        sa.Column("knowledge_entry_id", sa.Integer(), nullable=True, unique=True),
        sa.Column("snoozed_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "total_occurrences >= 0", name="ck_candidate_total_nonnegative"
        ),
        sa.CheckConstraint(
            "last_threshold_total >= 0", name="ck_candidate_threshold_nonnegative"
        ),
        sa.CheckConstraint(
            "last_reminded_total >= 0", name="ck_candidate_reminded_nonnegative"
        ),
        sa.CheckConstraint(
            "examples_version >= 0", name="ck_candidate_examples_version_nonnegative"
        ),
        sa.CheckConstraint(
            "draft_generation >= 0", name="ck_candidate_generation_nonnegative"
        ),
        sa.CheckConstraint(
            "draft_attempts >= 0", name="ck_candidate_attempts_nonnegative"
        ),
        sa.ForeignKeyConstraint(["knowledge_entry_id"], ["knowledge_entries.id"]),
    )
    op.create_index(
        "ix_candidate_status_snoozed",
        "knowledge_candidates",
        ["status", "snoozed_until"],
    )
    op.create_table(
        "knowledge_candidate_occurrences",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("candidate_id", sa.Integer(), nullable=False),
        sa.Column("source_message_id", sa.String(length=128), nullable=False, unique=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["candidate_id"],
            ["knowledge_candidates.id"],
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_candidate_occurrence_window",
        "knowledge_candidate_occurrences",
        ["candidate_id", "occurred_at"],
    )


def downgrade() -> None:
    """按依赖逆序删除高频 FAQ 候选表。"""
    op.drop_index(
        "ix_candidate_occurrence_window",
        table_name="knowledge_candidate_occurrences",
    )
    op.drop_table("knowledge_candidate_occurrences")
    op.drop_index("ix_candidate_status_snoozed", table_name="knowledge_candidates")
    op.drop_table("knowledge_candidates")
