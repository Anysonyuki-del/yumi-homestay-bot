"""为待审批单增加来源消息幂等键。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_approval_source_message"
down_revision: str | None = "0008_complaint_reviews"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """增加可为空的来源消息唯一字段，兼容历史审批单。"""
    with op.batch_alter_table("booking_approvals") as batch_op:
        batch_op.add_column(
            sa.Column("source_message_id", sa.String(length=128), nullable=True)
        )
        batch_op.create_unique_constraint(
            "uq_booking_approval_source_message", ["source_message_id"]
        )


def downgrade() -> None:
    """删除审批来源消息唯一字段。"""
    with op.batch_alter_table("booking_approvals") as batch_op:
        batch_op.drop_constraint("uq_booking_approval_source_message", type_="unique")
        batch_op.drop_column("source_message_id")
