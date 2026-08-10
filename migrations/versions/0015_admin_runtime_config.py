"""新增单管理员凭证和加密运行配置版本表。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015_admin_runtime_config"
down_revision: str | None = "0014_query_indexes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps() -> list[sa.Column]:
    """为可变单例表创建独立的 UTC 时间列。"""
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
    """创建单例管理员凭证、不可变配置版本和激活指针。"""
    op.create_table(
        "admin_credentials",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("employee_id", sa.Integer(), nullable=False, unique=True),
        sa.Column("username", sa.String(length=128), nullable=False),
        sa.Column("password_hash", sa.String(length=512), nullable=False),
        sa.Column("must_change_password", sa.Boolean(), nullable=False),
        sa.Column("failed_attempts", sa.Integer(), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("session_version", sa.Integer(), nullable=False),
        sa.Column("last_authenticated_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.CheckConstraint("id = 1", name="ck_admin_credentials_singleton"),
        sa.ForeignKeyConstraint(
            ["employee_id"], ["employees.id"], ondelete="CASCADE"
        ),
    )
    op.create_index(
        "ix_admin_credentials_username",
        "admin_credentials",
        ["username"],
        unique=True,
    )
    op.create_table(
        "admin_csrf_nonces",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("purpose", sa.String(length=64), nullable=False),
        sa.Column("admin_id", sa.Integer(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["admin_id"], ["admin_credentials.id"], ondelete="CASCADE"
        ),
    )
    op.create_index(
        "ix_admin_csrf_nonces_token_hash",
        "admin_csrf_nonces",
        ["token_hash"],
        unique=True,
    )
    op.create_index(
        "ix_admin_csrf_nonces_expires_at",
        "admin_csrf_nonces",
        ["expires_at"],
        unique=False,
    )
    op.create_table(
        "runtime_config_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("encrypted_payload", sa.LargeBinary(), nullable=False),
        sa.Column("masked_summary", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["created_by"], ["employees.id"]),
    )
    op.create_table(
        "runtime_config_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("active_version_id", sa.Integer(), nullable=True),
        sa.Column("previous_version_id", sa.Integer(), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint("id = 1", name="ck_runtime_config_state_singleton"),
        sa.ForeignKeyConstraint(
            ["active_version_id"], ["runtime_config_versions.id"]
        ),
        sa.ForeignKeyConstraint(
            ["previous_version_id"], ["runtime_config_versions.id"]
        ),
    )


def downgrade() -> None:
    """按外键依赖逆序删除管理员和运行配置数据结构。"""
    op.drop_table("runtime_config_state")
    op.drop_table("runtime_config_versions")
    op.drop_index(
        "ix_admin_csrf_nonces_expires_at",
        table_name="admin_csrf_nonces",
    )
    op.drop_index(
        "ix_admin_csrf_nonces_token_hash",
        table_name="admin_csrf_nonces",
    )
    op.drop_table("admin_csrf_nonces")
    op.drop_index("ix_admin_credentials_username", table_name="admin_credentials")
    op.drop_table("admin_credentials")
