"""知识缺口转交记录、关闭抑制与来源完成收据的持久化（修复 Spec 2026-10-08 fix2 §2.6）。

所有草稿写回都按「状态 = 整理中 且 代次 = 期望值」做条件更新：同一代次只允许一个当前结果，
换代、入库、关闭、清空之后迟到的工作不能改变记录（Spec §2.6 不变量 4、5）。
"""

from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta
from typing import Any, cast

from sqlalchemy import case, func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from homestay_bot.domain.enums import KnowledgeReferralStatus, MessageOrigin
from homestay_bot.domain.models import (
    KnowledgeReferral,
    KnowledgeReferralSuppression,
    Message,
    SourceCompletionReceipt,
)

# 管家消息窗口：转交创建时间（截到秒）起 48 小时内（Spec §2.6(3)，V7-R5）。
SERVICER_WINDOW = timedelta(hours=48)
# 一次整理最多冻结的管家消息条数（D-G20）。
DRAFT_SOURCE_LIMIT = 5
# 可以重新点「整理草稿」的状态；整理中不能强制重开（V8-R2）。
DRAFTABLE_STATUSES = (
    KnowledgeReferralStatus.PENDING,
    KnowledgeReferralStatus.DRAFTED,
    KnowledgeReferralStatus.NO_ANSWER,
    KnowledgeReferralStatus.DRAFT_FAILED,
)
# 仍在等待管家或管理员处理的开放状态，后台待处理数量按它统计（D-G17）。
OPEN_STATUSES = (*DRAFTABLE_STATUSES, KnowledgeReferralStatus.DRAFTING)
# 草稿任务的三个终态写回目标。
DRAFT_RESULT_STATUSES = (
    KnowledgeReferralStatus.DRAFTED,
    KnowledgeReferralStatus.NO_ANSWER,
    KnowledgeReferralStatus.DRAFT_FAILED,
)


def _property_filter(column: Any, property_id: int | None) -> Any:
    """房源可空：空值必须用 IS NULL 比较，否则「未确定房源」的冷却与抑制永远查不到。"""
    return column.is_(None) if property_id is None else column == property_id


def window_start(created_at: datetime) -> datetime:
    """管家消息窗口起点：平台发送时间按秒，本地创建时间截到秒后比较（V7-R5）。"""
    return created_at.replace(microsecond=0)


