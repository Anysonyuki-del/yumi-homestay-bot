import hashlib
import re
import unicodedata
from datetime import datetime, timedelta
from typing import Any, cast

from sqlalchemy import case, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
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
        await self.reopen_expired(now=now)
        result = await self._session.scalars(
            select(KnowledgeCandidate)
            .where(KnowledgeCandidate.status == KnowledgeCandidateStatus.OPEN)
            .order_by(KnowledgeCandidate.updated_at.desc(), KnowledgeCandidate.id.desc())
            .limit(limit)
            .execution_options(populate_existing=True)
        )
        return list(result.all())

    async def get_or_create(
        self,
        *,
        canonical_question: str,
        category: str,
    ) -> KnowledgeCandidate:
        """用数据库冲突忽略原子复用候选，避免先查后插的并发竞态。"""
        key = _canonical_key(canonical_question)
        values = {
            "canonical_key": key,
            "canonical_question": canonical_question.strip(),
            "category": category.strip(),
            "status": KnowledgeCandidateStatus.OPEN,
            "total_occurrences": 0,
            "last_threshold_total": 0,
            "last_reminded_total": 0,
            "notification_pending": False,
            "examples": [],
            "examples_version": 0,
            "draft_status": KnowledgeCandidateDraftStatus.NONE,
            "draft_generation": 0,
            "draft_attempts": 0,
            "draft_examples_version": 0,
        }
        dialect_name = self._session.get_bind().dialect.name
        statement: Any
        if dialect_name == "sqlite":
            statement = sqlite_insert(KnowledgeCandidate).values(**values)
        elif dialect_name == "postgresql":
            statement = postgresql_insert(KnowledgeCandidate).values(**values)
        else:
            raise RuntimeError(f"不支持的 FAQ 候选数据库方言: {dialect_name}")
        await self._session.execute(
            statement.on_conflict_do_nothing(
                index_elements=[KnowledgeCandidate.canonical_key]
            )
        )
        candidate = await self._session.scalar(
            select(KnowledgeCandidate).where(
                KnowledgeCandidate.canonical_key == key
            )
        )
        if candidate is None:
            raise RuntimeError("FAQ 候选原子创建后无法读取")
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
        candidate = await self._session.scalar(
            select(KnowledgeCandidate)
            .where(KnowledgeCandidate.id == candidate_id)
            .with_for_update()
        )
        if candidate is None:
            raise LookupError(f"FAQ 候选不存在: {candidate_id}")
        if candidate.status is not KnowledgeCandidateStatus.OPEN:
            return False

        occurrence_values = {
            "candidate_id": candidate_id,
            "source_message_id": source_message_id,
            "occurred_at": occurred_at,
        }
        dialect_name = self._session.get_bind().dialect.name
        occurrence_insert: Any
        if dialect_name == "sqlite":
            occurrence_insert = sqlite_insert(
                KnowledgeCandidateOccurrence
            ).values(**occurrence_values)
        elif dialect_name == "postgresql":
            occurrence_insert = postgresql_insert(
                KnowledgeCandidateOccurrence
            ).values(**occurrence_values)
        else:
            raise RuntimeError(f"不支持的 FAQ 明细数据库方言: {dialect_name}")
        inserted = cast(
            CursorResult[Any],
            await self._session.execute(
                occurrence_insert.on_conflict_do_nothing(
                    index_elements=[
                        KnowledgeCandidateOccurrence.source_message_id
                    ]
                )
            )
        )
        if inserted.rowcount == 0:
            return False

        # 累计数必须由数据库原子自增，不能依赖并发会话各自读到的旧值。
        await self._session.execute(
            update(KnowledgeCandidate)
            .where(KnowledgeCandidate.id == candidate_id)
            .values(
                total_occurrences=KnowledgeCandidate.total_occurrences + 1,
                last_seen_at=case(
                    (
                        KnowledgeCandidate.last_seen_at.is_(None),
                        occurred_at,
                    ),
                    (
                        KnowledgeCandidate.last_seen_at < occurred_at,
                        occurred_at,
                    ),
                    else_=KnowledgeCandidate.last_seen_at,
                ),
            )
        )
        await self._session.refresh(candidate)
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
        *,
        expected_generation: int | None = None,
    ) -> KnowledgeCandidate | None:
        """保存结构化草稿并记录本轮使用的示例版本。"""
        candidate = await self._require(candidate_id)
        if expected_generation is not None:
            result = await self._session.execute(
                update(KnowledgeCandidate)
                .where(
                    KnowledgeCandidate.id == candidate_id,
                    KnowledgeCandidate.status
                    == KnowledgeCandidateStatus.OPEN,
                    KnowledgeCandidate.draft_generation
                    == expected_generation,
                )
                .values(
                    draft_status=KnowledgeCandidateDraftStatus.READY,
                    draft_payload=payload,
                    draft_examples_version=(
                        KnowledgeCandidate.examples_version
                    ),
                    draft_attempts=0,
                )
                .returning(KnowledgeCandidate.id)
                .execution_options(synchronize_session=False)
            )
            if result.scalar_one_or_none() is None:
                return None
            await self._session.refresh(candidate)
            return candidate
        candidate.draft_status = KnowledgeCandidateDraftStatus.READY
        candidate.draft_payload = payload
        candidate.draft_examples_version = candidate.examples_version
        candidate.draft_attempts = 0
        await self._session.flush()
        return candidate

    async def increment_draft_attempts(
        self,
        candidate_id: int,
        *,
        expected_generation: int | None = None,
    ) -> KnowledgeCandidate | None:
        """持久化一次草稿生成失败，供 worker 跨重试判断上限。"""
        candidate = await self._require(candidate_id)
        if expected_generation is not None:
            result = await self._session.execute(
                update(KnowledgeCandidate)
                .where(
                    KnowledgeCandidate.id == candidate_id,
                    KnowledgeCandidate.status
                    == KnowledgeCandidateStatus.OPEN,
                    KnowledgeCandidate.draft_generation
                    == expected_generation,
                )
                .values(
                    draft_attempts=KnowledgeCandidate.draft_attempts + 1
                )
                .returning(KnowledgeCandidate.id)
                .execution_options(synchronize_session=False)
            )
            if result.scalar_one_or_none() is None:
                return None
            await self._session.refresh(candidate)
            return candidate
        candidate.draft_attempts += 1
        await self._session.flush()
        return candidate

    async def mark_draft_failed(
        self,
        candidate_id: int,
        *,
        expected_generation: int | None = None,
    ) -> KnowledgeCandidate | None:
        """记录草稿最终失败，保留脱敏示例供管理员人工归纳。"""
        candidate = await self._require(candidate_id)
        if expected_generation is not None:
            result = await self._session.execute(
                update(KnowledgeCandidate)
                .where(
                    KnowledgeCandidate.id == candidate_id,
                    KnowledgeCandidate.status
                    == KnowledgeCandidateStatus.OPEN,
                    KnowledgeCandidate.draft_generation
                    == expected_generation,
                )
                .values(
                    draft_status=KnowledgeCandidateDraftStatus.FAILED,
                    draft_payload=None,
                    notification_pending=True,
                )
                .returning(KnowledgeCandidate.id)
                .execution_options(synchronize_session=False)
            )
            if result.scalar_one_or_none() is None:
                return None
            await self._session.refresh(candidate)
            return candidate
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
        # 用数据库当前累计数原子推进两个游标，避免 DeepSeek 调用期间新增次数
        # 被当前会话 identity map 中的陈旧对象覆盖。
        await self._session.execute(
            update(KnowledgeCandidate)
            .where(KnowledgeCandidate.id == candidate_id)
            .values(
                notification_pending=False,
                last_reminded_total=KnowledgeCandidate.total_occurrences,
                last_threshold_total=KnowledgeCandidate.total_occurrences,
                last_reminded_at=reminded_at,
            )
            .execution_options(synchronize_session=False)
        )
        await self._session.refresh(candidate)
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

    async def maintain(self, *, now: datetime) -> tuple[int, int]:
        """周期清理窗口外明细并重开关闭期已满的候选。"""
        removed = await self.prune_occurrences(
            before=now - timedelta(hours=72)
        )
        reopened = await self.reopen_expired(now=now)
        return removed, reopened

    async def _require(self, candidate_id: int) -> KnowledgeCandidate:
        """读取必需候选，不存在时抛出稳定异常。"""
        candidate = await self.get(candidate_id)
        if candidate is None:
            raise LookupError(f"FAQ 候选不存在: {candidate_id}")
        return candidate

    async def reopen_expired(self, *, now: datetime) -> int:
        """关闭期满后清空旧周期的累计数、游标和出现明细。"""
        result = await self._session.execute(
            update(KnowledgeCandidate)
            .where(
                KnowledgeCandidate.status == KnowledgeCandidateStatus.SNOOZED,
                KnowledgeCandidate.snoozed_until <= now,
            )
            .values(
                status=KnowledgeCandidateStatus.OPEN,
                snoozed_until=None,
                total_occurrences=0,
                last_seen_at=None,
                last_threshold_total=0,
                last_reminded_total=0,
                last_reminded_at=None,
                notification_pending=False,
            )
            .returning(KnowledgeCandidate.id)
            .execution_options(synchronize_session=False)
        )
        candidate_ids = list(result.scalars().all())
        for candidate_id in candidate_ids:
            # 自动重开属于系统动作，审计不复制问题、示例或草稿正文。
            self._session.add(
                AuditLog(
                    actor_employee_id=None,
                    action="faq_candidate.reopen",
                    target_type="knowledge_candidate",
                    target_id=str(candidate_id),
                    details={"candidate_id": candidate_id},
                )
            )
            await self._delete_occurrences(candidate_id)
        await self._session.flush()
        return len(candidate_ids)

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
        # 关闭或转换时废止所有已经排队的旧代次草稿任务。
        candidate.draft_generation += 1
        candidate.draft_status = KnowledgeCandidateDraftStatus.NONE
        candidate.draft_payload = None
        candidate.draft_attempts = 0
        candidate.draft_examples_version = 0
        candidate.notification_pending = False
