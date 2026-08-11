"""验证管理员 AI 调试服务的只读、安全和并发边界。"""

import asyncio
from contextlib import asynccontextmanager
from datetime import date
from types import SimpleNamespace

import pytest

from homestay_bot.domain.enums import Language
from homestay_bot.integrations.deepseek_client import AssistantDecision
from homestay_bot.services.admin_debug_service import (
    AdminDebugRateLimiter,
    AdminDebugService,
    DebugPreviewCommand,
    DebugPreviewInputError,
    DebugPreviewRateLimitError,
)


class AssistantStub:
    """返回固定决定并记录每次调用上下文与 trace sink。"""

    def __init__(self, *, wait_for_two: bool = False) -> None:
        """初始化调用记录和并发门闩。"""
        self.calls: list[dict[str, object]] = []
        self.entered = 0
        self.all_entered = asyncio.Event()
        self.wait_for_two = wait_for_two

    async def respond(self, **kwargs: object) -> AssistantDecision:
        """同时进入两个调用后，分别写入当前请求的 trace。"""
        self.calls.append(kwargs)
        self.entered += 1
        if self.wait_for_two:
            if self.entered == 2:
                self.all_entered.set()
            await self.all_entered.wait()
        sink = kwargs["tool_trace_sink"]
        context = kwargs["request_context"]
        sink(
            SimpleNamespace(
                name="search_availability",
                succeeded=True,
                duration_ms=5,
                check_in_date=context.check_in_date,
                check_out_date=context.check_out_date,
            )
        )
        return AssistantDecision(
            reply_text=f"回复-{context.property_title}",
            language=Language.ZH,
            intent="availability_query",
            confidence=0.91,
            knowledge_gap=True,
            knowledge_gap_topic="parking",
            staff_confirmation_required=True,
            staff_confirmation_reason="availability_result_confirmation",
        )


class RegistryStub:
    """提供固定 revision 的单请求租约。"""

    def __init__(self, assistant: AssistantStub) -> None:
        """保存调试 assistant。"""
        self.bundle = SimpleNamespace(revision=7, assistant=assistant)
        self.acquire_count = 0

    @asynccontextmanager
    async def acquire(self):
        """记录每次 preview 只获取一个租约。"""
        self.acquire_count += 1
        yield self.bundle


class PropertyStub:
    """只返回后台允许选择的房源安全投影。"""

    async def get_debug_property(self, property_id: int):
        """仅编号 11 有效。"""
        if property_id != 11:
            return None
        return SimpleNamespace(id=11, title="江汉路一号房")


class AuditStub:
    """收集不含原文的安全审计。"""

    def __init__(self) -> None:
        """初始化审计记录。"""
        self.items: list[dict[str, object]] = []

    async def record_debug_preview(self, **details: object) -> None:
        """保存服务提交的最小元数据。"""
        self.items.append(details)


class FailingAuditStub(AuditStub):
    """模拟安全审计存储故障。"""

    async def record_debug_preview(self, **details: object) -> None:
        """抛出携敏感正文异常，主结果不得被覆盖。"""
        raise RuntimeError("audit-raw-secret")


class CancelledAssistant:
    """模拟请求在模型调用期间被取消。"""

    async def respond(self, **kwargs: object) -> AssistantDecision:
        """抛出取消信号，finally 仍应尝试最小审计。"""
        raise asyncio.CancelledError


def command(*, room: int = 11, start: date = date(2026, 8, 12)) -> DebugPreviewCommand:
    """构造合法武汉日期范围的调试命令。"""
    return DebugPreviewCommand(
        actor_employee_id=1,
        admin_id=1,
        question="明天有房吗？",
        language=Language.ZH,
        property_id=room,
        check_in_date=start,
        check_out_date=date.fromordinal(start.toordinal() + 1),
    )


