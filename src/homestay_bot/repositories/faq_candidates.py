import hashlib
import re
import unicodedata
from datetime import datetime
from typing import Any, cast

from sqlalchemy import delete, func, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from homestay_bot.domain.enums import (
    KnowledgeCandidateDraftStatus,
    KnowledgeCandidateStatus,
)
from homestay_bot.domain.models import (
    AuditLog,
    KnowledgeCandidate,
    KnowledgeCandidateOccurrence,
)


def _canonical_key(question: str) -> str:
    """统一空白和问号后计算稳定主题键。"""
    normalized = unicodedata.normalize("NFKC", question).strip().lower()
    normalized = re.sub(r"\s+", "", normalized).replace("?", "？")
    return hashlib.sha256(normalized.encode()).hexdigest()


class SQLAlchemyFaqCandidateRepository:
    """持久化 FAQ 候选、脱敏示例和滚动窗口出现记录。"""

    def __init__(self, session: AsyncSession) -> None:
        """绑定当前业务事务的数据库会话。"""
        self._session = session

    async def get(self, candidate_id: int) -> KnowledgeCandidate | None:
        """按主键返回候选。"""
        return await self._session.get(KnowledgeCandidate, candidate_id)

    async def list_context(
        self,
        *,
        now: datetime,
        limit: int = 50,
    ) -> list[KnowledgeCandidate]:
        """重开关闭期已满的候选，并返回模型可匹配的有限上下文。"""
        await self._reopen_expired(now=now)
        result = await self._session.scalars(
            select(KnowledgeCandidate)
            .where(KnowledgeCandidate.status == KnowledgeCandidateStatus.OPEN)
            .order_by(KnowledgeCandidate.updated_at.desc(), KnowledgeCandidate.id.desc())
            .limit(limit)
        )
        return list(result.all())

    async def get_or_create(
        self,
        *,
        canonical_question: str,
        category: str,
    ) -> KnowledgeCandidate:
        """按规范化主题键复用候选，不用分类差异制造重复主题。"""
        key = _canonical_key(canonical_question)
        existing = await self._session.scalar(
            select(KnowledgeCandidate).where(
                KnowledgeCandidate.canonical_key == key
            )
        )
        if existing is not None:
            return existing
        candidate = KnowledgeCandidate(
            canonical_key=key,
            canonical_question=canonical_question.strip(),
            category=category.strip(),
        )
        self._session.add(candidate)
        await self._session.flush()
        return candidate

    async def add_occurrence(
        self,
        candidate_id: int,
        *,
        source_message_id: str,
        occurred_at: datetime,
        example: str | None,
    ) -> bool:
        """为开放候选增加一次幂等出现，并最多保留三条不同示例。"""
        candidate = await self.get(candidate_id)
        if candidate is None:
            raise LookupError(f"FAQ 候选不存在: {candidate_id}")
        if candidate.status is not KnowledgeCandidateStatus.OPEN:
            return False
        duplicate = await self._session.scalar(
            select(KnowledgeCandidateOccurrence.id).where(
                KnowledgeCandidateOccurrence.source_message_id == source_message_id
            )
        )
        if duplicate is not None:
            return False

        self._session.add(
            KnowledgeCandidateOccurrence(
                candidate_id=candidate_id,
                source_message_id=source_message_id,
                occurred_at=occurred_at,
            )
        )
        candidate.total_occurrences += 1
        if candidate.last_seen_at is None or occurred_at > candidate.last_seen_at:
            candidate.last_seen_at = occurred_at
        clean_example = example.strip() if example else ""
        if clean_example and clean_example not in candidate.examples:
            # 保留最近三条不同问法，让后续提醒能用新表达刷新参考草稿。
            candidate.examples = [*candidate.examples, clean_example][-3:]
            candidate.examples_version += 1
        await self._session.flush()
        return True

    async def count_since(
        self,
        candidate_id: int,
        *,
        since: datetime,
        until: datetime,
    ) -> int:
        """统计闭区间内的候选出现次数。"""
        count = await self._session.scalar(
            select(func.count(KnowledgeCandidateOccurrence.id)).where(
                KnowledgeCandidateOccurrence.candidate_id == candidate_id,
                KnowledgeCandidateOccurrence.occurred_at >= since,
                KnowledgeCandidateOccurrence.occurred_at <= until,
            )
        )
        return int(count or 0)

    async def mark_draft_pending(self, candidate_id: int) -> KnowledgeCandidate:
        """开启新一代草稿生成并清除旧草稿正文。"""
        candidate = await self._require(candidate_id)
        candidate.draft_generation += 1
        candidate.draft_attempts = 0
        candidate.draft_status = KnowledgeCandidateDraftStatus.PENDING
        candidate.draft_payload = None
        candidate.notification_pending = True
        await self._session.flush()
        return candidate

    async def mark_draft_ready(
        self,
        candidate_id: int,
        payload: dict[str, Any],
    ) -> KnowledgeCandidate:
        """保存结构化草稿并记录本轮使用的示例版本。"""
        candidate = await self._require(candidate_id)
        candidate.draft_status = KnowledgeCandidateDraftStatus.READY
        candidate.draft_payload = payload
        candidate.draft_examples_version = candidate.examples_version
        candidate.draft_attempts = 0
        await self._session.flush()
        return candidate

    async def increment_draft_attempts(
        self, candidate_id: int
    ) -> KnowledgeCandidate:
        """持久化一次草稿生成失败，供 worker 跨重试判断上限。"""
        candidate = await self._require(candidate_id)
        candidate.draft_attempts += 1
        await self._session.flush()
        return candidate

    async def mark_draft_failed(self, candidate_id: int) -> KnowledgeCandidate:
        """记录草稿最终失败，保留脱敏示例供管理员人工归纳。"""
        candidate = await self._require(candidate_id)
        candidate.draft_status = KnowledgeCandidateDraftStatus.FAILED
        candidate.draft_payload = None
        candidate.notification_pending = True
        await self._session.flush()
        return candidate

    async def mark_notified(
        self,
        candidate_id: int,
        *,
        reminded_at: datetime,
    ) -> KnowledgeCandidate:
        """在管理员通知入队后更新本轮提醒游标。"""
        candidate = await self._require(candidate_id)
        candidate.notification_pending = False
        candidate.last_reminded_total = candidate.total_occurrences
        candidate.last_reminded_at = reminded_at
        await self._session.flush()
        return candidate

    async def snooze(
        self,
        candidate_id: int,
        *,
        until: datetime,
    ) -> KnowledgeCandidate:
        """关闭候选至指定时间并立即删除草稿、示例和出现明细。"""
        candidate = await self._require(candidate_id)
        candidate.status = KnowledgeCandidateStatus.SNOOZED
        candidate.snoozed_until = until
        self._clear_private_content(candidate)
        await self._delete_occurrences(candidate_id)
        await self._session.flush()
        return candidate

    async def convert(
        self,
        candidate_id: int,
        *,
        knowledge_entry_id: int,
    ) -> KnowledgeCandidate:
        """关联正式知识并停止候选后续统计。"""
        candidate = await self._require(candidate_id)
        candidate.status = KnowledgeCandidateStatus.CONVERTED
        candidate.knowledge_entry_id = knowledge_entry_id
        candidate.snoozed_until = None
        self._clear_private_content(candidate)
        await self._delete_occurrences(candidate_id)
        await self._session.flush()
        return candidate

    async def prune_occurrences(self, *, before: datetime) -> int:
        """删除滚动窗口之前的出现明细，只保留候选累计次数。"""
        result = cast(
            CursorResult[Any],
            await self._session.execute(
                delete(KnowledgeCandidateOccurrence).where(
                    KnowledgeCandidateOccurrence.occurred_at < before
                )
            ),
        )
        await self._session.flush()
        return int(result.rowcount)

    async def _require(self, candidate_id: int) -> KnowledgeCandidate:
        """读取必需候选，不存在时抛出稳定异常。"""
        candidate = await self.get(candidate_id)
        if candidate is None:
            raise LookupError(f"FAQ 候选不存在: {candidate_id}")
        return candidate

    async def _reopen_expired(self, *, now: datetime) -> None:
        """关闭期满后清空旧周期并按当前累计数设置新阈值基线。"""
        expired = await self._session.scalars(
            select(KnowledgeCandidate).where(
                KnowledgeCandidate.status == KnowledgeCandidateStatus.SNOOZED,
                KnowledgeCandidate.snoozed_until <= now,
            )
        )
        for candidate in expired:
            candidate.status = KnowledgeCandidateStatus.OPEN
            candidate.snoozed_until = None
            candidate.last_threshold_total = candidate.total_occurrences
            candidate.last_reminded_total = candidate.total_occurrences
            candidate.last_reminded_at = None
            # 自动重开属于系统动作，审计不复制问题、示例或草稿正文。
            self._session.add(
                AuditLog(
                    actor_employee_id=None,
                    action="faq_candidate.reopen",
                    target_type="knowledge_candidate",
                    target_id=str(candidate.id),
                    details={"candidate_id": candidate.id},
                )
            )
            candidate.notification_pending = False
            await self._delete_occurrences(candidate.id)
        await self._session.flush()

    async def _delete_occurrences(self, candidate_id: int) -> None:
        """显式删除出现明细，兼容未启用外键级联的 SQLite。"""
        await self._session.execute(
            delete(KnowledgeCandidateOccurrence).where(
                KnowledgeCandidateOccurrence.candidate_id == candidate_id
            )
        )

    @staticmethod
    def _clear_private_content(candidate: KnowledgeCandidate) -> None:
        """删除脱敏示例和未采用草稿，避免处理完成后继续保留正文。"""
        candidate.examples = []
        candidate.examples_version = 0
        candidate.draft_status = KnowledgeCandidateDraftStatus.NONE
        candidate.draft_payload = None
        candidate.draft_attempts = 0
        candidate.draft_examples_version = 0
        candidate.notification_pending = False
