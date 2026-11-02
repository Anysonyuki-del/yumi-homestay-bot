"""小程序住客自助：入住指南展示开关与房间介绍（小程序住客自助 Spec 2026-11-03）。"""
import sqlalchemy as sa
from alembic import op

revision = "0037_miniprogram_showcase"
down_revision = "0036_miniprogram_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """知识条目加默认关闭的展示开关；新增房间介绍与照片两张空表。"""
    with op.batch_alter_table("knowledge_entries") as batch_op:
        batch_op.add_column(
            sa.Column(
                "show_in_miniprogram",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
    op.create_table(
        "room_showcases",
        sa.Column(
            "property_id",
            sa.BigInteger(),
            sa.ForeignKey("property_profiles.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("is_visible", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("display_name", sa.String(length=64), nullable=True),
        sa.Column("intro", sa.Text(), nullable=True),
        sa.Column("bed_type", sa.String(length=64), nullable=True),
        sa.Column("max_guests", sa.Integer(), nullable=True),
        sa.Column("area_sqm", sa.Integer(), nullable=True),
        sa.Column("facilities", sa.JSON(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=True),
        sa.Column("updated_by", sa.Integer(), sa.ForeignKey("employees.id"), nullable=True),
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
            "max_guests IS NULL OR (max_guests BETWEEN 1 AND 20)",
            name="ck_room_showcases_max_guests",
        ),
        sa.CheckConstraint(
            "area_sqm IS NULL OR (area_sqm BETWEEN 1 AND 1000)",
            name="ck_room_showcases_area_sqm",
        ),
        sa.CheckConstraint(
            "sort_order IS NULL OR (sort_order BETWEEN 0 AND 9999)",
            name="ck_room_showcases_sort_order",
        ),
    )
    op.create_table(
        "room_showcase_photos",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "property_id",
            sa.BigInteger(),
            sa.ForeignKey("property_profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("file_id", sa.String(length=128), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("file_id", name="uq_room_showcase_photos_file_id"),
    )
    op.create_index(
        "ix_room_showcase_photos_property_id",
        "room_showcase_photos",
        ["property_id"],
    )


def downgrade() -> None:
    """删除房间介绍两张表和展示开关；已填写的介绍随之丢失，照片文件需另行清理。"""
    op.drop_index("ix_room_showcase_photos_property_id", table_name="room_showcase_photos")
    op.drop_table("room_showcase_photos")
    op.drop_table("room_showcases")
    with op.batch_alter_table("knowledge_entries") as batch_op:
        batch_op.drop_column("show_in_miniprogram")
