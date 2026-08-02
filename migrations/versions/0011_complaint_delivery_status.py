"""记录客诉出站队列和实际投递结果。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_complaint_delivery_status"
down_revision: str | None = "0010_property_room_number"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """为客诉增加安全的投递错误类型字段。"""
    op.add_column(
        "complaint_reviews",
        sa.Column("delivery_error_code", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    """移除客诉投递错误类型字段。"""
    op.drop_column("complaint_reviews", "delivery_error_code")
