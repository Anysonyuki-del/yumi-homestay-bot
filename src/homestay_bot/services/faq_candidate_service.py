import re
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from homestay_bot.domain.enums import KnowledgeCandidateDraftStatus
from homestay_bot.integrations.deepseek_client import AssistantDecision

_WINDOW = timedelta(hours=72)
_REMINDER_COOLDOWN = timedelta(hours=24)
_EXCLUDED_PATTERN = re.compile(
    r"有房|房态|可订|房价|价格|多少钱|订单|退款|取消|改期|付款|"
    r"预订|订房|入住|退房|旅游|景点|活动|演出|展览|"
    r"availability|price|rate|order|refund|cancel|booking|"
    r"check[- ]?in|check[- ]?out|attraction|event|exhibition",
    re.IGNORECASE,
)
_EXCLUDED_INTENTS = {
    "availability",
    "price",
    "booking",
    "booking_confirmed",
    "refund",
    "cancellation",
    "payment",
    "order",
    "tourism",
    "emergency",
}
_EMAIL_PATTERN = re.compile(
    r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.-])"
)
_MOBILE_PATTERN = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_IDENTITY_PATTERN = re.compile(
    r"(?<!\d)\d{17}[\dXx](?!\d)|(?<!\d)\d{15}(?!\d)"
)
_ORDER_PATTERN = re.compile(
    r"(?P<label>订单号?|预订号|order(?:\s+number)?)"
    r"\s*[:：#]?\s*[A-Za-z0-9-]{4,}",
    re.IGNORECASE,
)


def _as_utc(value: datetime) -> datetime:
    """统一 SQLite 无时区时间与业务 UTC 时间，避免冷却期比较失败。"""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class CandidateRecord(Protocol):
    """定义高频判定所需的候选字段。"""

    id: int
    total_occurrences: int
    last_threshold_total: int
    last_reminded_total: int
    last_reminded_at: datetime | None
    notification_pending: bool
    examples_version: int
    draft_examples_version: int
    draft_generation: int
    draft_status: KnowledgeCandidateDraftStatus
    draft_payload: dict[str, Any] | None


class FaqCandidateRepositoryPort(Protocol):
    """定义高频服务依赖的候选仓储操作。"""

    async def get(self, candidate_id: int) -> CandidateRecord | None:
        """按主键读取候选。"""

    async def get_or_create(
        self, *, canonical_question: str, category: str
    ) -> CandidateRecord:
        """按标准问题读取或创建候选。"""

    async def add_occurrence(
        self,
        candidate_id: int,
        *,
        source_message_id: str,
        occurred_at: datetime,
        example: str | None,
    ) -> bool:
        """幂等记录一次出现。"""

    async def count_since(
        self,
        candidate_id: int,
        *,
        since: datetime,
        until: datetime,
    ) -> int:
        """统计滚动窗口内出现次数。"""

    async def prune_occurrences(self, *, before: datetime) -> int:
        """删除窗口之前的出现明细。"""

    async def mark_draft_pending(
        self, candidate_id: int
    ) -> CandidateRecord:
        """开启新草稿代次。"""


class JobRepositoryPort(Protocol):
    """定义候选服务登记后台任务的接口。"""

    async def enqueue(
        self,
        job_type: str,
        payload: dict[str, Any],
        *,
        available_at: datetime | None = None,
        dedupe_key: str | None = None,
    ) -> Any:
        """持久化带去重键的任务。"""


