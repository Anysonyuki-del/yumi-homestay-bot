"""增加高频运营查询所需的复合索引。"""

from collections.abc import Sequence

from alembic import op

revision: str = "0014_query_indexes"
down_revision: str | None = "0013_complaint_delivery_links"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """覆盖消息、订单、任务、合并和队列领取条件。"""
    op.create_index(
        "ix_messages_conversation_type_id",
        "messages",
        ["conversation_id", "message_type", "id"],
    )
    op.create_index(
        "ix_jobs_type_claim",
        "jobs",
        ["status", "job_type", "available_at"],
    )
    op.create_index(
        "ix_customer_merge_source_target_status",
        "customer_merge_suggestions",
        ["source_customer_id", "target_customer_id", "status"],
    )
    op.create_index(
        "ix_stay_orders_customer_status_checkin",
        "stay_orders",
        ["customer_id", "status", "check_in_date"],
    )
    op.create_index(
        "ix_business_tasks_status_assignee_service_date",
        "business_tasks",
        ["status", "assigned_employee_id", "service_date"],
    )


def downgrade() -> None:
    """删除本迁移新增的复合索引。"""
    op.drop_index(
        "ix_business_tasks_status_assignee_service_date",
        table_name="business_tasks",
    )
    op.drop_index(
        "ix_stay_orders_customer_status_checkin",
        table_name="stay_orders",
    )
    op.drop_index(
        "ix_customer_merge_source_target_status",
        table_name="customer_merge_suggestions",
    )
    op.drop_index("ix_jobs_type_claim", table_name="jobs")
    op.drop_index("ix_messages_conversation_type_id", table_name="messages")
