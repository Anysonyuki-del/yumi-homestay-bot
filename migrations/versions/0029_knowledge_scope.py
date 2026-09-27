"""知识增加明确适用范围和有效期，存量保留正文并按启用状态给出待复核范围。"""
import re
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

revision: str = "0029_knowledge_scope"
down_revision: str | None = "0028_guest_plaintext"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# 冻结迁移时的词表，不导入会随应用版本变化的判断函数。
_EXTERNAL_SCOPE_PATTERN = re.compile(
    r"附近|周边|周围|楼下|街口|巷口|巷子口|路口|隔壁|对面|不远处"
    r"|与本店(?:没有|无)(?:合作|关系)|非本店|不是本店"
    r"|nearby|next\s+door|across\s+the\s+street|downstairs|down\s+the\s+lane"
    r"|around\s+the\s+corner|not\s+affiliated", re.I,
)


def upgrade() -> None:
    """用批量修改兼容 SQLite；不猜测存量知识适用房间。"""
    with op.batch_alter_table("knowledge_entries") as batch:
        batch.add_column(sa.Column("scope", sa.String(16), nullable=False,
                                   server_default="unreviewed"))
        batch.add_column(sa.Column("property_id", sa.BigInteger(), nullable=True))
        batch.add_column(sa.Column("valid_from", sa.Date(), nullable=True))
        batch.add_column(sa.Column("valid_until", sa.Date(), nullable=True))
        batch.create_foreign_key("fk_knowledge_property", "property_profiles",
                                 ["property_id"], ["id"])
        batch.create_check_constraint("ck_knowledge_scope",
                                      "scope IN ('unreviewed', 'global', 'property', 'public')")
        batch.create_check_constraint("ck_knowledge_scope_property",
                                      "(scope = 'property' AND property_id IS NOT NULL) OR "
                                      "(scope <> 'property' AND property_id IS NULL)")
        batch.create_check_constraint("ck_knowledge_valid_dates",
                                      "valid_from IS NULL OR valid_until IS NULL "
                                      "OR valid_from <= valid_until")

    if context.is_offline_mode():
        # PostgreSQL 离线发布脚本不能读取行；使用同一冻结词表生成等价更新。
        pattern = str(op.inline_literal(_EXTERNAL_SCOPE_PATTERN.pattern))
        op.execute(
            "UPDATE knowledge_entries SET scope = CASE WHEN "
            "concat_ws(' ', category, question_zh, question_en) ~* " + pattern +
            " THEN 'public' ELSE 'global' END WHERE is_enabled"
        )
        return
    connection = op.get_bind()
    entries = connection.execute(sa.text(
        "SELECT id, category, question_zh, question_en FROM knowledge_entries WHERE is_enabled"
    )).mappings().all()
    for entry in entries:
        title = " ".join(str(entry[field] or "") for field in
                         ("category", "question_zh", "question_en"))
        scope = "public" if _EXTERNAL_SCOPE_PATTERN.search(title) else "global"
        connection.execute(sa.text("UPDATE knowledge_entries SET scope=:scope WHERE id=:id"),
                           {"scope": scope, "id": entry["id"]})


def downgrade() -> None:
    """删除范围元数据；只用于已备份的隔离验证，原正文保留。"""
    with op.batch_alter_table("knowledge_entries") as batch:
        batch.drop_constraint("ck_knowledge_valid_dates", type_="check")
        batch.drop_constraint("ck_knowledge_scope_property", type_="check")
        batch.drop_constraint("ck_knowledge_scope", type_="check")
        batch.drop_constraint("fk_knowledge_property", type_="foreignkey")
        for column in ("valid_until", "valid_from", "property_id", "scope"):
            batch.drop_column(column)
