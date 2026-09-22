"""新增审核知识向量表，供可选的语义检索召回使用。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0027_knowledge_embeddings"
down_revision: str | None = "0026_settle_retry_latch"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """新增 knowledge_embeddings 普通表。

    向量用 JSON 存放、检索时在应用内精确计算相似度，不依赖 pgvector 扩展，也不改
    数据库镜像；SQLite 与 PostgreSQL 同一份迁移。每条知识每种语言、每个模型一行，
    知识删除时向量随外键级联删除。表是可重建的派生数据，回退迁移不影响知识本身。
    """
    op.create_table(
        "knowledge_embeddings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("entry_id", sa.Integer(), nullable=False),
        sa.Column("language", sa.String(length=8), nullable=False),
        sa.Column("model", sa.String(length=256), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("vector", sa.JSON(), nullable=False),
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
            ["entry_id"],
            ["knowledge_entries.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "entry_id",
            "language",
            "model",
            name="uq_knowledge_embeddings_entry_language_model",
        ),
    )
    op.create_index(
        "ix_knowledge_embeddings_model_language",
        "knowledge_embeddings",
        ["model", "language"],
    )


def downgrade() -> None:
    """删除向量表；审核知识原文不受影响，重新升级后由维护循环重建向量。"""
    op.drop_index(
        "ix_knowledge_embeddings_model_language",
        table_name="knowledge_embeddings",
    )
    op.drop_table("knowledge_embeddings")
