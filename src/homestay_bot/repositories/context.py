import re
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from homestay_bot.domain.enums import (
    BusinessTaskStatus,
    CustomerMemoryCategory,
    CustomerMemoryEvidenceType,
    CustomerMemoryStatus,
    MessageOrigin,
)
from homestay_bot.domain.models import (
    BusinessTask,
    Conversation,
    CustomerContextSummary,
    CustomerMemoryItem,
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

    # 摘要任务按批处理，避免单个高频客户一次性载入全部历史正文。
    SUMMARY_BATCH_LIMIT = 50
    MEMORY_RECALL_LIMIT = 8
    MEMORY_RECALL_CHARS = 2_400

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

    async def expire_customer_memories(
        self, customer_id: int, now: datetime
    ) -> None:
        """把到期或进入复核期的有效记忆标记为失效。"""
        memories = list(
            (
                await self._session.scalars(
                    select(CustomerMemoryItem).where(
                        CustomerMemoryItem.customer_id == customer_id,
                        CustomerMemoryItem.status == CustomerMemoryStatus.ACTIVE,
                        (CustomerMemoryItem.review_at <= now)
                        | (CustomerMemoryItem.expires_at <= now),
                    )
                )
            ).all()
        )
        for memory in memories:
            memory.status = CustomerMemoryStatus.STALE
            memory.status_reason = "到达复核期或有效期"
        if memories:
            await self._session.flush()

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
        # 先按倒序跳过最近原文窗口，再限制本轮摘要批量；后续周期会继续处理剩余记录。
        candidates = list(
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
                    .offset(max(raw_limit, 0))
                    .limit(self.SUMMARY_BATCH_LIMIT)
                )
            ).all()
        )
        candidates.reverse()
        return candidates

    async def list_recent_unobserved(
        self, customer_id: int, now: datetime
    ) -> list[Message]:
        """返回最近七天尚未提取结构化记忆的文本消息。"""
        return list(
            (
                await self._session.scalars(
                    select(Message)
                    .join(Conversation)
                    .where(
                        Conversation.customer_id == customer_id,
                        Message.sent_at >= now - timedelta(days=7),
                        Message.content.is_not(None),
                        Message.memory_processed_at.is_(None),
                        Message.message_type == "text",
                    )
                    .order_by(Message.id)
                    .limit(self.SUMMARY_BATCH_LIMIT)
                )
            ).all()
        )

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
                    .limit(self.SUMMARY_BATCH_LIMIT)
                )
            ).all()
        )

    async def save_short_summary(
        self,
        customer_id: int,
        result: ContextSummaryResult,
        messages: list[Message],
        now: datetime,
        *,
        source_messages: list[Message] | None = None,
        observed_messages: list[Message] | None = None,
    ) -> None:
        """保存短摘要并标记已覆盖消息，提交由维护循环负责。"""
        summary = await self._get_or_create_summary(customer_id)
        summary.short_summary = result.summary
        summary.unresolved_items = self._merge_unresolved(
            summary.unresolved_items, result.unresolved_items
        )
        summary.short_cutoff_at = max(item.sent_at for item in messages)
        summary.version += 1
        for item in messages:
            item.short_summarized_at = now
        await self._save_memory_candidates(
            customer_id,
            result,
            source_messages or messages,
            now,
        )
        for item in observed_messages or messages:
            item.memory_processed_at = now
        await self._session.flush()

    async def save_memory_observations(
        self,
        customer_id: int,
        result: ContextSummaryResult,
        messages: list[Message],
        now: datetime,
    ) -> None:
        """保存最近原文候选，但不把最近原文标记为已进入分层摘要。"""
        await self._save_memory_candidates(customer_id, result, messages, now)
        for item in messages:
            item.memory_processed_at = now
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
        summary.unresolved_items = self._merge_unresolved(
            summary.unresolved_items, result.unresolved_items
        )
        summary.long_cutoff_at = max(item.sent_at for item in messages)
        summary.version += 1
        for item in messages:
            item.content = None
            item.purged_at = now
            item.memory_processed_at = now
        await self._save_memory_candidates(customer_id, result, messages, now)
        await self._session.flush()

    async def load_model_context(
        self, customer_id: int, *, query: str = ""
    ) -> CustomerModelContext:
        """读取不含原文和敏感字段的客户摘要上下文。"""
        summary = await self.get_summary(customer_id)
        memories = await self._recall_memories(customer_id, query, datetime.now(UTC))
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
            memories=memories,
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

    async def get_customer_room_number(self, customer_id: int) -> str | None:
        """返回客户唯一有效订单对应的房间号，无法唯一确定时返回空值。"""
        room_numbers = list(
            (
                await self._session.scalars(
                    select(PropertyProfile.room_number)
                    .select_from(StayOrder)
                    .join(PropertyProfile, PropertyProfile.id == StayOrder.property_id)
                    .where(
                        StayOrder.customer_id == customer_id,
                        StayOrder.status.not_in(
                            ["cancelled", "canceled", "checked_out", "completed"]
                        ),
                    )
                    .order_by(StayOrder.id)
                    .limit(2)
                )
            ).all()
        )
        if len(room_numbers) != 1:
            return None
        room_number = room_numbers[0]
        return str(room_number) if room_number else None

    async def _save_memory_candidates(
        self,
        customer_id: int,
        result: ContextSummaryResult,
        messages: list[Message],
        now: datetime,
    ) -> None:
        """核验候选证据并在当前摘要事务中完成晋级、冲突或覆盖。"""
        sources = {item.external_message_id: item for item in messages}
        for candidate in result.memory_candidates:
            source = sources.get(candidate.source_message_id or "")
            evidence = self._verified_evidence(candidate.evidence_type, source)
            status = self._initial_memory_status(
                candidate.category,
                evidence,
                candidate.confidence,
            )
            existing = list(
                (
                    await self._session.scalars(
                        select(CustomerMemoryItem)
                        .where(
                            CustomerMemoryItem.customer_id == customer_id,
                            CustomerMemoryItem.subject_key == candidate.subject_key,
                            CustomerMemoryItem.status.in_(
                                [
                                    CustomerMemoryStatus.CANDIDATE,
                                    CustomerMemoryStatus.ACTIVE,
                                    CustomerMemoryStatus.DISPUTED,
                                ]
                            ),
                        )
                        .order_by(CustomerMemoryItem.id)
                        .with_for_update()
                    )
                ).all()
            )
            duplicate = next(
                (
                    item
                    for item in existing
                    if self._normalize_statement(item.statement)
                    == self._normalize_statement(candidate.statement)
                ),
                None,
            )
            review_at, expires_at = self._memory_deadlines(candidate.category, now)
            if duplicate is not None:
                duplicate.confidence = max(duplicate.confidence, candidate.confidence)
                duplicate.source_message_id = source.external_message_id if source else None
                duplicate.evidence_type = evidence
                duplicate.review_at = review_at
                duplicate.expires_at = expires_at
                if status is CustomerMemoryStatus.ACTIVE:
                    duplicate.status = CustomerMemoryStatus.ACTIVE
                    duplicate.confirmed_at = now
                    duplicate.status_reason = None
                continue

            active_conflicts = [
                item for item in existing if item.status is CustomerMemoryStatus.ACTIVE
            ]
            supersedes_id = None
            if status is CustomerMemoryStatus.ACTIVE and active_conflicts:
                if candidate.is_correction:
                    for item in active_conflicts:
                        item.status = CustomerMemoryStatus.SUPERSEDED
                        item.status_reason = "客户或员工明确纠正"
                    supersedes_id = active_conflicts[-1].id
                else:
                    status = CustomerMemoryStatus.DISPUTED
                    for item in active_conflicts:
                        item.status = CustomerMemoryStatus.DISPUTED
                        item.status_reason = "同一主题存在冲突陈述"

            self._session.add(
                CustomerMemoryItem(
                    customer_id=customer_id,
                    subject_key=candidate.subject_key,
                    category=candidate.category,
                    statement=candidate.statement,
                    status=status,
                    evidence_type=evidence,
                    source_message_id=source.external_message_id if source else None,
                    confidence=candidate.confidence,
                    confirmed_at=now if status is CustomerMemoryStatus.ACTIVE else None,
                    review_at=review_at,
                    expires_at=expires_at,
                    supersedes_id=supersedes_id,
                    status_reason=(
                        "等待人工复核"
                        if status is CustomerMemoryStatus.CANDIDATE
                        else "同一主题存在冲突陈述"
                        if status is CustomerMemoryStatus.DISPUTED
                        else None
                    ),
                )
            )
            # 纠正关系依赖旧行主键；刷新可让后续同批候选看见本条记录。
            await self._session.flush()

    async def _recall_memories(
        self, customer_id: int, query: str, now: datetime
    ) -> list[dict[str, str | float]]:
        """按当前问题相关性和固定字符预算召回当前客户的有效记忆。"""
        rows = list(
            (
                await self._session.scalars(
                    select(CustomerMemoryItem)
                    .where(
                        CustomerMemoryItem.customer_id == customer_id,
                        CustomerMemoryItem.status == CustomerMemoryStatus.ACTIVE,
                        CustomerMemoryItem.review_at > now,
                        CustomerMemoryItem.expires_at > now,
                    )
                    .order_by(
                        CustomerMemoryItem.confirmed_at.desc(),
                        CustomerMemoryItem.id.desc(),
                    )
                    .limit(50)
                )
            ).all()
        )
        scored = sorted(
            (
                (self._relevance_score(query, item), item)
                for item in rows
            ),
            key=lambda pair: (pair[0], pair[1].confirmed_at or pair[1].created_at),
            reverse=True,
        )
        recalled: list[dict[str, str | float]] = []
        used_chars = 0
        for score, item in scored:
            # 有查询时不注入完全无关的历史事实，避免客户画像污染当前回答。
            if query.strip() and score <= 0:
                continue
            item_chars = len(item.subject_key) + len(item.statement)
            if used_chars + item_chars > self.MEMORY_RECALL_CHARS:
                continue
            recalled.append(
                {
                    "subject_key": item.subject_key,
                    "category": item.category.value,
                    "statement": item.statement,
                    "confidence": item.confidence,
                }
            )
            used_chars += item_chars
            if len(recalled) >= self.MEMORY_RECALL_LIMIT:
                break
        return recalled

    @staticmethod
    def _verified_evidence(
        requested: CustomerMemoryEvidenceType,
        source: Message | None,
    ) -> CustomerMemoryEvidenceType:
        """只在消息来源与模型声明一致时承认证据等级。"""
        if (
            requested is CustomerMemoryEvidenceType.USER_EXPLICIT
            and source is not None
            and source.origin is MessageOrigin.GUEST
        ):
            return requested
        if (
            requested is CustomerMemoryEvidenceType.EMPLOYEE_CONFIRMED
            and source is not None
            and source.origin is MessageOrigin.SERVICER
        ):
            return requested
        return CustomerMemoryEvidenceType.MODEL_INFERENCE

    @staticmethod
    def _initial_memory_status(
        category: CustomerMemoryCategory,
        evidence: CustomerMemoryEvidenceType,
        confidence: float,
    ) -> CustomerMemoryStatus:
        """仅让高置信的稳定明示事实自动晋级，推断永远等待复核。"""
        stable_category = category in {
            CustomerMemoryCategory.PREFERENCE,
            CustomerMemoryCategory.CONFIRMED_FACT,
        }
        if (
            stable_category
            and evidence is not CustomerMemoryEvidenceType.MODEL_INFERENCE
            and confidence >= 0.8
        ):
            return CustomerMemoryStatus.ACTIVE
        return CustomerMemoryStatus.CANDIDATE

    @staticmethod
    def _memory_deadlines(
        category: CustomerMemoryCategory, now: datetime
    ) -> tuple[datetime, datetime]:
        """为不同类别设置保守复核期和最长有效期。"""
        review_days = {
            CustomerMemoryCategory.PREFERENCE: 365,
            CustomerMemoryCategory.CONFIRMED_FACT: 180,
            CustomerMemoryCategory.UNRESOLVED: 30,
            CustomerMemoryCategory.SERVICE_HISTORY: 180,
        }[category]
        expiry_days = {
            CustomerMemoryCategory.PREFERENCE: 730,
            CustomerMemoryCategory.CONFIRMED_FACT: 365,
            CustomerMemoryCategory.UNRESOLVED: 60,
            CustomerMemoryCategory.SERVICE_HISTORY: 365,
        }[category]
        return now + timedelta(days=review_days), now + timedelta(days=expiry_days)

    @staticmethod
    def _merge_unresolved(existing: list[str], incoming: list[str]) -> list[str]:
        """稳定去重两个摘要层级的待确认项，避免后写层覆盖前写层。"""
        cleaned = [item.strip()[:500] for item in [*existing, *incoming] if item.strip()]
        return list(dict.fromkeys(cleaned))[:20]

    @staticmethod
    def _normalize_statement(statement: str) -> str:
        """忽略空白和常见标点比较候选陈述。"""
        return re.sub(r"[\s，。！？、,.!?]+", "", statement).lower()

    @classmethod
    def _relevance_score(cls, query: str, memory: CustomerMemoryItem) -> int:
        """使用可解释的字符片段匹配中文和英文主题，不引入向量旁路。"""
        compact_query = cls._normalize_statement(query)
        if not compact_query:
            return 1
        compact_memory = cls._normalize_statement(
            f"{memory.subject_key}{memory.statement}"
        )
        tokens = {
            compact_query[index : index + 2]
            for index in range(max(len(compact_query) - 1, 0))
        }
        if len(compact_query) == 1:
            tokens.add(compact_query)
        return sum(1 for token in tokens if token and token in compact_memory)

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
