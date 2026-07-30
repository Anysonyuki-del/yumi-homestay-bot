from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from homestay_bot.domain.enums import BusinessTaskStatus
from homestay_bot.domain.models import (
    BusinessTask,
    Conversation,
    CustomerContextSummary,
    Message,
    PropertyProfile,
    StayOrder,
)
from homestay_bot.services.context_retention import (
    ContextSummaryResult,
    CustomerModelContext,
)


class SQLAlchemyContextRepository:
    """按客户隔离读取摘要候选并原子保存分层摘要。"""

    def __init__(self, session: AsyncSession) -> None:
        """绑定当前维护事务。"""
        self._session = session

    async def get_summary(
        self, customer_id: int
    ) -> CustomerContextSummary | None:
        """读取客户唯一摘要记录。"""
        result = await self._session.scalars(
            select(CustomerContextSummary).where(
                CustomerContextSummary.customer_id == customer_id
            )
        )
        return result.first()

    async def list_customer_ids_with_messages(self) -> list[int]:
        """返回至少拥有一条消息的正式客户主键。"""
        customer_ids = list(
            (
                await self._session.scalars(
                    select(Conversation.customer_id)
                    .join(Message)
                    .where(Conversation.customer_id.is_not(None))
                    .distinct()
                )
            ).all()
        )
        return [customer_id for customer_id in customer_ids if customer_id is not None]

    async def list_short_candidates(
        self,
        customer_id: int,
        now: datetime,
        raw_limit: int,
    ) -> list[Message]:
        """返回七天内未摘要消息，并保留处理顺序最新的原文窗口。"""
        messages = list(
            (
                await self._session.scalars(
                    select(Message)
                    .join(Conversation)
                    .where(
                        Conversation.customer_id == customer_id,
                        Message.sent_at >= now - timedelta(days=7),
                        Message.content.is_not(None),
                        Message.short_summarized_at.is_(None),
                        Message.message_type == "text",
                    )
                    .order_by(Message.id.desc())
                )
            ).all()
        )
        candidates = messages[raw_limit:]
        candidates.reverse()
        return candidates

    async def list_expired_unpurged(
        self,
        customer_id: int,
        before: datetime,
    ) -> list[Message]:
        """按处理顺序返回七天外仍有正文的消息。"""
        return list(
            (
                await self._session.scalars(
                    select(Message)
                    .join(Conversation)
                    .where(
                        Conversation.customer_id == customer_id,
                        Message.sent_at < before,
                        Message.content.is_not(None),
                        Message.purged_at.is_(None),
                        Message.message_type == "text",
                    )
                    .order_by(Message.id)
                )
            ).all()
        )

    async def save_short_summary(
        self,
        customer_id: int,
        result: ContextSummaryResult,
        messages: list[Message],
        now: datetime,
    ) -> None:
        """保存短摘要并标记已覆盖消息，提交由维护循环负责。"""
        summary = await self._get_or_create_summary(customer_id)
        summary.short_summary = result.summary
        summary.unresolved_items = result.unresolved_items
        summary.short_cutoff_at = max(item.sent_at for item in messages)
        summary.version += 1
        for item in messages:
            item.short_summarized_at = now
        await self._session.flush()

    async def save_long_summary_and_purge(
        self,
        customer_id: int,
        result: ContextSummaryResult,
        messages: list[Message],
        now: datetime,
    ) -> None:
        """在同一事务写长期摘要并清除已覆盖正文。"""
        summary = await self._get_or_create_summary(customer_id)
        summary.long_summary = result.summary
        summary.unresolved_items = result.unresolved_items
        summary.long_cutoff_at = max(item.sent_at for item in messages)
        summary.version += 1
        for item in messages:
            item.content = None
            item.purged_at = now
        await self._session.flush()

    async def load_model_context(self, customer_id: int) -> CustomerModelContext:
        """读取不含原文和敏感字段的客户摘要上下文。"""
        summary = await self.get_summary(customer_id)
        order_rows = (
            await self._session.execute(
                select(StayOrder, PropertyProfile.title)
                .join(
                    PropertyProfile,
                    PropertyProfile.id == StayOrder.property_id,
                )
                .where(
                    StayOrder.customer_id == customer_id,
                    StayOrder.status.not_in(
                        ["cancelled", "canceled", "checked_out", "completed"]
                    ),
                )
                .order_by(StayOrder.check_in_date, StayOrder.id)
                .limit(5)
            )
        ).all()
        tasks = list(
            (
                await self._session.scalars(
                    select(BusinessTask)
                    .where(
                        BusinessTask.customer_id == customer_id,
                        BusinessTask.status.not_in(
                            [
                                BusinessTaskStatus.COMPLETED,
                                BusinessTaskStatus.CANCELLED,
                            ]
                        ),
                    )
                    .order_by(BusinessTask.created_at, BusinessTask.id)
                    .limit(10)
                )
            ).all()
        )
        return CustomerModelContext(
            short_summary=summary.short_summary if summary else "",
            long_summary=summary.long_summary if summary else "",
            unresolved_items=list(summary.unresolved_items) if summary else [],
            active_orders=[
                {
                    "property_id": order.property_id,
                    "property_title": property_title,
                    "check_in_date": order.check_in_date.isoformat(),
                    "check_out_date": order.check_out_date.isoformat(),
                    "status": order.status,
                }
                for order, property_title in order_rows
            ],
            open_tasks=[
                {
                    "task_type": task.task_type.value,
                    "status": task.status.value,
                    "property_id": task.property_id,
                    "service_date": (
                        task.service_date.isoformat()
                        if task.service_date is not None
                        else None
                    ),
                }
                for task in tasks
            ],
        )

    async def _get_or_create_summary(
        self, customer_id: int
    ) -> CustomerContextSummary:
        """在当前事务幂等返回客户摘要行。"""
        summary = await self.get_summary(customer_id)
        if summary is not None:
            return summary
        summary = CustomerContextSummary(customer_id=customer_id)
        self._session.add(summary)
        await self._session.flush()
        return summary
