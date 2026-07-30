from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from homestay_bot.domain.enums import (
    KnowledgeCandidateDraftStatus,
    KnowledgeCandidateStatus,
)
from homestay_bot.integrations.deepseek_client import AssistantDecision
from homestay_bot.services.faq_candidate_service import FrequentFaqService


class CandidateRepositoryStub:
    """在内存中实现高频统计所需的候选仓储。"""

    def __init__(self) -> None:
        """初始化候选、出现记录和消息幂等集合。"""
        self.candidate = None
        self.occurrences: list[datetime] = []
        self.message_ids: set[str] = set()

    async def get(self, candidate_id: int):
        """按固定主键返回候选。"""
        if self.candidate is not None and self.candidate.id == candidate_id:
            return self.candidate
        return None

    async def get_or_create(self, *, canonical_question: str, category: str):
        """创建或返回唯一测试候选。"""
        if self.candidate is None:
            self.candidate = SimpleNamespace(
                id=1,
                canonical_question=canonical_question,
                category=category,
                status=KnowledgeCandidateStatus.OPEN,
                total_occurrences=0,
                last_threshold_total=0,
                last_reminded_total=0,
                last_reminded_at=None,
                notification_pending=False,
                examples=[],
                examples_version=0,
                draft_examples_version=0,
                draft_generation=0,
                draft_status=KnowledgeCandidateDraftStatus.NONE,
                draft_payload=None,
            )
        return self.candidate

    async def add_occurrence(
        self,
        candidate_id: int,
        *,
        source_message_id: str,
        occurred_at: datetime,
        example: str | None,
    ) -> bool:
        """按消息编号去重并保存最近三条不同示例。"""
        if source_message_id in self.message_ids:
            return False
        self.message_ids.add(source_message_id)
        self.occurrences.append(occurred_at)
        self.candidate.total_occurrences += 1
        if example and example not in self.candidate.examples:
            self.candidate.examples = [
                *self.candidate.examples[-2:],
                example,
            ]
            self.candidate.examples_version += 1
        return True

    async def count_since(
        self,
        candidate_id: int,
        *,
        since: datetime,
        until: datetime,
    ) -> int:
        """统计测试窗口内出现次数。"""
        return sum(since <= item <= until for item in self.occurrences)

    async def prune_occurrences(self, *, before: datetime) -> int:
        """删除窗口前测试记录。"""
        old = len(self.occurrences)
        self.occurrences = [item for item in self.occurrences if item >= before]
        return old - len(self.occurrences)

    async def mark_draft_pending(self, candidate_id: int):
        """开启新的草稿代次。"""
        self.candidate.draft_generation += 1
        self.candidate.draft_status = KnowledgeCandidateDraftStatus.PENDING
        self.candidate.draft_payload = None
        self.candidate.notification_pending = True
        return self.candidate


class JobRepositoryStub:
    """记录候选服务创建的后台任务。"""

    def __init__(self) -> None:
        """初始化任务列表。"""
        self.jobs: list[dict[str, object]] = []

    async def enqueue(self, job_type: str, payload: dict, **kwargs):
        """保存任务类型、载荷、执行时间和去重键。"""
        self.jobs.append(
            {
                "job_type": job_type,
                "payload": payload,
                **kwargs,
            }
        )


def decision(**updates) -> AssistantDecision:
    """构造可进入 FAQ 统计的模型决定。"""
    values = {
        "reply_text": "当前资料未确认，请管理员补充。",
        "language": "zh",
        "intent": "property_facility",
        "confidence": 0.8,
        "knowledge_gap": True,
        "faq_candidate": True,
        "faq_candidate_id": None,
        "faq_canonical_question": "民宿是否提供停车位？",
        "faq_category": "停车",
    }
    values.update(updates)
    return AssistantDecision.model_validate(values)


@pytest.mark.asyncio
async def test_third_occurrence_in_72_hours_enqueues_one_draft_job() -> None:
    """滚动窗口内前两次不触发，第三次创建唯一草稿任务。"""
    now = datetime(2026, 7, 30, 4, tzinfo=UTC)
    candidates = CandidateRepositoryStub()
    jobs = JobRepositoryStub()
    service = FrequentFaqService(
        candidates=candidates,
        jobs=jobs,
        now_provider=lambda: now,
    )

    for index in range(1, 4):
        await service.track(
            source_message_id=f"msg-{index}",
            question="民宿有停车位吗？",
            occurred_at=now - timedelta(hours=3 - index),
            decision=decision(),
        )

    assert len(jobs.jobs) == 1
    assert jobs.jobs[0]["job_type"] == "faq_draft_generate"
    assert jobs.jobs[0]["payload"] == {
        "candidate_id": 1,
        "generation": 1,
        "refresh_draft": True,
    }
    assert jobs.jobs[0]["dedupe_key"] == "faq-draft:1:1"


