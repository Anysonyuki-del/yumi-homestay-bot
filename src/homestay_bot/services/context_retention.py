from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Protocol

from homestay_bot.domain.models import CustomerContextSummary, Message


@dataclass(frozen=True)
class ContextSummaryResult:
    """表示模型生成并通过本地安全检查的摘要。"""

    summary: str
    unresolved_items: list[str]


@dataclass(frozen=True)
class CustomerModelContext:
    """表示可安全传给客服模型的客户摘要上下文。"""

    short_summary: str
    long_summary: str
    unresolved_items: list[str]
    active_orders: list[dict[str, str | int]] = field(default_factory=list)
    open_tasks: list[dict[str, str | int | None]] = field(default_factory=list)


class ContextRepository(Protocol):
    """定义摘要维护和模型上下文读取所需的持久化操作。"""

    async def get_summary(self, customer_id: int) -> CustomerContextSummary | None:
        """读取客户当前分层摘要。"""

    async def list_short_candidates(
        self, customer_id: int, now: datetime, raw_limit: int
    ) -> list[Message]:
        """返回七天内但不属于最近原文窗口的未摘要消息。"""

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
    ) -> None:
        """保存短摘要并标记已覆盖消息。"""

    async def save_long_summary_and_purge(
        self,
        customer_id: int,
        result: ContextSummaryResult,
        messages: list[Message],
        now: datetime,
    ) -> None:
        """原子保存长期摘要并清除已覆盖原文。"""


class ContextSummarizer(Protocol):
    """定义脱敏分层摘要模型的最小接口。"""

    async def summarize(
        self,
        *,
        tier: str,
        existing_summary: str,
        messages: list[str],
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
    ) -> None:
        """注入仓储、摘要器和模型最近原文数量。"""
        self._repository = repository
        self._summarizer = summarizer
        self._raw_limit = raw_limit

    async def maintain_customer(self, customer_id: int, now: datetime) -> None:
        """依次更新短摘要和长期摘要，任一步失败都保留相应原文。"""
        summary = await self._repository.get_summary(customer_id)
        short_candidates = await self._repository.list_short_candidates(
            customer_id,
            now,
            self._raw_limit,
        )
        if short_candidates:
            result = await self._summarizer.summarize(
                tier="short",
                existing_summary=summary.short_summary if summary else "",
                messages=self._contents(short_candidates),
            )
            await self._repository.save_short_summary(
                customer_id,
                result,
                short_candidates,
                now,
            )

        expired = await self._repository.list_expired_unpurged(
            customer_id,
            now - timedelta(days=7),
        )
        if not expired:
            return
        result = await self._summarizer.summarize(
            tier="long",
            existing_summary=summary.long_summary if summary else "",
            messages=self._contents(expired),
        )
        # 仓储必须把摘要更新和清除正文放在同一事务内。
        await self._repository.save_long_summary_and_purge(
            customer_id,
            result,
            expired,
            now,
        )

    @staticmethod
    def _contents(messages: list[Message]) -> list[str]:
        """过滤已经清理或没有正文的记录。"""
        return [item.content for item in messages if item.content]
