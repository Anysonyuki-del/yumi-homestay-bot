"""新增订单、房态、任务、附件和凭证投递表。

Revision ID: 0005_operations
Revises: 0004_customer_context
Create Date: 2026-07-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_operations"
down_revision: str | None = "0004_customer_context"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps() -> list[sa.Column]:
    """为每张运营表创建独立的 UTC 时间列对象。"""
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    ]


def upgrade() -> None:
    """创建一期运营状态和安全凭证投递数据结构。"""
    op.create_table(
        "hostex_webhook_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_key", sa.String(length=128), nullable=False, unique=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("reservation_code", sa.String(length=128), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        *_timestamps(),
    )
    op.create_table(
        "property_profiles",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("title", sa.String(length=128), nullable=False),
        sa.Column("room_type", sa.String(length=128), nullable=True),
        sa.Column("district", sa.String(length=64), nullable=True),
        sa.Column("address_hint", sa.Text(), nullable=True),
        sa.Column("parking_instructions", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        *_timestamps(),
    )
    op.create_table(
        "stay_orders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("hostex_reservation_code", sa.String(length=128), nullable=False, unique=True),
        sa.Column("stay_code", sa.String(length=128), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=True),
        sa.Column("property_id", sa.BigInteger(), nullable=False),
        sa.Column("check_in_date", sa.Date(), nullable=False),
        sa.Column("check_out_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("last_hostex_sync_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
        sa.ForeignKeyConstraint(["property_id"], ["property_profiles.id"]),
    )
    op.create_index("ix_stay_orders_customer_id", "stay_orders", ["customer_id"])
    op.create_index("ix_stay_orders_property_id", "stay_orders", ["property_id"])
    op.create_table(
        "room_operational_states",
        sa.Column("property_id", sa.BigInteger(), primary_key=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("changed_by", sa.Integer(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["property_id"], ["property_profiles.id"]),
        sa.ForeignKeyConstraint(["changed_by"], ["employees.id"]),
    )
    op.create_table(
        "business_tasks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("dedupe_key", sa.String(length=160), nullable=True, unique=True),
        sa.Column("source_message_id", sa.String(length=128), nullable=True, unique=True),
        sa.Column("task_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=True),
        sa.Column("order_id", sa.Integer(), nullable=True),
        sa.Column("property_id", sa.BigInteger(), nullable=False),
        sa.Column("service_date", sa.Date(), nullable=False),
        sa.Column("assigned_employee_id", sa.Integer(), nullable=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("checklist", sa.JSON(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
        sa.ForeignKeyConstraint(["order_id"], ["stay_orders.id"]),
        sa.ForeignKeyConstraint(["property_id"], ["property_profiles.id"]),
        sa.ForeignKeyConstraint(["assigned_employee_id"], ["employees.id"]),
    )
    op.create_index("ix_business_tasks_customer_id", "business_tasks", ["customer_id"])
    op.create_index("ix_business_tasks_order_id", "business_tasks", ["order_id"])
    op.create_index("ix_business_tasks_property_id", "business_tasks", ["property_id"])
    op.create_table(
        "task_attachments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("private_file_id", sa.String(length=128), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("uploaded_by", sa.Integer(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["task_id"], ["business_tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["uploaded_by"], ["employees.id"]),
    )
    op.create_index("ix_task_attachments_task_id", "task_attachments", ["task_id"])
    op.create_table(
        "room_credentials",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("property_id", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("password_ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("guide_ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("qr_file_id", sa.String(length=128), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["property_id"], ["property_profiles.id"]),
        sa.UniqueConstraint("property_id", "version", name="uq_room_credential_version"),
    )
    op.create_table(
        "credential_deliveries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("credential_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["order_id"], ["stay_orders.id"]),
        sa.ForeignKeyConstraint(["credential_id"], ["room_credentials.id"]),
        sa.UniqueConstraint("order_id", "credential_id", name="uq_credential_delivery"),
    )
    op.create_table(
        "credential_delivery_parts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("delivery_id", sa.Integer(), nullable=False),
        sa.Column("part_type", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("external_message_id", sa.String(length=128), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["delivery_id"],
            ["credential_deliveries.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("delivery_id", "part_type", name="uq_delivery_part_type"),
    )


def downgrade() -> None:
    """按外键依赖逆序删除一期运营表。"""
    op.drop_table("credential_delivery_parts")
    op.drop_table("credential_deliveries")
    op.drop_table("room_credentials")
    op.drop_index("ix_task_attachments_task_id", table_name="task_attachments")
    op.drop_table("task_attachments")
    op.drop_index("ix_business_tasks_property_id", table_name="business_tasks")
    op.drop_index("ix_business_tasks_order_id", table_name="business_tasks")
    op.drop_index("ix_business_tasks_customer_id", table_name="business_tasks")
    op.drop_table("business_tasks")
    op.drop_table("room_operational_states")
    op.drop_index("ix_stay_orders_property_id", table_name="stay_orders")
    op.drop_index("ix_stay_orders_customer_id", table_name="stay_orders")
    op.drop_table("stay_orders")
    op.drop_table("property_profiles")
    op.drop_table("hostex_webhook_events")
