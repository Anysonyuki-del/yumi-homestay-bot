"""建立客服机器人初始业务表。

Revision ID: 0001_initial
Revises:
Create Date: 2026-07-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建员工、会话、消息、知识、审批、任务和审计表。"""
    op.create_table(
        "employees",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("wecom_userid", sa.String(length=128), nullable=False, unique=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_table(
        "conversations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("open_kfid", sa.String(length=128), nullable=False),
        sa.Column("external_userid", sa.String(length=128), nullable=False),
        sa.Column("language", sa.String(length=8), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("assigned_employee_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["assigned_employee_id"], ["employees.id"]),
        sa.UniqueConstraint("open_kfid", "external_userid", name="uq_conversation_participants"),
    )
    op.create_table(
        "knowledge_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column("question_zh", sa.Text(), nullable=False),
        sa.Column("answer_zh", sa.Text(), nullable=False),
        sa.Column("question_en", sa.Text(), nullable=False),
        sa.Column("answer_en", sa.Text(), nullable=False),
        sa.Column("keywords", sa.JSON(), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["updated_by"], ["employees.id"]),
    )
    op.create_table(
        "messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("external_message_id", sa.String(length=128), nullable=False, unique=True),
        sa.Column("origin", sa.String(length=16), nullable=False),
        sa.Column("message_type", sa.String(length=32), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_messages_conversation_id", "messages", ["conversation_id"])
    op.create_table(
        "booking_approvals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("approval_code", sa.String(length=64), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("check_in_date", sa.Date(), nullable=False),
        sa.Column("check_out_date", sa.Date(), nullable=False),
        sa.Column("number_of_guests", sa.Integer(), nullable=False),
        sa.Column("guest_name", sa.String(length=100), nullable=False),
        sa.Column("guest_mobile", sa.String(length=32), nullable=False),
        sa.Column("room_type_preference", sa.String(length=128), nullable=False),
        sa.Column("special_requests", sa.Text(), nullable=True),
        sa.Column("property_id", sa.BigInteger(), nullable=True),
        sa.Column("final_rate_amount", sa.Integer(), nullable=True),
        sa.Column("received_amount", sa.Integer(), nullable=True),
        sa.Column("income_method_id", sa.BigInteger(), nullable=True),
        sa.Column("approved_by", sa.Integer(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("hostex_reservation_code", sa.String(length=128), nullable=True),
        sa.Column("hostex_request_id", sa.String(length=128), nullable=True),
        sa.Column("failure_code", sa.Integer(), nullable=True),
        sa.Column("failure_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["approved_by"], ["employees.id"]),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"]),
        sa.UniqueConstraint("approval_code", name="uq_booking_approval_code"),
        sa.UniqueConstraint("hostex_reservation_code", name="uq_booking_hostex_reservation_code"),
    )
    op.create_index(
        "ix_booking_approval_status_created",
        "booking_approvals",
        ["status", "created_at"],
    )
    op.create_index(
        "ix_booking_approvals_conversation_id",
        "booking_approvals",
        ["conversation_id"],
    )
    op.create_table(
        "external_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("method", sa.String(length=16), nullable=False),
        sa.Column("path", sa.String(length=255), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=True),
        sa.Column("business_code", sa.Integer(), nullable=True),
        sa.Column("succeeded", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_external_requests_request_id", "external_requests", ["request_id"])
    op.create_table(
        "jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_type", sa.String(length=64), nullable=False),
        sa.Column("dedupe_key", sa.String(length=128), nullable=True, unique=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_jobs_claim", "jobs", ["status", "available_at"])
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("actor_employee_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("target_type", sa.String(length=64), nullable=False),
        sa.Column("target_id", sa.String(length=128), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["actor_employee_id"], ["employees.id"]),
    )


def downgrade() -> None:
    """按外键依赖的逆序删除初始业务表。"""
    op.drop_table("audit_logs")
    op.drop_index("ix_jobs_claim", table_name="jobs")
    op.drop_table("jobs")
    op.drop_index("ix_external_requests_request_id", table_name="external_requests")
    op.drop_table("external_requests")
    op.drop_index("ix_booking_approvals_conversation_id", table_name="booking_approvals")
    op.drop_index("ix_booking_approval_status_created", table_name="booking_approvals")
    op.drop_table("booking_approvals")
    op.drop_index("ix_messages_conversation_id", table_name="messages")
    op.drop_table("messages")
    op.drop_table("knowledge_entries")
    op.drop_table("conversations")
    op.drop_table("employees")
