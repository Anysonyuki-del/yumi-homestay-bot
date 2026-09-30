"""微信客服消息同步游标持久化，避免回调同步与补拉重放旧事件。"""
import sqlalchemy as sa
from alembic import op

revision = "0034_wecom_sync_cursors"
down_revision = "0033_knowledge_images"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """新表为空：上线后第一次同步仍从头读一次（消息去重、事件有时间判断），之后接着读。"""
    op.create_table(
        "wecom_sync_cursors",
        sa.Column("open_kfid", sa.String(length=128), primary_key=True),
        sa.Column("cursor", sa.Text(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    """回滚删表；旧代码不读这张表，回到每次从头同步。"""
    op.drop_table("wecom_sync_cursors")
