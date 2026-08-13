"""记录订单首次观察到退房终态的本地日期。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018_stay_checkout_observation"
down_revision: str | None = "0017_runtime_config_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """新增退房观察日期，并用计划退房日回填已有退房终态订单。"""
    with op.batch_alter_table("stay_orders") as batch_op:
        batch_op.add_column(sa.Column("checkout_observed_on", sa.Date(), nullable=True))

    # 历史数据没有真实观察时刻，只能按既定规则使用计划退房日作为兼容锚点。
    op.execute(
        sa.text(
            "UPDATE stay_orders "
            "SET checkout_observed_on = check_out_date "
            "WHERE lower(trim(status)) IN ('checked_out', 'completed')"
        )
    )


def downgrade() -> None:
    """移除退房观察日期，恢复到 0017 数据结构。"""
    with op.batch_alter_table("stay_orders") as batch_op:
        batch_op.drop_column("checkout_observed_on")
