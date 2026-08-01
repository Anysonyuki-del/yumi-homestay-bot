"""增加运营使用的真实房间号。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_property_room_number"
down_revision: str | None = "0009_approval_source_message"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """在百居易房源主键之外保存员工可读房间号。"""
    op.add_column(
        "property_profiles",
        sa.Column("room_number", sa.String(length=32), nullable=True),
    )
    op.create_index(
        "ix_property_profiles_room_number",
        "property_profiles",
        ["room_number"],
    )


def downgrade() -> None:
    """删除真实房间号字段。"""
    op.drop_index("ix_property_profiles_room_number", table_name="property_profiles")
    op.drop_column("property_profiles", "room_number")
