"""小程序客人登录会话（微信小程序基础登录 Spec 2026-10-29）。"""
import sqlalchemy as sa
from alembic import op

revision = "0036_miniprogram_sessions"
down_revision = "0035_knowledge_referrals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """新增空会话表；身份渠道是无 CHECK 约束的字符串列，新增枚举值无需改表。"""
    op.create_table(
        "miniprogram_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "identity_id",
            sa.Integer(),
            sa.ForeignKey("customer_identities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("token_hash", name="uq_miniprogram_sessions_token_hash"),
    )
    op.create_index(
        "ix_miniprogram_sessions_identity_id",
        "miniprogram_sessions",
        ["identity_id"],
    )
    op.create_index(
        "ix_miniprogram_sessions_expires_at",
        "miniprogram_sessions",
        ["expires_at"],
    )


def downgrade() -> None:
    """只删除本期会话表，使小程序登录全部失效；客户和身份档案保留。"""
    op.drop_index("ix_miniprogram_sessions_expires_at", table_name="miniprogram_sessions")
    op.drop_index("ix_miniprogram_sessions_identity_id", table_name="miniprogram_sessions")
    op.drop_table("miniprogram_sessions")
