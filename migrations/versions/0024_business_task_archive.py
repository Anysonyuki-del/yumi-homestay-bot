"""为运营任务增加软归档字段。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024_business_task_archive"
down_revision: str | None = "0023_approval_pii_final"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """新增归档时间与归档人，不改变既有任务数据与状态。

    归档只影响列表可见性，不参与状态机；用时间戳而非布尔标记，
    这样「何时归档」不需要再查审计即可得到。
    """
    with op.batch_alter_table("business_tasks") as batch_op:
        batch_op.add_column(
            sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("archived_by_employee_id", sa.Integer(), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_business_tasks_archived_by_employee_id",
            "employees",
            ["archived_by_employee_id"],
            ["id"],
            ondelete="SET NULL",
        )
    # 默认列表恒带 archived_at IS NULL，归档视图按归档时间倒序。
    op.create_index(
        "ix_business_tasks_archived_at",
        "business_tasks",
        ["archived_at"],
    )


def downgrade() -> None:
    """删除归档字段。归档记录会丢失，但任务本身与状态不受影响。"""
    op.drop_index("ix_business_tasks_archived_at", table_name="business_tasks")
    with op.batch_alter_table("business_tasks") as batch_op:
        batch_op.drop_constraint(
            "fk_business_tasks_archived_by_employee_id", type_="foreignkey"
        )
        batch_op.drop_column("archived_by_employee_id")
        batch_op.drop_column("archived_at")
