"""结束审批明文兼容期并删除旧明文字段。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023_approval_pii_final"
down_revision: str | None = "0022_approval_pii"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INCOMPLETE_MESSAGE = "审批敏感资料密文回填未完成，拒绝删除旧明文字段"


def _require_complete_ciphertext() -> None:
    """删列前确认所有未清理审批都具备姓名和手机号密文。"""
    connection = op.get_bind()
    condition = (
        "pii_purged_at IS NULL AND "
        "(guest_name_ciphertext IS NULL OR guest_mobile_ciphertext IS NULL)"
    )
    if connection.dialect.name == "postgresql":
        # DO 块同时适用于在线执行和 PostgreSQL 离线 SQL 生成。
        op.execute(
            sa.text(
                "DO $approval_pii$ BEGIN "
                f"IF EXISTS (SELECT 1 FROM booking_approvals WHERE {condition}) THEN "
                f"RAISE EXCEPTION '{_INCOMPLETE_MESSAGE}'; "
                "END IF; END $approval_pii$"
            )
        )
        return

    incomplete = connection.execute(
        sa.text(
            "SELECT count(*) FROM booking_approvals "
            f"WHERE {condition}"
        )
    ).scalar_one()
    if incomplete:
        raise RuntimeError(_INCOMPLETE_MESSAGE)


def upgrade() -> None:
    """密文完整后删除三个旧明文字段，不改变审批业务和审计字段。"""
    _require_complete_ciphertext()
    with op.batch_alter_table("booking_approvals") as batch_op:
        batch_op.drop_column("special_requests")
        batch_op.drop_column("guest_mobile")
        batch_op.drop_column("guest_name")


def downgrade() -> None:
    """拒绝伪造已删除明文；回滚必须恢复升级前数据库备份。"""
    raise RuntimeError("0023 不可直接降级，请恢复升级前数据库备份并切回旧镜像")
