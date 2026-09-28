"""知识条目配图与房源欢迎图片（Spec 2026-09-29 G1、G3）。"""
import sqlalchemy as sa
from alembic import op

revision = "0033_knowledge_images"
down_revision = "0032_knowledge_triggers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """新表只引用私有存储文件名；欢迎图片列可空，存量房源为空即不发图。"""
    op.create_table(
        "knowledge_images",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "knowledge_entry_id",
            sa.Integer(),
            sa.ForeignKey("knowledge_entries.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("file_id", sa.String(length=128), nullable=False),
        sa.Column("content_type", sa.String(length=32), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column(
            "created_by",
            sa.Integer(),
            sa.ForeignKey("employees.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_knowledge_images_entry_order",
        "knowledge_images",
        ["knowledge_entry_id", "sort_order"],
    )
    op.add_column(
        "property_profiles",
        sa.Column("welcome_image_file_id", sa.String(length=128), nullable=True),
    )


def downgrade() -> None:
    """回滚只删除引用；私有文件留在目录里，不在迁移中碰文件系统。"""
    op.drop_column("property_profiles", "welcome_image_file_id")
    op.drop_index("ix_knowledge_images_entry_order", table_name="knowledge_images")
    op.drop_table("knowledge_images")
