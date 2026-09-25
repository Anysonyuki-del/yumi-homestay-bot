"""客人资料改存明文：预订审批与客户手机号新增明文列（1.41.0）。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0028_guest_plaintext"
down_revision: str | None = "0027_knowledge_embeddings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """只新增可空列，不改动、不删除现有密文列。

    用户 2026-09-26 决定数据库可以明文保存客人信息、不再到期清除。明文列由应用写入，
    存量记录由 `tools/backfill_guest_plaintext.py` 在生产容器内解密回填（需要运行配置
    里的数据密钥，迁移阶段拿不到，所以不在迁移里回填）。密文列保留一个版本供回滚。
    """
    op.add_column("booking_approvals", sa.Column("guest_name", sa.Text(), nullable=True))
    op.add_column(
        "booking_approvals", sa.Column("guest_mobile", sa.String(length=32), nullable=True)
    )
    op.add_column("booking_approvals", sa.Column("special_requests", sa.Text(), nullable=True))
    op.add_column("customers", sa.Column("phone", sa.String(length=32), nullable=True))


def downgrade() -> None:
    """删除明文列；密文列一直保留，回退后旧版本照常读取密文。"""
    op.drop_column("customers", "phone")
    op.drop_column("booking_approvals", "special_requests")
    op.drop_column("booking_approvals", "guest_mobile")
    op.drop_column("booking_approvals", "guest_name")
