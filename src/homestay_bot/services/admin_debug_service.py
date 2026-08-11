"""提供不会触发生产写操作的管理员 AI 调试预览。"""

import asyncio
import hashlib
import logging
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol

from homestay_bot.domain.enums import Language
from homestay_bot.integrations.deepseek_client import (
    AssistantDecision,
    AssistantRequestContext,
    AssistantToolTrace,
    TaskSuggestion,
)

logger = logging.getLogger(__name__)


class DebugPreviewInputError(ValueError):
    """表示调试输入不满足安全边界。"""


class DebugPreviewRateLimitError(RuntimeError):
    """表示管理员调试调用超过固定窗口限额。"""


@dataclass(frozen=True, slots=True)
class DebugProperty:
    """后台调试允许使用的房源安全投影。"""

    id: int
    title: str


@dataclass(frozen=True, slots=True)
class DebugPreviewCommand:
    """保存一次模拟客人问题及后台独立选择。"""

    actor_employee_id: int
    admin_id: int
    question: str
    language: Language = Language.ZH
    property_id: int | None = None
    check_in_date: date | None = None
    check_out_date: date | None = None


@dataclass(frozen=True, slots=True)
class DebugPreviewResult:
    """返回管理员可见的安全调试结果。"""

    reply_text: str
    intent: str
    confidence: float
    knowledge_gap: bool
    knowledge_gap_topic: str | None
    tool_trace: tuple[AssistantToolTrace, ...]
    selected_property_id: int | None
    selected_property_title: str | None
    check_in_date: date | None
    check_out_date: date | None
    staff_confirmation_required: bool
    staff_confirmation_reason: str | None
    task_suggestion: TaskSuggestion | None
    revision: int


class DebugAssistantPort(Protocol):
    """限定调试服务可调用的助手方法。"""

    async def respond(self, **kwargs: object) -> AssistantDecision:
        """生成只读预览决定。"""


class RuntimeBundlePort(Protocol):
    """限定单次租约读取的字段。"""

    revision: int
    assistant: DebugAssistantPort


class DebugPropertyRepositoryPort(Protocol):
    """定义房源安全投影查询。"""

    async def get_debug_property(self, property_id: int) -> DebugProperty | None:
        """按编号返回启用房源。"""

    async def list_debug_properties(self) -> tuple[DebugProperty, ...]:
        """稳定返回启用房源安全投影。"""


class DebugAuditRepositoryPort(Protocol):
    """定义调试元数据审计写入。"""

    async def record_debug_preview(self, **details: object) -> None:
        """写入不含问题和回复原文的审计。"""


class AdminDebugRateLimiter:
    """按管理员限制短时间模型费用，状态不包含问题内容。"""

    def __init__(
        self,
        *,
        limit: int = 10,
        window: timedelta = timedelta(minutes=10),
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """保存限额、窗口与测试可注入时钟。"""
        self._limit = max(1, limit)
        self._window = window
        self._clock = clock or (lambda: datetime.now(UTC))
        self._attempts: defaultdict[int, deque[datetime]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def consume(self, admin_id: int) -> bool:
        """原子消费一次额度，超限时不新增记录。"""
        now = self._clock()
        cutoff = now - self._window
        async with self._lock:
            attempts = self._attempts[admin_id]
            while attempts and attempts[0] <= cutoff:
                attempts.popleft()
            if len(attempts) >= self._limit:
                return False
            attempts.append(now)
            return True


class AdminDebugService:
    """在一个 registry 租约内生成调试预览并写最小安全审计。"""

    MAX_QUESTION_LENGTH = 1000
    MAX_ADVANCE_DAYS = 365
    MAX_STAY_DAYS = 30

    def __init__(
        self,
        *,
        registry: Any,
        properties: DebugPropertyRepositoryPort,
        audits: DebugAuditRepositoryPort,
        limiter: AdminDebugRateLimiter,
        local_date_provider: Callable[[], date],
    ) -> None:
        """注入单请求 registry、房源投影、审计与管理员限频。"""
        self._registry = registry
        self._properties = properties
        self._audits = audits
        self._limiter = limiter
        self._local_date_provider = local_date_provider

    async def preview(self, command: DebugPreviewCommand) -> DebugPreviewResult:
        """校验输入后生成只读预览，绝不创建会话、消息或业务任务。"""
        question = command.question.strip()
        if not question or len(question) > self.MAX_QUESTION_LENGTH:
            raise DebugPreviewInputError("问题长度无效")
        selected = await self._validate_context(command)
        if not await self._limiter.consume(command.admin_id):
            raise DebugPreviewRateLimitError("调试请求过于频繁")
        traces: list[AssistantToolTrace] = []
        succeeded = False
        intent = "unavailable"
        try:
            async with self._registry.acquire() as bundle:
                decision = await bundle.assistant.respond(
                    guest_identifier="admin-debug",
                    language=command.language,
                    messages=[{"role": "user", "content": question}],
                    request_context=AssistantRequestContext(
                        property_id=selected.id if selected else None,
                        property_title=selected.title if selected else None,
                        check_in_date=command.check_in_date,
                        check_out_date=command.check_out_date,
                    ),
                    tool_trace_sink=traces.append,
                )
                intent = str(decision.intent)
                succeeded = True
                return DebugPreviewResult(
                    reply_text=decision.reply_text,
                    intent=intent,
                    confidence=float(decision.confidence),
                    knowledge_gap=bool(decision.knowledge_gap),
                    knowledge_gap_topic=decision.knowledge_gap_topic,
                    tool_trace=tuple(traces),
                    selected_property_id=selected.id if selected else None,
                    selected_property_title=selected.title if selected else None,
                    check_in_date=command.check_in_date,
                    check_out_date=command.check_out_date,
                    staff_confirmation_required=bool(
                        decision.staff_confirmation_required
                    ),
                    staff_confirmation_reason=decision.staff_confirmation_reason,
                    task_suggestion=decision.task_suggestion,
                    revision=int(bundle.revision),
                )
        finally:
            try:
                await self._audits.record_debug_preview(
                    actor_employee_id=command.actor_employee_id,
                    question_hash=hashlib.sha256(question.encode("utf-8")).hexdigest(),
                    question_length=len(question),
                    intent=intent,
                    tool_names=list(dict.fromkeys(trace.name for trace in traces)),
                    succeeded=succeeded,
                )
            except Exception as error:
                # 审计存储故障不能覆盖模型结果或原始业务异常，且日志只保留类型。
                logger.warning("管理员调试审计失败：error_type=%s", type(error).__name__)

    async def list_properties(self) -> tuple[DebugProperty, ...]:
        """返回表单可选的启用房源安全投影。"""
        return await self._properties.list_debug_properties()

    async def _validate_context(
        self,
        command: DebugPreviewCommand,
    ) -> DebugProperty | None:
        """校验武汉自然日、合理住宿范围和启用房源投影。"""
        today = self._local_date_provider()
        start, end = command.check_in_date, command.check_out_date
        if (start is None) != (end is None):
            raise DebugPreviewInputError("入住和退房日期必须同时提供")
        if start is not None and end is not None:
            if start < today or start > today + timedelta(days=self.MAX_ADVANCE_DAYS):
                raise DebugPreviewInputError("入住日期超出范围")
            if end <= start or end - start > timedelta(days=self.MAX_STAY_DAYS):
                raise DebugPreviewInputError("退房日期超出范围")
        if command.property_id is None:
            return None
        selected = await self._properties.get_debug_property(command.property_id)
        if selected is None:
            raise DebugPreviewInputError("房源不存在或未启用")
        return selected
