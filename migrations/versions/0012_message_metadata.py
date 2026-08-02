"""为非文本企业微信消息保存安全元数据。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_message_metadata"
down_revision: str | None = "0011_complaint_delivery_status"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """增加非空 JSON 元数据列，兼容已有消息记录。"""
    op.add_column(
        "messages",
        sa.Column(
            "metadata",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )


def downgrade() -> None:
    """移除消息安全元数据列。"""
    op.drop_column("messages", "metadata")
