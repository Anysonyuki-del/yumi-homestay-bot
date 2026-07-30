from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from homestay_bot.domain.enums import (
    KnowledgeCandidateDraftStatus,
    Language,
)
from homestay_bot.integrations.deepseek_faq_drafter import (
    DeepSeekFaqDrafter,
    FaqDraft,
    FaqDraftUnavailableError,
)
from homestay_bot.services.knowledge_service import (
    KnowledgeService,
    KnowledgeSnippet,
)
from homestay_bot.worker import DeferredRetryJobError, RetrySafeJobError


class DraftCandidate(Protocol):
    """定义草稿任务读取的候选字段。"""

    id: int
    canonical_question: str
    category: str
    total_occurrences: int
    examples: list[str]
    examples_version: int
    draft_status: KnowledgeCandidateDraftStatus
    draft_examples_version: int
    draft_payload: dict[str, Any] | None
    draft_generation: int
    draft_attempts: int
    notification_pending: bool


class DraftCandidateRepository(Protocol):
    """定义草稿任务所需的候选仓储操作。"""

    async def get(self, candidate_id: int) -> DraftCandidate | None:
        """按主键读取候选。"""

    async def count_since(
        self,
        candidate_id: int,
        *,
        since: datetime,
        until: datetime,
    ) -> int:
        """统计候选在指定 UTC 窗口内的真实出现次数。"""

    async def increment_draft_attempts(
        self, candidate_id: int
    ) -> DraftCandidate:
        """增加一次草稿失败次数。"""

    async def mark_draft_ready(
        self, candidate_id: int, payload: dict[str, Any]
    ) -> DraftCandidate:
        """保存安全草稿。"""

    async def mark_draft_failed(self, candidate_id: int) -> DraftCandidate:
        """标记草稿最终失败。"""

    async def mark_notified(
        self, candidate_id: int, *, reminded_at: datetime
    ) -> DraftCandidate:
        """记录管理员通知已经进入发件箱。"""


class AdministratorRepository(Protocol):
    """定义管理员通知收件人查询接口。"""

    async def list_active_admin_userids(self) -> list[str]:
        """返回全部启用管理员的企业微信 userid。"""


class InternalNotificationPort(Protocol):
    """定义事务型企业微信内部通知接口。"""

    async def send_internal_text(
        self,
        *,
        agent_id: int,
        employee_userids: list[str],
        content: str,
    ) -> None:
        """登记一条只发给管理员的内部通知。"""


