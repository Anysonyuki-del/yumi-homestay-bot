"""知识条目增加附加条件词与排除词（借鉴世界书的次要关键词与排除词，Spec F5）。"""
import sqlalchemy as sa
from alembic import op

revision = "0032_knowledge_triggers"
down_revision = "0031_guest_verification"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """两列都可空，存量条目为空表示不加限制，行为与升级前一致。"""
    op.add_column("knowledge_entries", sa.Column("trigger_any", sa.JSON(), nullable=True))
    op.add_column("knowledge_entries", sa.Column("trigger_exclude", sa.JSON(), nullable=True))


def downgrade() -> None:
    """移除新增列；条目正文不受影响。"""
    op.drop_column("knowledge_entries", "trigger_exclude")
    op.drop_column("knowledge_entries", "trigger_any")
