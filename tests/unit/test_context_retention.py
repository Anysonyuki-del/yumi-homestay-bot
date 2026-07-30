import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from homestay_bot.domain.enums import MessageOrigin
from homestay_bot.domain.models import Message
from homestay_bot.integrations.deepseek_context_summarizer import (
    ContextSummarySafetyError,
    DeepSeekContextSummarizer,
)
from homestay_bot.services.context_retention import (
    ContextRetentionService,
    ContextSummaryResult,
)

NOW = datetime(2026, 7, 31, 8, tzinfo=UTC)


def message(message_id: int, content: str, age_days: int) -> Message:
    """构造带确定时间的客户文本消息。"""
    return Message(
        id=message_id,
        conversation_id=1,
        external_message_id=f"msg-{message_id}",
        origin=MessageOrigin.GUEST,
        message_type="text",
        content=content,
        sent_at=NOW - timedelta(days=age_days),
    )


class ContextRepositoryStub:
    """模拟分层摘要候选和原子保存行为。"""

    def __init__(self) -> None:
        """初始化一条七天外原文和保存记录。"""
        self.short_candidates: list[Message] = []
        self.expired = [message(1, "七天前的原文", 8)]
        self.short_saved: list[ContextSummaryResult] = []
        self.long_saved: list[ContextSummaryResult] = []

    async def list_short_candidates(
        self, customer_id: int, now: datetime, raw_limit: int
    ) -> list[Message]:
        """返回七天内但不属于最近原文窗口的消息。"""
        return self.short_candidates

    async def get_summary(self, customer_id: int):
        """测试默认没有既有摘要。"""
        return None

    async def list_expired_unpurged(
        self, customer_id: int, before: datetime
    ) -> list[Message]:
        """返回七天外仍保留正文的消息。"""
        return self.expired

    async def save_short_summary(
        self,
        customer_id: int,
        result: ContextSummaryResult,
        messages: list[Message],
        now: datetime,
    ) -> None:
        """记录短摘要并标记原文已覆盖。"""
        self.short_saved.append(result)
        for item in messages:
            item.short_summarized_at = now

    async def save_long_summary_and_purge(
        self,
        customer_id: int,
        result: ContextSummaryResult,
        messages: list[Message],
        now: datetime,
    ) -> None:
        """在同一次模拟事务保存长期摘要并清除原文。"""
        self.long_saved.append(result)
        for item in messages:
            item.content = None
            item.purged_at = now


class SummarizerStub:
    """返回固定摘要或模拟外部模型失败。"""

    def __init__(self, *, fail: bool = False) -> None:
        """配置失败行为并初始化调用记录。"""
        self.fail = fail
        self.calls: list[tuple[str, list[str]]] = []

    async def summarize(
        self, *, tier: str, existing_summary: str, messages: list[str]
    ) -> ContextSummaryResult:
        """记录脱敏后的摘要输入。"""
        self.calls.append((tier, messages))
        if self.fail:
            raise RuntimeError("summary unavailable")
        return ContextSummaryResult(
            summary=f"{tier}摘要",
            unresolved_items=[],
        )


@pytest.mark.asyncio
async def test_messages_are_purged_only_after_long_summary_succeeds() -> None:
    """长期摘要成功落库后才允许清除七天外原文。"""
    repository = ContextRepositoryStub()
    summarizer = SummarizerStub()
    service = ContextRetentionService(repository, summarizer)

    await service.maintain_customer(customer_id=1, now=NOW)

    assert summarizer.calls == [("long", ["七天前的原文"])]
    assert repository.expired[0].content is None
    assert repository.expired[0].purged_at == NOW


@pytest.mark.asyncio
async def test_summary_failure_keeps_original_message() -> None:
    """模型摘要失败时必须保留原文供下一轮安全重试。"""
    repository = ContextRepositoryStub()
    summarizer = SummarizerStub(fail=True)
    service = ContextRetentionService(repository, summarizer)

    with pytest.raises(RuntimeError, match="summary unavailable"):
        await service.maintain_customer(customer_id=1, now=NOW)

    assert repository.expired[0].content == "七天前的原文"
    assert repository.expired[0].purged_at is None


@pytest.mark.asyncio
async def test_recent_raw_messages_are_excluded_from_short_summary() -> None:
    """仓储返回的较早七天内消息进入短摘要，最近原文窗口不被重复概括。"""
    repository = ContextRepositoryStub()
    repository.expired = []
    repository.short_candidates = [
        message(2, "需要安静房间", 2),
        message(3, "喜欢靠近地铁", 1),
    ]
    summarizer = SummarizerStub()
    service = ContextRetentionService(repository, summarizer, raw_limit=3)

    await service.maintain_customer(customer_id=7, now=NOW)

    assert summarizer.calls == [
        ("short", ["需要安静房间", "喜欢靠近地铁"])
    ]
    assert all(item.short_summarized_at == NOW for item in repository.short_candidates)


class SummaryClientStub:
    """记录摘要请求并返回固定 JSON。"""

    def __init__(self, payload: dict[str, object]) -> None:
        """构造 OpenAI 兼容客户端层级。"""
        self.requests: list[dict[str, object]] = []
        self.payload = payload
        self.chat = SimpleNamespace(completions=self)

    async def create(self, **kwargs):
        """记录请求并返回固定摘要。"""
        self.requests.append(kwargs)
        message = SimpleNamespace(content=json.dumps(self.payload, ensure_ascii=False))
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


@pytest.mark.asyncio
async def test_context_summarizer_redacts_sensitive_input() -> None:
    """手机号和门锁密码不得进入 DeepSeek 摘要请求。"""
    client = SummaryClientStub({"summary": "客人偏好安静", "unresolved_items": []})
    summarizer = DeepSeekContextSummarizer(client, "deepseek-v4-flash")

    await summarizer.summarize(
        tier="short",
        existing_summary="",
        messages=["手机号13800138000，门锁密码839201，地址武汉市洪山区珞喻路12号"],
    )

    request = json.dumps(client.requests[0], ensure_ascii=False)
    assert "13800138000" not in request
    assert "839201" not in request
    assert "珞喻路12号" not in request


@pytest.mark.asyncio
async def test_context_summarizer_rejects_sensitive_output() -> None:
    """模型重新生成敏感字段时必须拒绝摘要并保留原文。"""
    client = SummaryClientStub(
        {"summary": "客人电话13800138000", "unresolved_items": []}
    )
    summarizer = DeepSeekContextSummarizer(client, "deepseek-v4-flash")

    with pytest.raises(ContextSummarySafetyError):
        await summarizer.summarize(
            tier="long",
            existing_summary="",
            messages=["普通内容"],
        )
