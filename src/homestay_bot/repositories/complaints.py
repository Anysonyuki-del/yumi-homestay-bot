import re
from datetime import datetime
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from homestay_bot.domain.enums import ComplaintReviewStatus
from homestay_bot.domain.models import ComplaintReview


class ComplaintVersionConflict(ValueError):
    """表示员工正在编辑过期版本的客诉草稿。"""


def _sanitize_text(value: str) -> str:
    """遮盖常见联系方式和长数字，避免分析结果复制敏感信息。"""
    value = re.sub(r"(?<!\d)\d{11}(?!\d)", "[手机号已脱敏]", value)
    value = re.sub(
        r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}",
        "[邮箱已脱敏]",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(r"(?<!\d)\d{12,}(?!\d)", "[编号已脱敏]", value)
    return value[:4000]


def _sanitize_analysis(value: Any) -> Any:
    """递归限制分析结构并脱敏字符串值。"""
    if isinstance(value, str):
        return _sanitize_text(value)
    if isinstance(value, list):
        return [_sanitize_analysis(item) for item in value[:20]]
    if isinstance(value, dict):
        return {
            str(key)[:64]: _sanitize_analysis(item)
            for key, item in list(value.items())[:40]
        }
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    return str(value)[:4000]


class SQLAlchemyComplaintRepository:
    """持久化脱敏客诉记录，并用版本号保护人工编辑。"""

    def __init__(self, session: AsyncSession) -> None:
        """绑定当前业务事务。"""
        self._session = session

    async def get(self, review_id: int) -> ComplaintReview | None:
        """按主键读取客诉记录。"""
        return await self._session.get(ComplaintReview, review_id)

    async def create_or_get(
        self,
        *,
        conversation_id: int,
        source_message_id: str,
        reason: str,
        risk_level: str,
    ) -> ComplaintReview:
        """按来源消息幂等创建客诉记录。"""
        existing = await self._session.scalar(
            select(ComplaintReview).where(
                ComplaintReview.source_message_id == source_message_id
            )
        )
        if existing is not None:
            return existing
        review = ComplaintReview(
            conversation_id=conversation_id,
            source_message_id=source_message_id,
            reason=reason[:64],
            risk_level=risk_level[:32],
        )
        self._session.add(review)
        try:
            await self._session.flush()
        except IntegrityError:
            await self._session.rollback()
            existing = await self._session.scalar(
                select(ComplaintReview).where(
                    ComplaintReview.source_message_id == source_message_id
                )
            )
            if existing is None:
                raise
            return cast(ComplaintReview, existing)
        return review

    async def mark_ready(
        self,
        review_id: int,
        *,
        analysis: dict[str, Any],
        draft: str,
    ) -> ComplaintReview:
        """保存脱敏分析和草稿，进入人工复核状态。"""
        review = await self._require(review_id)
        if review.status not in {
            ComplaintReviewStatus.PENDING_ANALYSIS,
            ComplaintReviewStatus.ANALYSIS_FAILED,
            ComplaintReviewStatus.RETURNED,
        }:
            raise ValueError("当前客诉状态不允许写入分析")
        review.analysis = _sanitize_analysis(analysis)
        review.draft = _sanitize_text(draft)
        review.status = ComplaintReviewStatus.READY_FOR_REVIEW
        review.version += 1
        await self._session.flush()
        return review

    async def update_draft(
        self,
        review_id: int,
        *,
        expected_version: int,
        draft: str,
    ) -> ComplaintReview:
        """按版本更新员工编辑内容。"""
        review = await self._require(review_id)
        self._check_version(review, expected_version)
        if review.status not in {
            ComplaintReviewStatus.READY_FOR_REVIEW,
            ComplaintReviewStatus.EDITING,
        }:
            raise ValueError("当前客诉状态不允许编辑")
        review.draft = _sanitize_text(draft)
        review.status = ComplaintReviewStatus.EDITING
        review.version += 1
        await self._session.flush()
        return review

    async def mark_sent(
        self,
        review_id: int,
        *,
        expected_version: int,
        sent_at: datetime,
    ) -> ComplaintReview:
        """按版本把人工确认后的草稿标记为已发送。"""
        review = await self._require(review_id)
        self._check_version(review, expected_version)
        if review.status not in {
            ComplaintReviewStatus.READY_FOR_REVIEW,
            ComplaintReviewStatus.EDITING,
        }:
            raise ValueError("当前客诉状态不允许发送")
        review.status = ComplaintReviewStatus.SENT
        review.sent_at = sent_at
        review.version += 1
        await self._session.flush()
        return review

    async def mark_returned(
        self, review_id: int, *, expected_version: int
    ) -> ComplaintReview:
        """按版本退回客诉，允许后台重新生成分析。"""
        review = await self._require(review_id)
        self._check_version(review, expected_version)
        review.status = ComplaintReviewStatus.RETURNED
        review.version += 1
        await self._session.flush()
        return review

    async def mark_cancelled(
        self, review_id: int, *, expected_version: int
    ) -> ComplaintReview:
        """按版本关闭客诉，避免继续发送草稿。"""
        review = await self._require(review_id)
        self._check_version(review, expected_version)
        review.status = ComplaintReviewStatus.CANCELLED
        review.version += 1
        await self._session.flush()
        return review

    async def _require(self, review_id: int) -> ComplaintReview:
        """读取客诉记录，不存在时返回明确错误。"""
        review = await self.get(review_id)
        if review is None:
            raise LookupError("客诉记录不存在")
        return review

    @staticmethod
    def _check_version(review: ComplaintReview, expected_version: int) -> None:
        """拒绝覆盖其他员工已经提交的新版本。"""
        if review.version != expected_version:
            raise ComplaintVersionConflict("客诉草稿已被其他员工更新")
