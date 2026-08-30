"""为审批客人资料增加用途隔离密文字段。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022_approval_pii"
down_revision: str | None = "0021_task_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """只增加可空密文与清理时间列，保留旧明文字段支持阶段性回滚。"""
    with op.batch_alter_table("booking_approvals") as batch_op:
        batch_op.add_column(
            sa.Column("guest_name_ciphertext", sa.LargeBinary(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("guest_mobile_ciphertext", sa.LargeBinary(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("special_requests_ciphertext", sa.LargeBinary(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("pii_purged_at", sa.DateTime(timezone=True), nullable=True)
        )


def downgrade() -> None:
    """移除阶段 2A 新列；旧明文仍在，因此降级不丢失审批资料。"""
    with op.batch_alter_table("booking_approvals") as batch_op:
        batch_op.drop_column("pii_purged_at")
        batch_op.drop_column("special_requests_ciphertext")
        batch_op.drop_column("guest_mobile_ciphertext")
        batch_op.drop_column("guest_name_ciphertext")
