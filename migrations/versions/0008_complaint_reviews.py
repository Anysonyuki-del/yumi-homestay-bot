"""增加客诉冷静辅助记录。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_complaint_reviews"
down_revision: str | None = "0007_lifecycle_reminders"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建客诉记录、脱敏分析和草稿版本字段。"""
    op.create_table(
        "complaint_reviews",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.Integer(),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_message_id", sa.String(length=128), nullable=False),
        sa.Column("reason", sa.String(length=64), nullable=False),
        sa.Column("risk_level", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("analysis", sa.JSON(), nullable=False),
        sa.Column("draft", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.UniqueConstraint("source_message_id", name="uq_complaint_review_source_message"),
    )
    op.create_index(
        "ix_complaint_reviews_conversation_id",
        "complaint_reviews",
        ["conversation_id"],
    )
    op.create_index(
        "ix_complaint_review_status_updated",
        "complaint_reviews",
        ["status", "updated_at"],
    )


def downgrade() -> None:
    """删除客诉记录表。"""
    op.drop_index("ix_complaint_review_status_updated", table_name="complaint_reviews")
    op.drop_index("ix_complaint_reviews_conversation_id", table_name="complaint_reviews")
    op.drop_table("complaint_reviews")
