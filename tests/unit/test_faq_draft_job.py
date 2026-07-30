from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from homestay_bot.domain.enums import (
    KnowledgeCandidateDraftStatus,
    Language,
)
from homestay_bot.integrations.deepseek_faq_drafter import (
    FaqDraft,
    FaqDraftUnavailableError,
)
from homestay_bot.services.faq_draft_job import FaqDraftJobService
from homestay_bot.services.knowledge_service import KnowledgeSnippet
from homestay_bot.worker import DeferredRetryJobError, RetrySafeJobError


def reviewable_draft() -> FaqDraft:
    """构造管理员可编辑的安全草稿。"""
    return FaqDraft(
        category="停车",
        question_zh="民宿是否提供停车位？",
        answer_zh="停车位数量为【待管理员确认】。",
        question_en="Does the homestay provide parking?",
        answer_en="Parking capacity requires admin confirmation.",
        keywords=["停车", "停车位"],
        verification_items=["停车位数量"],
    )


def candidate(**updates):
    """构造草稿任务使用的最小候选记录。"""
    values = {
        "id": 7,
        "canonical_question": "民宿是否提供停车位？",
        "category": "停车",
        "total_occurrences": 3,
        "examples": ["有停车位吗？", "开车过去方便停车吗？"],
        "examples_version": 2,
        "draft_status": KnowledgeCandidateDraftStatus.PENDING,
        "draft_examples_version": 0,
        "draft_payload": None,
        "draft_generation": 1,
        "draft_attempts": 0,
        "notification_pending": True,
    }
    values.update(updates)
    return SimpleNamespace(**values)


class CandidateRepositoryStub:
    """在内存中维护候选草稿、失败次数和提醒状态。"""

    def __init__(self, record) -> None:
        """保存单个候选。"""
        self.record = record
        self.notified_at: datetime | None = None

    async def get(self, candidate_id: int):
        """按固定主键返回候选。"""
        return self.record if candidate_id == self.record.id else None

    async def count_since(
        self,
        candidate_id: int,
        *,
        since: datetime,
        until: datetime,
    ) -> int:
        """返回测试候选在最近窗口内的次数。"""
        return 3

    async def increment_draft_attempts(self, candidate_id: int):
        """增加一次草稿失败次数。"""
        self.record.draft_attempts += 1
        return self.record

    async def mark_draft_ready(self, candidate_id: int, payload: dict):
        """保存成功草稿并重置失败次数。"""
        self.record.draft_status = KnowledgeCandidateDraftStatus.READY
        self.record.draft_payload = payload
        self.record.draft_examples_version = self.record.examples_version
        self.record.draft_attempts = 0
        return self.record

    async def mark_draft_failed(self, candidate_id: int):
        """标记最终失败。"""
        self.record.draft_status = KnowledgeCandidateDraftStatus.FAILED
        self.record.draft_payload = None
        return self.record

    async def mark_notified(self, candidate_id: int, *, reminded_at: datetime):
        """记录管理员提醒已进入事务型发件箱。"""
        self.record.notification_pending = False
        self.record.last_reminded_total = self.record.total_occurrences
        self.record.last_reminded_at = reminded_at
        self.notified_at = reminded_at
        return self.record


class KnowledgeStub:
    """返回同一审核知识的中英文上下文。"""

    def __init__(self) -> None:
        """初始化语言调用记录。"""
        self.languages: list[Language] = []

    async def build_context(self, language: Language) -> list[KnowledgeSnippet]:
        """按语言返回审核知识。"""
        self.languages.append(language)
        return [
            KnowledgeSnippet(
                source_id=1,
                category="交通",
                question=("How to arrive?" if language is Language.EN else "怎么到民宿？"),
                answer=(
                    "Please follow navigation."
                    if language is Language.EN
                    else "请按导航前往。"
                ),
            )
        ]


class DrafterStub:
    """返回固定草稿或模拟 DeepSeek 失败。"""

    def __init__(self, *, fails: bool = False) -> None:
        """保存预期行为。"""
        self.fails = fails
        self.calls: list[dict] = []

    async def generate(self, **kwargs) -> FaqDraft:
        """记录输入并返回草稿。"""
        self.calls.append(kwargs)
        if self.fails:
            raise FaqDraftUnavailableError()
        return reviewable_draft()


class AdminRepositoryStub:
    """返回启用管理员的企业微信 userid。"""

    def __init__(self, userids: list[str]) -> None:
        """保存管理员列表。"""
        self.userids = userids

    async def list_active_admin_userids(self) -> list[str]:
        """返回启用管理员。"""
        return self.userids


class NotificationStub:
    """记录事务内管理员通知。"""

    def __init__(self) -> None:
        """初始化通知记录。"""
        self.messages: list[dict] = []

    async def send_internal_text(self, **kwargs) -> None:
        """保存通知参数。"""
        self.messages.append(kwargs)


def build_service(
    record,
    *,
    drafter: DrafterStub | None = None,
    admin_userids: list[str] | None = None,
):
    """组装可观察的草稿任务服务及其依赖。"""
    candidates = CandidateRepositoryStub(record)
    selected_drafter = drafter or DrafterStub()
    knowledge = KnowledgeStub()
    notifications = NotificationStub()
    now = datetime(2026, 7, 30, 8, tzinfo=UTC)
    service = FaqDraftJobService(
        candidates=candidates,
        drafter=selected_drafter,
        knowledge=knowledge,
        administrators=AdminRepositoryStub(
            ["admin-1"] if admin_userids is None else admin_userids
        ),
        notifications=notifications,
        agent_id=1000002,
        knowledge_admin_url="https://example.test/employee/knowledge",
        now_provider=lambda: now,
    )
    return service, candidates, selected_drafter, knowledge, notifications, now


