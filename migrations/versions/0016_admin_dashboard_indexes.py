"""为管理员今日入住与退房聚合增加复合索引。"""

from collections.abc import Sequence

from alembic import op

revision: str = "0016_admin_dashboard_indexes"
down_revision: str | None = "0015_admin_runtime_config"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建日期优先、状态辅助过滤的总览索引。"""
    op.create_index(
        "ix_stay_orders_check_in_status",
        "stay_orders",
        ["check_in_date", "status"],
        unique=False,
    )
    op.create_index(
        "ix_stay_orders_check_out_status",
        "stay_orders",
        ["check_out_date", "status"],
        unique=False,
    )


def downgrade() -> None:
    """按创建逆序删除管理员总览索引。"""
    op.drop_index("ix_stay_orders_check_out_status", table_name="stay_orders")
    op.drop_index("ix_stay_orders_check_in_status", table_name="stay_orders")