@pytest.mark.asyncio
async def test_occurrence_outside_72_hours_does_not_reach_threshold() -> None:
    """超过滚动窗口的旧问题不得参与三次门槛。"""
    now = datetime(2026, 7, 30, 4, tzinfo=UTC)
    candidates = CandidateRepositoryStub()
    jobs = JobRepositoryStub()
    service = FrequentFaqService(
        candidates=candidates,
        jobs=jobs,
        now_provider=lambda: now,
    )

    for index, occurred_at in enumerate(
        (now - timedelta(hours=73), now - timedelta(hours=2), now),
        start=1,
    ):
        await service.track(
            source_message_id=f"msg-{index}",
            question="民宿有停车位吗？",
            occurred_at=occurred_at,
            decision=decision(),
        )

    assert jobs.jobs == []


@pytest.mark.asyncio
async def test_duplicate_message_and_excluded_transaction_are_not_counted() -> None:
    """重复消息及房态价格等动态问题不得制造 FAQ 次数。"""
    now = datetime(2026, 7, 30, 4, tzinfo=UTC)
    candidates = CandidateRepositoryStub()
    service = FrequentFaqService(
        candidates=candidates,
        jobs=JobRepositoryStub(),
        now_provider=lambda: now,
    )

    await service.track(
        source_message_id="same-message",
        question="民宿有停车位吗？",
        occurred_at=now,
        decision=decision(),
    )
    await service.track(
        source_message_id="same-message",
        question="民宿有停车位吗？",
        occurred_at=now,
        decision=decision(),
    )
    await service.track(
        source_message_id="price-message",
        question="今天房价多少钱？",
        occurred_at=now,
        decision=decision(intent="price"),
    )

    assert candidates.candidate.total_occurrences == 1


def test_redact_example_masks_sensitive_identifiers() -> None:
    """示例入库前必须遮盖手机号、订单号、身份证号和邮箱。"""
    content = (
        "手机号13800138000，订单号 AB-123456，"
        "身份证420106199001011234，邮箱guest@example.com，能停车吗？"
    )

    redacted = FrequentFaqService.redact_example(content)

    assert "13800138000" not in redacted
    assert "AB-123456" not in redacted
    assert "420106199001011234" not in redacted
    assert "guest@example.com" not in redacted
    assert "能停车吗" in redacted


@pytest.mark.asyncio
async def test_sixth_occurrence_waits_for_24_hour_cooldown_and_reuses_draft() -> None:
    """无新示例的第二批三次应延迟到冷却结束并复用已有草稿。"""
    now = datetime(2026, 7, 30, 4, tzinfo=UTC)
    candidates = CandidateRepositoryStub()
    jobs = JobRepositoryStub()
    service = FrequentFaqService(
        candidates=candidates,
        jobs=jobs,
        now_provider=lambda: now,
    )
    for index in range(1, 4):
        await service.track(
            source_message_id=f"first-{index}",
            question="民宿有停车位吗？",
            occurred_at=now,
            decision=decision(),
        )
    candidate = candidates.candidate
    candidate.notification_pending = False
    # SQLite 会把带时区字段重读为无时区时间，服务必须仍按 UTC 计算。
    candidate.last_reminded_at = now.replace(tzinfo=None)
    candidate.last_reminded_total = 3
    candidate.draft_status = KnowledgeCandidateDraftStatus.READY
    candidate.draft_examples_version = candidate.examples_version
    jobs.jobs.clear()

    for index in range(1, 4):
        await service.track(
            source_message_id=f"second-{index}",
            question="民宿有停车位吗？",
            occurred_at=now,
            decision=decision(),
        )

    assert len(jobs.jobs) == 1
    assert jobs.jobs[0]["available_at"] == now + timedelta(hours=24)
    assert jobs.jobs[0]["payload"]["refresh_draft"] is False