@pytest.mark.asyncio
async def test_successful_draft_is_saved_and_only_notifies_admins_without_identity() -> None:
    """成功草稿应保存并提醒管理员，通知不得包含客人身份。"""
    record = candidate()
    service, candidates, drafter, knowledge, notifications, now = build_service(record)

    await service.handle({"candidate_id": 7, "generation": 1, "refresh_draft": True})

    assert record.draft_status is KnowledgeCandidateDraftStatus.READY
    assert record.draft_payload == reviewable_draft().model_dump()
    assert candidates.notified_at == now
    assert knowledge.languages == [Language.ZH, Language.EN]
    assert len(drafter.calls) == 1
    assert notifications.messages[0]["employee_userids"] == ["admin-1"]
    content = notifications.messages[0]["content"]
    assert "民宿是否提供停车位" in content
    assert "【待管理员确认】" in content
    assert "最近72小时出现：3 次" in content
    assert "wm-" not in content
    assert "external_userid" not in content


@pytest.mark.asyncio
async def test_first_two_draft_failures_increment_attempts_and_retry() -> None:
    """前两次草稿失败应持久化次数并交给 worker 安全重试。"""
    record = candidate()
    service, _, _, _, notifications, _ = build_service(
        record,
        drafter=DrafterStub(fails=True),
    )

    for expected_attempt in (1, 2):
        with pytest.raises(RetrySafeJobError):
            await service.handle(
                {"candidate_id": 7, "generation": 1, "refresh_draft": True}
            )
        assert record.draft_attempts == expected_attempt
        assert record.draft_status is KnowledgeCandidateDraftStatus.PENDING

    assert notifications.messages == []


@pytest.mark.asyncio
async def test_third_draft_failure_marks_failed_and_still_notifies_admin() -> None:
    """第三次失败不得继续重试，应标记失败并附脱敏示例提醒管理员。"""
    record = candidate(draft_attempts=2)
    service, candidates, _, _, notifications, now = build_service(
        record,
        drafter=DrafterStub(fails=True),
    )

    await service.handle({"candidate_id": 7, "generation": 1, "refresh_draft": True})

    assert record.draft_attempts == 3
    assert record.draft_status is KnowledgeCandidateDraftStatus.FAILED
    assert candidates.notified_at == now
    assert "草稿生成失败" in notifications.messages[0]["content"]
    assert "有停车位吗" in notifications.messages[0]["content"]


@pytest.mark.asyncio
async def test_no_admin_keeps_notification_pending_and_retries_safely() -> None:
    """没有启用管理员时应保留待提醒状态并让任务安全重试。"""
    record = candidate()
    service, _, _, _, notifications, _ = build_service(
        record,
        admin_userids=[],
    )

    with pytest.raises(DeferredRetryJobError):
        await service.handle(
            {"candidate_id": 7, "generation": 1, "refresh_draft": True}
        )

    assert record.draft_status is KnowledgeCandidateDraftStatus.READY
    assert record.notification_pending is True
    assert notifications.messages == []


@pytest.mark.asyncio
async def test_ready_draft_with_unchanged_examples_is_reused() -> None:
    """示例版本未变化时应复用已有草稿，不重复调用 DeepSeek。"""
    payload = reviewable_draft().model_dump()
    record = candidate(
        draft_status=KnowledgeCandidateDraftStatus.READY,
        draft_payload=payload,
        draft_examples_version=2,
    )
    service, _, drafter, _, notifications, _ = build_service(record)

    await service.handle({"candidate_id": 7, "generation": 1, "refresh_draft": True})

    assert drafter.calls == []
    assert len(notifications.messages) == 1


@pytest.mark.asyncio
async def test_failed_draft_notification_retry_never_calls_deepseek_again() -> None:
    """最终失败候选后续补发通知时只能复用失败状态，不得再次调用模型。"""
    record = candidate(
        draft_status=KnowledgeCandidateDraftStatus.FAILED,
        draft_attempts=3,
        draft_payload=None,
    )
    service, _, drafter, knowledge, notifications, _ = build_service(record)

    await service.handle({"candidate_id": 7, "generation": 1, "refresh_draft": True})

    assert drafter.calls == []
    assert knowledge.languages == []
    assert len(notifications.messages) == 1
    assert "草稿生成失败" in notifications.messages[0]["content"]


@pytest.mark.asyncio
async def test_long_notification_keeps_entry_all_checks_and_three_bounded_examples() -> None:
    """长草稿通知必须保留管理入口、全部待核实项和三条有界示例。"""
    verification_items = [f"核实项{index}-" + "甲" * 80 for index in range(1, 9)]
    long_draft = reviewable_draft().model_copy(
        update={
            "answer_zh": "【待管理员确认】" + "答" * 1400,
            "verification_items": verification_items,
        }
    )
    examples = [f"示例{index}-" + "问" * 140 for index in range(1, 4)]
    record = candidate(
        examples=examples,
        examples_version=3,
        draft_status=KnowledgeCandidateDraftStatus.READY,
        draft_examples_version=3,
        draft_payload=long_draft.model_dump(),
    )
    service, _, _, _, notifications, _ = build_service(record)

    await service.handle({"candidate_id": 7, "generation": 1, "refresh_draft": False})

    content = notifications.messages[0]["content"]
    assert "管理页面：https://example.test/employee/knowledge" in content
    assert all(f"核实项{index}-" in content for index in range(1, 9))
    assert all(example in content for example in examples)
    assert len(content) <= 2200
