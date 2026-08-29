"""增加任务失效终态、来源与关闭审计元数据。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021_task_lifecycle_room_operations"
down_revision: str | None = "0020_memory_trust_timeline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """扩展任务生命周期结构，并回填可由现有唯一键证明的来源。"""
    with op.batch_alter_table("business_tasks") as batch_op:
        batch_op.add_column(
            sa.Column(
                "origin_kind",
                sa.String(length=32),
                server_default="UNKNOWN",
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("closure_reason_code", sa.String(length=32), nullable=True)
        )
        batch_op.add_column(
            sa.Column("closure_source", sa.String(length=16), nullable=True)
        )
        batch_op.add_column(
            sa.Column("closed_by_employee_id", sa.Integer(), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_business_tasks_closed_by_employee",
            "employees",
            ["closed_by_employee_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.drop_constraint(
            "ck_business_task_execution_fields",
            type_="check",
        )
        batch_op.create_check_constraint(
            "ck_business_task_execution_fields",
            (
                "status IN ('PENDING_CONFIRMATION', 'CANCELLED', 'EXPIRED') "
                "OR (property_id IS NOT NULL AND service_date IS NOT NULL)"
            ),
        )
    op.execute(
        sa.text(
            "UPDATE business_tasks SET origin_kind = CASE "
            "WHEN dedupe_key LIKE 'turnover:%' THEN 'TURNOVER' "
            "WHEN dedupe_key LIKE 'lifecycle-manual:%' THEN 'LIFECYCLE_REMINDER' "
            "WHEN source_message_id IS NOT NULL THEN 'AI_SUGGESTION' "
            "ELSE 'UNKNOWN' END"
        )
    )
    op.create_index(
        "ix_business_tasks_status_expires_at",
        "business_tasks",
        ["status", "expires_at"],
    )


def downgrade() -> None:
    """把失效任务保守映射为取消，并恢复 0020 的任务结构。"""
    op.execute(
        sa.text(
            "UPDATE business_tasks SET status = 'CANCELLED' WHERE status = 'EXPIRED'"
        )
    )
    op.drop_index(
        "ix_business_tasks_status_expires_at",
        table_name="business_tasks",
    )
    with op.batch_alter_table("business_tasks") as batch_op:
        batch_op.drop_constraint(
            "ck_business_task_execution_fields",
            type_="check",
        )
        batch_op.create_check_constraint(
            "ck_business_task_execution_fields",
            (
                "status IN ('PENDING_CONFIRMATION', 'CANCELLED') "
                "OR (property_id IS NOT NULL AND service_date IS NOT NULL)"
            ),
        )
        batch_op.drop_constraint(
            "fk_business_tasks_closed_by_employee",
            type_="foreignkey",
        )
        batch_op.drop_column("closed_by_employee_id")
        batch_op.drop_column("closure_source")
        batch_op.drop_column("closure_reason_code")
        batch_op.drop_column("closed_at")
        batch_op.drop_column("expires_at")
        batch_op.drop_column("origin_kind")
