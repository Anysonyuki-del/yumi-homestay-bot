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

    async def list_open(self, *, offset: int, limit: int) -> list[ComplaintReview]:
        """按最近更新时间分页返回尚未结束的客诉复核。"""
        statement = (
            select(ComplaintReview)
            .where(
                ComplaintReview.status.not_in(
                    (
                        ComplaintReviewStatus.SENT,
                        ComplaintReviewStatus.CANCELLED,
                    )
                )
            )
            .order_by(ComplaintReview.updated_at.desc(), ComplaintReview.id.desc())
            .offset(offset)
            .limit(limit)
        )
        return list((await self._session.scalars(statement)).all())

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
        try:
            # 唯一键竞争只回滚当前保存点，不能破坏调用方事务中的其他写入。
            async with self._session.begin_nested():
                self._session.add(review)
                await self._session.flush()
        except IntegrityError:
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

    async def mark_send_queued(
        self,
        review_id: int,
        *,
        expected_version: int,
        outbox_id: str,
    ) -> ComplaintReview:
        """保存客诉出站任务已入队，等待 worker 回写真实投递结果。"""
        review = await self._require(review_id)
        self._check_version(review, expected_version)
        if review.status not in {
            ComplaintReviewStatus.READY_FOR_REVIEW,
            ComplaintReviewStatus.EDITING,
            ComplaintReviewStatus.DELIVERY_FAILED,
        }:
            raise ValueError("当前客诉状态不允许发送")
        review.status = ComplaintReviewStatus.SEND_QUEUED
        review.delivery_error_code = None
        review.delivery_outbox_id = outbox_id[:128]
        review.delivery_external_message_id = None
        review.sent_at = None
        review.version += 1
        await self._session.flush()
        return review

    async def mark_delivery_failed(
        self,
        review_id: int,
        *,
        error_code: str,
    ) -> ComplaintReview:
        """记录企业微信实际投递失败，保留安全错误类型供后台重试。"""
        review = await self._require(review_id)
        if review.status not in {
            ComplaintReviewStatus.SEND_QUEUED,
            ComplaintReviewStatus.DELIVERY_FAILED,
        }:
            return review
        review.status = ComplaintReviewStatus.DELIVERY_FAILED
        review.delivery_error_code = error_code[:64]
        review.sent_at = None
        review.version += 1
        await self._session.flush()
        return review

    async def mark_delivery_sent(
        self,
        review_id: int,
        *,
        sent_at: datetime,
        external_message_id: str,
    ) -> ComplaintReview:
        """在企业微信返回真实消息编号后标记客诉已实际发送。"""
        review = await self._require(review_id)
        if review.status not in {
            ComplaintReviewStatus.SEND_QUEUED,
            ComplaintReviewStatus.DELIVERY_FAILED,
        }:
            return review
        review.status = ComplaintReviewStatus.SENT
        review.sent_at = sent_at
        review.delivery_error_code = None
        review.delivery_external_message_id = external_message_id[:128]
        review.version += 1
        await self._session.flush()
        return review

    async def mark_delivery_failed_by_external_message_id(
        self,
        external_message_id: str,
        *,
        error_code: str,
    ) -> ComplaintReview | None:
        """按企业微信真实消息编号回写异步投递失败。"""
        review = await self._session.scalar(
            select(ComplaintReview).where(
                ComplaintReview.delivery_external_message_id == external_message_id
            )
        )
        if review is None:
            return None
        if review.status not in {
            ComplaintReviewStatus.SENT,
            ComplaintReviewStatus.SEND_QUEUED,
            ComplaintReviewStatus.DELIVERY_FAILED,
        }:
            return review
        review.status = ComplaintReviewStatus.DELIVERY_FAILED
        review.sent_at = None
        review.delivery_error_code = error_code[:64]
        review.version += 1
        await self._session.flush()
        return review

    async def mark_delivery_failed_by_outbox_id(
        self,
        outbox_id: str,
        *,
        error_code: str,
    ) -> ComplaintReview | None:
        """按遗留出站任务编号回写 worker 崩溃导致的投递失败。"""
        review = await self._session.scalar(
            select(ComplaintReview).where(
                ComplaintReview.delivery_outbox_id == outbox_id
            )
        )
        if review is None:
            return None
        if review.status not in {
            ComplaintReviewStatus.SEND_QUEUED,
            ComplaintReviewStatus.DELIVERY_FAILED,
        }:
            return review
        review.status = ComplaintReviewStatus.DELIVERY_FAILED
        review.sent_at = None
        review.delivery_error_code = error_code[:64]
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
