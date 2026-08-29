from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Protocol

from homestay_bot.domain.enums import (
    CustomerMemoryCategory,
    CustomerMemoryEvidenceType,
)
from homestay_bot.domain.models import CustomerContextSummary, Message


@dataclass(frozen=True)
class MemorySource:
    """表示可供摘要器引用的一条带身份消息。"""

    message_id: str
    origin: str
    content: str
    summary_eligible: bool = True


@dataclass(frozen=True)
class CustomerMemoryCandidate:
    """表示模型提取、仍需本地证据治理的客户记忆候选。"""

    subject_key: str
    category: CustomerMemoryCategory
    statement: str
    evidence_type: CustomerMemoryEvidenceType
    source_message_id: str | None
    confidence: float
    is_correction: bool = False
    source_excerpt: str | None = None


@dataclass(frozen=True)
class ContextSummaryResult:
    """表示模型生成并通过本地安全检查的摘要。"""

    summary: str
    unresolved_items: list[str]
    memory_candidates: list[CustomerMemoryCandidate] = field(default_factory=list)
    processed_source_ids: frozenset[str] | None = None


@dataclass(frozen=True)
class CustomerModelContext:
    """表示可安全传给客服模型的限量客户上下文。"""

    recent_episode: str = ""
    historical_episode: str = ""
    memories: list[dict[str, str | float]] = field(default_factory=list)
    active_orders: list[dict[str, str | int]] = field(default_factory=list)
    open_tasks: list[dict[str, str | int | None]] = field(default_factory=list)


class ContextRepository(Protocol):
    """定义摘要维护和模型上下文读取所需的持久化操作。"""

    async def get_summary(self, customer_id: int) -> CustomerContextSummary | None:
        """读取客户当前分层摘要。"""

    async def expire_customer_memories(self, customer_id: int, now: datetime) -> None:
        """维护记忆到期、终态正文和事件保留期。"""

    async def reconcile_legacy_memories(
        self, customer_id: int, now: datetime
    ) -> None:
        """使用本地证据规则重新核验历史候选。"""

    async def list_short_candidates(
        self, customer_id: int, now: datetime, raw_limit: int
    ) -> list[Message]:
        """返回七天内但不属于最近原文窗口的未摘要消息。"""

    async def list_recent_unobserved(
        self, customer_id: int, now: datetime
    ) -> list[Message]:
        """返回七天内尚未进行结构化记忆观察的文本消息。"""

    async def list_expired_unpurged(
        self, customer_id: int, before: datetime
    ) -> list[Message]:
        """返回七天外仍有正文的消息。"""

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
        """版本一致时保存短摘要并标记已覆盖消息。"""

    async def save_memory_observations(
        self,
        customer_id: int,
        result: ContextSummaryResult,
        messages: list[Message],
        now: datetime,
    ) -> None:
        """保存最近原文提取的记忆，并标记消息已经观察。"""

    async def save_long_summary_and_purge(
        self,
        customer_id: int,
        result: ContextSummaryResult,
        messages: list[Message],
        now: datetime,
        *,
        expected_version: int | None = None,
    ) -> bool:
        """版本一致时原子保存长期摘要并清除原文。"""


class ContextSummarizer(Protocol):
    """定义脱敏分层摘要模型的最小接口。"""

    async def summarize(
        self,
        *,
        tier: str,
        existing_summary: str,
        messages: list[MemorySource],
    ) -> ContextSummaryResult:
        """合并既有摘要与新消息并返回安全结果。"""


