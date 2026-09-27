"""任务保存待人工核对的明文资料，不设置个人信息清除期限。"""
import sqlalchemy as sa
from alembic import op

revision = "0031_guest_verification"
down_revision = "0030_stay_confirmation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """核对字段可空，存量任务保持原样。"""
    op.add_column("business_tasks", sa.Column("verification_data", sa.JSON(), nullable=True))


def downgrade() -> None:
    """移除新增字段，降级前需备份核对资料。"""
    op.drop_column("business_tasks", "verification_data")
