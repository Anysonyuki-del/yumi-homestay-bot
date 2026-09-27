"""保存由客人明确确认的本次住宿快照，不推断存量会话。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0030_stay_confirmation"
down_revision: str | None = "0029_knowledge_scope"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """存量字段保持为空，等待可信订单关联和客人确认。"""
    op.add_column("conversations", sa.Column("stay_confirmation", sa.JSON(), nullable=True))


def downgrade() -> None:
    """仅在隔离验证或已备份环境删除确认快照。"""
    op.drop_column("conversations", "stay_confirmation")
