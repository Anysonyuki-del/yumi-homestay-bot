import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
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
    CustomerMemoryEvent,
    CustomerMemoryItem,
    Message,
    PropertyProfile,
    StayOrder,
)
from homestay_bot.services.context_retention import (
    ContextSummaryResult,
    CustomerModelContext,
)
from homestay_bot.services.customer_memory_policy import (
    can_auto_activate_subject,
    candidate_value_is_grounded,
    contains_sensitive_memory_text,
    is_dynamic_memory_text,
    is_explicit_correction,
    is_historical_query,
    is_instruction_like_memory,
    memory_relevance_score,
    normalize_source_text,
    normalize_subject_key,
    redact_memory_text,
    source_excerpt_hash,
    stronger_evidence,
    verify_source_excerpt,
)

logger = logging.getLogger(__name__)


class SQLAlchemyContextRepository:
    """按客户隔离读取摘要候选并原子保存分层摘要。"""

    # 摘要任务按批处理，避免单个高频客户一次性载入全部历史正文。
    SUMMARY_BATCH_LIMIT = 50
    MEMORY_RECALL_LIMIT = 6
    MEMORY_RECALL_CHARS = 900
    RECENT_EPISODE_CHARS = 1_000
    HISTORICAL_EPISODE_CHARS = 800
    DYNAMIC_CONTEXT_CHARS = 3_000
    CANDIDATE_RETENTION_DAYS = 30
    DISPUTED_RETENTION_DAYS = 90
    TERMINAL_CONTENT_DAYS = 90
    EVENT_RETENTION_DAYS = 365

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
        """统一失效超期记忆、清理终态正文并限制事件保留期。"""
        memories = list(
            (
                await self._session.scalars(
                    select(CustomerMemoryItem).where(
                        CustomerMemoryItem.customer_id == customer_id,
                        (
                            (
                                CustomerMemoryItem.status
                                == CustomerMemoryStatus.ACTIVE
                            )
                            & (
                                (CustomerMemoryItem.review_at <= now)
                                | (CustomerMemoryItem.expires_at <= now)
                            )
                        )
                        | (
                            (
                                CustomerMemoryItem.status
                                == CustomerMemoryStatus.CANDIDATE
                            )
                            & (
                                CustomerMemoryItem.created_at
                                <= now - timedelta(days=self.CANDIDATE_RETENTION_DAYS)
                            )
                        )
                        | (
                            (
                                CustomerMemoryItem.status
                                == CustomerMemoryStatus.DISPUTED
                            )
                            & (
                                CustomerMemoryItem.created_at
                                <= now - timedelta(days=self.DISPUTED_RETENTION_DAYS)
                            )
                        ),
                    )
                )
            ).all()
        )
        for memory in memories:
            previous_status = memory.status
            memory.status = CustomerMemoryStatus.STALE
            memory.status_reason = "到达复核期或治理保留期"
            memory.version += 1
            self._add_memory_event(
                memory,
                "expired",
                previous_status=previous_status,
                reason=memory.status_reason,
                occurred_at=now,
            )

        terminal_statuses = (
            CustomerMemoryStatus.STALE,
            CustomerMemoryStatus.REJECTED,
            CustomerMemoryStatus.SUPERSEDED,
        )
        terminal_memories = list(
            (
                await self._session.scalars(
                    select(CustomerMemoryItem).where(
                        CustomerMemoryItem.customer_id == customer_id,
                        CustomerMemoryItem.status.in_(terminal_statuses),
                        CustomerMemoryItem.content_redacted_at.is_(None),
                        CustomerMemoryItem.updated_at
                        <= now - timedelta(days=self.TERMINAL_CONTENT_DAYS),
                    )
                )
            ).all()
        )
        for memory in terminal_memories:
            memory.statement = "[历史内容已清理]"
            memory.source_excerpt = None
            memory.content_redacted_at = now
            memory.version += 1

        old_events = list(
            (
                await self._session.scalars(
                    select(CustomerMemoryEvent).where(
                        CustomerMemoryEvent.customer_id == customer_id,
                        CustomerMemoryEvent.occurred_at
                        <= now - timedelta(days=self.TERMINAL_CONTENT_DAYS),
                        CustomerMemoryEvent.content_redacted_at.is_(None),
                    )
                )
            ).all()
        )
        for event in old_events:
            event.statement_snapshot = None
            event.content_redacted_at = now

        await self._session.execute(
            delete(CustomerMemoryEvent).where(
                CustomerMemoryEvent.customer_id == customer_id,
                CustomerMemoryEvent.occurred_at
                <= now - timedelta(days=self.EVENT_RETENTION_DAYS),
            ).execution_options(synchronize_session=False)
        )
        await self._session.flush()

    async def reconcile_legacy_memories(
        self, customer_id: int, now: datetime
    ) -> None:
        """只用尚存的来源原文重验证历史候选，不调用模型。"""
        rows = list(
            (
                await self._session.scalars(
                    select(CustomerMemoryItem)
                    .where(
                        CustomerMemoryItem.customer_id == customer_id,
                        CustomerMemoryItem.status == CustomerMemoryStatus.CANDIDATE,
                        CustomerMemoryItem.status_reason == "历史证据不可验证",
                        CustomerMemoryItem.source_message_id.is_not(None),
                    )
                    .order_by(CustomerMemoryItem.id)
                    .with_for_update()
                )
            ).all()
        )
        if not rows:
            return
        source_ids = [item.source_message_id for item in rows if item.source_message_id]
        sources = {
            item.external_message_id: item
            for item in (
                await self._session.scalars(
                    select(Message).where(Message.external_message_id.in_(source_ids))
                )
            ).all()
        }
        for memory in rows:
            source = sources.get(memory.source_message_id or "")
            if source is None or not source.content:
                continue
            excerpt = redact_memory_text(source.content)[:300]
            if not self._candidate_is_grounded(
                subject_key=memory.subject_key,
                statement=memory.statement,
                source_excerpt=excerpt,
                source=source,
            ):
                continue
            memory.source_excerpt = excerpt
            memory.source_excerpt_hash = source_excerpt_hash(excerpt)
            memory.source_occurred_at = source.sent_at
            memory.verified_at = now
            evidence = self._grounded_evidence(
                memory.evidence_type,
                source,
                grounded=True,
            )
            memory.evidence_type = evidence
            proposed_status = self._initial_memory_status(
                memory.subject_key,
                memory.category,
                evidence,
                memory.confidence,
                grounded=True,
            )
            active_conflict = await self._session.scalar(
                select(CustomerMemoryItem.id).where(
                    CustomerMemoryItem.customer_id == customer_id,
                    CustomerMemoryItem.subject_key == memory.subject_key,
                    CustomerMemoryItem.status == CustomerMemoryStatus.ACTIVE,
                    CustomerMemoryItem.id != memory.id,
                )
            )
            if (
                proposed_status is CustomerMemoryStatus.ACTIVE
                and active_conflict is None
            ):
                memory.status = CustomerMemoryStatus.ACTIVE
                memory.confirmed_at = now
                memory.status_reason = None
            else:
                memory.status_reason = "历史证据已核验，等待人工复核"
            memory.version += 1
            self._add_memory_event(
                memory,
                "legacy_reverified",
                previous_status=CustomerMemoryStatus.CANDIDATE,
                reason=memory.status_reason,
                occurred_at=now,
            )
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
        expected_version: int | None = None,
    ) -> bool:
        """版本一致时保存短摘要；冲突时不标记任何消息。"""
        summary = await self._get_or_create_summary(
            customer_id,
            expected_version=expected_version,
        )
        if summary is None:
            return False
        summary.short_summary = result.summary
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
        return True

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
        *,
        expected_version: int | None = None,
    ) -> bool:
        """版本一致时原子写长摘要并清除已覆盖正文。"""
        summary = await self._get_or_create_summary(
            customer_id,
            expected_version=expected_version,
        )
        if summary is None:
            return False
        summary.long_summary = result.summary
        summary.long_cutoff_at = max(item.sent_at for item in messages)
        summary.version += 1
        for item in messages:
            item.content = None
            item.purged_at = now
            item.memory_processed_at = now
        await self._save_memory_candidates(customer_id, result, messages, now)
        await self._session.flush()
        return True

    async def load_model_context(
        self, customer_id: int, *, query: str = ""
    ) -> CustomerModelContext:
        """按实时运营优先级和硬预算构建客户上下文。"""
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
                                BusinessTaskStatus.EXPIRED,
                            ]
                        ),
                    )
                    .order_by(BusinessTask.created_at, BusinessTask.id)
                    .limit(10)
                )
            ).all()
        )
        active_orders = [
                {
                    "property_id": order.property_id,
                    "property_title": property_title,
                    "check_in_date": order.check_in_date.isoformat(),
                    "check_out_date": order.check_out_date.isoformat(),
                    "status": order.status,
                }
                for order, property_title in order_rows
            ]
        open_tasks = [
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
            ]
        operational_chars = len(str(active_orders)) + len(str(open_tasks))
        memory_chars = sum(
            len(str(item.get("subject_key", "")))
            + len(str(item.get("statement", "")))
            for item in memories
        )
        episode_budget = max(
            0,
            self.DYNAMIC_CONTEXT_CHARS - operational_chars - memory_chars,
        )
        recent_episode = self._relevant_episode(
            query,
            summary.short_summary if summary else "",
            min(self.RECENT_EPISODE_CHARS, episode_budget),
        )
        episode_budget -= len(recent_episode)
        historical_episode = ""
        if is_historical_query(query):
            historical_episode = self._relevant_episode(
                query,
                summary.long_summary if summary else "",
                min(self.HISTORICAL_EPISODE_CHARS, max(0, episode_budget)),
                allow_history=True,
            )
        logger.info(
            "customer_context_recalled",
            extra={
                "customer_id": customer_id,
                "memory_count": len(memories),
                "memory_ids": [item["memory_id"] for item in memories],
                "summary_version": summary.version if summary else 0,
                "used_chars": (
                    operational_chars
                    + memory_chars
                    + len(recent_episode)
                    + len(historical_episode)
                ),
            },
        )
        safe_memories = [
            {key: value for key, value in item.items() if key != "memory_id"}
            for item in memories
        ]
        return CustomerModelContext(
            recent_episode=recent_episode,
            historical_episode=historical_episode,
            memories=safe_memories,
            active_orders=active_orders,
            open_tasks=open_tasks,
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
        """在同一事务核验引用、保持证据单调并记录状态时间线。"""
        sources = {item.external_message_id: item for item in messages}
        for candidate in result.memory_candidates:
            source = sources.get(candidate.source_message_id or "")
            subject_key = normalize_subject_key(candidate.subject_key)
            grounded = self._candidate_is_grounded(
                subject_key=subject_key,
                statement=candidate.statement,
                source_excerpt=candidate.source_excerpt,
                source=source,
            )
            evidence = self._grounded_evidence(
                candidate.evidence_type,
                source,
                grounded=grounded,
            )
            status = self._initial_memory_status(
                subject_key,
                candidate.category,
                evidence,
                candidate.confidence,
                grounded=grounded,
            )
            existing = list(
                (
                    await self._session.scalars(
                        select(CustomerMemoryItem)
                        .where(
                            CustomerMemoryItem.customer_id == customer_id,
                            CustomerMemoryItem.subject_key == subject_key,
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
            active_conflicts = [
                item
                for item in existing
                if item.status is CustomerMemoryStatus.ACTIVE and item is not duplicate
            ]
            correction_is_proven = bool(
                candidate.is_correction
                and grounded
                and source is not None
                and source.content
                and is_explicit_correction(source.content)
            )
            if duplicate is not None:
                duplicate.confidence = max(duplicate.confidence, candidate.confidence)
                selected_evidence = stronger_evidence(duplicate.evidence_type, evidence)
                if selected_evidence is evidence and evidence is not duplicate.evidence_type:
                    duplicate.evidence_type = evidence
                    duplicate.source_message_id = (
                        source.external_message_id if source else None
                    )
                    duplicate.source_excerpt = (
                        redact_memory_text(candidate.source_excerpt or "") or None
                    )
                    duplicate.source_excerpt_hash = (
                        source_excerpt_hash(candidate.source_excerpt or "")
                        if candidate.source_excerpt
                        else None
                    )
                    duplicate.source_occurred_at = source.sent_at if source else None
                    duplicate.verified_at = now if grounded else duplicate.verified_at
                if evidence is not CustomerMemoryEvidenceType.MODEL_INFERENCE:
                    duplicate.review_at = self._later_datetime(
                        duplicate.review_at,
                        review_at,
                    )
                    duplicate.expires_at = self._later_datetime(
                        duplicate.expires_at,
                        expires_at,
                    )
                if status is CustomerMemoryStatus.ACTIVE:
                    previous_status = duplicate.status
                    if active_conflicts and not correction_is_proven:
                        duplicate.status = CustomerMemoryStatus.DISPUTED
                        duplicate.status_reason = "同一主题存在冲突陈述"
                        for item in active_conflicts:
                            active_previous_status = item.status
                            item.status = CustomerMemoryStatus.DISPUTED
                            item.status_reason = "同一主题存在冲突陈述"
                            item.version += 1
                            self._add_memory_event(
                                item,
                                "disputed",
                                previous_status=active_previous_status,
                                reason=item.status_reason,
                                occurred_at=now,
                            )
                    else:
                        for item in active_conflicts:
                            active_previous_status = item.status
                            item.status = CustomerMemoryStatus.SUPERSEDED
                            item.status_reason = "客户或员工明确纠正"
                            item.version += 1
                            self._add_memory_event(
                                item,
                                "superseded",
                                previous_status=active_previous_status,
                                reason=item.status_reason,
                                occurred_at=now,
                            )
                        duplicate.status = CustomerMemoryStatus.ACTIVE
                        duplicate.confirmed_at = now
                        duplicate.status_reason = None
                        duplicate.verified_at = duplicate.verified_at or now
                    if previous_status is not duplicate.status:
                        self._add_memory_event(
                            duplicate,
                            (
                                "activated"
                                if duplicate.status is CustomerMemoryStatus.ACTIVE
                                else "disputed"
                            ),
                            previous_status=previous_status,
                            reason=duplicate.status_reason,
                            occurred_at=now,
                        )
                duplicate.version += 1
                continue

            supersedes_id = None
            if status is CustomerMemoryStatus.ACTIVE and active_conflicts:
                if correction_is_proven:
                    for item in active_conflicts:
                        previous_status = item.status
                        item.status = CustomerMemoryStatus.SUPERSEDED
                        item.status_reason = "客户或员工明确纠正"
                        item.version += 1
                        self._add_memory_event(
                            item,
                            "superseded",
                            previous_status=previous_status,
                            reason=item.status_reason,
                            occurred_at=now,
                        )
                    supersedes_id = active_conflicts[-1].id
                else:
                    status = CustomerMemoryStatus.DISPUTED
                    for item in active_conflicts:
                        previous_status = item.status
                        item.status = CustomerMemoryStatus.DISPUTED
                        item.status_reason = "同一主题存在冲突陈述"
                        item.version += 1
                        self._add_memory_event(
                            item,
                            "disputed",
                            previous_status=previous_status,
                            reason=item.status_reason,
                            occurred_at=now,
                        )

            memory = CustomerMemoryItem(
                customer_id=customer_id,
                subject_key=subject_key,
                category=candidate.category,
                statement=candidate.statement.strip(),
                status=status,
                evidence_type=evidence,
                source_message_id=source.external_message_id if source else None,
                source_excerpt=(
                    redact_memory_text(candidate.source_excerpt or "") or None
                ),
                source_excerpt_hash=(
                    source_excerpt_hash(candidate.source_excerpt or "")
                    if candidate.source_excerpt
                    else None
                ),
                source_occurred_at=source.sent_at if source else None,
                verified_at=(
                    now
                    if grounded
                    and evidence is not CustomerMemoryEvidenceType.MODEL_INFERENCE
                    else None
                ),
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
            self._session.add(memory)
            # 纠正关系依赖旧行主键；刷新可让后续同批候选看见本条记录。
            await self._session.flush()
            self._add_memory_event(
                memory,
                "created",
                previous_status=None,
                reason=memory.status_reason,
                occurred_at=now,
            )

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
                        CustomerMemoryItem.verified_at.is_not(None),
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
                (
                    memory_relevance_score(
                        query,
                        subject_key=item.subject_key,
                        statement=item.statement,
                    ),
                    item,
                )
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
            if (
                contains_sensitive_memory_text(item.statement)
                or is_dynamic_memory_text(f"{item.subject_key} {item.statement}")
                or is_instruction_like_memory(item.statement)
            ):
                continue
            item_chars = len(item.subject_key) + len(item.statement)
            if used_chars + item_chars > self.MEMORY_RECALL_CHARS:
                continue
            recalled.append(
                {
                    "memory_id": item.id,
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

    @classmethod
    def _candidate_is_grounded(
        cls,
        *,
        subject_key: str,
        statement: str,
        source_excerpt: str | None,
        source: Message | None,
    ) -> bool:
        """校验引用、候选值和安全边界，不信任模型自证。"""
        if source is None or not source.content:
            return False
        candidate_text = f"{subject_key} {statement} {source_excerpt or ''}"
        return bool(
            verify_source_excerpt(source_excerpt, source.content)
            and candidate_value_is_grounded(
                statement,
                source_excerpt or "",
                subject_key=subject_key,
            )
            and not contains_sensitive_memory_text(statement)
            and not is_dynamic_memory_text(candidate_text)
            and not is_instruction_like_memory(candidate_text)
        )

    @staticmethod
    def _later_datetime(left: datetime, right: datetime) -> datetime:
        """兼容 SQLite 返回的无时区时间，选择实际更晚的截止时间。"""
        comparable_left = left if left.tzinfo else left.replace(tzinfo=UTC)
        comparable_right = right if right.tzinfo else right.replace(tzinfo=UTC)
        return left if comparable_left >= comparable_right else right

    @staticmethod
    def _grounded_evidence(
        requested: CustomerMemoryEvidenceType,
        source: Message | None,
        *,
        grounded: bool,
    ) -> CustomerMemoryEvidenceType:
        """只在身份、引用和候选值同时成立时承认证据等级。"""
        if not grounded:
            return CustomerMemoryEvidenceType.MODEL_INFERENCE
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
        subject_key: str,
        category: CustomerMemoryCategory,
        evidence: CustomerMemoryEvidenceType,
        confidence: float,
        *,
        grounded: bool,
    ) -> CustomerMemoryStatus:
        """仅让高置信的稳定明示事实自动晋级，推断永远等待复核。"""
        stable_category = category in {
            CustomerMemoryCategory.PREFERENCE,
            CustomerMemoryCategory.CONFIRMED_FACT,
        }
        if (
            stable_category
            and grounded
            and can_auto_activate_subject(subject_key)
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
    def _normalize_statement(statement: str) -> str:
        """忽略空白和常见标点比较候选陈述。"""
        return normalize_source_text(statement).replace(" ", "")

    @staticmethod
    def _relevant_episode(
        query: str,
        episode: str,
        budget: int,
        *,
        allow_history: bool = False,
    ) -> str:
        """只在当前问题相关或明确查历史时返回限量情节摘要。"""
        if budget <= 0 or not episode.strip():
            return ""
        score = memory_relevance_score(
            query,
            subject_key="episodic_summary",
            statement=episode,
        )
        if query.strip() and score <= 0 and not allow_history:
            return ""
        return redact_memory_text(episode)[:budget]

    def _add_memory_event(
        self,
        memory: CustomerMemoryItem,
        event_type: str,
        *,
        previous_status: CustomerMemoryStatus | None,
        reason: str | None = None,
        occurred_at: datetime,
        actor_employee_id: int | None = None,
    ) -> None:
        """写入不包含原始消息正文的记忆状态事件。"""
        self._session.add(
            CustomerMemoryEvent(
                customer_id=memory.customer_id,
                memory_item_id=memory.id,
                subject_key=memory.subject_key,
                event_type=event_type,
                previous_status=(previous_status.name if previous_status else None),
                new_status=memory.status.name,
                statement_snapshot=redact_memory_text(memory.statement),
                source_message_id=memory.source_message_id,
                actor_employee_id=actor_employee_id,
                reason=reason,
                occurred_at=occurred_at,
            )
        )

    async def _get_or_create_summary(
        self,
        customer_id: int,
        *,
        expected_version: int | None = None,
    ) -> CustomerContextSummary | None:
        """锁定客户摘要，期望版本不匹配时返回空值。"""
        summary = await self._session.scalar(
            select(CustomerContextSummary)
            .where(CustomerContextSummary.customer_id == customer_id)
            .with_for_update()
        )
        if summary is not None:
            if expected_version is not None and summary.version != expected_version:
                return None
            return summary
        if expected_version not in {None, 0}:
            return None
        summary = CustomerContextSummary(customer_id=customer_id)
        self._session.add(summary)
        await self._session.flush()
        return summary
