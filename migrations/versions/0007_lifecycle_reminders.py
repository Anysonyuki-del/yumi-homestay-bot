"""增加入住生命周期主动提醒状态。

Revision ID: 0007_lifecycle_reminders
Revises: 0006_employee_roles
Create Date: 2026-07-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_lifecycle_reminders"
down_revision: str | None = "0006_employee_roles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建提醒表、唯一计划约束和状态查询索引。"""
    op.create_table(
        "lifecycle_reminders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "order_id",
            sa.Integer(),
            sa.ForeignKey("stay_orders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "reminder_type",
            sa.Enum(
                "PRE_ARRIVAL",
                "ARRIVAL_DAY",
                "CHECKOUT",
                "THANK_YOU",
                name="remindertype",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("scheduled_local_date", sa.Date(), nullable=False),
        sa.Column(
            "scheduled_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "SCHEDULED",
                "PLATFORM_ACCEPTED",
                "MANUAL_FOLLOWUP",
                "CANCELLED",
                name="reminderstatus",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "external_message_id",
            sa.String(length=128),
            nullable=True,
            unique=True,
        ),
        sa.Column(
            "failure_reason",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column(
            "platform_accepted_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "manual_followup_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
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
        sa.UniqueConstraint(
            "order_id",
            "reminder_type",
            "scheduled_local_date",
            name="uq_lifecycle_reminder_schedule",
        ),
    )
    op.create_index(
        "ix_lifecycle_reminders_order_id",
        "lifecycle_reminders",
        ["order_id"],
    )
    op.create_index(
        "ix_lifecycle_reminder_status_schedule",
        "lifecycle_reminders",
        ["status", "scheduled_at"],
    )


def downgrade() -> None:
    """删除入住生命周期提醒表。"""
    op.drop_index(
        "ix_lifecycle_reminder_status_schedule",
        table_name="lifecycle_reminders",
    )
    op.drop_index(
        "ix_lifecycle_reminders_order_id",
        table_name="lifecycle_reminders",
    )
    op.drop_table("lifecycle_reminders")
