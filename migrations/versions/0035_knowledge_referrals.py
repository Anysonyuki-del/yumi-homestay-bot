"""知识缺口转管家核实与来源完成收据（回复泛用化第二轮修复 Spec 2026-10-08 fix2 §2.6(7)）。"""
import sqlalchemy as sa
from alembic import op

revision = "0035_knowledge_referrals"
down_revision = "0034_wecom_sync_cursors"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """新增三张空表：转交记录、转交抑制、来源完成收据；不改动已有表与数据。"""
    op.create_table(
        "knowledge_referrals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.Integer(),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_message_id", sa.String(length=128), nullable=False),
        sa.Column("unit_id", sa.String(length=64), nullable=False),
        sa.Column("plan_item_id", sa.Integer(), nullable=False),
        sa.Column("question", sa.Text(), nullable=True),
        sa.Column("canonical_question", sa.Text(), nullable=True),
        sa.Column("normalized_question", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("category", sa.String(length=64), nullable=True),
        sa.Column("property_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="pending"),
        sa.Column("generation", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source_message_ids", sa.JSON(), nullable=False),
        sa.Column("draft", sa.JSON(), nullable=True),
        sa.Column("sensitive", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "knowledge_entry_id",
            sa.Integer(),
            sa.ForeignKey("knowledge_entries.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("source_message_id", "unit_id", name="uq_referral_source_unit"),
        sa.CheckConstraint("generation >= 0", name="ck_referral_generation_nonnegative"),
    )
    op.create_index(
        "ix_referral_conversation_question",
        "knowledge_referrals",
        ["conversation_id", "normalized_question", "property_id", "created_at"],
    )
    op.create_index(
        "ix_referral_status_created", "knowledge_referrals", ["status", "created_at"]
    )
    op.create_table(
        "knowledge_referral_suppressions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("normalized_question", sa.String(length=300), nullable=False),
        sa.Column("property_id", sa.Integer(), nullable=True),
        sa.Column("suppressed_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_by", sa.Integer(), sa.ForeignKey("employees.id"), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "ix_referral_suppression_question",
        "knowledge_referral_suppressions",
        ["normalized_question", "property_id"],
    )
    op.create_table(
        "source_completion_receipts",
        sa.Column(
            "source_message_id",
            sa.String(length=128),
            sa.ForeignKey("messages.external_message_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("referral_ids", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def downgrade() -> None:
    """回滚删表；旧代码不读这三张表，恢复入口回到不查收据的旧行为。"""
    op.drop_table("source_completion_receipts")
    op.drop_index("ix_referral_suppression_question", table_name="knowledge_referral_suppressions")
    op.drop_table("knowledge_referral_suppressions")
    op.drop_index("ix_referral_status_created", table_name="knowledge_referrals")
    op.drop_index("ix_referral_conversation_question", table_name="knowledge_referrals")
    op.drop_table("knowledge_referrals")
