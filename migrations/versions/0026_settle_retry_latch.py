"""收敛历史消息上永远清不掉的「重试在途」标记。"""

import json
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import context, op

revision: str = "0026_settle_retry_latch"
down_revision: str | None = "0025_purged_task_marks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _loads(raw: Any) -> dict[str, Any]:
    """messages.metadata 在 PostgreSQL 是 json、在 SQLite 是文本，统一成字典。"""
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, (str, bytes)):
        try:
            parsed = json.loads(raw)
        except ValueError:
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


def upgrade() -> None:
    """把已了结却仍标着「重试在途」的历史消息清干净。

    `delivery_retry_pending` 压制的是失败通知：为真时不惊动员工，等重试有结果。
    代码此前只在终态失败补偿里清它，「重试被受理」与「重试失败且已就地通知」两条
    正常出口都没清，于是闩锁永久留存。同版本已在这两处补上，本迁移只处理存量。

    判据严格对应那两条出口，逐条要有证据，不按时间或数量一刀切：
    重试消息（metadata.retry_of_message_id 指向本条）已被受理，或它自己失败但
    已登记过员工通知。两者都不满足的保持原样——那可能是真的还在途中。

    只改这一个布尔标记，不动 delivery_status，也不补发任何通知：受理不等于送达，
    历史消息更不该因为一次数据收敛而重新惊动员工。
    """
    if context.is_offline_mode():
        # 这是数据迁移不是结构迁移：判据要逐条读现有 metadata 才能成立，离线
        # 生成 SQL 时没有连接可读。生产走 deploy/start.sh 的在线 `upgrade head`，
        # 离线 SQL 只是完整性检查用的产物，跳过这一步不影响任何实际部署。
        return
    messages = sa.table(
        "messages",
        sa.column("id", sa.Integer),
        sa.column("metadata", sa.JSON),
    )
    connection = op.get_bind()
    rows = connection.execute(
        sa.select(messages.c.id, messages.c.metadata)
    ).fetchall()

    parsed = {row.id: _loads(row.metadata) for row in rows}
    # 原消息 → 它的重试消息们；一条原消息理论上只有一次重试，但不假设唯一。
    retries: dict[int, list[dict[str, Any]]] = {}
    for metadata in parsed.values():
        raw_origin = metadata.get("retry_of_message_id")
        if not raw_origin:
            continue
        try:
            origin_id = int(raw_origin)
        except (TypeError, ValueError):
            continue
        retries.setdefault(origin_id, []).append(metadata)

    for message_id, metadata in parsed.items():
        if not metadata.get("delivery_retry_pending"):
            continue
        settled = any(
            retry.get("delivery_status") == "accepted"
            or retry.get("delivery_failure_notified")
            for retry in retries.get(message_id, [])
        )
        if not settled:
            continue
        updated = dict(metadata)
        updated["delivery_retry_pending"] = False
        updated["delivery_rewrite_pending"] = False
        connection.execute(
            sa.update(messages)
            .where(messages.c.id == message_id)
            .values(metadata=updated)
        )


def downgrade() -> None:
    """不回滚。

    这些标记原本就是错的：把它们改回 true 只会重新制造假阳性，而真正的「在途」
    状态无法从数据里区分出来。回滚本迁移不影响任何功能。
    """
