"""新增带证据和生命周期的结构化客户记忆。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019_customer_memory_items"
down_revision: str | None = "0018_stay_checkout_observation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建按客户隔离的记忆表及召回、冲突治理索引。"""
    with op.batch_alter_table("messages") as batch_op:
        batch_op.add_column(
            sa.Column(
                "memory_processed_at",
                sa.DateTime(timezone=True),
                nullable=True,
            )
        )
    op.create_table(
        "customer_memory_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("subject_key", sa.String(length=128), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("evidence_type", sa.String(length=32), nullable=False),
        sa.Column("source_message_id", sa.String(length=128), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("supersedes_id", sa.Integer(), nullable=True),
        sa.Column("status_reason", sa.String(length=256), nullable=True),
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
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_customer_memory_confidence_range",
        ),
        sa.ForeignKeyConstraint(
            ["customer_id"], ["customers.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["source_message_id"],
            ["messages.external_message_id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_id"], ["customer_memory_items.id"], ondelete="SET NULL"
        ),
    )
    op.create_index(
        "ix_customer_memory_customer_status_review",
        "customer_memory_items",
        ["customer_id", "status", "review_at"],
    )
    op.create_index(
        "ix_customer_memory_customer_subject",
        "customer_memory_items",
        ["customer_id", "subject_key"],
    )


def downgrade() -> None:
    """删除结构化客户记忆及其索引。"""
    op.drop_index(
        "ix_customer_memory_customer_subject", table_name="customer_memory_items"
    )
    op.drop_index(
        "ix_customer_memory_customer_status_review",
        table_name="customer_memory_items",
    )
    op.drop_table("customer_memory_items")
    with op.batch_alter_table("messages") as batch_op:
        batch_op.drop_column("memory_processed_at")
