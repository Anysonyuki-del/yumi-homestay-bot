"""为客户记忆增加可信证据、单一当前状态和事件时间线。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020_memory_trust_timeline"
down_revision: str | None = "0019_customer_memory_items"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _dialect_name() -> str:
    """返回当前在线连接或离线 SQL 生成器使用的数据库方言。"""
    return op.get_bind().dialect.name


def _approval_match_sql(memory_alias: str) -> str:
    """按数据库方言生成管理员批准审计与记忆编号的匹配条件。"""
    if _dialect_name() == "postgresql":
        return (
            "audit.details ->> 'memory_id' = CAST("
            f"{memory_alias}.id AS TEXT) "
            "AND audit.details ->> 'decision' = 'approve'"
        )
    return (
        "json_extract(audit.details, '$.memory_id') = "
        f"{memory_alias}.id "
        "AND json_extract(audit.details, '$.decision') = 'approve'"
    )


def _backfill_trust_metadata() -> None:
    """回填可确定的来源时间，并隔离缺少管理员批准证明的历史有效项。"""
    approval_match = _approval_match_sql("memory")
    op.execute(
        sa.text(
            "UPDATE customer_memory_items AS memory "
            "SET source_occurred_at = ("
            "SELECT message.sent_at FROM messages AS message "
            "WHERE message.external_message_id = memory.source_message_id LIMIT 1"
            ") WHERE memory.source_message_id IS NOT NULL"
        )
    )
    op.execute(
        sa.text(
            "UPDATE customer_memory_items AS memory "
            "SET verified_at = ("
            "SELECT MAX(audit.created_at) FROM audit_logs AS audit "
            "WHERE audit.action = 'customer_memory_reviewed' "
            f"AND {approval_match}"
            ") WHERE memory.status = 'ACTIVE' AND EXISTS ("
            "SELECT 1 FROM audit_logs AS audit "
            "WHERE audit.action = 'customer_memory_reviewed' "
            f"AND {approval_match}"
            ")"
        )
    )
    op.execute(
        sa.text(
            "UPDATE customer_memory_items AS memory "
            "SET status = 'CANDIDATE', confirmed_at = NULL, "
            "status_reason = '历史证据不可验证' "
            "WHERE memory.status = 'ACTIVE' AND NOT EXISTS ("
            "SELECT 1 FROM audit_logs AS audit "
            "WHERE audit.action = 'customer_memory_reviewed' "
            f"AND {approval_match}"
            ")"
        )
    )


def _resolve_historical_active_conflicts() -> None:
    """在建立唯一索引前，将同主题冲突转为争议并合并重复陈述。"""
    op.execute(
        sa.text(
            "UPDATE customer_memory_items SET "
            "status = 'DISPUTED', confirmed_at = NULL, "
            "status_reason = '历史同主题存在冲突陈述' "
            "WHERE status = 'ACTIVE' AND (customer_id, subject_key) IN ("
            "SELECT customer_id, subject_key FROM customer_memory_items "
            "WHERE status = 'ACTIVE' GROUP BY customer_id, subject_key "
            "HAVING COUNT(DISTINCT lower(trim(statement))) > 1"
            ")"
        )
    )
    op.execute(
        sa.text(
            "UPDATE customer_memory_items AS memory SET "
            "status = 'SUPERSEDED', confirmed_at = NULL, "
            "status_reason = '历史重复记忆已合并' "
            "WHERE memory.status = 'ACTIVE' AND EXISTS ("
            "SELECT 1 FROM customer_memory_items AS newer "
            "WHERE newer.customer_id = memory.customer_id "
            "AND newer.subject_key = memory.subject_key "
            "AND newer.status = 'ACTIVE' "
            "AND lower(trim(newer.statement)) = lower(trim(memory.statement)) "
            "AND newer.id > memory.id"
            ")"
        )
    )


def _insert_legacy_events() -> None:
    """为每条既有记忆建立一次不伪造原状态的迁移事件。"""
    approval_match = _approval_match_sql("memory")
    op.execute(
        sa.text(
            "INSERT INTO customer_memory_events ("
            "customer_id, memory_item_id, subject_key, event_type, previous_status, "
            "new_status, statement_snapshot, source_message_id, actor_employee_id, "
            "reason, occurred_at"
            ") SELECT memory.customer_id, memory.id, memory.subject_key, "
            "'legacy_migrated', NULL, memory.status, memory.statement, "
            "memory.source_message_id, ("
            "SELECT audit.actor_employee_id FROM audit_logs AS audit "
            "WHERE audit.action = 'customer_memory_reviewed' "
            f"AND {approval_match} "
            "ORDER BY audit.created_at DESC, audit.id DESC LIMIT 1"
            "), COALESCE(memory.status_reason, '历史记忆迁移'), CURRENT_TIMESTAMP "
            "FROM customer_memory_items AS memory"
        )
    )


def upgrade() -> None:
    """增加可信证据字段、事件表，并安全治理既有有效记忆。"""
    with op.batch_alter_table("customer_memory_items") as batch_op:
        batch_op.add_column(sa.Column("source_excerpt", sa.Text(), nullable=True))
        batch_op.add_column(
            sa.Column("source_excerpt_hash", sa.String(length=64), nullable=True)
        )
        batch_op.add_column(
            sa.Column("source_occurred_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "version",
                sa.Integer(),
                server_default=sa.text("0"),
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column("content_redacted_at", sa.DateTime(timezone=True), nullable=True)
        )

    op.create_table(
        "customer_memory_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("memory_item_id", sa.Integer(), nullable=False),
        sa.Column("subject_key", sa.String(length=128), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("previous_status", sa.String(length=16), nullable=True),
        sa.Column("new_status", sa.String(length=16), nullable=True),
        sa.Column("statement_snapshot", sa.Text(), nullable=True),
        sa.Column("source_message_id", sa.String(length=128), nullable=True),
        sa.Column("actor_employee_id", sa.Integer(), nullable=True),
        sa.Column("reason", sa.String(length=256), nullable=True),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("content_redacted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["customer_id"], ["customers.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["memory_item_id"], ["customer_memory_items.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["source_message_id"],
            ["messages.external_message_id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["actor_employee_id"], ["employees.id"], ondelete="SET NULL"
        ),
    )
    op.create_index(
        "ix_customer_memory_event_customer_occurred",
        "customer_memory_events",
        ["customer_id", "occurred_at"],
    )
    op.create_index(
        "ix_customer_memory_event_memory_occurred",
        "customer_memory_events",
        ["memory_item_id", "occurred_at"],
    )

    _backfill_trust_metadata()
    _resolve_historical_active_conflicts()
    _insert_legacy_events()
    op.create_index(
        "uq_customer_memory_active_subject",
        "customer_memory_items",
        ["customer_id", "subject_key"],
        unique=True,
        postgresql_where=sa.text("status = 'ACTIVE'"),
        sqlite_where=sa.text("status = 'ACTIVE'"),
    )


def downgrade() -> None:
    """移除事件时间线和可信证据字段，恢复 0019 的数据结构。"""
    op.drop_index(
        "uq_customer_memory_active_subject", table_name="customer_memory_items"
    )
    op.drop_index(
        "ix_customer_memory_event_memory_occurred",
        table_name="customer_memory_events",
    )
    op.drop_index(
        "ix_customer_memory_event_customer_occurred",
        table_name="customer_memory_events",
    )
    op.drop_table("customer_memory_events")
    with op.batch_alter_table("customer_memory_items") as batch_op:
        batch_op.drop_column("content_redacted_at")
        batch_op.drop_column("version")
        batch_op.drop_column("verified_at")
        batch_op.drop_column("source_occurred_at")
        batch_op.drop_column("source_excerpt_hash")
        batch_op.drop_column("source_excerpt")