class ContextRetentionService:
    """维护七天原文窗口，并确保摘要成功后才清除过期正文。"""

    def __init__(
        self,
        repository: ContextRepository,
        summarizer: ContextSummarizer,
        *,
        raw_limit: int = 3,
        before_external: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        """注入仓储、摘要器、事务边界和模型最近原文数量。"""
        self._repository = repository
        self._summarizer = summarizer
        self._raw_limit = raw_limit
        self._before_external = before_external

    async def maintain_customer(self, customer_id: int, now: datetime) -> None:
        """依次更新短摘要和长期摘要，任一步失败都保留相应原文。"""
        await self._repository.reconcile_legacy_memories(customer_id, now)
        await self._repository.expire_customer_memories(customer_id, now)
        summary = await self._repository.get_summary(customer_id)
        summary_version = summary.version if summary else 0
        short_candidates = await self._repository.list_short_candidates(
            customer_id,
            now,
            self._raw_limit,
        )
        recent_unobserved = await self._repository.list_recent_unobserved(
            customer_id, now
        )
        if short_candidates or recent_unobserved:
            if self._before_external is not None:
                # 读取消息快照后提交，避免模型调用长时间占用数据库连接事务。
                await self._before_external()
            result = await self._summarizer.summarize(
                tier="short" if short_candidates else "memory",
                existing_summary=summary.short_summary if summary else "",
                messages=self._sources(
                    self._dedupe_messages(
                        [*short_candidates, *recent_unobserved]
                    ),
                    summary_messages=short_candidates,
                ),
            )
            processed_short = self._processed_messages(short_candidates, result)
            processed_recent = self._processed_messages(recent_unobserved, result)
            processed_sources = self._dedupe_messages(
                [*processed_short, *processed_recent]
            )
            if processed_short:
                saved = await self._repository.save_short_summary(
                    customer_id,
                    result,
                    processed_short,
                    now,
                    source_messages=processed_sources,
                    observed_messages=processed_recent,
                    expected_version=summary_version,
                )
                if not saved:
                    # 模型调用期间摘要已被人工或其他任务更改；
                    # 保留原文给下一维护周期，本轮不追加模型请求。
                    return
            elif processed_recent:
                await self._repository.save_memory_observations(
                    customer_id,
                    result,
                    processed_recent,
                    now,
                )

        expired = await self._repository.list_expired_unpurged(
            customer_id,
            now - timedelta(days=7),
        )
        if not expired:
            return
        if self._before_external is not None:
            # 短摘要可能刚写入数据库，先提交再开始下一次模型调用。
            await self._before_external()
        # 短摘要可能已在本轮更新，重新读取可避免长期摘要基于过期版本合并。
        summary = await self._repository.get_summary(customer_id)
        summary_version = summary.version if summary else 0
        result = await self._summarizer.summarize(
            tier="long",
            existing_summary=summary.long_summary if summary else "",
            messages=self._sources(expired),
        )
        processed_expired = self._processed_messages(expired, result)
        if not processed_expired:
            return
        # 仓储必须把摘要更新和清除正文放在同一事务内。
        await self._repository.save_long_summary_and_purge(
            customer_id,
            result,
            processed_expired,
            now,
            expected_version=summary_version,
        )

    @staticmethod
    def _sources(
        messages: list[Message], *, summary_messages: list[Message] | None = None
    ) -> list[MemorySource]:
        """过滤空正文并保留可由仓储核验的消息身份与来源。"""
        eligible_ids = (
            {item.id for item in summary_messages}
            if summary_messages is not None
            else None
        )
        return [
            MemorySource(
                message_id=item.external_message_id,
                origin=item.origin.value,
                content=item.content,
                summary_eligible=(eligible_ids is None or item.id in eligible_ids),
            )
            for item in messages
            if item.content
        ]

    @staticmethod
    def _dedupe_messages(messages: list[Message]) -> list[Message]:
        """按数据库消息主键稳定去重摘要与观察集合。"""
        return list({item.external_message_id: item for item in messages}.values())

    @staticmethod
    def _processed_messages(
        messages: list[Message], result: ContextSummaryResult
    ) -> list[Message]:
        """只保存和清除摘要器实际接收的消息；测试桩未声明时兼容全部。"""
        if result.processed_source_ids is None:
            return messages
        return [
            item
            for item in messages
            if item.external_message_id in result.processed_source_ids
        ]