class FrequentFaqService:
    """记录知识缺口并在三天三次时安排 FAQ 草稿任务。"""

    def __init__(
        self,
        *,
        candidates: FaqCandidateRepositoryPort,
        jobs: JobRepositoryPort,
        now_provider: Callable[[], datetime] | None = None,
        savepoint_factory: (
            Callable[[], AbstractAsyncContextManager[Any]] | None
        ) = None,
    ) -> None:
        """注入候选、任务、UTC 时钟和可选数据库保存点。"""
        self._candidates = candidates
        self._jobs = jobs
        self._now_provider = now_provider or (lambda: datetime.now(UTC))
        self._savepoint_factory = savepoint_factory

    @staticmethod
    def redact_example(text: str) -> str:
        """遮盖常见身份和订单标识，再限制示例最大长度。"""
        redacted = _EMAIL_PATTERN.sub("[邮箱已隐藏]", text)
        redacted = _MOBILE_PATTERN.sub("[手机号已隐藏]", redacted)
        redacted = _IDENTITY_PATTERN.sub("[身份证号已隐藏]", redacted)
        redacted = _ORDER_PATTERN.sub(
            lambda match: f"{match.group('label')} [编号已隐藏]",
            redacted,
        )
        return redacted.strip()[:500]

    @staticmethod
    def _is_eligible(
        question: str,
        decision: AssistantDecision,
    ) -> bool:
        """用确定性规则排除动态、高风险和已覆盖问题。"""
        return (
            decision.faq_candidate
            and decision.knowledge_gap
            and not decision.staff_confirmation_required
            and decision.intent not in _EXCLUDED_INTENTS
            and _EXCLUDED_PATTERN.search(question) is None
            and bool((decision.faq_canonical_question or "").strip())
            and bool((decision.faq_category or "").strip())
        )

    async def _resolve_candidate(
        self,
        decision: AssistantDecision,
    ) -> CandidateRecord:
        """优先复用模型确认的候选编号，否则按标准问题创建。"""
        if decision.faq_candidate_id is not None:
            existing = await self._candidates.get(decision.faq_candidate_id)
            if existing is not None:
                return existing
        return await self._candidates.get_or_create(
            canonical_question=decision.faq_canonical_question or "",
            category=decision.faq_category or "",
        )

    async def _enqueue_trigger(
        self,
        candidate: CandidateRecord,
        *,
        now: datetime,
    ) -> None:
        """占用本批三次门槛，并安排立即或冷却后的草稿任务。"""
        candidate.last_threshold_total = candidate.total_occurrences
        refresh_draft = (
            candidate.draft_status is not KnowledgeCandidateDraftStatus.READY
            or candidate.examples_version > candidate.draft_examples_version
        )
        if refresh_draft:
            candidate = await self._candidates.mark_draft_pending(candidate.id)
        else:
            candidate.draft_generation += 1
            candidate.notification_pending = True

        available_at = now
        if candidate.last_reminded_at is not None:
            available_at = max(
                _as_utc(available_at),
                _as_utc(candidate.last_reminded_at) + _REMINDER_COOLDOWN,
            )
        generation = candidate.draft_generation
        await self._jobs.enqueue(
            "faq_draft_generate",
            {
                "candidate_id": candidate.id,
                "generation": generation,
                "refresh_draft": refresh_draft,
            },
            available_at=available_at,
            dedupe_key=f"faq-draft:{candidate.id}:{generation}",
        )

    async def track(
        self,
        *,
        source_message_id: str,
        question: str,
        occurred_at: datetime,
        decision: AssistantDecision,
    ) -> None:
        """记录一次候选出现，并在满足窗口和批次条件时安排任务。"""
        if not self._is_eligible(question, decision):
            return
        if self._savepoint_factory is not None:
            # 保存点回滚只撤销 FAQ 统计，不影响已经登记的客人回复。
            async with self._savepoint_factory():
                await self._track_eligible(
                    source_message_id=source_message_id,
                    question=question,
                    occurred_at=occurred_at,
                    decision=decision,
                )
            return
        await self._track_eligible(
            source_message_id=source_message_id,
            question=question,
            occurred_at=occurred_at,
            decision=decision,
        )

    async def _track_eligible(
        self,
        *,
        source_message_id: str,
        question: str,
        occurred_at: datetime,
        decision: AssistantDecision,
    ) -> None:
        """在已通过固定规则后执行候选写入和阈值计算。"""
        now = self._now_provider()
        candidate = await self._resolve_candidate(decision)
        added = await self._candidates.add_occurrence(
            candidate.id,
            source_message_id=source_message_id,
            occurred_at=occurred_at,
            example=self.redact_example(question),
        )
        if not added:
            return
        window_start = now - _WINDOW
        await self._candidates.prune_occurrences(before=window_start)
        recent_count = await self._candidates.count_since(
            candidate.id,
            since=window_start,
            until=now,
        )
        if (
            recent_count < 3
            or candidate.notification_pending
            or candidate.total_occurrences - candidate.last_threshold_total < 3
        ):
            return
        await self._enqueue_trigger(candidate, now=now)