class SQLAlchemyKnowledgeReferralRepository:
    """转交记录、抑制与来源完成收据都在调用方事务内读写，由调用方提交。"""

    def __init__(self, session: AsyncSession) -> None:
        """绑定当前业务事务。"""
        self._session = session

    # ---- 来源完成收据（§2.6(2) 恢复规则） ----

    async def receipt_exists(self, source_message_id: str) -> bool:
        """本来源的业务处理是否已终结并提交。"""
        found = await self._session.scalar(
            select(SourceCompletionReceipt.source_message_id).where(
                SourceCompletionReceipt.source_message_id == source_message_id
            )
        )
        return found is not None

    async def record_receipt(self, source_message_id: str, referral_ids: Sequence[int]) -> None:
        """与本来源全部业务副作用同一事务写入收据；已存在时不重复写。

        并发重复写入会因主键冲突让整个事务回滚，作业重试时读到收据直接结束，不会产生
        第二份副作用。收据只含编号，不含正文。
        """
        if await self._session.get(SourceCompletionReceipt, source_message_id) is not None:
            return
        self._session.add(
            SourceCompletionReceipt(
                source_message_id=source_message_id,
                referral_ids=list(referral_ids),
            )
        )
        await self._session.flush()

    # ---- 会话层创建转交（§2.6(2)） ----

    async def suppressed(
        self, normalized_question: str, property_id: int | None, *, now: datetime
    ) -> bool:
        """同一归一化问题与房源是否在关闭后的 30 天抑制期内（D-G16）。"""
        found = await self._session.scalar(
            select(KnowledgeReferralSuppression.id)
            .where(
                KnowledgeReferralSuppression.normalized_question == normalized_question,
                _property_filter(KnowledgeReferralSuppression.property_id, property_id),
                KnowledgeReferralSuppression.suppressed_until > now,
            )
            .limit(1)
        )
        return found is not None

    async def recent_in_conversation(
        self,
        conversation_id: int,
        normalized_question: str,
        property_id: int | None,
        *,
        since: datetime,
    ) -> KnowledgeReferral | None:
        """同一会话、同一问题与房源在冷却期内最近的一条转交。"""
        return cast(
            KnowledgeReferral | None,
            await self._session.scalar(
                select(KnowledgeReferral)
                .where(
                    KnowledgeReferral.conversation_id == conversation_id,
                    KnowledgeReferral.normalized_question == normalized_question,
                    _property_filter(KnowledgeReferral.property_id, property_id),
                    KnowledgeReferral.created_at >= since,
                )
                .order_by(KnowledgeReferral.created_at.desc(), KnowledgeReferral.id.desc())
                .limit(1)
            ),
        )

    async def find_by_source(
        self, source_message_id: str, unit_id: str
    ) -> KnowledgeReferral | None:
        """按「来源消息 + 单元编号」查已有记录，供重放复用。"""
        return cast(
            KnowledgeReferral | None,
            await self._session.scalar(
                select(KnowledgeReferral).where(
                    KnowledgeReferral.source_message_id == source_message_id,
                    KnowledgeReferral.unit_id == unit_id,
                )
            ),
        )

    async def create(self, **fields: Any) -> KnowledgeReferral:
        """新建待处理转交；唯一约束（来源消息 + 单元编号）是重放的最后防线。"""
        referral = KnowledgeReferral(**fields)
        self._session.add(referral)
        await self._session.flush()
        return referral

    # ---- 卡片与后台读取 ----

    async def get(self, referral_id: int, *, lock: bool = False) -> KnowledgeReferral | None:
        """按编号读取当前列值；`lock` 时取 PostgreSQL 行锁，入库与整理按它串行。"""
        statement = (
            select(KnowledgeReferral)
            .where(KnowledgeReferral.id == referral_id)
            .execution_options(populate_existing=True)
        )
        if lock:
            statement = statement.with_for_update()
        return cast(KnowledgeReferral | None, await self._session.scalar(statement))

    async def list_recent(self, *, limit: int = 50, offset: int = 0) -> list[KnowledgeReferral]:
        """后台列表：开放状态在前，其余按创建时间倒序。"""
        rows = await self._session.scalars(
            select(KnowledgeReferral)
            .order_by(
                case((KnowledgeReferral.status.in_(OPEN_STATUSES), 0), else_=1),
                KnowledgeReferral.created_at.desc(), KnowledgeReferral.id.desc(),
            )
            .offset(offset)
            .limit(limit)
        )
        return list(rows.all())

    async def count_open(self) -> int:
        """待处理数量（D-G17：不催办，只在后台显示）。"""
        count = await self._session.scalar(
            select(func.count()).select_from(KnowledgeReferral)
            .where(KnowledgeReferral.status.in_(OPEN_STATUSES))
        )
        return int(count or 0)

    async def servicer_messages(self, referral: KnowledgeReferral) -> list[Message]:
        """转交后的管家文字消息窗口（Spec §2.6(3)，V7-R5），按（发送时间，编号）升序。

        条件：同一会话；SERVICER 文字消息；消息编号大于来源客人消息；发送时间在转交创建时间
        （截到秒）到其后 48 小时之间。转交之前的管家消息、迟到补拉的旧消息不计入。
        """
        source_id = await self._session.scalar(
            select(Message.id).where(Message.external_message_id == referral.source_message_id)
        )
        if source_id is None:
            return []
        start = window_start(referral.created_at)
        rows = await self._session.scalars(
            select(Message)
            .where(
                Message.conversation_id == referral.conversation_id,
                Message.origin == MessageOrigin.SERVICER,
                Message.message_type == "text",
                Message.id > source_id,
                Message.sent_at >= start,
                Message.sent_at <= start + SERVICER_WINDOW,
                Message.content.is_not(None),
                Message.content != "",
            )
            .order_by(Message.sent_at, Message.id)
            .execution_options(populate_existing=True)
        )
        return list(rows.all())

    async def messages_by_ids(self, message_ids: Iterable[int]) -> list[Message]:
        """按冻结编号读取管家消息的当前列值（复核未被清空用），按（发送时间，编号）升序。"""
        ids = list(message_ids)
        if not ids:
            return []
        rows = await self._session.scalars(
            select(Message)
            .where(Message.id.in_(ids))
            .order_by(Message.sent_at, Message.id)
            .execution_options(populate_existing=True)
        )
        return list(rows.all())

    # ---- 草稿代次（§2.6(3)） ----

    async def finish_draft(
        self,
        referral_id: int,
        *,
        generation: int,
        status: KnowledgeReferralStatus,
        draft: dict[str, Any] | None = None,
    ) -> bool:
        """草稿任务唯一的写回入口：只匹配「整理中 + 期望代次」，返回是否写入。

        成功、没找到答案、重试耗尽三种结果都走这一条条件更新；同代次的迟到结果因状态已不是
        整理中而被拒，换代、入库、关闭、清空之后的写回同样被拒（V6-R4、V8-R2）。
        """
        if status not in DRAFT_RESULT_STATUSES:
            raise ValueError("草稿写回只能进入已整理、没找到答案或草稿失败")
        result = cast(
            CursorResult[Any],
            await self._session.execute(
                update(KnowledgeReferral)
                .where(
                    KnowledgeReferral.id == referral_id,
                    KnowledgeReferral.status == KnowledgeReferralStatus.DRAFTING,
                    KnowledgeReferral.generation == generation,
                )
                .values(status=status, draft=draft)
                .execution_options(synchronize_session=False)
            ),
        )
        return result.rowcount == 1

    async def suppress(
        self,
        normalized_question: str,
        property_id: int | None,
        *,
        until: datetime,
        closed_by: int | None,
    ) -> None:
        """管理员关闭为「不入库」时写入 30 天抑制。"""
        self._session.add(
            KnowledgeReferralSuppression(
                normalized_question=normalized_question,
                property_id=property_id,
                suppressed_until=until,
                closed_by=closed_by,
            )
        )
        await self._session.flush()

    # ---- 测试数据清空（§2.6(6)） ----

    async def clear_for_conversations(self, conversation_ids: Sequence[int]) -> int:
        """清掉这些会话转交记录的正文与草稿，状态改为已清空、代次加一，保留编号。

        代次加一让在途草稿任务的写回失配；已清空的记录不再发卡片、不可整理或入库。
        已入库记录的知识编号保留：知识沿用自己的生命周期，不随聊天清空删除。
        """
        if not conversation_ids:
            return 0
        result = cast(
            CursorResult[Any],
            await self._session.execute(
                update(KnowledgeReferral)
                .where(KnowledgeReferral.conversation_id.in_(list(conversation_ids)))
                .values(
                    question=None,
                    canonical_question=None,
                    # 归一化问题由客人问法派生，同样清掉；清空后新来源的同类问题可再转交。
                    normalized_question="",
                    category=None,
                    draft=None,
                    source_message_ids=[],
                    status=KnowledgeReferralStatus.CLEARED,
                    generation=KnowledgeReferral.generation + 1,
                )
                .execution_options(synchronize_session=False)
            ),
        )
        return int(result.rowcount or 0)
