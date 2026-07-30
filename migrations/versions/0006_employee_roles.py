"""把员工权限收敛为管理员和普通员工两级。

Revision ID: 0006_employee_roles
Revises: 0005_operations
Create Date: 2026-07-31
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006_employee_roles"
down_revision: str | None = "0005_operations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """保留管理员，并把历史非管理员角色统一为普通员工。"""
    op.execute(
        "UPDATE employees SET role = 'STAFF' "
        "WHERE UPPER(role) != 'ADMIN'"
    )


def downgrade() -> None:
    """回退时把普通员工映射为历史普通客服角色。"""
    op.execute(
        "UPDATE employees SET role = 'CUSTOMER_SERVICE' "
        "WHERE UPPER(role) = 'STAFF'"
    )
