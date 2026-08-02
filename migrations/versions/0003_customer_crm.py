"""新增客户主档、渠道身份、标签和合并建议。

Revision ID: 0003_customer_crm
Revises: 0002_frequent_faq_candidates
Create Date: 2026-07-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

revision: str = "0003_customer_crm"
down_revision: str | None = "0002_frequent_faq_candidates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _backfill_existing_conversations() -> None:
    """为已有微信客服联系人建立唯一客户主档和可靠渠道身份。"""
    if context.is_offline_mode():
        # 回填依赖读取现有联系人，离线 SQL 无法获得查询结果；
        # 实际部署使用在线 upgrade，会完整执行下方数据迁移。
        return
    connection = op.get_bind()
    metadata = sa.MetaData()
    customers = sa.Table(
        "customers",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("display_name", sa.String(length=100), nullable=False),
    )
    external_userids = list(
        connection.execute(
            sa.text("SELECT DISTINCT external_userid FROM conversations")
        ).scalars()
    )
    for external_userid in external_userids:
        # 使用 SQLAlchemy Insert 构造，确保 SQLite 与 PostgreSQL 都能返回新主键。
        customer_id = connection.execute(
            sa.insert(customers)
            .values(display_name="微信客户")
            .returning(customers.c.id)
        ).scalar_one()
        connection.execute(
            sa.text(
                """
                INSERT INTO customer_identities
                    (customer_id, provider, external_id, is_verified)
                VALUES
                    (:customer_id, :provider, :external_id, :is_verified)
                """
            ),
            {
                "customer_id": customer_id,
                "provider": "wecom_kf",
                "external_id": external_userid,
                "is_verified": True,
            },
        )
        connection.execute(
            sa.text(
                """
                UPDATE conversations
                SET customer_id = :customer_id
                WHERE external_userid = :external_id
                """
            ),
            {"customer_id": customer_id, "external_id": external_userid},
        )


def upgrade() -> None:
    """创建 CRM 表并把已有会话迁移为正式客户。"""
    op.create_table(
        "customers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("display_name", sa.String(length=100), nullable=False),
        sa.Column("phone_ciphertext", sa.LargeBinary(), nullable=True),
        sa.Column("phone_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("merged_into_customer_id", sa.Integer(), nullable=True),
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
        sa.ForeignKeyConstraint(["merged_into_customer_id"], ["customers.id"]),
    )
    op.create_index(
        "ix_customers_phone_fingerprint",
        "customers",
        ["phone_fingerprint"],
    )
    op.create_table(
        "customer_identities",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("external_id", sa.String(length=128), nullable=False),
        sa.Column("is_verified", sa.Boolean(), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["customers.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "provider",
            "external_id",
            name="uq_customer_identity_provider_external_id",
        ),
    )
    op.create_index(
        "ix_customer_identities_customer_id",
        "customer_identities",
        ["customer_id"],
    )
    op.create_table(
        "customer_tags",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=64), nullable=False, unique=True),
        sa.Column("wecom_tag_id", sa.String(length=128), nullable=True, unique=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
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
    )
    op.create_table(
        "customer_tag_links",
        sa.Column("customer_id", sa.Integer(), primary_key=True),
        sa.Column("tag_id", sa.Integer(), primary_key=True),
        sa.Column("sync_pending", sa.Boolean(), nullable=False),
        sa.Column("last_sync_error_code", sa.String(length=64), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["customers.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tag_id"],
            ["customer_tags.id"],
            ondelete="CASCADE",
        ),
    )
    op.create_table(
        "customer_merge_suggestions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_customer_id", sa.Integer(), nullable=False),
        sa.Column("target_customer_id", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("reviewed_by", sa.Integer(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint(
            "source_customer_id != target_customer_id",
            name="ck_customer_merge_distinct",
        ),
        sa.ForeignKeyConstraint(["source_customer_id"], ["customers.id"]),
        sa.ForeignKeyConstraint(["target_customer_id"], ["customers.id"]),
        sa.ForeignKeyConstraint(["reviewed_by"], ["employees.id"]),
    )
    op.create_index(
        "ix_customer_merge_status_created",
        "customer_merge_suggestions",
        ["status", "created_at"],
    )
    with op.batch_alter_table("conversations") as batch_op:
        batch_op.add_column(sa.Column("customer_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_conversations_customer_id",
            "customers",
            ["customer_id"],
            ["id"],
        )
        batch_op.create_index(
            "ix_conversations_customer_id",
            ["customer_id"],
        )
    _backfill_existing_conversations()


def downgrade() -> None:
    """移除 CRM 关联和表，恢复到高频 FAQ 版本。"""
    with op.batch_alter_table("conversations") as batch_op:
        batch_op.drop_index("ix_conversations_customer_id")
        batch_op.drop_constraint(
            "fk_conversations_customer_id",
            type_="foreignkey",
        )
        batch_op.drop_column("customer_id")
    op.drop_index(
        "ix_customer_merge_status_created",
        table_name="customer_merge_suggestions",
    )
    op.drop_table("customer_merge_suggestions")
    op.drop_table("customer_tag_links")
    op.drop_table("customer_tags")
    op.drop_index(
        "ix_customer_identities_customer_id",
        table_name="customer_identities",
    )
    op.drop_table("customer_identities")
    op.drop_index("ix_customers_phone_fingerprint", table_name="customers")
    op.drop_table("customers")
