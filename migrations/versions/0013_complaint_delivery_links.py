"""关联客诉出站任务与企业微信真实消息编号。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_complaint_delivery_links"
down_revision: str | None = "0012_message_metadata"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """增加客诉投递关联字段，支持异步失败回写和可靠重试。"""
    # SQLite 不支持直接 ALTER TABLE 增加约束，批量迁移会自动复制表结构，
    # 同时兼容本地 SQLite 和生产 PostgreSQL。
    with op.batch_alter_table("complaint_reviews") as batch_op:
        batch_op.add_column(
            sa.Column("delivery_outbox_id", sa.String(length=128), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "delivery_external_message_id",
                sa.String(length=128),
                nullable=True,
            )
        )
        batch_op.create_unique_constraint(
            "uq_complaint_reviews_delivery_outbox_id",
            ["delivery_outbox_id"],
        )
        batch_op.create_unique_constraint(
            "uq_complaint_reviews_delivery_external_message_id",
            ["delivery_external_message_id"],
        )


def downgrade() -> None:
    """移除客诉出站关联字段。"""
    # 与升级保持相同的批量迁移策略，确保 SQLite 可以删除约束和字段。
    with op.batch_alter_table("complaint_reviews") as batch_op:
        batch_op.drop_constraint(
            "uq_complaint_reviews_delivery_external_message_id",
            type_="unique",
        )
        batch_op.drop_constraint(
            "uq_complaint_reviews_delivery_outbox_id",
            type_="unique",
        )
        batch_op.drop_column("delivery_external_message_id")
        batch_op.drop_column("delivery_outbox_id")