@pytest.mark.asyncio
async def test_preview_returns_safe_fields_and_uses_one_revision_lease() -> None:
    """调试结果应完整，且审计只保存哈希、长度和工具名。"""
    assistant = AssistantStub()
    registry = RegistryStub(assistant)
    audit = AuditStub()
    service = AdminDebugService(
        registry=registry,
        properties=PropertyStub(),
        audits=audit,
        limiter=AdminDebugRateLimiter(limit=10),
        local_date_provider=lambda: date(2026, 8, 11),
    )

    result = await service.preview(command())

    assert result.reply_text == "回复-江汉路一号房"
    assert result.intent == "availability_query"
    assert result.confidence == 0.91
    assert result.knowledge_gap_topic == "parking"
    assert result.tool_trace[0].name == "search_availability"
    assert result.selected_property_title == "江汉路一号房"
    assert result.check_in_date == date(2026, 8, 12)
    assert result.staff_confirmation_required is True
    assert result.task_suggestion is None
    assert result.revision == 7
    assert registry.acquire_count == 1
    assert len(audit.items) == 1
    serialized = repr(audit.items[0])
    assert "明天有房吗" not in serialized
    assert "回复-江汉路" not in serialized
    assert "question_hash" in audit.items[0]
    assert audit.items[0]["question_length"] == 6
    assert audit.items[0]["tool_names"] == ["search_availability"]


@pytest.mark.asyncio
async def test_concurrent_previews_keep_trace_and_context_isolated() -> None:
    """共享生产 assistant 的并发调试请求不得串联房间、日期或 trace。"""
    assistant = AssistantStub(wait_for_two=True)
    service = AdminDebugService(
        registry=RegistryStub(assistant),
        properties=PropertyStub(),
        audits=AuditStub(),
        limiter=AdminDebugRateLimiter(limit=10),
        local_date_provider=lambda: date(2026, 8, 11),
    )

    first, second = await asyncio.gather(
        service.preview(command(start=date(2026, 8, 12))),
        service.preview(command(start=date(2026, 8, 20))),
    )

    assert first.tool_trace is not second.tool_trace
    assert first.tool_trace[0].check_in_date == date(2026, 8, 12)
    assert second.tool_trace[0].check_in_date == date(2026, 8, 20)
    for call in assistant.calls:
        assert call["messages"] == [{"role": "user", "content": "明天有房吗？"}]


@pytest.mark.asyncio
async def test_preview_rejects_invalid_room_date_range_and_rate_limit() -> None:
    """服务端拒绝未知房间、过期/过长日期以及管理员超限请求。"""
    assistant = AssistantStub()
    service = AdminDebugService(
        registry=RegistryStub(assistant),
        properties=PropertyStub(),
        audits=AuditStub(),
        limiter=AdminDebugRateLimiter(limit=1),
        local_date_provider=lambda: date(2026, 8, 11),
    )

    with pytest.raises(DebugPreviewInputError):
        await service.preview(command(room=999))
    with pytest.raises(DebugPreviewInputError):
        await service.preview(command(start=date(2026, 8, 10)))
    with pytest.raises(DebugPreviewInputError):
        await service.preview(
            DebugPreviewCommand(
                actor_employee_id=1,
                admin_id=1,
                question="有房吗",
                language=Language.ZH,
                property_id=11,
                check_in_date=date(2026, 8, 12),
                check_out_date=date(2026, 9, 12),
            )
        )

    await service.preview(command())
    with pytest.raises(DebugPreviewRateLimitError):
        await service.preview(command())


@pytest.mark.asyncio
async def test_audit_failure_does_not_override_success_and_cancel_is_audited() -> None:
    """审计故障不覆盖结果；模型取消仍写失败元数据且不保存原文。"""
    assistant = AssistantStub()
    service = AdminDebugService(
        registry=RegistryStub(assistant),
        properties=PropertyStub(),
        audits=FailingAuditStub(),
        limiter=AdminDebugRateLimiter(limit=10),
        local_date_provider=lambda: date(2026, 8, 11),
    )
    result = await service.preview(command())
    assert result.intent == "availability_query"

    audit = AuditStub()
    cancelled_registry = RegistryStub(assistant)
    cancelled_registry.bundle.assistant = CancelledAssistant()
    cancelled = AdminDebugService(
        registry=cancelled_registry,
        properties=PropertyStub(),
        audits=audit,
        limiter=AdminDebugRateLimiter(limit=10),
        local_date_provider=lambda: date(2026, 8, 11),
    )
    with pytest.raises(asyncio.CancelledError):
        await cancelled.preview(command())
    assert audit.items[0]["succeeded"] is False
    assert "明天有房吗" not in repr(audit.items)