class FaqDraftJobService:
    """生成 FAQ 草稿并把审核提醒交给启用管理员。"""

    def __init__(
        self,
        *,
        candidates: DraftCandidateRepository,
        drafter: DeepSeekFaqDrafter,
        knowledge: KnowledgeService,
        administrators: AdministratorRepository,
        notifications: InternalNotificationPort,
        agent_id: int,
        knowledge_admin_url: str,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        """保存后台任务所需依赖。"""
        self._candidates = candidates
        self._drafter = drafter
        self._knowledge = knowledge
        self._administrators = administrators
        self._notifications = notifications
        self._agent_id = agent_id
        self._knowledge_admin_url = knowledge_admin_url.rstrip("/")
        self._now_provider = now_provider or (lambda: datetime.now(UTC))

    async def handle(self, payload: dict[str, Any]) -> None:
        """处理一项草稿生成任务。"""
        candidate_id = int(payload["candidate_id"])
        generation = int(payload["generation"])
        candidate = await self._candidates.get(candidate_id)
        if candidate is None or candidate.draft_generation != generation:
            # 候选已删除或进入更新代次时，旧任务直接幂等结束。
            return

        needs_draft = (
            candidate.draft_status
            not in {
                KnowledgeCandidateDraftStatus.READY,
                KnowledgeCandidateDraftStatus.FAILED,
            }
            or (
                candidate.draft_status
                is KnowledgeCandidateDraftStatus.READY
                and (
                    candidate.draft_payload is None
                    or candidate.examples_version
                    > candidate.draft_examples_version
                )
            )
        )
        if needs_draft:
            candidate = await self._generate_draft(candidate)
        now = self._now_provider()
        recent_count = await self._candidates.count_since(
            candidate.id,
            since=now - timedelta(hours=72),
            until=now,
        )
        await self._notify_administrators(
            candidate,
            recent_count=recent_count,
            reminded_at=now,
        )

    async def _generate_draft(
        self,
        candidate: DraftCandidate,
    ) -> DraftCandidate:
        """生成并保存草稿；第三次失败改为人工兜底提醒。"""
        approved_knowledge = await self._build_approved_knowledge()
        try:
            draft = await self._drafter.generate(
                canonical_question=candidate.canonical_question,
                category=candidate.category,
                examples=candidate.examples[:3],
                approved_knowledge=approved_knowledge,
            )
        except FaqDraftUnavailableError as error:
            failed = await self._candidates.increment_draft_attempts(
                candidate.id
            )
            if failed.draft_attempts < 3:
                raise RetrySafeJobError("FAQ 草稿暂不可用") from error
            return await self._candidates.mark_draft_failed(candidate.id)
        return await self._candidates.mark_draft_ready(
            candidate.id,
            draft.model_dump(),
        )

    async def _build_approved_knowledge(self) -> list[dict[str, str]]:
        """把语言分离的审核知识合并为双语草稿上下文。"""
        zh_items = await self._knowledge.build_context(Language.ZH)
        en_items = await self._knowledge.build_context(Language.EN)
        zh_by_id = {item.source_id: item for item in zh_items}
        en_by_id = {item.source_id: item for item in en_items}
        approved: list[dict[str, str]] = []
        for source_id in list(dict.fromkeys([*zh_by_id, *en_by_id]))[:100]:
            zh = zh_by_id.get(source_id)
            en = en_by_id.get(source_id)
            approved.append(self._knowledge_payload(zh, en))
        return approved

    @staticmethod
    def _knowledge_payload(
        zh: KnowledgeSnippet | None,
        en: KnowledgeSnippet | None,
    ) -> dict[str, str]:
        """把同一审核知识转换为 DeepSeek 草稿所需字段。"""
        if zh is None and en is None:
            raise ValueError("审核知识双语上下文不可同时为空")
        if zh is not None:
            category = zh.category
        else:
            assert en is not None
            category = en.category
        return {
            "category": category,
            "question_zh": zh.question if zh is not None else "",
            "answer_zh": zh.answer if zh is not None else "",
            "question_en": en.question if en is not None else "",
            "answer_en": en.answer if en is not None else "",
        }

    async def _notify_administrators(
        self,
        candidate: DraftCandidate,
        *,
        recent_count: int,
        reminded_at: datetime,
    ) -> None:
        """只向启用管理员登记通知；无人可收时保留待提醒并安全重试。"""
        administrators = (
            await self._administrators.list_active_admin_userids()
        )
        if not administrators:
            raise DeferredRetryJobError("没有启用管理员")
        await self._notifications.send_internal_text(
            agent_id=self._agent_id,
            employee_userids=administrators,
            content=self._notification_content(
                candidate,
                recent_count=recent_count,
            ),
        )
        await self._candidates.mark_notified(
            candidate.id,
            reminded_at=reminded_at,
        )

    def _notification_content(
        self,
        candidate: DraftCandidate,
        *,
        recent_count: int,
    ) -> str:
        """按段限制长度，确保关键审核信息和管理入口始终保留。"""
        examples = "\n".join(
            f"{index}. {self._limit_segment(text, 180)}"
            for index, text in enumerate(candidate.examples[:3], start=1)
        )
        if candidate.draft_status is KnowledgeCandidateDraftStatus.FAILED:
            summary = "FAQ 草稿生成失败，请管理员人工归纳。"
            verification = "待核实事项：请根据参考问法人工核实"
        else:
            draft = FaqDraft.model_validate(candidate.draft_payload)
            summary = (
                "参考答案："
                f"{self._limit_segment(draft.answer_zh, 360)}\n"
                "建议关键词："
                + (
                    "、".join(
                        self._limit_segment(keyword, 30)
                        for keyword in draft.keywords
                    )
                    or "无"
                )
            )
            verification = "待核实事项：" + (
                "、".join(
                    self._limit_segment(item, 48)
                    for item in draft.verification_items
                )
                or "无"
            )
        return (
            "高频待归纳问题\n"
            f"最近72小时出现：{recent_count} 次\n"
            "标准问题："
            f"{self._limit_segment(candidate.canonical_question, 160)}\n"
            f"{summary}\n{verification}\n"
            f"参考问法：\n{examples or '无'}\n"
            f"管理页面：{self._knowledge_admin_url}"
        )

    @staticmethod
    def _limit_segment(text: str, maximum: int) -> str:
        """单独限制非关键正文，避免整体截断丢失后续审核字段。"""
        clean = text.strip()
        if len(clean) <= maximum:
            return clean
        return clean[: maximum - 1] + "…"
