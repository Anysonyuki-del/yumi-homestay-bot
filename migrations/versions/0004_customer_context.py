"""新增客户分层摘要和消息清理状态。

Revision ID: 0004_customer_context
Revises: 0003_customer_crm
Create Date: 2026-07-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_customer_context"
down_revision: str | None = "0003_customer_crm"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建客户摘要表并增加消息摘要、清理时间。"""
    op.create_table(
        "customer_context_summaries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("customer_id", sa.Integer(), nullable=False, unique=True),
        sa.Column("short_summary", sa.Text(), nullable=False),
        sa.Column("long_summary", sa.Text(), nullable=False),
        sa.Column("unresolved_items", sa.JSON(), nullable=False),
        sa.Column("short_cutoff_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("long_cutoff_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["customers.id"],
            ondelete="CASCADE",
        ),
    )
    with op.batch_alter_table("messages") as batch_op:
        batch_op.add_column(
            sa.Column(
                "short_summarized_at",
                sa.DateTime(timezone=True),
                nullable=True,
            )
        )
        batch_op.add_column(
            sa.Column("purged_at", sa.DateTime(timezone=True), nullable=True)
        )


def downgrade() -> None:
    """移除消息清理状态和客户分层摘要。"""
    with op.batch_alter_table("messages") as batch_op:
        batch_op.drop_column("purged_at")
        batch_op.drop_column("short_summarized_at")
    op.drop_table("customer_context_summaries")
