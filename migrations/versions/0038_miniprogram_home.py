"""小程序首页：门店公开信息与首页横幅（小程序三标签 Spec 2026-11-03）。"""
import sqlalchemy as sa
from alembic import op

revision = "0038_miniprogram_home"
down_revision = "0037_miniprogram_showcase"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """新增门店信息单行表（预置空行 id=1）和横幅空表。"""
    store = op.create_table(
        "miniprogram_store_profile",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=32), nullable=True),
        sa.Column("intro", sa.Text(), nullable=True),
        sa.Column("address", sa.String(length=120), nullable=True),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("phone", sa.String(length=20), nullable=True),
        sa.Column("updated_by", sa.Integer(), sa.ForeignKey("employees.id"), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("id = 1", name="ck_miniprogram_store_profile_singleton"),
        sa.CheckConstraint(
            # 必须显式写 IS NOT NULL：只填一个坐标时 BETWEEN 得到 NULL，CHECK 会把「未知」当作通过。
            "(latitude IS NULL AND longitude IS NULL) OR "
            "(latitude IS NOT NULL AND longitude IS NOT NULL"
            " AND latitude BETWEEN -90 AND 90 AND longitude BETWEEN -180 AND 180)",
            name="ck_miniprogram_store_profile_location",
        ),
    )
    # 预置唯一一行：后台写入与横幅上传都锁这一行，不必处理「首次创建」的并发。
    op.bulk_insert(store, [{"id": 1}])
    op.create_table(
        "miniprogram_banners",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("file_id", sa.String(length=128), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("file_id", name="uq_miniprogram_banners_file_id"),
    )


def downgrade() -> None:
    """删除两张表；已填写的门店信息随之丢失，横幅文件需另行清理。"""
    op.drop_table("miniprogram_banners")
    op.drop_table("miniprogram_store_profile")
