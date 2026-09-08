"""记录已永久删除的系统任务来源，防止同步把它重新造出来。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025_purged_task_marks"
down_revision: str | None = "0024_business_task_archive"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """新增删除墓碑表。

    永久删除任务会连同去重依据一起删掉，下一次订单同步因此查不到任何现存任务，
    于是把已经处理完并被清理的历史工作重新造成待分派任务。墓碑保留最小信息：
    只存被删任务的去重键与删除时间，不含正文、照片或客户身份。

    去重键沿用 `turnover:{房间}:{服务日}`，与 create_turnover 现有语义一致，
    不引入第二套身份概念；配合保留期到期后允许重建，避免永久封禁某房间的某一天。
    """
    op.create_table(
        "purged_task_marks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("dedupe_key", sa.String(length=128), nullable=False),
        sa.Column("purged_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedupe_key", name="uq_purged_task_marks_dedupe_key"),
    )
    op.create_index(
        "ix_purged_task_marks_purged_at",
        "purged_task_marks",
        ["purged_at"],
    )


def downgrade() -> None:
    """回退时删除墓碑表；历史任务数据不受影响。"""
    op.drop_index("ix_purged_task_marks_purged_at", table_name="purged_task_marks")
    op.drop_table("purged_task_marks")
